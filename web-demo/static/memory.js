// Local memory 薄适配层：语义保留（add/search/merge/markSynced），
// 存储改走 IndexedDB Working Memory 的 hot_memory（拍板：丢弃 localStorage，不做迁移）。
const MemorySource = {
  LOCAL: "local",
  AGENTNEXUS: "agentnexus",
  UNKNOWN: "unknown",
};

const LocalMemory = (() => {
  let cache = [];
  let userId = null;

  function tokenize(text) {
    const tokens = new Set();
    for (const word of text.toLowerCase().split(/[\s,.!?;:，。！？；：]+/).filter(Boolean)) {
      tokens.add(word);
      for (const ch of word) {
        if (/[^\x00-\x7F]/.test(ch)) tokens.add(ch);
      }
    }
    return tokens;
  }

  async function bindUser(uid) {
    userId = uid;
    if (!uid) {
      cache = [];
      return;
    }
    const doc = await WorkingMemory.get(uid);
    cache = (doc.hot_memory || []).map((e) => ({
      id: e.id || crypto.randomUUID(),
      text: e.text,
      timestamp: e.timestamp || Date.now(),
      source: e.source || MemorySource.LOCAL,
      sourceId: e.source_id || e.sourceId,
      layer: e.layer,
    }));
  }

  async function persist() {
    if (!userId) return;
    const doc = await WorkingMemory.get(userId);
    doc.hot_memory = cache.map((e) => ({
      id: e.id,
      text: e.text,
      timestamp: e.timestamp,
      source: e.source,
      source_id: e.sourceId,
      layer: e.layer,
    }));
    await WorkingMemory.put(doc);
  }

  return {
    bindUser,

    add(text, meta = {}) {
      const trimmed = (text || "").trim();
      if (trimmed.length < 2) return undefined;
      const entry = {
        id: crypto.randomUUID(),
        text: trimmed,
        timestamp: Date.now(),
        source: MemorySource.LOCAL,
        ...meta,
      };
      cache.push(entry);
      persist();
      return entry;
    },

    markSynced(id, { source, sourceId }) {
      const entry = cache.find((e) => e.id === id);
      if (!entry) return;
      entry.source = source;
      entry.sourceId = sourceId;
      persist();
    },

    merge(remoteEntries) {
      const existingBySourceId = new Map(
        cache
          .filter((e) => e.source === MemorySource.AGENTNEXUS && e.sourceId != null)
          .map((e) => [e.sourceId, e])
      );
      const reconciled = remoteEntries.map((remote) => {
        const existing = existingBySourceId.get(remote.sourceId);
        if (existing && existing.timestamp >= remote.timestamp) return existing;
        return {
          id: remote.id,
          text: remote.text,
          timestamp: remote.timestamp,
          source: MemorySource.AGENTNEXUS,
          sourceId: remote.sourceId,
          layer: remote.layer,
        };
      });
      cache = [...cache.filter((e) => e.source !== MemorySource.AGENTNEXUS), ...reconciled];
      persist();
    },

    search(query, limit = 5) {
      const queryTokens = tokenize(query);
      if (queryTokens.size === 0) return [];
      const now = Date.now();
      const scored = cache
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
      return [...cache].sort((a, b) => b.timestamp - a.timestamp);
    },

    clear() {
      cache = [];
      persist();
    },
  };
})();
