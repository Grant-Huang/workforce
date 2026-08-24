import Foundation

struct ConversationTurn: Codable {
    enum Speaker: String, Codable { case user, assistant }
    let speaker: Speaker
    let text: String
    let timestamp: Date
}

/// Full conversation history — every turn (user + assistant), from both the voice
/// session and the text session, in one chronological, unfiltered record. A different
/// grain of thing from `MemoryStore`: that's a searchable, extracted-fragment store
/// used to ground replies; this is the complete transcript itself, kept purely so it
/// survives an app relaunch (docs/app-design.md 8.4 — previously only the user's half
/// of the conversation got pushed to AgentNexus, fire-and-forget, and neither the
/// assistant's replies nor the full conversation structure were persisted anywhere).
///
/// Deliberately no search, no dedup, no cap yet — see roadmap-todo.md's "原始对话记录
/// 加滚动窗口裁剪" item, intentionally sequenced after this. Mirrors web-demo's history.js.
final class ConversationHistoryStore {
    private let fileURL: URL
    private var turns: [ConversationTurn] = []
    private let queue = DispatchQueue(label: "com.jacer.voicechat.conversationhistorystore")

    init(fileName: String = "conversationHistory.json") {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        self.fileURL = dir.appendingPathComponent(fileName)
        self.turns = Self.load(from: fileURL)
    }

    func add(speaker: ConversationTurn.Speaker, text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        queue.sync {
            turns.append(ConversationTurn(speaker: speaker, text: trimmed, timestamp: Date()))
            persist()
        }
    }

    func all() -> [ConversationTurn] {
        queue.sync { turns }
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
