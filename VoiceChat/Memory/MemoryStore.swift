import Foundation

struct MemoryEntry: Identifiable, Codable, Equatable {
    let id: UUID
    let timestamp: Date
    let text: String

    init(id: UUID = UUID(), timestamp: Date = Date(), text: String) {
        self.id = id
        self.timestamp = timestamp
        self.text = text
    }
}

/// On-device memory store: every transcribed thing the user says gets appended here,
/// and later questions can retrieve relevant past entries to ground the voice model's
/// answer.
///
/// This is deliberately the simplest thing that could validate the idea — a JSON file,
/// keyword-overlap retrieval, no embeddings/vector DB. If this App later gets folded
/// into 自书 (AgentNexus's notebook), swap this out for a client that reads/writes
/// 自书's shared memory instead; `add`/`search` is the seam to replace.
final class MemoryStore {
    private let fileURL: URL
    private var entries: [MemoryEntry] = []
    private let queue = DispatchQueue(label: "com.jacer.voicechat.memorystore")

    init(fileName: String = "memory.json") {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        self.fileURL = dir.appendingPathComponent(fileName)
        self.entries = Self.load(from: fileURL)
    }

    /// Appends a new memory entry. Ignores blank/very short transcripts (noise, filler sounds).
    func add(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count >= 2 else { return }
        queue.sync {
            entries.append(MemoryEntry(text: trimmed))
            persist()
        }
    }

    func all() -> [MemoryEntry] {
        queue.sync { entries.sorted { $0.timestamp > $1.timestamp } }
    }

    /// Naive keyword-overlap + recency ranking. Good enough to validate "can retrieved
    /// local memory ground a spoken answer" without pulling in an embedding model.
    func search(query: String, limit: Int = 5) -> [MemoryEntry] {
        let queryTokens = tokenize(query)
        guard !queryTokens.isEmpty else { return [] }

        let now = Date()
        let scored: [(entry: MemoryEntry, score: Double)] = queue.sync {
            entries.compactMap { entry in
                let entryTokens = tokenize(entry.text)
                let overlap = queryTokens.intersection(entryTokens).count
                guard overlap > 0 else { return nil }
                let ageInDays = now.timeIntervalSince(entry.timestamp) / 86400
                let recencyBoost = 1.0 / (1.0 + ageInDays)
                return (entry, Double(overlap) + recencyBoost)
            }
        }
        return scored.sorted { $0.score > $1.score }.prefix(limit).map(\.entry)
    }

    /// Splits into single Han characters (for CJK) plus whitespace-delimited words
    /// (for Latin text), so overlap scoring works reasonably for mixed Chinese/English input.
    private func tokenize(_ text: String) -> Set<String> {
        var tokens = Set<String>()
        for word in text.lowercased().split(whereSeparator: { $0.isWhitespace || $0.isPunctuation }) {
            tokens.insert(String(word))
            for character in word where character.isLetter && !character.isASCII {
                tokens.insert(String(character))
            }
        }
        return tokens
    }

    private func persist() {
        guard let data = try? JSONEncoder().encode(entries) else { return }
        try? data.write(to: fileURL, options: .atomic)
    }

    private static func load(from url: URL) -> [MemoryEntry] {
        guard let data = try? Data(contentsOf: url),
              let entries = try? JSONDecoder().decode([MemoryEntry].self, from: data) else { return [] }
        return entries
    }
}
