// Local Working Memory — IndexedDB，按 user_id 隔离（Runtime §8）。
// 丢弃旧 localStorage 记忆/历史键，不做迁移（拍板 §4）。
const WorkingMemory = (() => {
  // 库名避免多余标点；keyPath 必须是合法 JS Identifier（用 user_id，勿用 user-id）
  const DB_NAME = "workforce_working_memory_v2";
  const STORE = "byUser";
  let dbPromise = null;

  function openDb() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          // keyPath「user_id」是合法 IdentifierName；「user-id」会触发 Safari SyntaxError
          // 「The string did not match the expected pattern.」
          db.createObjectStore(STORE, { keyPath: "user_id" });
        }
      };
      req.onsuccess = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          db.close();
          dbPromise = null;
          reject(new Error("WorkingMemory object store 缺失，请刷新页面重试"));
          return;
        }
        resolve(db);
      };
      req.onerror = () => {
        const err = req.error;
        dbPromise = null;
        reject(err || new Error("IndexedDB 打开失败"));
      };
    });
    return dbPromise;
  }

  function emptyDoc(userId) {
    return {
      user_id: userId,
      user_profile: {},
      conversation_summary: {},
      recent_turns: [],
      hot_memory: [],
      active_entities: [],
      active_tasks: [],
      updated_at: Date.now(),
    };
  }

  async function get(userId) {
    if (!userId) return emptyDoc("");
    try {
      const db = await openDb();
      return await new Promise((resolve, reject) => {
        const tx = db.transaction(STORE, "readonly");
        const req = tx.objectStore(STORE).get(userId);
        req.onsuccess = () => resolve(req.result || emptyDoc(userId));
        req.onerror = () => reject(req.error);
      });
    } catch (e) {
      console.warn("WorkingMemory.get failed, using empty doc:", e);
      return emptyDoc(userId);
    }
  }

  async function put(doc) {
    if (!doc || !doc.user_id) {
      throw new Error("WorkingMemory.put 需要 user_id");
    }
    const db = await openDb();
    doc.updated_at = Date.now();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, "readwrite");
      const req = tx.objectStore(STORE).put(doc);
      req.onsuccess = () => resolve(doc);
      req.onerror = () => reject(req.error);
    });
  }

  async function applyBootstrap(userId, data) {
    const doc = await get(userId);
    doc.user_profile = data.user_profile || doc.user_profile || {};
    doc.conversation_summary = data.conversation_summary || doc.conversation_summary || {};
    doc.hot_memory = data.hot_memory || [];
    doc.active_entities = data.active_entities || [];
    doc.active_tasks = data.active_tasks || [];
    return put(doc);
  }

  async function appendTurn(userId, turn) {
    const doc = await get(userId);
    doc.recent_turns = [...(doc.recent_turns || []), turn].slice(-200);
    return put(doc);
  }

  async function upsertHotMemory(userId, entry) {
    const doc = await get(userId);
    const list = doc.hot_memory || [];
    const idx = list.findIndex((e) => e.id === entry.id || (entry.source_id && e.source_id === entry.source_id));
    if (idx >= 0) list[idx] = { ...list[idx], ...entry };
    else list.unshift(entry);
    doc.hot_memory = list.slice(0, 50);
    return put(doc);
  }

  /** 关键词重合检索（本地可答 / Answerability），不走网络。 */
  function searchHot(doc, query, limit = 5) {
    const entries = doc.hot_memory || [];
    const q = (query || "").toLowerCase();
    if (!q.trim()) return [];
    const scored = entries
      .map((e) => {
        const text = (e.text || "").toLowerCase();
        let overlap = 0;
        for (const ch of q) if (text.includes(ch)) overlap++;
        // 也按词切
        for (const w of q.split(/[\s,.!?;:，。！？；：]+/).filter(Boolean)) {
          if (text.includes(w)) overlap += 2;
        }
        return overlap > 0 ? { entry: e, score: overlap } : null;
      })
      .filter(Boolean);
    scored.sort((a, b) => b.score - a.score);
    return scored.slice(0, limit).map((s) => s.entry);
  }

  return { get, put, applyBootstrap, appendTurn, upsertHotMemory, searchHot, emptyDoc };
})();
