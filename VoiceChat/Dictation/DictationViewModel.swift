import Foundation

enum DictationState: Equatable {
    case idle
    case recording
    case cleaning
}

/// Voice-dictate-to-text (docs/app-design.md section 3): captures speech, transcribes
/// it via the same Realtime protocol the live-conversation mode uses (reusing the
/// transcript event `ConversationViewModel` already relies on), but never triggers a
/// spoken reply — `turn_detection.create_response: false` and `response.create()` is
/// simply never called. The raw transcript then goes through `DictationCleanupClient`
/// (a separate, non-Realtime call) before the caller does anything with it.
///
/// Deliberately a separate model from `ConversationViewModel` rather than a mode flag
/// on it: dictation and live conversation are mutually exclusive (can't run two mic
/// captures at once) but otherwise independent, and mixing their state machines was a
/// larger, riskier change for what should be an isolated feature. `ConversationView`
/// is responsible for disabling each one's controls while the other is active.
@MainActor
final class DictationViewModel: ObservableObject {
    @Published private(set) var state: DictationState = .idle
    @Published var errorMessage: String?

    private let audio = AudioIOManager()
    private var client = RealtimeClient()
    private var rawTranscript = ""

    func start() {
        guard case .idle = state else { return }
        guard let apiKey = APIKeyStore.load(), !apiKey.isEmpty else {
            errorMessage = "请先在设置里填入 API Key"
            return
        }

        state = .recording
        errorMessage = nil
        rawTranscript = ""

        client.onUserTranscript = { [weak self] text in
            guard let self, !text.isEmpty else { return }
            self.rawTranscript += (self.rawTranscript.isEmpty ? "" : " ") + text
        }
        client.onError = { [weak self] message in
            self?.errorMessage = message
        }

        do {
            try audio.start()
        } catch {
            errorMessage = "麦克风启动失败：\(error.localizedDescription)"
            state = .idle
            return
        }
        audio.onCapturedChunk = { [weak self] data in
            self?.client.sendAudioChunk(data)
        }

        client.connect(
            baseURL: RealtimeConfigStore.effectiveBaseURL,
            apiKey: apiKey,
            model: RealtimeConfigStore.model,
            instructions: "",
            voice: RealtimeConfigStore.voice,
            autoRespond: false,
            // Dictation never generates a spoken reply -- this is the same
            // text-only configuration the text session uses, which means
            // finishForDirectSend()'s handoff into the text session needs zero
            // modality change, only an instructions patch. Mirrors
            // web-demo/static/app.js's startDictation().
            modalities: ["text"]
        ) {
            // Session ready — nothing else to do, the mic tap is already forwarding
            // audio chunks; RealtimeClient.send() no-ops until the socket exists
            // anyway, so nothing was lost by not gating capture start on this.
        }
    }

    /// Discards whatever's been captured so far — no cleanup call, no message.
    func cancel() {
        teardown()
        rawTranscript = ""
        state = .idle
    }

    /// Stops capturing and runs the AI-cleanup step, returning the cleaned text (or
    /// nil if there was nothing to clean, or the cleanup call failed — check
    /// `errorMessage` in that case). Used by the "edit before sending" button, which
    /// doesn't need the connection afterward — closes it same as `cancel()`.
    func finish() async -> String? {
        guard case .recording = state else { return nil }
        teardown()
        let raw = rawTranscript.trimmingCharacters(in: .whitespacesAndNewlines)
        rawTranscript = ""
        guard !raw.isEmpty else {
            state = .idle
            return nil
        }

        state = .cleaning
        defer { state = .idle }
        do {
            return try await DictationCleanupClient.cleanup(raw)
        } catch {
            errorMessage = "口述整理失败：\(error.localizedDescription)"
            return nil
        }
    }

    /// Used by the "send directly" button instead of `finish()`. Stops capturing audio
    /// but — unlike `finish()` — does NOT close the underlying `RealtimeClient`
    /// connection when there's text to send: that connection is the same kind of
    /// Realtime connection the text session uses (same protocol, same relay, same
    /// `modalities: ["text"]` configuration), just configured with empty instructions
    /// and response.create never called, so it's handed back to the caller to promote
    /// into the text session instead of being torn down and reconnected from scratch.
    /// Mirrors web-demo/static/app.js's promoteDictationConnectionToTextSession — see
    /// docs/app-design.md section 8 for the measured savings on the web side (this
    /// iOS port of the optimization is unverified, same caveat as the rest of this file).
    ///
    /// Returns nil (and leaves nothing to reuse — any handed-off connection has already
    /// been closed) when there was nothing captured or the cleanup call failed; callers
    /// only need to do something with the connection when this returns non-nil.
    func finishForDirectSend() async -> (text: String, client: RealtimeClient)? {
        guard case .recording = state else { return nil }
        audio.stop() // stop listening; leave the socket itself open for the caller to reuse

        let raw = rawTranscript.trimmingCharacters(in: .whitespacesAndNewlines)
        rawTranscript = ""

        let handoffClient = client
        client = RealtimeClient() // handoffClient now belongs to the caller; this instance needs a fresh, unconnected one for next time

        guard !raw.isEmpty else {
            handoffClient.disconnect()
            state = .idle
            return nil
        }

        state = .cleaning
        defer { state = .idle }
        do {
            let cleaned = try await DictationCleanupClient.cleanup(raw)
            return (cleaned, handoffClient)
        } catch {
            errorMessage = "口述整理失败：\(error.localizedDescription)"
            handoffClient.disconnect()
            return nil
        }
    }

    private func teardown() {
        client.disconnect()
        audio.stop()
    }
}
