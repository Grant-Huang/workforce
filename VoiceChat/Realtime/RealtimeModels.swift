import Foundation

/// JSON event types for the OpenAI Realtime API (WebSocket transport).
/// Reference: https://platform.openai.com/docs/guides/realtime
///
/// Only the fields this app actually uses are modeled — the Realtime API sends many
/// more event types than this; unknown types are ignored by `RealtimeClient`.
enum RealtimeOutgoingEvent {
    /// Configures the session: modalities, voice, audio formats, VAD, system instructions.
    static func sessionUpdate(instructions: String, voice: String) -> [String: Any] {
        [
            "type": "session.update",
            "session": [
                "modalities": ["audio", "text"],
                "instructions": instructions,
                "voice": voice,
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "turn_detection": [
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 500,
                ],
            ],
        ]
    }

    /// Appends a chunk of base64-encoded PCM16 mono/24kHz audio to the server's input buffer.
    static func inputAudioAppend(base64Audio: String) -> [String: Any] {
        [
            "type": "input_audio_buffer.append",
            "audio": base64Audio,
        ]
    }

    /// Cancels the assistant's in-flight response — used when the user barges in.
    static func responseCancel() -> [String: Any] {
        ["type": "response.cancel"]
    }
}

/// Parsed subset of incoming Realtime API events the UI/audio layer reacts to.
enum RealtimeIncomingEvent {
    case audioDelta(base64: String)
    case transcriptDelta(text: String)
    case userTranscript(text: String)
    case speechStarted
    case responseDone
    case error(message: String)
    case unhandled(type: String)

    init(json: [String: Any]) {
        guard let type = json["type"] as? String else {
            self = .unhandled(type: "unknown")
            return
        }
        switch type {
        case "response.audio.delta":
            self = .audioDelta(base64: json["delta"] as? String ?? "")
        case "response.audio_transcript.delta":
            self = .transcriptDelta(text: json["delta"] as? String ?? "")
        case "conversation.item.input_audio_transcription.completed":
            self = .userTranscript(text: json["transcript"] as? String ?? "")
        case "input_audio_buffer.speech_started":
            self = .speechStarted
        case "response.done":
            self = .responseDone
        case "error":
            let message = (json["error"] as? [String: Any])?["message"] as? String ?? "unknown error"
            self = .error(message: message)
        default:
            self = .unhandled(type: type)
        }
    }
}
