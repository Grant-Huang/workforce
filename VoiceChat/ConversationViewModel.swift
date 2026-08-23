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

struct SuggestionChip: Identifiable {
    let id = UUID()
    let label: String
    let query: String
}

@MainActor
final class ConversationViewModel: ObservableObject {
    @Published private(set) var state: ConversationState = .idle
    @Published private(set) var transcript: [TranscriptLine] = []

    private let audio = AudioIOManager()
    private let client = RealtimeClient()
    let memoryStore = MemoryStore()
    private let agentNexusClient = AgentNexusClient()
    private var assistantLineIndex: Int?

    // ---- connection lifecycle (ported from web-demo/static/app.js, 2026-08-22) ----
    //
    // Three rules: (a) a connection attempt that never actually produces a working
    // session must surface an error, not hang silently forever; (b) once connected,
    // never release on our own initiative; (c) if the user goes quiet for a long time
    // while connected, release proactively instead of holding the socket open forever.
    private static let connectTimeoutSeconds: UInt64 = 8
    private static let idleTimeoutSeconds: UInt64 = 5 * 60
    private var connectTimeoutTask: Task<Void, Never>?
    private var idleTimeoutTask: Task<Void, Never>?
    /// Set when the user asks to send a text message while idle — `start()` needs a
    /// running session before the message can actually go out, so this is submitted
    /// once the session becomes ready rather than sent immediately.
    private var pendingTextOnReady: String?

    var systemInstructions = """
        你是一个语音助手，正在和用户实时语音对话。

        说话方式：
        - 像日常聊天一样自然口语化，不要用书面语（比如不要说"因此""综上所述""值得注意的是"）。
        - 不要用任何视觉格式：不用列表符号、编号、加粗，也不要读网址或代码。

        回答长度：根据问题本身决定，不要机械地限制在一两句话。
        - 简单的问题、闲聊、确认类的话，几句话说完就行，不用刻意拉长。
        - 如果用户是让你整理工作内容、待办事项，或者问一个需要讲清楚的技术/工程问题，内容可以详细，但要按口语习惯组织——比如"主要有这么几件事，第一……第二……"这样一段段说，不要用书面的分点列表腔调；内容特别多的时候，可以先说完整体，再问要不要展开某一部分。

        背景信息的使用：
        - 如果背景信息里有跟当前问题相关的内容，用自己的话自然带出来，不要逐字复述，也不要提"背景信息"这个说法本身。
        - 如果问题明显需要用户之前提到的具体信息（比如某个日程、决定、事实），但背景信息里完全没有相关内容，不要编造答案——诚实说明你目前没有这方面的记录，比如"这个我目前没有相关记录"或者"这个我还得再查一下"，可以顺带问用户要不要现在告诉你。
        - 常识性、闲聊性的问题正常回答，不用刻意强调"没有记录"。
        """

    /// Convenience for View-side `.disabled(...)` bindings (e.g. the dictate button
    /// can't be used while a live conversation is connected) — matching `if case`
    /// checks inline at call sites gets noisy fast.
    var isIdle: Bool {
        if case .idle = state { return true }
        return false
    }

    /// 0-1 time-based suggestions shown in the empty state (before any turns) —
    /// restrained on purpose: no auto-speak, no auto-connect, just a tappable prompt.
    /// Mirrors web-demo/static/app.js's `getTimeSuggestions`.
    var suggestionChips: [SuggestionChip] {
        let hour = Calendar.current.component(.hour, from: Date())
        if hour < 12 {
            return [SuggestionChip(label: "查一下今天的日程安排", query: "查一下我今天的日程安排")]
        } else if hour >= 17 {
            return [SuggestionChip(label: "总结复盘一下今天的工作", query: "帮我总结复盘一下今天的工作")]
        }
        return []
    }

    func start() {
        switch state {
        case .idle, .error: break
        default: return
        }
        guard let apiKey = APIKeyStore.load(), !apiKey.isEmpty else {
            setState(.error("请先在设置里填入 API Key"))
            return
        }

        setState(.connecting)
        wireCallbacks()

        do {
            try audio.start()
        } catch {
            setState(.error("麦克风启动失败：\(error.localizedDescription)"))
            return
        }

        client.connect(
            baseURL: RealtimeConfigStore.effectiveBaseURL,
            apiKey: apiKey,
            model: RealtimeConfigStore.model,
            instructions: systemInstructions,
            voice: RealtimeConfigStore.voice
        ) { [weak self] in
            guard let self else { return }
            self.setState(.listening)
            if let pending = self.pendingTextOnReady {
                self.pendingTextOnReady = nil
                self.submitUserText(pending)
            }
        }

        // Belt-and-suspenders for rule (a): if the socket opens but the session never
        // actually comes up (session.updated never arrives), don't sit at "连接中…"
        // forever — a real observed failure mode, not hypothetical (see
        // docs/qwen-realtime-voice-setup.md). Cleared by `setState` once we leave
        // .connecting, from either the ready callback above or an error/disconnect.
        connectTimeoutTask?.cancel()
        connectTimeoutTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: Self.connectTimeoutSeconds * 1_000_000_000)
            guard let self, !Task.isCancelled else { return }
            if case .connecting = self.state {
                self.stop(reason: "连接超时，请重试")
            }
        }

        // Pull-sync from AgentNexus in the background — short REST call, not on the
        // conversation's critical path. If it fails or is slow, the conversation just
        // proceeds with whatever's already in the local cache from last time.
        Task { [weak self] in
            guard let self, AgentNexusConfigStore.isConfigured else { return }
            guard let entries = try? await self.agentNexusClient.fetchMemoryEntries() else { return }
            let formatter = ISO8601DateFormatter()
            let mapped = entries.map { entry -> (text: String, timestamp: Date) in
                let text = entry.title.map { "\($0)：\(entry.content)" } ?? entry.content
                let date = entry.updatedAt.flatMap { formatter.date(from: $0) } ?? Date()
                return (text, date)
            }
            self.memoryStore.merge(remoteEntries: mapped)
        }
    }

    func stop(reason: String? = nil) {
        pendingTextOnReady = nil
        client.disconnect()
        audio.stop()
        setState(reason.map { .error($0) } ?? .idle)
    }

    /// Every state transition goes through here so the idle/connect timers stay in
    /// sync with what's actually displayed, instead of being armed/cleared ad hoc at
    /// each call site (that's how the web version's equivalent bug surfaced — a state
    /// change that forgot to clear a timer). `.listening` means "connected, waiting on
    /// the user" — the only state that should count toward rule (c)'s idle clock;
    /// anything else (still connecting, assistant talking, idle) clears it.
    private func setState(_ next: ConversationState) {
        state = next
        if case .listening = next {
            idleTimeoutTask?.cancel()
            idleTimeoutTask = Task { [weak self] in
                try? await Task.sleep(nanoseconds: Self.idleTimeoutSeconds * 1_000_000_000)
                guard let self, !Task.isCancelled else { return }
                self.stop(reason: "长时间没有说话，已自动挂断")
            }
        } else {
            idleTimeoutTask?.cancel()
            idleTimeoutTask = nil
        }
        if case .connecting = next {} else {
            connectTimeoutTask?.cancel()
            connectTimeoutTask = nil
        }
    }

    /// Shared by the mic-driven voice turn path and the typed/dictated text path.
    /// Starts the session first if needed — see `pendingTextOnReady`.
    func sendText(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        switch state {
        case .idle, .error:
            pendingTextOnReady = trimmed
            start()
        default:
            submitUserText(trimmed)
        }
    }

    private func submitUserText(_ text: String) {
        transcript.append(TranscriptLine(speaker: .user, text: text))
        client.sendUserText(text)
        groundAndRespond(to: text)
    }

    private func wireCallbacks() {
        audio.onCapturedChunk = { [weak self] data in
            self?.client.sendAudioChunk(data)
        }

        client.onAudioDelta = { [weak self] data in
            self?.audio.play(pcm16: data)
            self?.setState(.assistantSpeaking)
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
            self?.setState(.listening)
        }

        client.onResponseDone = { [weak self] in
            self?.assistantLineIndex = nil
            self?.setState(.listening)
        }

        client.onError = { [weak self] message in
            self?.pendingTextOnReady = nil
            self?.setState(.error(message))
        }

        client.onDisconnect = { [weak self] error in
            guard let self else { return }
            self.pendingTextOnReady = nil
            if let error {
                self.setState(.error(error.localizedDescription))
            } else {
                self.setState(.idle)
            }
        }
    }

    /// Retrieves relevant local memory for what the user just said, patches it into the
    /// session's instructions, then triggers the reply once that patch is acked. The
    /// session is configured with `autoRespond: false` (see `start()`), so this is the
    /// only place a response gets requested — it must run for every user turn, not just
    /// when memory is found (and must always patch instructions, even back to the base
    /// prompt with nothing found, so a previous turn's injected memory doesn't linger).
    ///
    /// Grounding via `conversation.item.create(role: "system")` was the original design
    /// but doesn't work — Qwen silently ignores it. Only `session.update`'s `instructions`
    /// field is actually honored; see `RealtimeOutgoingEvent.sessionInstructionsPatch`.
    ///
    /// An explicit "记住…" turn takes a different path: it writes a curated entry into
    /// AgentNexus's structured memory layers (not just the raw message log every turn
    /// gets) and skips memory retrieval — it's a command, not a question, so the model
    /// just needs to briefly confirm rather than search-and-answer.
    private func groundAndRespond(to userText: String) {
        agentNexusClient.pushMessage(userText)

        if let saveIntent = SaveIntent.detect(userText) {
            memoryStore.add(saveIntent.content)
            Task { [weak self] in
                try? await self?.agentNexusClient.createMemoryEntry(layer: "PROGRESS", content: saveIntent.content)
            }
            let instructions = systemInstructions
                + "\n\n用户刚才明确要求记住这件事：\"\(saveIntent.content)\"，你已经帮TA记下了。只需要简短确认一句就行，不要复述内容、不要追问。"
            client.updateInstructions(instructions) { [weak self] in
                self?.client.requestResponse()
            }
            return
        }

        let relevant = memoryStore.search(query: userText, limit: 5)
        memoryStore.add(userText)

        var instructions = systemInstructions
        if !relevant.isEmpty {
            let formatter = DateFormatter()
            formatter.dateFormat = "M月d日 HH:mm"
            let lines = relevant.map { "- [\(formatter.string(from: $0.timestamp))] \($0.text)" }
            instructions += "\n\n以下是用户过去说过、可能相关的内容，如果有帮助请参考：\n" + lines.joined(separator: "\n")
        }

        client.updateInstructions(instructions) { [weak self] in
            self?.client.requestResponse()
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
