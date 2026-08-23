import Foundation

/// WebSocket client for Realtime-API-style voice endpoints (OpenAI Realtime API,
/// Qwen-Omni-Realtime / Qwen-Audio-Realtime — see `RealtimeModels.swift`).
///
/// Owns the socket connection and translates between raw JSON events and the
/// typed callbacks the view model consumes. Networking-only: it doesn't know
/// anything about audio capture/playback (see `AudioIOManager`).
final class RealtimeClient: NSObject {
    private var task: URLSessionWebSocketTask?
    private let session: URLSession

    var onAudioDelta: ((Data) -> Void)?
    var onTranscriptDelta: ((String) -> Void)?
    var onTranscriptDone: ((String) -> Void)?
    var onUserTranscript: ((String) -> Void)?
    var onSpeechStarted: (() -> Void)?
    var onResponseDone: (() -> Void)?
    var onError: ((String) -> Void)?
    var onDisconnect: ((Error?) -> Void)?

    /// Resolved on the next `session.updated` event — see `updateInstructions`.
    private var pendingInstructionsAck: (() -> Void)?

    override init() {
        self.session = URLSession(configuration: .default)
        super.init()
    }

    /// - Parameters:
    ///   - baseURL: The realtime endpoint, without the `model` query param, e.g.
    ///     `wss://api.openai.com/v1/realtime` or
    ///     `wss://<workspace-id>.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime`.
    ///   - model: Model id sent as the `model` query param (e.g. `gpt-realtime`,
    ///     `qwen-audio-3.0-realtime-plus`). Check your provider's console for the
    ///     current name — these change more often than the wire protocol does.
    /// `autoRespond: false` (the default here) leaves the server detecting end-of-speech
    /// and committing the input buffer, but lets the client decide *when* to actually
    /// trigger a reply via `requestResponse()` — see `RealtimeOutgoingEvent.sessionUpdate`.
    ///
    /// `onSessionReady` fires once the initial `session.update` is acked via
    /// `session.updated` — callers must wait for it before treating the session as
    /// actually usable (e.g. before showing "connected" or sending a turn). Firing the
    /// initial config and moving on without waiting for this was a real bug: nothing
    /// verified the session had actually come up, so a socket that opened but never
    /// got a working session (a real observed Qwen failure mode, see
    /// docs/qwen-realtime-voice-setup.md) looked identical to a healthy connection.
    func connect(baseURL: String, apiKey: String, model: String, instructions: String, voice: String, turnDetection: String? = "server_vad", autoRespond: Bool = false, onSessionReady: @escaping () -> Void) {
        guard var components = URLComponents(string: baseURL) else {
            onError?("invalid WebSocket URL: \(baseURL)")
            return
        }
        components.queryItems = [URLQueryItem(name: "model", value: model)]
        guard let url = components.url else {
            onError?("could not build WebSocket URL from: \(baseURL)")
            return
        }

        var request = URLRequest(url: url)
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        if url.host?.contains("openai.com") == true {
            request.setValue("realtime=v1", forHTTPHeaderField: "OpenAI-Beta")
        }

        let task = session.webSocketTask(with: request)
        self.task = task
        task.resume()
        receiveLoop()

        pendingInstructionsAck = onSessionReady
        send(RealtimeOutgoingEvent.sessionUpdate(instructions: instructions, voice: voice, turnDetection: turnDetection, autoRespond: autoRespond))
    }

    /// Submits a typed or dictated-and-cleaned-up text message as a user turn.
    func sendUserText(_ text: String) {
        send(RealtimeOutgoingEvent.conversationItemCreateUserText(text))
    }

    func disconnect() {
        task?.cancel(with: .normalClosure, reason: nil)
        task = nil
    }

    func sendAudioChunk(_ data: Data) {
        send(RealtimeOutgoingEvent.inputAudioAppend(base64Audio: data.base64EncodedString()))
    }

    /// Patches the session's system instructions (e.g. base prompt + retrieved local
    /// memory) and calls `completion` once the server confirms it via `session.updated`.
    /// Callers must wait for that ack before calling `requestResponse()` — sending a
    /// second `session.update` before the first is acked raced and produced an empty
    /// reply in testing (see `RealtimeOutgoingEvent.sessionInstructionsPatch`).
    func updateInstructions(_ instructions: String, completion: @escaping () -> Void) {
        pendingInstructionsAck = completion
        send(RealtimeOutgoingEvent.sessionInstructionsPatch(instructions: instructions))
    }

    /// Triggers response generation — required when the session was configured with
    /// `autoRespond: false`.
    func requestResponse() {
        send(RealtimeOutgoingEvent.responseCreate())
    }

    func cancelResponse() {
        send(RealtimeOutgoingEvent.responseCancel())
    }

    private func send(_ payload: [String: Any]) {
        guard let task, let data = try? JSONSerialization.data(withJSONObject: payload) else { return }
        let message = URLSessionWebSocketTask.Message.data(data)
        task.send(message) { [weak self] error in
            if let error {
                self?.onError?("send failed: \(error.localizedDescription)")
            }
        }
    }

    private func receiveLoop() {
        task?.receive { [weak self] result in
            guard let self else { return }
            switch result {
            case .failure(let error):
                self.onDisconnect?(error)
                return
            case .success(let message):
                self.handle(message)
                self.receiveLoop()
            }
        }
    }

    private func handle(_ message: URLSessionWebSocketTask.Message) {
        let data: Data?
        switch message {
        case .data(let d): data = d
        case .string(let s): data = Data(s.utf8)
        @unknown default: data = nil
        }
        guard let data,
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }

        switch RealtimeIncomingEvent(json: json) {
        case .audioDelta(let base64):
            if let audio = Data(base64Encoded: base64) {
                onAudioDelta?(audio)
            }
        case .transcriptDelta(let text):
            onTranscriptDelta?(text)
        case .transcriptDone(let text):
            onTranscriptDone?(text)
        case .userTranscript(let text):
            onUserTranscript?(text)
        case .speechStarted:
            onSpeechStarted?()
        case .sessionUpdated:
            pendingInstructionsAck?()
            pendingInstructionsAck = nil
        case .responseDone:
            onResponseDone?()
        case .error(let message):
            onError?(message)
        case .unhandled:
            break
        }
    }
}
