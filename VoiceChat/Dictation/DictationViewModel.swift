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
    private let client = RealtimeClient()
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
            autoRespond: false
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
    /// `errorMessage` in that case). The caller decides what to do with the result:
    /// populate the text field for editing, or send it directly — this type only
    /// owns "get from speech to clean text," not message delivery.
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

    private func teardown() {
        client.disconnect()
        audio.stop()
    }
}
