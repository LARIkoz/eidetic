#!/usr/bin/env python3
"""Public Core worker for the Eidetic Engine JSONL protocol v1.

The worker is the storage boundary used by ``eidetic-sdk``. Public messages use
logical index IDs and source-owned record IDs; filesystem paths, SQLite layout,
embeddings, and internal row IDs never cross stdout.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import re
import sqlite3
import sys
import uuid
from pathlib import Path
from urllib.parse import quote


PROTOCOL = "eidetic.engine"
PROTOCOL_VERSION = "1.0"
OPERATIONS = ("capabilities", "health", "reconcile", "sync", "search", "rerank")
INDEX_ID_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
MAX_RECORDS = 100_000
EMBED_BATCH = 64


def _load_engine_module():
    path = Path(__file__).resolve().with_name("engine.py")
    # Core-private embed.py still imports its MLX sibling by module name. Keep
    # that compatibility path inside the Core process; it never crosses the
    # SDK protocol or consumer environment.
    sibling_root = str(path.parent)
    if sibling_root not in sys.path:
        sys.path.insert(0, sibling_root)
    spec = importlib.util.spec_from_file_location("eidetic_public_engine", path)
    if spec is None or spec.loader is None:
        raise ImportError("public Engine module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProtocolError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


class EngineWorker:
    def __init__(self, engine_module=None, runtime_root=None, index_map=None):
        self.engine = engine_module or _load_engine_module()
        provider = os.environ.get("EIDETIC_SDK_ENGINE_PROVIDER")
        threads = os.environ.get("EIDETIC_SDK_ENGINE_THREADS")
        if provider or threads:
            try:
                self.engine.configure(
                    provider=provider or None,
                    threads=int(threads) if threads else None,
                )
            except (TypeError, ValueError) as exc:
                raise ProtocolError("invalid_request", "Core execution configuration is invalid") from exc
        self.runtime_root = Path(
            runtime_root
            or os.environ.get("EIDETIC_MEMORY_SYSTEM")
            or Path(__file__).resolve().parent.parent
        ).expanduser().resolve()
        self.state_root = Path(
            os.environ.get("EIDETIC_SDK_STATE_ROOT")
            or self.runtime_root / "sdk-state"
        ).expanduser().resolve()
        self.index_map = dict(index_map or self._load_index_map())
        self.session_id = uuid.uuid4().hex

    def _load_index_map(self) -> dict[str, str]:
        raw = os.environ.get("EIDETIC_SDK_INDEX_MAP", "").strip()
        data = None
        if raw:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ProtocolError("invalid_request", "Core index configuration is invalid") from exc
        else:
            config = self.runtime_root / ".sdk-indexes.json"
            if config.is_file():
                try:
                    data = json.loads(config.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise ProtocolError("invalid_request", "Core index configuration is invalid") from exc
        if data is None:
            return {}
        if not isinstance(data, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in data.items()
        ):
            raise ProtocolError("invalid_request", "Core index configuration is invalid")
        return data

    @staticmethod
    def _index_id(value) -> str:
        if not isinstance(value, str) or not INDEX_ID_RE.fullmatch(value):
            raise ProtocolError("invalid_request", "index_id is invalid")
        return value

    def _index_path(self, index_id: str) -> Path:
        index_id = self._index_id(index_id)
        configured = self.index_map.get(index_id)
        if configured:
            return Path(configured).expanduser().resolve()
        return (self.state_root / "indices" / f"{index_id}.sqlite").resolve()

    @staticmethod
    def _require_string(record: dict, name: str, *, allow_empty=False, maximum=1_000_000) -> str:
        value = record.get(name)
        if not isinstance(value, str) or (not allow_empty and not value) or len(value) > maximum:
            raise ProtocolError("invalid_request", f"record {name} is invalid")
        return value

    @staticmethod
    def _chunk_id(record_id: str) -> int:
        return int(hashlib.sha1(record_id.encode("utf-8")).hexdigest()[:15], 16)

    def _records(self, payload: dict) -> tuple[list[dict], str, str]:
        values = payload.get("records")
        snapshot = payload.get("snapshot")
        if snapshot not in ("complete", "partial"):
            raise ProtocolError("invalid_request", "snapshot must be complete or partial")
        if not isinstance(values, list) or len(values) > MAX_RECORDS:
            raise ProtocolError("invalid_request", "records must be a bounded array")
        normalized = []
        seen_records: set[str] = set()
        seen_chunks: dict[int, str] = {}
        for value in values:
            if not isinstance(value, dict) or set(value) != {
                "record_id", "object_id", "title", "kind", "text"
            }:
                raise ProtocolError("invalid_request", "record fields are invalid")
            record_id = self._require_string(value, "record_id", maximum=512)
            object_id = self._require_string(value, "object_id", maximum=512)
            title = self._require_string(value, "title", allow_empty=True, maximum=4096)
            kind = self._require_string(value, "kind", maximum=128)
            text = self._require_string(value, "text", maximum=1_000_000)
            if record_id in seen_records:
                raise ProtocolError("invalid_request", "record_id values must be unique")
            seen_records.add(record_id)
            chunk_id = self._chunk_id(record_id)
            collision = seen_chunks.get(chunk_id)
            if collision is not None and collision != record_id:
                raise ProtocolError("invalid_request", "record identity collision")
            seen_chunks[chunk_id] = record_id
            digest = self.engine.content_hash(title, "", text, "")
            normalized.append({
                "record_id": record_id,
                "object_id": object_id,
                "title": title,
                "kind": kind,
                "text": text,
                "chunk_id": chunk_id,
                "digest": digest,
            })
        source = hashlib.sha256()
        for record in sorted(normalized, key=lambda item: item["record_id"]):
            source.update(record["record_id"].encode("utf-8"))
            source.update(b"\0")
            source.update(record["digest"].encode("ascii"))
            source.update(b"\n")
        return normalized, snapshot, source.hexdigest()

    @staticmethod
    def _ro_connection(path: Path) -> sqlite3.Connection:
        uri = f"file:{quote(str(path), safe='/')}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        connection.execute("PRAGMA query_only=ON")
        return connection

    def _index_state(self, path: Path) -> tuple[dict[int, str], dict[str, str]]:
        if not path.is_file():
            return {}, {}
        try:
            connection = self._ro_connection(path)
            try:
                hashes = {
                    int(chunk_id): (digest or "")
                    for chunk_id, digest in connection.execute(
                        "SELECT chunk_id, content_hash FROM vectors"
                    )
                }
                try:
                    stamps = {
                        str(key): "" if value is None else str(value)
                        for key, value in connection.execute("SELECT key, value FROM meta")
                    }
                except sqlite3.Error:
                    stamps = {}
                return hashes, stamps
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise ProtocolError("stale_index", "Derived index is unreadable") from exc

    def _model_info(self) -> dict:
        info = dict(self.engine.model_info())
        return {
            "model": str(info.get("model") or "unknown"),
            "dim": int(info.get("dim") or 0),
            "hash_scheme": str(info.get("hash_scheme") or "unknown"),
            "fastembed": info.get("fastembed"),
            "engine_api": str(info.get("engine_api") or getattr(self.engine, "ENGINE_API", "0.0")),
            "profile": str(info.get("profile") or "unknown"),
        }

    def _stamps_match(self, stamps: dict[str, str]) -> bool:
        if not stamps:
            return False
        info = self._model_info()
        expected = {
            "model": info["model"],
            "dim": str(info["dim"]),
            "hash_scheme": info["hash_scheme"],
            "fastembed_version": "" if info["fastembed"] is None else str(info["fastembed"]),
        }
        return all(stamps.get(key, "") == value for key, value in expected.items())

    def _core_release(self) -> str:
        metadata = self.runtime_root / ".installed.json"
        if metadata.is_file():
            try:
                value = json.loads(metadata.read_text(encoding="utf-8")).get("version")
                if isinstance(value, str) and value:
                    return value
            except (OSError, json.JSONDecodeError):
                pass
        return "source"

    def _receipt(
        self,
        *,
        index_id: str,
        snapshot: str,
        source_digest: str,
        status: str,
        expected: int,
        indexed: int,
        missing: int,
        changed: int,
        orphaned: int,
        upserted: int = 0,
        deleted: int = 0,
        repair=None,
    ) -> dict:
        info = self._model_info()
        identity = json.dumps({
            "index_id": index_id,
            "snapshot": snapshot,
            "source_digest": source_digest,
            "status": status,
            "expected": expected,
            "indexed": indexed,
            "missing": missing,
            "changed": changed,
            "orphaned": orphaned,
            "upserted": upserted,
            "deleted": deleted,
            "engine_api": info["engine_api"],
            "model": info["model"],
        }, sort_keys=True, separators=(",", ":"))
        return {
            "receipt_id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            "index_id": index_id,
            "snapshot": snapshot,
            "source_digest": source_digest,
            "status": status,
            "expected": expected,
            "indexed": indexed,
            "missing": missing,
            "changed": changed,
            "orphaned": orphaned,
            "upserted": upserted,
            "deleted": deleted,
            "engine_api": info["engine_api"],
            "model": info["model"],
            "observed_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "repair": repair,
        }

    def capabilities(self, payload: dict) -> dict:
        if payload:
            raise ProtocolError("invalid_request", "capabilities payload must be empty")
        info = self._model_info()
        return {
            "protocol": PROTOCOL,
            "protocol_version": PROTOCOL_VERSION,
            "core_release": self._core_release(),
            "engine_api": info["engine_api"],
            "operations": list(OPERATIONS),
            "model": info,
            "session_id": self.session_id,
            "storage_identity": "logical-index-only",
        }

    def health(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or set(payload) - {"index_id"}:
            raise ProtocolError("invalid_request", "health payload is invalid")
        result = {
            "runtime": "available",
            "protocol_version": PROTOCOL_VERSION,
            "engine_api": self._model_info()["engine_api"],
            "session_id": self.session_id,
        }
        if "index_id" in payload:
            index_id = self._index_id(payload["index_id"])
            path = self._index_path(index_id)
            hashes, stamps = self._index_state(path)
            result["index"] = {
                "index_id": index_id,
                "available": path.is_file(),
                "vectors": len(hashes),
                "stamped": self._stamps_match(stamps) if hashes else False,
            }
        return result

    def reconcile(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or set(payload) - {
            "index_id", "records", "snapshot"
        }:
            raise ProtocolError("invalid_request", "reconcile payload is invalid")
        index_id = self._index_id(payload.get("index_id"))
        records, snapshot, source_digest = self._records(payload)
        existing, stamps = self._index_state(self._index_path(index_id))
        current = {record["chunk_id"]: record for record in records}
        missing_records = [record for cid, record in current.items() if cid not in existing]
        changed_records = [
            record for cid, record in current.items()
            if cid in existing and existing[cid] != record["digest"]
        ]
        stamp_drift = bool(existing) and not self._stamps_match(stamps)
        if stamp_drift:
            changed_records = list(records)
        orphaned = len(set(existing) - set(current)) if snapshot == "complete" else 0
        status = "fresh"
        repair = None
        if missing_records or changed_records or orphaned:
            status = "degraded" if stamp_drift else "stale"
            repair = {"operation": "rebuild" if stamp_drift else "sync", "bounded": True}
        receipt = self._receipt(
            index_id=index_id,
            snapshot=snapshot,
            source_digest=source_digest,
            status=status,
            expected=len(current),
            indexed=len(existing),
            missing=len(missing_records),
            changed=len(changed_records),
            orphaned=orphaned,
            repair=repair,
        )
        return {
            "receipt": receipt,
            "pending_record_ids": sorted({
                record["record_id"] for record in missing_records + changed_records
            }),
        }

    def sync(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or set(payload) - {
            "index_id", "records", "snapshot", "force"
        }:
            raise ProtocolError("invalid_request", "sync payload is invalid")
        index_id = self._index_id(payload.get("index_id"))
        records, snapshot, source_digest = self._records(payload)
        force = payload.get("force", False)
        if not isinstance(force, bool):
            raise ProtocolError("invalid_request", "force must be boolean")
        path = self._index_path(index_id)
        existing, stamps = self._index_state(path)
        stamp_drift = bool(existing) and not self._stamps_match(stamps)
        if stamp_drift and snapshot != "complete":
            raise ProtocolError(
                "stale_index",
                "A complete snapshot is required after an Engine stamp change",
                details={"repair": "rebuild"},
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_handle = self.engine.acquire_build_lock(str(path))
        if lock_handle is None:
            raise ProtocolError("core_busy", "Derived index writer is busy", retryable=True)
        index = None
        try:
            index = self.engine.open_index(str(path))
            existing = index.existing_hashes()
            current = {record["chunk_id"]: record for record in records}
            if stamp_drift or force:
                to_embed = list(records)
            else:
                to_embed = [
                    record for cid, record in current.items()
                    if cid not in existing or existing[cid] != record["digest"]
                ]
            upserted = 0
            for offset in range(0, len(to_embed), EMBED_BATCH):
                batch = to_embed[offset:offset + EMBED_BATCH]
                texts = [
                    self.engine.embedding_text(record["title"], "", record["text"], "")
                    for record in batch
                ]
                embeddings = self.engine.embed_passages(texts)
                rows = [{
                    "chunk_id": record["chunk_id"],
                    "path": record["object_id"],
                    "name": record["title"],
                    "section_heading": record["kind"],
                    "content_hash": record["digest"],
                    "embedding": embeddings[position],
                } for position, record in enumerate(batch)]
                upserted += index.upsert(rows)
            stale_ids = set(existing) - set(current) if snapshot == "complete" else set()
            deleted = index.delete(sorted(stale_ids)) if stale_ids else 0
            if to_embed or not existing or stamp_drift:
                index.stamp()
            indexed = len(existing) - len(stale_ids) + sum(
                1 for record in to_embed if record["chunk_id"] not in existing
            )
        finally:
            if index is not None:
                index.close()
            try:
                lock_handle.close()
            except Exception:
                pass
        receipt = self._receipt(
            index_id=index_id,
            snapshot=snapshot,
            source_digest=source_digest,
            status="fresh",
            expected=len(records),
            indexed=indexed,
            missing=0,
            changed=0,
            orphaned=0,
            upserted=upserted,
            deleted=deleted,
            repair=None,
        )
        return {"receipt": receipt}

    def search(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or set(payload) != {"index_id", "query", "limit"}:
            raise ProtocolError("invalid_request", "search payload is invalid")
        index_id = self._index_id(payload.get("index_id"))
        query = payload.get("query")
        limit = payload.get("limit")
        if not isinstance(query, str) or not query or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ProtocolError("invalid_request", "search query or limit is invalid")
        path = self._index_path(index_id)
        if not path.is_file():
            raise ProtocolError("index_not_found", "Logical derived index is absent")
        _, stamps = self._index_state(path)
        if not self._stamps_match(stamps):
            return {
                "status": "degraded",
                "hits": [],
                "degradation": {"code": "stale_index", "retryable": False},
                "session_id": self.session_id,
            }
        index = self.engine.open_index(str(path))
        notice = io.StringIO()
        try:
            with contextlib.redirect_stderr(notice):
                hits = index.search(query, limit=limit)
        finally:
            index.close()
        diagnostic = notice.getvalue().casefold()
        degraded = not hits and (
            "eidetic-engine:" in diagnostic
            or "drift" in diagnostic
            or "unavailable" in diagnostic
        )
        if degraded:
            print("eidetic-engine-worker: vector search degraded; returning no hits", file=sys.stderr)
        return {
            "status": "degraded" if degraded else "ok",
            "hits": [] if degraded else [{
                "score": float(hit["score"]),
                "object_id": str(hit["path"]),
                "title": str(hit.get("name") or ""),
                "kind": str(hit.get("section_heading") or ""),
            } for hit in hits],
            "degradation": (
                {"code": "engine_unavailable", "retryable": True} if degraded else None
            ),
            "session_id": self.session_id,
        }

    def rerank(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or set(payload) != {"query", "documents"}:
            raise ProtocolError("invalid_request", "rerank payload is invalid")
        query = payload.get("query")
        documents = payload.get("documents")
        if not isinstance(query, str) or not query or not isinstance(documents, list) or len(documents) > 1000:
            raise ProtocolError("invalid_request", "rerank query or documents are invalid")
        ids = []
        texts = []
        for document in documents:
            if not isinstance(document, dict) or set(document) != {"document_id", "text"}:
                raise ProtocolError("invalid_request", "rerank document is invalid")
            document_id = document.get("document_id")
            text = document.get("text")
            if not isinstance(document_id, str) or not document_id or not isinstance(text, str):
                raise ProtocolError("invalid_request", "rerank document is invalid")
            ids.append(document_id)
            texts.append(text)
        notice = io.StringIO()
        with contextlib.redirect_stderr(notice):
            scores = self.engine.rerank(query, texts)
        degraded = bool(texts) and len(scores) != len(texts)
        if degraded:
            print("eidetic-engine-worker: reranker degraded; returning no scores", file=sys.stderr)
        return {
            "status": "degraded" if degraded else "ok",
            "scores": [] if degraded else [
                {"document_id": ids[index], "score": float(score)}
                for index, score in enumerate(scores)
            ],
            "degradation": (
                {"code": "engine_unavailable", "retryable": True} if degraded else None
            ),
            "session_id": self.session_id,
        }

    def dispatch(self, operation: str, payload: dict) -> dict:
        if operation not in OPERATIONS:
            raise ProtocolError("unsupported_operation", "Operation is not supported")
        return getattr(self, operation)(payload)

    def handle(self, request) -> dict:
        request_id = "unknown"
        try:
            if not isinstance(request, dict):
                raise ProtocolError("invalid_request", "Request envelope must be an object")
            request_id = request.get("request_id", "unknown")
            if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
                request_id = "unknown"
                raise ProtocolError("invalid_request", "request_id is invalid")
            if set(request) != {"protocol", "version", "request_id", "operation", "payload"}:
                raise ProtocolError("invalid_request", "Request envelope fields are invalid")
            if request.get("protocol") != PROTOCOL:
                raise ProtocolError("invalid_request", "Protocol identifier is invalid")
            version = request.get("version")
            if version != PROTOCOL_VERSION:
                raise ProtocolError(
                    "incompatible_version",
                    "Requested protocol version is not supported",
                    details={"supported": [PROTOCOL_VERSION]},
                )
            payload = request.get("payload")
            if not isinstance(payload, dict):
                raise ProtocolError("invalid_request", "payload must be an object")
            result = self.dispatch(request.get("operation"), payload)
            return self._response(request_id, True, result=result)
        except ProtocolError as exc:
            return self._response(request_id, False, error={
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
                **({"details": exc.details} if exc.details else {}),
            })
        except getattr(self.engine, "EngineUnavailable", RuntimeError):
            return self._response(request_id, False, error={
                "code": "engine_unavailable",
                "message": "Engine runtime cannot complete the operation",
                "retryable": True,
            })
        except Exception as exc:  # fail closed; details stay on local stderr.
            print(f"eidetic-engine-worker: internal {type(exc).__name__}", file=sys.stderr)
            return self._response(request_id, False, error={
                "code": "internal_error",
                "message": "Core worker failed safely",
                "retryable": False,
            })

    @staticmethod
    def _response(request_id: str, ok: bool, *, result=None, error=None) -> dict:
        return {
            "protocol": PROTOCOL,
            "version": PROTOCOL_VERSION,
            "request_id": request_id,
            "ok": ok,
            "result": result if ok else None,
            "error": None if ok else error,
        }


def _decode_line(line: str):
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def serve(worker: EngineWorker, *, once=False) -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        request = _decode_line(line)
        response = worker.handle(request)
        print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)
        if once:
            break
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Eidetic Engine JSONL worker v1")
    parser.add_argument("--once", action="store_true", help="Handle one request and exit")
    args = parser.parse_args(argv)
    try:
        worker = EngineWorker()
    except ProtocolError as exc:
        print(f"eidetic-engine-worker: startup failed ({exc.code})", file=sys.stderr)
        return 2
    return serve(worker, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
