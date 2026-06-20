// Eidetic Apple translation backend — self-contained, no 3rd-party dependency.
// Uses the headless TranslationSession(installedSource:target:) initializer
// (macOS 15+, documented "for contexts where there's no UI" — no SwiftUI / NSApplication).
// Auto-detects source via NaturalLanguage when --from is omitted; target defaults to en.
//
// Compiled on first use by bin/translate.py to ~/.cache/eidetic/apple_translate:
//   swiftc -parse-as-library -O apple_translate.swift -o apple_translate
//
// Contract: stdout = translation · exit 0 ok · exit 3 = pack not installed
//           (LanguageAvailability != .installed) · exit 2 = usage · exit 1 = error.
// `--status` prints "<src>-><tgt>: <availability>" and exits 0 (doctor/preflight probe).
import Foundation
import Translation
import NaturalLanguage

@main
struct AppleTranslate {
    static func main() async {
        let args = Array(CommandLine.arguments.dropFirst())
        var from: String? = nil
        var to = "en"
        var statusOnly = false
        var parts: [String] = []
        var i = 0
        while i < args.count {
            switch args[i] {
            case "--from" where i + 1 < args.count: from = args[i + 1]; i += 2
            case "--to"   where i + 1 < args.count: to   = args[i + 1]; i += 2
            case "--status": statusOnly = true; i += 1
            default: parts.append(args[i]); i += 1
            }
        }
        var text = parts.joined(separator: " ")
        if text.isEmpty && !statusOnly {
            text = String(data: FileHandle.standardInput.readDataToEndOfFile(), encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        }
        if text.isEmpty && !statusOnly {
            FileHandle.standardError.write(Data("usage: apple_translate <text> [--from xx] [--to en] | --status\n".utf8))
            exit(2)
        }

        var src = from
        if src == nil {
            if statusOnly {
                src = "ru"
            } else {
                let rec = NLLanguageRecognizer(); rec.processString(text)
                src = rec.dominantLanguage?.rawValue ?? "en"
            }
        }
        let source = Locale.Language(identifier: src!)
        let target = Locale.Language(identifier: to)

        let availability = LanguageAvailability()
        let status = await availability.status(from: source, to: target)
        if statusOnly {
            print("\(src!)->\(to): \(String(describing: status))")
            exit(0)
        }
        guard status == .installed else {
            FileHandle.standardError.write(Data("PACK_NOT_INSTALLED \(src!)->\(to) status=\(String(describing: status))\n".utf8))
            exit(3)
        }

        let session = TranslationSession(installedSource: source, target: target)
        do {
            let r = try await session.translate(text)
            print(r.targetText)
        } catch {
            FileHandle.standardError.write(Data("TRANSLATE_ERROR: \(error)\n".utf8))
            exit(1)
        }
    }
}
