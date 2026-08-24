// Full conversation history — every turn (user + assistant), from both the voice
// session and the text session, in one chronological, unfiltered record. This is a
// different grain of thing from LocalMemory (memory.js): that's a searchable,
// extracted-fragment store used to ground replies; this is the complete transcript
// itself, kept purely so it survives a reload/app-kill (docs/app-design.md 8.4 --
// before this, only the user's half got pushed to AgentNexus, fire-and-forget, and the
// assistant's replies + the full conversation structure weren't persisted anywhere).
//
// Also owns pushing each turn to AgentNexus (previously a separate, truly
// fire-and-forget AgentNexusBridge.pushMessage call at each call site) and tracking
// whether that push actually succeeded -- docs/roadmap-todo.md, "记忆" section, item 4:
// a failed push used to leave no trace at all, so that turn would just never show up in
// AgentNexus with nothing locally aware it hadn't. `synced` records that per turn;
// `retryUnsynced()` re-attempts anything still unconfirmed, called alongside the
// existing pull-sync moments (app/tab start and foreground -- see app.js) since those
// are already "we likely have network, worth checking in" moments.
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

  async function pushOne(turn) {
    try {
      await AgentNexusBridge.pushMessage(turn.text, turn.speaker);
      turn.synced = true;
      persist(turns);
    } catch (e) {
      // Stays unsynced (or missing the flag entirely on entries persisted before this
      // field existed, which is equally falsy) -- retryUnsynced() will try again later.
      console.warn("AgentNexus push failed, will retry on next sync opportunity:", e);
    }
  }

  return {
    /** @param {"user"|"assistant"} speaker */
    add(speaker, text) {
      const trimmed = (text || "").trim();
      if (!trimmed) return undefined;
      const turn = { speaker, text: trimmed, timestamp: Date.now(), synced: false };
      turns.push(turn);
      persist(turns);
      pushOne(turn);
      return turn;
    },

    /** Re-attempts pushing every turn not yet confirmed synced. Fire-and-forget, like
     * the pull-sync calls it's meant to ride alongside. */
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
      persist(turns);
    },
  };
})();
