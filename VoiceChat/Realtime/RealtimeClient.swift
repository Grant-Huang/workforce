import Foundation

/// WebSocket client for the OpenAI Realtime API.
///
/// Owns the socket connection and translates between raw JSON events and the
/// typed callbacks the view model consumes. Networking-only: it doesn't know
/// anything about audio capture/playback (see `AudioIOManager`).
final class RealtimeClient: NSObject {
    private var task: URLSessionWebSocketTask?
    private let session: URLSession

    var onAudioDelta: ((Data) -> Void)?
    var onTranscriptDelta: ((String) -> Void)?
    var onUserTranscript: ((String) -> Void)?
    var onSpeechStarted: (() -> Void)?
    var onResponseDone: (() -> Void)?
    var onError: ((String) -> Void)?
    var onDisconnect: ((Error?) -> Void)?

    override init() {
        self.session = URLSession(configuration: .default)
        super.init()
    }

    func connect(apiKey: String, model: String = "gpt-realtime", instructions: String, voice: String) {
        guard var components = URLComponents(string: "wss://api.openai.com/v1/realtime") else { return }
        components.queryItems = [URLQueryItem(name: "model", value: model)]
        guard let url = components.url else { return }

        var request = URLRequest(url: url)
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("realtime=v1", forHTTPHeaderField: "OpenAI-Beta")

        let task = session.webSocketTask(with: request)
        self.task = task
        task.resume()
        receiveLoop()

        send(RealtimeOutgoingEvent.sessionUpdate(instructions: instructions, voice: voice))
    }

    func disconnect() {
        task?.cancel(with: .normalClosure, reason: nil)
        task = nil
    }

    func sendAudioChunk(_ data: Data) {
        send(RealtimeOutgoingEvent.inputAudioAppend(base64Audio: data.base64EncodedString()))
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
        case .userTranscript(let text):
            onUserTranscript?(text)
        case .speechStarted:
            onSpeechStarted?()
        case .responseDone:
            onResponseDone?()
        case .error(let message):
            onError?(message)
        case .unhandled:
            break
        }
    }
}
