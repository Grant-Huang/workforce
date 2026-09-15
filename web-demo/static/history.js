// Full conversation history — every turn (user + assistant).
// Phase 1：改存 IndexedDB（WorkingMemory.recent_turns），丢弃旧 localStorage 键，不做迁移。
const ConversationHistory = (() => {
  const MAX_ENTRIES = 500;
  const MAX_AGE_DAYS = 30;
  const MAX_AGE_MS = MAX_AGE_DAYS * 24 * 60 * 60 * 1000;

  let turns = [];
  let userId = null;

  function trim(list) {
    if (list.length <= MAX_ENTRIES) return list;
    const cutoffTime = Date.now() - MAX_AGE_MS;
    const recentByCount = new Set(list.slice(-MAX_ENTRIES));
    return list.filter((t) => recentByCount.has(t) || t.timestamp >= cutoffTime);
  }

  async function persist() {
    if (!userId || typeof WorkingMemory === "undefined") return;
    const doc = await WorkingMemory.get(userId);
    doc.recent_turns = turns;
    await WorkingMemory.put(doc);
  }

  async function bindUser(uid) {
    userId = uid;
    if (!uid) {
      turns = [];
      return;
    }
    const doc = await WorkingMemory.get(uid);
    turns = Array.isArray(doc.recent_turns) ? doc.recent_turns : [];
  }

  async function pushOne(turn) {
    try {
      await AgentNexusBridge.pushMessage(turn.text, turn.speaker);
      turn.synced = true;
      persist();
    } catch (e) {
      console.warn("AgentNexus push failed, will retry on next sync opportunity:", e);
    }
  }

  return {
    bindUser,

    /** @param {"user"|"assistant"} speaker */
    add(speaker, text) {
      const trimmed = (text || "").trim();
      if (!trimmed) return undefined;
      const turn = { speaker, text: trimmed, timestamp: Date.now(), synced: false };
      turns.push(turn);
      turns = trim(turns);
      persist();
      pushOne(turn);
      return turn;
    },

    retryUnsynced() {
      for (const turn of turns) {
        if (!turn.synced) pushOne(turn);
      }
    },

    all() {
      return [...turns];
    },

    clear() {
      turns = [];
      persist();
    },
  };
})();
