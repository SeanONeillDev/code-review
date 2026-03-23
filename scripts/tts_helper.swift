import AVFoundation
import Foundation

// ── JSON string escape ────────────────────────────────────────────────────────
func jsonStr(_ s: String) -> String {
    var out = "\""
    for c in s.unicodeScalars {
        switch c.value {
        case 0x22: out += "\\\""
        case 0x5C: out += "\\\\"
        case 0x0A: out += "\\n"
        case 0x0D: out += "\\r"
        case 0x09: out += "\\t"
        default:
            if c.value < 0x20 {
                out += String(format: "\\u%04x", c.value)
            } else {
                out += String(c)
            }
        }
    }
    out += "\""
    return out
}

// ── Delegate ──────────────────────────────────────────────────────────────────
class TTS: NSObject, AVSpeechSynthesizerDelegate {
    let synth = AVSpeechSynthesizer()
    var finished = false
    var inputText = ""

    override init() {
        super.init()
        synth.delegate = self
    }

    func speak(_ text: String, voice: AVSpeechSynthesisVoice?, rate: Float) {
        inputText = text
        let utt = AVSpeechUtterance(string: text)
        utt.voice = voice
        utt.rate = rate
        utt.volume = 1.0
        synth.speak(utt)
    }

    // Called just before each word is spoken
    func speechSynthesizer(
        _ synthesizer: AVSpeechSynthesizer,
        willSpeakRangeOfSpeechString characterRange: NSRange,
        utterance: AVSpeechUtterance
    ) {
        guard let range = Range(characterRange, in: inputText) else { return }
        let word = String(inputText[range])
        let msg = "{\"type\":\"word\",\"word\":\(jsonStr(word)),\"char_offset\":\(characterRange.location),\"char_len\":\(characterRange.length)}"
        print(msg)
        fflush(stdout)
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        print("{\"type\":\"done\"}")
        fflush(stdout)
        finished = true
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        print("{\"type\":\"done\"}")
        fflush(stdout)
        finished = true
    }
}

// ── Voice lookup ──────────────────────────────────────────────────────────────
func findVoice(_ name: String) -> AVSpeechSynthesisVoice? {
    let voices = AVSpeechSynthesisVoice.speechVoices()
    // Prefer premium or enhanced quality
    if let v = voices.first(where: {
        $0.name.lowercased() == name.lowercased() &&
        ($0.quality == .premium || $0.quality == .enhanced)
    }) { return v }
    // Any quality match
    if let v = voices.first(where: { $0.name.lowercased() == name.lowercased() }) {
        return v
    }
    // Partial name match
    return voices.first(where: { $0.name.lowercased().contains(name.lowercased()) })
}

// ── Main ──────────────────────────────────────────────────────────────────────
// Args: tts_helper [voice_name [rate]]
// Text is read from stdin
let voiceName = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "Zoe"
let rateArg   = CommandLine.arguments.count > 2 ? Float(CommandLine.arguments[2]) : nil
let rate      = rateArg ?? 0.52  // slightly above default (0.5)

// Read all stdin
var inputLines: [String] = []
while let line = readLine(strippingNewline: false) {
    inputLines.append(line)
}
let text = inputLines.joined()

guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
    print("{\"type\":\"done\"}")
    exit(0)
}

// Clean exit on SIGTERM / SIGINT (skip signal from server)
signal(SIGTERM) { _ in
    print("{\"type\":\"done\"}")
    fflush(stdout)
    exit(0)
}
signal(SIGINT) { _ in
    print("{\"type\":\"done\"}")
    fflush(stdout)
    exit(0)
}

// Pause / resume via SIGUSR1 / SIGUSR2
var shouldPause  = false
var shouldResume = false
signal(SIGUSR1) { _ in shouldPause  = true }
signal(SIGUSR2) { _ in shouldResume = true }

let tts = TTS()
tts.speak(text, voice: findVoice(voiceName), rate: rate)

while !tts.finished {
    RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.05))
    if shouldPause  { shouldPause  = false; tts.synth.pauseSpeaking(at: .word) }
    if shouldResume { shouldResume = false; tts.synth.continueSpeaking() }
}
