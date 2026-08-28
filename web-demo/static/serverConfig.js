// Makes the backend this page talks to configurable, instead of hardcoded to
// same-origin (this page's own server.py). Needed now that omni-server exists as a
// separately deployed service (see repo root README's "相关仓库" section) -- this
// same frontend, unchanged otherwise, should be able to point at either one.
//
// Default (no ?server= param, no prior choice saved) is the empty string, which
// keeps every existing call exactly as it was: same-origin WebSocket/fetch, talking
// to this page's own server.py. Nothing changes for anyone running the demo the way
// it's always been run.
const ServerConfig = (() => {
  const STORAGE_KEY = "voicechat.serverBase";
  const params = new URLSearchParams(location.search);
  const fromQuery = params.get("server");

  if (fromQuery !== null) {
    // An explicit ?server= (even "" to reset back to same-origin) is a deliberate
    // choice -- persist it so it survives a reload without retyping it every time.
    try {
      if (fromQuery) localStorage.setItem(STORAGE_KEY, fromQuery);
      else localStorage.removeItem(STORAGE_KEY);
    } catch (e) {
      // storage unavailable -- the choice just won't survive a reload this session.
    }
  }

  let base = fromQuery;
  if (base === null) {
    try {
      base = localStorage.getItem(STORAGE_KEY) || "";
    } catch (e) {
      base = "";
    }
  }
  base = base.replace(/\/+$/, "");

  /** @param {string} path e.g. "/ws" */
  function wsUrl(path) {
    if (base) return `${base.replace(/^http/, "ws")}${path}`;
    const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${wsProto}//${location.host}${path}`;
  }

  /** @param {string} path e.g. "/api/config" */
  function apiUrl(path) {
    return base ? `${base}${path}` : path;
  }

  return { wsUrl, apiUrl, base };
})();
