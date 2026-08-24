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
    /// Returns the created entry (nil if too short to store) so a caller that goes on to
    /// push it to AgentNexus can call `markSynced` on the right one once that's confirmed.
    @discardableResult
    func add(_ text: String) -> MemoryEntry? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count >= 2 else { return nil }
        let entry = MemoryEntry(text: trimmed, source: MemorySource.local)
        queue.sync {
            entries.append(entry)
            persist()
        }
        return entry
    }

    /// "过户" a locally-produced entry to its now-confirmed AgentNexus identity, once a
    /// create call for it has actually succeeded (docs/roadmap-todo.md, "记忆" section,
    /// item 3) -- before this point the entry stays `source: .local`, honestly
    /// reflecting "not yet confirmed synced" rather than assuming success up front.
    func markSynced(id: UUID, source: String, sourceId: String) {
        queue.sync {
            guard let index = entries.firstIndex(where: { $0.id == id }) else { return }
            entries[index].source = source
            entries[index].sourceId = sourceId
            persist()
        }
    }

    func all() -> [MemoryEntry] {
        queue.sync { entries.sorted { $0.timestamp > $1.timestamp } }
    }

    /// Reconciles the local `source: .agentNexus` subset against a fresh pull, keyed on
    /// `sourceId` (docs/roadmap-todo.md, "记忆" section, items 1+2): entries missing from
    /// `remoteEntries` are dropped (deleted upstream, or otherwise no longer current) --
    /// a full replace of that subset rather than tracking deletions one by one, since
    /// the whole point of caching AgentNexus's memory locally is that it's a disposable,
    /// rebuildable mirror. An entry present in both is only overwritten when the pulled
    /// copy is at least as new (by `timestamp`, which for agentNexus-sourced entries is
    /// already the source's own `updated_at` -- see `ConversationViewModel.pullMemoryInBackground`)
    /// -- guards against an out-of-order/late-arriving pull response clobbering
    /// something a more recent pull already updated. `source: .local`/`.unknown`
    /// entries are a completely different lifecycle (this device's own not-yet-synced
    /// content, or pre-Phase-1 data of unknown provenance) and this never touches them.
    func merge(remoteEntries: [(text: String, timestamp: Date, sourceId: String)]) {
        queue.sync {
            var existingBySourceId: [String: MemoryEntry] = [:]
            for entry in entries where entry.source == MemorySource.agentNexus {
                if let sourceId = entry.sourceId { existingBySourceId[sourceId] = entry }
            }
            let reconciled: [MemoryEntry] = remoteEntries.map { remote in
                if let existing = existingBySourceId[remote.sourceId], existing.timestamp >= remote.timestamp {
                    return existing
                }
                return MemoryEntry(timestamp: remote.timestamp, text: remote.text, source: MemorySource.agentNexus, sourceId: remote.sourceId)
            }
            entries = entries.filter { $0.source != MemorySource.agentNexus } + reconciled
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
