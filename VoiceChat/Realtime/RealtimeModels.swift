import Foundation

/// JSON event types for Realtime-API-style WebSocket transports.
///
/// This event shape (session.update / input_audio_buffer.append / response.audio.delta / …)
/// originated with OpenAI's Realtime API (https://platform.openai.com/docs/guides/realtime)
/// but Alibaba's Qwen-Omni-Realtime / Qwen-Audio-Realtime APIs use the same event names, so
/// this same client works against either — only the WebSocket URL, model, and audio sample
/// rates (see `AudioIOManager`) differ. Only the fields this app actually uses are modeled;
/// unknown event types are ignored by `RealtimeClient`.
enum RealtimeOutgoingEvent {
    /// Configures the session: modalities, voice, audio formats, VAD, system instructions.
    /// `turnDetection` is one of "server_vad", "smart_turn", or nil for manual/push-to-talk.
    ///
    /// `autoRespond: false` asks the server to detect end-of-speech and commit the input
    /// buffer *without* immediately generating a reply — mirrors OpenAI's `create_response`
    /// flag. This app relies on that gap to inject retrieved local memory (see
    /// `conversationItemCreate`/`responseCreate`) before the model answers. Confirmed live
    /// against `qwen-omni-turbo-realtime` on 2026-08-19 — its `session.created` response
    /// echoed back `turn_detection.create_response` (and `interrupt_response`, for barge-in),
    /// so this is real, not guessed (see `web-demo/README.md`). If a different model doesn't
    /// support it, fall back to `turnDetection: nil` (manual/push-to-talk).
    static func sessionUpdate(instructions: String, voice: String, turnDetection: String? = "server_vad", autoRespond: Bool = true) -> [String: Any] {
        var session: [String: Any] = [
            "modalities": ["audio", "text"],
            "instructions": instructions,
            "voice": voice,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
        ]
        if let turnDetection {
            session["turn_detection"] = [
                "type": turnDetection,
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 500,
                "create_response": autoRespond,
            ]
        } else {
            session["turn_detection"] = NSNull()
        }
        return [
            "type": "session.update",
            "session": session,
        ]
    }

    /// Appends a chunk of base64-encoded PCM16 mono/24kHz audio to the server's input buffer.
    static func inputAudioAppend(base64Audio: String) -> [String: Any] {
        [
            "type": "input_audio_buffer.append",
            "audio": base64Audio,
        ]
    }

    /// Patches the session's system instructions — the mechanism this app uses to hand
    /// the model retrieved local-memory context before it answers.
    ///
    /// Tested directly against `qwen-omni-turbo-realtime` on 2026-08-19: a
    /// `conversation.item.create` with role "system" (or a fake "assistant" turn) is
    /// silently ignored by the model — only content actually inside
    /// `session.update.session.instructions` gets used. Firing a second session.update
    /// before the first is acked also produced an empty reply in testing, so callers
    /// must wait for the `session.updated` event before calling `responseCreate()` (see
    /// `RealtimeClient.updateInstructions`). Full test transcript in web-demo/README.md.
    static func sessionInstructionsPatch(instructions: String) -> [String: Any] {
        [
            "type": "session.update",
            "session": ["instructions": instructions],
        ]
    }

    /// Explicitly asks the model to generate a response — needed once `autoRespond: false`
    /// takes the server out of auto-triggering after each turn.
    static func responseCreate() -> [String: Any] {
        ["type": "response.create"]
    }

    /// Cancels the assistant's in-flight response — used when the user barges in.
    static func responseCancel() -> [String: Any] {
        ["type": "response.cancel"]
    }

    /// Submits a typed (or dictated-and-cleaned-up) text message as a user turn — the
    /// counterpart to speaking, for the text-input path. Mirrors web-demo/static/app.js's
    /// `sendTextMessage`.
    static func conversationItemCreateUserText(_ text: String) -> [String: Any] {
        [
            "type": "conversation.item.create",
            "item": [
                "type": "message",
                "role": "user",
                "content": [["type": "input_text", "text": text]],
            ],
        ]
    }
}

/// Parsed subset of incoming Realtime API events the UI/audio layer reacts to.
enum RealtimeIncomingEvent {
    case audioDelta(base64: String)
    case transcriptDelta(text: String)
    /// Final spoken-response transcript. Some providers (e.g. Qwen) only send this,
    /// without incremental `.delta` events — the view model uses it as a fallback.
    case transcriptDone(text: String)
    case userTranscript(text: String)
    case speechStarted
    /// Ack for `session.update` — including the instructions-only patch used for
    /// memory grounding. Callers must wait for this before requesting a response.
    case sessionUpdated
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
        case "response.audio_transcript.done":
            self = .transcriptDone(text: json["transcript"] as? String ?? "")
        case "conversation.item.input_audio_transcription.completed":
            self = .userTranscript(text: json["transcript"] as? String ?? "")
        case "input_audio_buffer.speech_started":
            self = .speechStarted
        case "session.updated":
            self = .sessionUpdated
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
