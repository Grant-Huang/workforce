import Foundation
import Starscream

/// WebSocket client for Realtime-API-style voice endpoints (OpenAI Realtime API,
/// Qwen-Omni-Realtime / Qwen-Audio-Realtime — see `RealtimeModels.swift`).
///
/// Owns the socket connection and translates between raw JSON events and the
/// typed callbacks the view model consumes. Networking-only: it doesn't know
/// anything about audio capture/playback (see `AudioIOManager`).
///
/// Built on Starscream instead of `URLSessionWebSocketTask` — real-device debugging
/// (2026-08-24) tracked a 100%-reproducible "Socket is not connected" failure (both
/// WiFi and cellular, independent of Key/Workspace config) to the system WebSocket
/// task negotiating HTTP/2 ALPN with servers that advertise it (this one does — Aliyun's
/// istio-envoy gateway accepts h2 for plain HTTPS requests fine, confirmed via curl).
/// The handshake itself completed (`HTTP 101`), but the underlying `nw_flow` never
/// became write-ready afterward (`nw_flow_add_write_request ... cannot accept write
/// requests`), and a self-imposed retry (see git history) didn't help — it wasn't a
/// one-off race, every attempt against this server failed the same way, while a
/// generic public WebSocket server (no h2 support) worked fine on the same device.
/// Starscream's default engine (`useCustomEngine: true`, set explicitly below) speaks
/// the WebSocket handshake over a raw `Foundation` stream it manages itself, entirely
/// bypassing `URLSessionWebSocketTask` and the h2 ALPN path that broke it.
final class RealtimeClient: NSObject {
    private var socket: WebSocket?

    var onAudioDelta: ((Data) -> Void)?
    var onTranscriptDelta: ((String) -> Void)?
    var onTranscriptDone: ((String) -> Void)?
    /// Text-session counterparts of `onTranscriptDelta`/`onTranscriptDone` — see
    /// `RealtimeIncomingEvent.textDelta`/`.textDone`.
    var onTextDelta: ((String) -> Void)?
    var onTextDone: ((String) -> Void)?
    var onUserTranscript: ((String) -> Void)?
    var onSpeechStarted: (() -> Void)?
    var onResponseDone: (() -> Void)?
    var onError: ((String) -> Void)?
    var onDisconnect: ((Error?) -> Void)?

    /// Resolved on the next `session.updated` event — see `updateInstructions`.
    private var pendingInstructionsAck: (() -> Void)?

    /// The initial `session.update` built by `connect()`, held until Starscream's
    /// `.connected` event actually fires. Unlike `URLSessionWebSocketTask` (documented as
    /// safe to `send()` immediately after `resume()`, before the handshake completes),
    /// Starscream's `write()` has no such guarantee, so this waits for the real
    /// connection instead of assuming it.
    private var pendingInitialSessionUpdate: [String: Any]?

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
    func connect(baseURL: String, apiKey: String, model: String, instructions: String, voice: String, turnDetection: String? = "server_vad", autoRespond: Bool = false, modalities: [String] = ["audio", "text"], onSessionReady: @escaping () -> Void) {
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

        let socket = WebSocket(request: request, useCustomEngine: true)
        // Starscream's callbacks otherwise fire on whatever queue the underlying
        // transport happens to use -- not necessarily the main thread. Every consumer
        // (`ConversationViewModel`, `DictationViewModel`) mutates `@Published` state in
        // these callbacks, which is only safe from the main thread (SwiftUI's
        // "Publishing changes from background threads is not allowed" warning was a
        // real, observed real-device symptom of getting this wrong -- see git history).
        socket.callbackQueue = .main
        socket.delegate = self
        self.socket = socket

        pendingInstructionsAck = onSessionReady
        pendingInitialSessionUpdate = RealtimeOutgoingEvent.sessionUpdate(instructions: instructions, voice: voice, turnDetection: turnDetection, autoRespond: autoRespond, modalities: modalities)
        socket.connect()
    }

    /// Submits a typed or dictated-and-cleaned-up text message as a user turn.
    func sendUserText(_ text: String) {
        send(RealtimeOutgoingEvent.conversationItemCreateUserText(text))
    }

    func disconnect() {
        socket?.disconnect()
        socket = nil
        pendingInitialSessionUpdate = nil
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
        guard let socket, let data = try? JSONSerialization.data(withJSONObject: payload) else { return }
        socket.write(data: data, completion: nil)
    }

    private func handle(_ data: Data) {
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }

        switch RealtimeIncomingEvent(json: json) {
        case .audioDelta(let base64):
            if let audio = Data(base64Encoded: base64) {
                onAudioDelta?(audio)
            }
        case .transcriptDelta(let text):
            onTranscriptDelta?(text)
        case .transcriptDone(let text):
            onTranscriptDone?(text)
        case .textDelta(let text):
            onTextDelta?(text)
        case .textDone(let text):
            onTextDone?(text)
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

    /// Turns Starscream's error types into the same actionable `"... [handshake HTTP
    /// xxx]"` diagnostic the old `URLSessionTask.response`-based code surfaced. Real-
    /// device testing (2026-08-24) caught this file's first cut of this method
    /// completely missing the case that actually matters: a rejected HTTP upgrade
    /// (server responds with a non-101 status) throws `HTTPUpgradeError.notAnUpgrade`,
    /// not `WSError` -- the `error as? WSError` cast silently failed for it, falling
    /// through to `error.localizedDescription`, which for an untyped Swift error bridged
    /// to NSError is a useless "operation couldn't be completed (Starscream.
    /// HTTPUpgradeError error 0)" (0 being the case's ordinal, not the HTTP status the
    /// error actually carries). Checked first since a rejected upgrade is exactly the
    /// server-side-error scenario this diagnostic exists for. `WSError.message` is now
    /// always surfaced when the cast succeeds too, for the same reason -- its `.code`
    /// isn't always a real HTTP status (e.g. transport/security errors reuse the field
    /// differently), and falling back to the generic description in that case hid a
    /// perfectly good message another real-device failure turned up ("Starscream.WSError
    /// error 1").
    private func errorDescription(_ error: Error?) -> String {
        guard let error else { return "unknown WebSocket error" }
        if case let HTTPUpgradeError.notAnUpgrade(code, _) = error {
            return "HTTP upgrade rejected [handshake HTTP \(code)]"
        }
        if let wsError = error as? WSError {
            let diagnostic = (100...599).contains(wsError.code) ? " [handshake HTTP \(wsError.code)]" : ""
            return "\(wsError.message)\(diagnostic)"
        }
        return error.localizedDescription
    }
}

extension RealtimeClient: WebSocketDelegate {
    func didReceive(event: WebSocketEvent, client: WebSocketClient) {
        switch event {
        case .connected:
            if let pending = pendingInitialSessionUpdate {
                pendingInitialSessionUpdate = nil
                send(pending)
            }
        case .text(let string):
            handle(Data(string.utf8))
        case .binary(let data):
            handle(data)
        case .disconnected(let reason, let code):
            onDisconnect?(NSError(domain: "RealtimeClient.WebSocket", code: Int(code), userInfo: [NSLocalizedDescriptionKey: reason]))
        case .peerClosed, .cancelled:
            onDisconnect?(nil)
        case .error(let error):
            onError?(errorDescription(error))
        case .viabilityChanged, .reconnectSuggested, .ping, .pong:
            break
        }
    }
}
