import Foundation
import Combine
import UIKit

enum ConversationState {
    case idle
    case connecting
    case listening
    case assistantSpeaking
    case error(String)
}

/// State machine for the text session (typing + dictation-to-text), fully independent
/// from `ConversationState` (the mic-tap live-voice session) — see the "会话状态机拆分"
/// discussion in docs/app-design.md section 8 and docs/roadmap-todo.md. The text session
/// never touches the microphone or TTS; there's no "connecting → listening →
/// assistantSpeaking" cycle here because there's nothing to listen for or speak.
enum TextSessionState: Equatable {
    case idle
    case connecting
    case ready
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
    @Published private(set) var textState: TextSessionState = .idle
    /// Surfaces text-session connect timeouts / disconnects to the UI — kept separate
    /// from `state`'s `.error` case on purpose: the text session must never touch the
    /// voice session's status label (that label is exactly what must never show
    /// "正在聆听"-style text for a text turn). Mirrors `DictationViewModel.errorMessage`.
    @Published var textSessionError: String?
    /// RMS level (mic input while `.listening`, playback while `.assistantSpeaking`)
    /// driving `ConversationView.voiceOrbView` — see docs/app-design.md 8.3. Only
    /// meaningful while `state` is one of those two cases; `wireCallbacks()` guards
    /// each callback so a stale reading from the other source never leaks through.
    @Published private(set) var orbLevel: Double = 0

    private let audio = AudioIOManager()
    private var client = RealtimeClient()
    /// The text session's own connection — fully independent from `client` (the voice
    /// session's). See `TextSessionState` and docs/app-design.md section 8.
    private var textClient = RealtimeClient()
    let memoryStore = MemoryStore()
    private let agentNexusClient = AgentNexusClient()
    /// Full conversation record, independent of `memoryStore` (extracted/searchable
    /// fragments) — see `ConversationHistoryStore`'s doc comment and docs/app-design.md
    /// 8.4. Shared by both sessions the same way `transcript` is. Built in `init()`
    /// (not inline) since it depends on `agentNexusClient`.
    private let conversationHistory: ConversationHistoryStore
    /// Shared between the voice and text sessions' streaming-reply-line building —
    /// safe only because the two sessions are mutually exclusive (`ConversationView`
    /// disables each one's entry points while the other is active), same as
    /// web-demo/static/app.js's shared `assistantBubbleEl`/`assistantHasDelta`.
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
    /// Set when the user asks to send a text message while the text session is idle —
    /// `startTextSession()` needs a running session before the message can actually go
    /// out, so this is submitted once the session becomes ready rather than sent
    /// immediately.
    private var pendingTextSessionMessage: String?
    private var textConnectTimeoutTask: Task<Void, Never>?
    private var textIdleTimeoutTask: Task<Void, Never>?
    private var foregroundObserver: NSObjectProtocol?

    init() {
        conversationHistory = ConversationHistoryStore(agentNexusClient: agentNexusClient)

        // Refresh the local memory cache when the app comes back to the foreground, on
        // top of the existing pull-on-conversation-start -- covers "memory changed on
        // another device while this one sat backgrounded" without needing to poll on a
        // timer (docs/roadmap-todo.md, "拉取时机加一条 app 回到前台时也拉一次"). Mirrors
        // web-demo/static/app.js's visibilitychange listener.
        foregroundObserver = NotificationCenter.default.addObserver(
            forName: UIApplication.didBecomeActiveNotification, object: nil, queue: .main
        ) { [weak self] _ in
            self?.pullMemoryInBackground()
        }
    }

    var systemInstructions = """
        你是一个语音助手，正在和用户实时语音对话。

        说话方式：
        - 像日常聊天一样自然口语化，不要用书面语（比如不要说"因此""综上所述""值得注意的是"）。
        - 不要用任何视觉格式：不用列表符号、编号、加粗，也不要读网址或代码。

        回答长度：先判断这条问题属于哪一类，再按对应的长度来，不要机械地都说成一两句话或者都展开成一大段：
        - **查询类**（问日期时间、单一事实、确认性问题）：1-3 句话说完，给答案不给报告，除非用户明确要求展开。
        - **列举类**（问日程安排、待办事项、多条信息）：一口气最多说 3 条左右，说完问一句"还有几条要不要都说说"，不要一次性倒完一大串，人一次性靠听记不住那么多。
        - **分析/解释类**（需要讲清楚原因、讲清楚一个技术/工程问题、帮用户理一件复杂的事）：可以说得详细，但先说一句"路线图"（比如"这个我从两方面说"），再按"第一……第二……"这样一段段说，段与段之间自然停顿，给用户留插话的空当；说了几点就是几点，中途不要冒出没预告过的第三点，语音没法让用户"往回听"，说漏了就是说漏了。
        语音是念给人听的，不是照着文字稿念——同样的内容，念出来比读一遍慢得多，能一句话说清楚的不要拖成三句。

        背景信息的使用：
        - 如果背景信息里有跟当前问题相关的内容，用自己的话自然带出来，不要逐字复述，也不要提"背景信息"这个说法本身。
        - 如果问题明显需要用户之前提到的具体信息（比如某个日程、决定、事实），但背景信息里完全没有相关内容，不要编造答案——诚实说明你目前没有这方面的记录，比如"这个我目前没有相关记录"或者"这个我还得再查一下"，可以顺带问用户要不要现在告诉你。
        - 常识性、闲聊性的问题正常回答，不用刻意强调"没有记录"。
        """

    /// Convenience for View-side `.disabled(...)` bindings (e.g. the dictate button
    /// can't be used while a live conversation is connected) — matching `if case`
    /// checks inline at call sites gets noisy fast. `.error` counts as "no active
    /// connection" here too, same as `.idle`: otherwise a conversation error would
    /// permanently lock dictation out until the conversation somehow got back to
    /// `.idle` on its own, even though the live-conversation mic button itself already
    /// treats `.error` as retryable (see `micButton`'s `case .idle, .error: start()`).
    var isIdle: Bool {
        switch state {
        case .idle, .error: return true
        default: return false
        }
    }

    /// Mutual exclusion across the three input modes (voice session / text session /
    /// dictation) — only one may be active at a time, because the voice and text
    /// sessions share `assistantLineIndex` (the streaming-reply-line builder); a
    /// concurrent reply from both would corrupt each other's line. Used to gate
    /// starting the voice session or dictation while the text session is connected.
    /// Mirrors web-demo/static/app.js's equivalent rule.
    var isTextSessionIdle: Bool {
        textState == .idle
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
            self?.setState(.listening)
        }

        // Belt-and-suspenders for rule (a): if the socket opens but the session never
        // actually comes up (session.updated never arrives), don't sit at "连接中…"
        // forever — a real observed failure mode, not hypothetical (see
        // docs/qwen-realtime-voice-setup.md). Cleared by `setState` once we leave
        // .connecting, from either the ready callback above or an error/disconnect.
        armConnectTimeout()

        // Pull-sync from AgentNexus in the background — short REST call, not on the
        // conversation's critical path. If it fails or is slow, the conversation just
        // proceeds with whatever's already in the local cache from last time.
        pullMemoryInBackground()
    }

    private func armConnectTimeout() {
        connectTimeoutTask?.cancel()
        connectTimeoutTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: Self.connectTimeoutSeconds * 1_000_000_000)
            guard let self, !Task.isCancelled else { return }
            if case .connecting = self.state {
                self.stop(reason: "连接超时，请重试")
            }
        }
    }

    private func pullMemoryInBackground() {
        Task { [weak self] in
            guard let self, AgentNexusConfigStore.isConfigured else { return }
            if let entries = try? await self.agentNexusClient.fetchMemoryEntries() {
                let formatter = ISO8601DateFormatter()
                let mapped = entries.map { entry -> (text: String, timestamp: Date, sourceId: String) in
                    let text = entry.title.map { "\($0)：\(entry.content)" } ?? entry.content
                    let date = entry.updatedAt.flatMap { formatter.date(from: $0) } ?? Date()
                    return (text, date, entry.entryId)
                }
                self.memoryStore.merge(remoteEntries: mapped)
            }
            // Independent of whether the memory pull above succeeded -- a transient
            // hiccup on one network call doesn't mean the other will also fail, and
            // this is exactly the kind of "we likely have network" moment
            // ConversationHistoryStore's retry is meant to ride alongside (item 4).
            self.conversationHistory.retryUnsynced()
        }
    }

    func stop(reason: String? = nil) {
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
        // .listening and .assistantSpeaking each drive orbLevel from their own source
        // (see wireCallbacks' onInputLevel/onOutputLevel); every other state has no
        // live audio to reflect, so reset it rather than leaving a stale reading.
        switch next {
        case .listening, .assistantSpeaking: break
        default: orbLevel = 0
        }
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

    private func wireCallbacks() {
        audio.onCapturedChunk = { [weak self] data in
            self?.client.sendAudioChunk(data)
        }

        // Fired on an audio thread -- hop to the main actor before touching @Published
        // state. Guarded by `state` so a reading from the "wrong" source (e.g. leftover
        // playback level right after a barge-in) never briefly drives the orb.
        audio.onInputLevel = { [weak self] level in
            Task { @MainActor [weak self] in
                guard let self, case .listening = self.state else { return }
                self.orbLevel = Double(level)
            }
        }
        audio.onOutputLevel = { [weak self] level in
            Task { @MainActor [weak self] in
                guard let self, case .assistantSpeaking = self.state else { return }
                self.orbLevel = Double(level)
            }
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
            self.appendUserTurn(text)
            self.groundAndRespond(to: text, session: self.client)
        }

        client.onSpeechStarted = { [weak self] in
            // User barged in — stop the assistant immediately. Not calling
            // finalizeAssistantTurn() here is deliberate: this is an interrupted,
            // incomplete reply, not a finished turn, so it's discarded rather than
            // persisted half-formed (matches web-demo's app.js).
            self?.audio.interruptPlayback()
            self?.client.cancelResponse()
            self?.assistantLineIndex = nil
            self?.setState(.listening)
        }

        client.onResponseDone = { [weak self] in
            self?.finalizeAssistantTurn()
            self?.setState(.listening)
        }

        client.onError = { [weak self] message in
            self?.setState(.error(message))
        }

        client.onDisconnect = { [weak self] error in
            guard let self else { return }
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
    ///
    /// Parameterized by `session` (the voice session's `client`, or the text session's
    /// `textClient`) so this grounding logic — the actually delicate part — isn't
    /// duplicated between the two, mirroring web-demo/static/app.js's
    /// `handleUserTurn(text, session)`.
    private func groundAndRespond(to userText: String, session: RealtimeClient) {
        // Pushing this turn to AgentNexus (with sync-status tracking + retry) is
        // handled by conversationHistory.add(), triggered from appendUserTurn() right
        // before this function runs at every call site -- not duplicated here.

        if let saveIntent = SaveIntent.detect(userText) {
            // source defaults to "local" here (not "agentnexus") -- honestly reflects
            // "not yet confirmed synced" until createMemoryEntry below actually
            // succeeds, per docs/roadmap-todo.md's "记忆" section item 3. "过户" to
            // agentnexus + the real sourceId happens via markSynced once that's
            // confirmed, not assumed up front.
            let localEntry = memoryStore.add(saveIntent.content)
            Task { [weak self] in
                guard let self else { return }
                if let created = try? await self.agentNexusClient.createMemoryEntry(layer: "PROGRESS", content: saveIntent.content),
                   let localEntry {
                    self.memoryStore.markSynced(id: localEntry.id, source: MemorySource.agentNexus, sourceId: created.entryId)
                }
            }
            let instructions = systemInstructions
                + "\n\n用户刚才明确要求记住这件事：\"\(saveIntent.content)\"，你已经帮TA记下了。只需要简短确认一句就行，不要复述内容、不要追问。"
            session.updateInstructions(instructions) {
                session.requestResponse()
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

        session.updateInstructions(instructions) {
            session.requestResponse()
        }
    }

    // ---- text session (typing + dictation-to-text; ported from
    // web-demo/static/app.js's textWs/TEXT_STATE, 2026-08-23) ----
    //
    // Fully independent connection/state machine from the voice session above: never
    // calls `audio.start()`, never touches the microphone, and its `session.update` is
    // configured with `modalities: ["text"]` only — verified live against the real API
    // to fully suppress all audio-related server events (see docs/app-design.md
    // section 8). This is what fixes the real bug where typing a message opened the
    // microphone and played a spoken reply: typed/dictated input now never reaches
    // `client`/`state` at all.

    /// Entry point for typed messages (and the time-based suggestion chips). Starts the
    /// text session first if needed — see `pendingTextSessionMessage`.
    func sendTextSessionMessage(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        switch textState {
        case .idle:
            pendingTextSessionMessage = trimmed
            startTextSession()
        default:
            submitTextSessionMessage(trimmed)
        }
    }

    /// Entry point for dictation's "direct send" — reuses the already-open,
    /// already-handshaken `RealtimeClient` handed off by
    /// `DictationViewModel.finishForDirectSend()` instead of opening a second
    /// connection. The dictation connection was already configured with
    /// `modalities: ["text"]` and no spoken reply (see `DictationViewModel.start()`),
    /// which is the same configuration the text session itself uses — so promoting it
    /// only needs one lightweight `instructions` patch, not a modality change or a full
    /// reconnect. Mirrors web-demo/static/app.js's
    /// `promoteDictationConnectionToTextSession`.
    func sendTextSessionMessage(_ text: String, reusingDictationClient reusableClient: RealtimeClient) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            reusableClient.disconnect()
            return
        }
        guard case .idle = textState else {
            // A text session is already connected some other way -- can't reuse a
            // second connection alongside it. Close the handed-off one and fall back
            // to the normal path.
            reusableClient.disconnect()
            sendTextSessionMessage(trimmed)
            return
        }

        setTextState(.connecting)
        textClient = reusableClient
        wireTextCallbacks()

        textClient.updateInstructions(systemInstructions) { [weak self] in
            guard let self else { return }
            self.setTextState(.ready)
            self.submitTextSessionMessage(trimmed)
        }

        armTextConnectTimeout()
        pullMemoryInBackground()
    }

    private func startTextSession() {
        guard case .idle = textState else { return }
        guard let apiKey = APIKeyStore.load(), !apiKey.isEmpty else {
            pendingTextSessionMessage = nil
            textSessionError = "请先在设置里填入 API Key"
            return
        }

        setTextState(.connecting)
        wireTextCallbacks()

        textClient.connect(
            baseURL: RealtimeConfigStore.effectiveBaseURL,
            apiKey: apiKey,
            model: RealtimeConfigStore.model,
            instructions: systemInstructions,
            voice: RealtimeConfigStore.voice,
            modalities: ["text"]
        ) { [weak self] in
            guard let self else { return }
            self.setTextState(.ready)
            if let pending = self.pendingTextSessionMessage {
                self.pendingTextSessionMessage = nil
                self.submitTextSessionMessage(pending)
            }
        }

        armTextConnectTimeout()
        pullMemoryInBackground()
    }

    func stopTextSession(reason: String? = nil) {
        pendingTextSessionMessage = nil
        textClient.disconnect()
        setTextState(.idle)
        textSessionError = reason
    }

    private func submitTextSessionMessage(_ text: String) {
        appendUserTurn(text)
        textClient.sendUserText(text)
        groundAndRespond(to: text, session: textClient)
    }

    private func armTextConnectTimeout() {
        textConnectTimeoutTask?.cancel()
        textConnectTimeoutTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: Self.connectTimeoutSeconds * 1_000_000_000)
            guard let self, !Task.isCancelled else { return }
            if case .connecting = self.textState {
                self.stopTextSession(reason: "连接超时，请重试")
            }
        }
    }

    /// Mirrors `setState`'s timer bookkeeping, for the text session's own idle/connect
    /// timers. `.ready` is the text-session equivalent of `.listening` — "connected,
    /// nothing pending" — the only state that counts toward the idle-disconnect clock.
    private func setTextState(_ next: TextSessionState) {
        textState = next
        if case .ready = next {
            textIdleTimeoutTask?.cancel()
            textIdleTimeoutTask = Task { [weak self] in
                try? await Task.sleep(nanoseconds: Self.idleTimeoutSeconds * 1_000_000_000)
                guard let self, !Task.isCancelled else { return }
                self.stopTextSession(reason: "长时间没有新消息，已自动断开")
            }
        } else {
            textIdleTimeoutTask?.cancel()
            textIdleTimeoutTask = nil
        }
        if case .connecting = next {} else {
            textConnectTimeoutTask?.cancel()
            textConnectTimeoutTask = nil
        }
    }

    private func wireTextCallbacks() {
        textClient.onTextDelta = { [weak self] text in
            self?.appendToAssistantLine(text)
        }

        textClient.onTextDone = { [weak self] text in
            // Fallback for providers that only send the final text, no deltas.
            guard let self, self.assistantLineIndex == nil, !text.isEmpty else { return }
            self.transcript.append(TranscriptLine(speaker: .assistant, text: text))
            self.assistantLineIndex = self.transcript.count - 1
        }

        textClient.onResponseDone = { [weak self] in
            self?.finalizeAssistantTurn()
        }

        textClient.onError = { [weak self] message in
            self?.stopTextSession(reason: message)
        }

        textClient.onDisconnect = { [weak self] error in
            self?.stopTextSession(reason: error?.localizedDescription)
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

    /// Single append point for every user-turn source (voice transcript, typed,
    /// dictation) — covers `transcript` (in-memory, for the UI) and
    /// `conversationHistory` (persisted) in one place, mirroring web-demo's addBubble().
    private func appendUserTurn(_ text: String) {
        transcript.append(TranscriptLine(speaker: .user, text: text))
        conversationHistory.add(speaker: .user, text: text)
    }

    /// Called once an assistant reply is actually complete (onResponseDone) — captures
    /// the full accumulated line text before assistantLineIndex gets reset, persists it
    /// locally and pushes it to AgentNexus (docs/app-design.md 8.4: previously only the
    /// user's half of the conversation was pushed/stored anywhere at all). Deliberately
    /// not called from onSpeechStarted's barge-in reset — see that callback's comment.
    private func finalizeAssistantTurn() {
        if let index = assistantLineIndex, index < transcript.count {
            let text = transcript[index].text
            // conversationHistory.add() also pushes to AgentNexus (with sync-status
            // tracking + retry) -- see ConversationHistoryStore.
            if !text.isEmpty { conversationHistory.add(speaker: .assistant, text: text) }
        }
        assistantLineIndex = nil
    }
}
