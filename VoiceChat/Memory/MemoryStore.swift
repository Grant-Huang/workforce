import Foundation

/// Known values for `MemoryEntry.source`. Deliberately a plain `String` field, not an
/// enum -- docs/app-design.md section 7.2/roadmap-todo.md's "记忆" section: this is meant
/// to grow to cover future source systems without a code change here every time one's
/// added, so these are just the ones this app itself currently produces/consumes.
enum MemorySource {
    /// Produced on this device and not yet confirmed synced anywhere (raw dialogue
    /// turns, freshly-explained personal jargon). Phase 2 will add the sync-status
    /// tracking that turns this into "confirmed synced" vs "needs retry".
    static let local = "local"
    /// Pulled from AgentNexus's channel memory API.
    static let agentNexus = "agentnexus"
    /// Entries persisted before this field existed (memory.json had no `source` key at
    /// all) -- deliberately NOT defaulted to `local`, which would incorrectly imply
    /// "not yet synced" for entries that, for all we know, already went through the old
    /// fire-and-forget push path. "unknown" is the honest answer, not a guess.
    static let unknown = "unknown"
}

struct MemoryEntry: Identifiable, Codable, Equatable {
    let id: UUID
    let timestamp: Date
    let text: String
    /// Which system this entry came from -- see `MemorySource`. Formal replacement for
    /// having no provenance at all (docs/roadmap-todo.md, "记忆" section, item 1).
    var source: String
    /// This entry's native id within `source` (e.g. AgentNexus's `entry_id`). `nil` for
    /// entries with no such id yet -- `source == .local` entries before they're synced.
    var sourceId: String?

    init(id: UUID = UUID(), timestamp: Date = Date(), text: String, source: String = MemorySource.local, sourceId: String? = nil) {
        self.id = id
        self.timestamp = timestamp
        self.text = text
        self.source = source
        self.sourceId = sourceId
    }

    // Custom decoding only -- encoding stays synthesized. Existing memory.json files
    // predate `source`/`sourceId` entirely; decoding them with the synthesized
    // Decodable (which would require both keys) would throw, and MemoryStore.load()'s
    // `try?` would then silently discard every existing entry on first launch after
    // this update. decodeIfPresent + a default keeps old data intact instead.
    enum CodingKeys: String, CodingKey {
        case id, timestamp, text, source, sourceId
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(UUID.self, forKey: .id)
        timestamp = try container.decode(Date.self, forKey: .timestamp)
        text = try container.decode(String.self, forKey: .text)
        source = try container.decodeIfPresent(String.self, forKey: .source) ?? MemorySource.unknown
        sourceId = try container.decodeIfPresent(String.self, forKey: .sourceId)
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
    /// Always `source: .local` -- this is content produced on this device, not pulled from
    /// anywhere. See MemorySource.local's doc comment for what that implies about sync status.
    func add(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count >= 2 else { return }
        queue.sync {
            entries.append(MemoryEntry(text: trimmed, source: MemorySource.local))
            persist()
        }
    }

    func all() -> [MemoryEntry] {
        queue.sync { entries.sorted { $0.timestamp > $1.timestamp } }
    }

    /// Merges entries pulled from AgentNexus, skipping any whose text already exists
    /// locally — cheap enough at personal-memory scale, and avoids re-adding the same
    /// AgentNexus content on every app launch without needing a separate persisted
    /// "already merged" id set.
    ///
    /// Phase 1 scope note (docs/roadmap-todo.md): this only adds the `sourceId` field to
    /// what gets stored -- the text-based dedup logic itself is unchanged here on purpose.
    /// Phase 2 is what replaces this with a real upsert keyed on (source, sourceId); doing
    /// that here too would be scope creep past what this pass is for.
    func merge(remoteEntries: [(text: String, timestamp: Date, sourceId: String)]) {
        queue.sync {
            let existingTexts = Set(entries.map(\.text))
            for entry in remoteEntries where !existingTexts.contains(entry.text) {
                entries.append(MemoryEntry(timestamp: entry.timestamp, text: entry.text, source: MemorySource.agentNexus, sourceId: entry.sourceId))
            }
            persist()
        }
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
