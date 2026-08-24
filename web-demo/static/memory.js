// Local memory store — JS port of VoiceChat/Memory/MemoryStore.swift.
//
// Every transcribed/typed user turn gets stored here (localStorage), and later turns
// retrieve relevant past entries by keyword overlap + recency to ground the model's
// answer. Deliberately the simplest thing that validates the concept — no embeddings.
//
// This is the "always fast, always local" layer described in
// docs/agentnexus-memory-integration-proposal.md — AgentNexusBridge (agentnexus.js)
// pulls into / pushes out of this same store in the background; the live conversation
// only ever touches this local object, never the network.
// Known values for an entry's `source` field -- deliberately a plain string, not a
// fixed enum (docs/app-design.md 7.2/roadmap-todo.md's "记忆" section: meant to grow to
// cover future source systems without a code change here every time one's added).
const MemorySource = {
  LOCAL: "local", // produced on this device, not yet confirmed synced anywhere
  AGENTNEXUS: "agentnexus", // pulled from AgentNexus's channel memory API
  UNKNOWN: "unknown", // entries persisted before `source` existed -- see load()
};

const LocalMemory = (() => {
  const STORAGE_KEY = "voicechat.localMemory.v1";

  function load() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      const parsed = raw ? JSON.parse(raw) : [];
      // Entries saved before `source` existed have no such key at all. Defaulting
      // those to "unknown" rather than "local" is deliberate -- "local" implies "not
      // yet confirmed synced", which we can't actually claim for entries that, for all
      // we know, already went through the old fire-and-forget push path.
      return parsed.map((e) => (e.source ? e : { ...e, source: MemorySource.UNKNOWN }));
    } catch (e) {
      return [];
    }
  }

  function persist(entries) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
    } catch (e) {
      // Storage full/unavailable — memory just won't persist across reloads this session.
    }
  }

  let entries = load();

  function tokenize(text) {
    const tokens = new Set();
    for (const word of text.toLowerCase().split(/[\s,.!?;:，。！？；：]+/).filter(Boolean)) {
      tokens.add(word);
      for (const ch of word) {
        if (/[^\x00-\x7F]/.test(ch)) tokens.add(ch); // CJK: also index individual characters
      }
    }
    return tokens;
  }

  return {
    /**
     * @param {string} text
     * @param {object} [meta] e.g. {source: "agentnexus", sourceId: "entry_abc", layer: "ANCHOR"}.
     *   Defaults to source: "local" (see MemorySource.LOCAL) when the caller doesn't
     *   specify one -- every raw dialogue turn stored via a bare `add(text)` call is
     *   produced on this device, so that default is correct, not a placeholder.
     * @returns the created entry (or undefined if the text was too short to store) --
     *   callers that go on to push this content to AgentNexus need the entry's `id`
     *   back so they can call `markSynced` on the right one once the push confirms.
     */
    add(text, meta = {}) {
      const trimmed = (text || "").trim();
      if (trimmed.length < 2) return undefined;
      const entry = { id: crypto.randomUUID(), text: trimmed, timestamp: Date.now(), source: MemorySource.LOCAL, ...meta };
      entries.push(entry);
      persist(entries);
      return entry;
    },

    /**
     * "过户" a locally-produced entry to its now-confirmed AgentNexus identity, once a
     * push/create call for it has actually succeeded (docs/roadmap-todo.md, "记忆"
     * section, item 3) -- before this point the entry stays `source: "local"`, honestly
     * reflecting "not yet confirmed synced" rather than assuming success up front.
     */
    markSynced(id, { source, sourceId }) {
      const entry = entries.find((e) => e.id === id);
      if (!entry) return;
      entry.source = source;
      entry.sourceId = sourceId;
      persist(entries);
    },

    /**
     * Reconciles the local `source: "agentnexus"` subset against a fresh pull, keyed on
     * `sourceId` (docs/roadmap-todo.md, "记忆" section, items 1+2): entries missing from
     * `remoteEntries` are dropped (deleted upstream, or otherwise no longer current) --
     * a full replace of that subset rather than tracking deletions one by one, since the
     * whole point of caching AgentNexus's memory locally is that it's a disposable,
     * rebuildable mirror. An entry present in both is only overwritten when the pulled
     * copy is at least as new (by `timestamp`, which for agentnexus-sourced entries is
     * already the source's own `updated_at` -- see agentnexus.js's pullMemory) --
     * guards against an out-of-order/late-arriving pull response clobbering something a
     * more recent pull already updated. `source: "local"`/`"unknown"` entries are a
     * completely different lifecycle (this device's own not-yet-synced content, or
     * pre-Phase-1 data of unknown provenance) and this never touches them.
     */
    merge(remoteEntries) {
      const existingBySourceId = new Map(
        entries.filter((e) => e.source === MemorySource.AGENTNEXUS && e.sourceId != null).map((e) => [e.sourceId, e])
      );
      const reconciled = remoteEntries.map((remote) => {
        const existing = existingBySourceId.get(remote.sourceId);
        if (existing && existing.timestamp >= remote.timestamp) return existing;
        return { id: remote.id, text: remote.text, timestamp: remote.timestamp, source: MemorySource.AGENTNEXUS, sourceId: remote.sourceId, layer: remote.layer };
      });
      entries = [...entries.filter((e) => e.source !== MemorySource.AGENTNEXUS), ...reconciled];
      persist(entries);
    },

    search(query, limit = 5) {
      const queryTokens = tokenize(query);
      if (queryTokens.size === 0) return [];
      const now = Date.now();
      const scored = entries
        .map((entry) => {
          const entryTokens = tokenize(entry.text);
          let overlap = 0;
          for (const t of queryTokens) if (entryTokens.has(t)) overlap++;
          if (overlap === 0) return null;
          const ageDays = (now - entry.timestamp) / 86400000;
          const recencyBoost = 1 / (1 + Math.max(ageDays, 0));
          return { entry, score: overlap + recencyBoost };
        })
        .filter(Boolean);
      scored.sort((a, b) => b.score - a.score);
      return scored.slice(0, limit).map((s) => s.entry);
    },

    all() {
      return [...entries].sort((a, b) => b.timestamp - a.timestamp);
    },

    clear() {
      entries = [];
      persist(entries);
    },
  };
})();
