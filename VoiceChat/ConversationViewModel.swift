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
    private var assistantLineIndex: Int?

    var systemInstructions = "你是一个友好、简洁的语音助手，用自然口语中文回答问题。"
    var voice = "alloy"

    func start() {
        guard case .idle = state else { return }
        guard let apiKey = APIKeyStore.load(), !apiKey.isEmpty else {
            state = .error("请先在设置里填入 OpenAI API Key")
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

        client.connect(apiKey: apiKey, instructions: systemInstructions, voice: voice)
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

        client.onUserTranscript = { [weak self] text in
            guard let self, !text.isEmpty else { return }
            self.transcript.append(TranscriptLine(speaker: .user, text: text))
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

    private func appendToAssistantLine(_ delta: String) {
        if let index = assistantLineIndex {
            transcript[index].text += delta
        } else {
            transcript.append(TranscriptLine(speaker: .assistant, text: delta))
            assistantLineIndex = transcript.count - 1
        }
    }
}
