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
const LocalMemory = (() => {
  const STORAGE_KEY = "voicechat.localMemory.v1";

  function load() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : [];
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
    /** @param {string} text @param {object} [meta] e.g. {source: "agentnexus", layer: "ANCHOR"} */
    add(text, meta = {}) {
      const trimmed = (text || "").trim();
      if (trimmed.length < 2) return;
      entries.push({ id: crypto.randomUUID(), text: trimmed, timestamp: Date.now(), ...meta });
      persist(entries);
    },

    /** Bulk-merge entries pulled from AgentNexus without duplicating by id. */
    merge(remoteEntries) {
      const existingIds = new Set(entries.map((e) => e.id));
      for (const e of remoteEntries) {
        if (!existingIds.has(e.id)) entries.push(e);
      }
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
