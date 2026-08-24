// Full conversation history — every turn (user + assistant), from both the voice
// session and the text session, in one chronological, unfiltered record. This is a
// different grain of thing from LocalMemory (memory.js): that's a searchable,
// extracted-fragment store used to ground replies; this is the complete transcript
// itself, kept purely so it survives a reload/app-kill (docs/app-design.md 8.4 --
// before this, only the user's half got pushed to AgentNexus, fire-and-forget, and the
// assistant's replies + the full conversation structure weren't persisted anywhere).
//
// Deliberately no search, no dedup, no cap yet -- see roadmap-todo.md's "原始对话记录
// 加滚动窗口裁剪" item, intentionally sequenced after this.
const ConversationHistory = (() => {
  const STORAGE_KEY = "voicechat.conversationHistory.v1";

  function load() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch (e) {
      return [];
    }
  }

  function persist(turns) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(turns));
    } catch (e) {
      // Storage full/unavailable — history just won't persist across reloads this session.
    }
  }

  let turns = load();

  return {
    /** @param {"user"|"assistant"} speaker */
    add(speaker, text) {
      const trimmed = (text || "").trim();
      if (!trimmed) return;
      turns.push({ speaker, text: trimmed, timestamp: Date.now() });
      persist(turns);
    },

    all() {
      return [...turns];
    },

    clear() {
      turns = [];
      persist(turns);
    },
  };
})();
