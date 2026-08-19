import Foundation
import Combine

enum ConversationState {
    case idle
    case connecting
    case listening
    case assistantSpeaking
    case error(String)
}

struct TranscriptLine: Identifiable {
    let id = UUID()
    let speaker: Speaker
    var text: String

    enum Speaker { case user, assistant }
}

@MainActor
final class ConversationViewModel: ObservableObject {
    @Published private(set) var state: ConversationState = .idle
    @Published private(set) var transcript: [TranscriptLine] = []

    private let audio = AudioIOManager()
    private let client = RealtimeClient()
    let memoryStore = MemoryStore()
    private var assistantLineIndex: Int?

    var systemInstructions = "你是一个友好、简洁的语音助手，用自然口语中文回答问题。如果背景信息里提供了用户过去说过的相关内容，用它来帮助回答，但不要生硬地照读，也不要提及“背景信息”这个说法本身。"

    func start() {
        guard case .idle = state else { return }
        guard let apiKey = APIKeyStore.load(), !apiKey.isEmpty else {
            state = .error("请先在设置里填入 API Key")
            return
        }

        state = .connecting
        wireCallbacks()

        do {
            try audio.start()
        } catch {
            state = .error("麦克风启动失败：\(error.localizedDescription)")
            return
        }

        client.connect(
            baseURL: RealtimeConfigStore.baseURL,
            apiKey: apiKey,
            model: RealtimeConfigStore.model,
            instructions: systemInstructions,
            voice: RealtimeConfigStore.voice
        )
        state = .listening
    }

    func stop() {
        client.disconnect()
        audio.stop()
        state = .idle
    }

    private func wireCallbacks() {
        audio.onCapturedChunk = { [weak self] data in
            self?.client.sendAudioChunk(data)
        }

        client.onAudioDelta = { [weak self] data in
            self?.audio.play(pcm16: data)
            self?.state = .assistantSpeaking
        }

        client.onTranscriptDelta = { [weak self] text in
            self?.appendToAssistantLine(text)
        }

        client.onTranscriptDone = { [weak self] text in
            // Fallback for providers that only send the final transcript, no deltas.
            guard let self, self.assistantLineIndex == nil, !text.isEmpty else { return }
            self.transcript.append(TranscriptLine(speaker: .assistant, text: text))
            self.assistantLineIndex = self.transcript.count - 1
        }

        client.onUserTranscript = { [weak self] text in
            guard let self, !text.isEmpty else { return }
            self.transcript.append(TranscriptLine(speaker: .user, text: text))
            self.groundAndRespond(to: text)
        }

        client.onSpeechStarted = { [weak self] in
            // User barged in — stop the assistant immediately.
            self?.audio.interruptPlayback()
            self?.client.cancelResponse()
            self?.assistantLineIndex = nil
            self?.state = .listening
        }

        client.onResponseDone = { [weak self] in
            self?.assistantLineIndex = nil
            self?.state = .listening
        }

        client.onError = { [weak self] message in
            self?.state = .error(message)
        }

        client.onDisconnect = { [weak self] error in
            guard let self else { return }
            if let error {
                self.state = .error(error.localizedDescription)
            } else {
                self.state = .idle
            }
        }
    }

    /// Retrieves relevant local memory for what the user just said, hands it to the
    /// model as background context, then triggers the reply. The session is configured
    /// with `autoRespond: false` (see `start()`), so this is the only place a response
    /// gets requested — it must run for every user turn, not just when memory is found.
    private func groundAndRespond(to userText: String) {
        let relevant = memoryStore.search(query: userText, limit: 5)
        memoryStore.add(userText)

        if !relevant.isEmpty {
            let formatter = DateFormatter()
            formatter.dateFormat = "M月d日 HH:mm"
            let lines = relevant.map { "- [\(formatter.string(from: $0.timestamp))] \($0.text)" }
            let context = "以下是用户过去说过、可能相关的内容：\n" + lines.joined(separator: "\n")
            client.sendContext(context)
        }

        client.requestResponse()
    }

    private func appendToAssistantLine(_ delta: String) {
        if let index = assistantLineIndex {
            transcript[index].text += delta
        } else {
            transcript.append(TranscriptLine(speaker: .assistant, text: delta))
            assistantLineIndex = transcript.count - 1
        }
    }
}
