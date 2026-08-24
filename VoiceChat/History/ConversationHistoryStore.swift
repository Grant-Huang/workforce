import Foundation

struct ConversationTurn: Codable, Identifiable {
    enum Speaker: String, Codable { case user, assistant }
    let id: UUID
    let speaker: Speaker
    let text: String
    let timestamp: Date
    var synced: Bool

    init(speaker: Speaker, text: String, timestamp: Date = Date(), synced: Bool = false) {
        self.id = UUID()
        self.speaker = speaker
        self.text = text
        self.timestamp = timestamp
        self.synced = synced
    }
}

/// Full conversation history — every turn (user + assistant), from both the voice
/// session and the text session, in one chronological, unfiltered record. A different
/// grain of thing from `MemoryStore`: that's a searchable, extracted-fragment store
/// used to ground replies; this is the complete transcript itself, kept purely so it
/// survives an app relaunch (docs/app-design.md 8.4 — previously only the user's half
/// of the conversation got pushed to AgentNexus, fire-and-forget, and neither the
/// assistant's replies nor the full conversation structure were persisted anywhere).
///
/// Also owns pushing each turn to AgentNexus (previously a separate, truly
/// fire-and-forget `AgentNexusClient.pushMessage` call at each call site) and tracking
/// whether that push actually succeeded — docs/roadmap-todo.md, "记忆" section, item 4:
/// a failed push used to leave no trace at all, so that turn would just never show up
/// in AgentNexus with nothing locally aware it hadn't. `synced` records that per turn;
/// `retryUnsynced()` re-attempts anything still unconfirmed, called from
/// `ConversationViewModel.pullMemoryInBackground()` alongside the existing pull-sync
/// (already a "we likely have network, worth checking in" moment).
///
/// Deliberately no search, no dedup, no cap yet — see roadmap-todo.md's "原始对话记录
/// 加滚动窗口裁剪" item, intentionally sequenced after this. Mirrors web-demo's history.js.
final class ConversationHistoryStore {
    private let fileURL: URL
    private var turns: [ConversationTurn] = []
    private let queue = DispatchQueue(label: "com.jacer.voicechat.conversationhistorystore")
    private let agentNexusClient: AgentNexusClient

    init(fileName: String = "conversationHistory.json", agentNexusClient: AgentNexusClient) {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        self.fileURL = dir.appendingPathComponent(fileName)
        self.turns = Self.load(from: fileURL)
        self.agentNexusClient = agentNexusClient
    }

    @discardableResult
    func add(speaker: ConversationTurn.Speaker, text: String) -> ConversationTurn? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        let turn = ConversationTurn(speaker: speaker, text: trimmed)
        queue.sync {
            turns.append(turn)
            persist()
        }
        Task { [weak self] in await self?.pushAndMarkSynced(turn) }
        return turn
    }

    /// Re-attempts pushing every turn not yet confirmed synced. Fire-and-forget, like
    /// the pull-sync call it's meant to ride alongside.
    func retryUnsynced() {
        let pending = queue.sync { turns.filter { !$0.synced } }
        for turn in pending {
            Task { [weak self] in await self?.pushAndMarkSynced(turn) }
        }
    }

    func all() -> [ConversationTurn] {
        queue.sync { turns }
    }

    private func pushAndMarkSynced(_ turn: ConversationTurn) async {
        do {
            try await agentNexusClient.pushMessage(turn.text, senderType: turn.speaker.rawValue)
            queue.sync {
                guard let index = turns.firstIndex(where: { $0.id == turn.id }) else { return }
                turns[index].synced = true
                persist()
            }
        } catch {
            // Stays unsynced -- retryUnsynced() will try again later.
        }
    }

    private func persist() {
        guard let data = try? JSONEncoder().encode(turns) else { return }
        try? data.write(to: fileURL, options: .atomic)
    }

    private static func load(from url: URL) -> [ConversationTurn] {
        guard let data = try? Data(contentsOf: url),
              let turns = try? JSONDecoder().decode([ConversationTurn].self, from: data) else { return [] }
        return turns
    }
}
