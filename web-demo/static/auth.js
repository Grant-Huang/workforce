// 登录态客户端：session token 存 sessionStorage（兼 cookie httponly）。
// V1 必须登录才能开语音 / 读写记忆。
const Auth = (() => {
  const TOKEN_KEY = "workforce.auth.token";
  let currentUser = null;

  function getToken() {
    return sessionStorage.getItem(TOKEN_KEY) || "";
  }

  function setToken(token) {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  }

  function authHeaders(extra = {}) {
    const headers = { "Content-Type": "application/json", ...extra };
    const token = getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
    return headers;
  }

  /**
   * Safari/WebKit 对空 body / 非 JSON 调用 response.json() 时会抛出
   * DOMException「The string did not match the expected pattern.」——先读 text 再 parse。
   */
  async function readJson(res) {
    const text = await res.text();
    if (!text || !text.trim()) {
      throw new Error(`服务器返回空响应 (${res.status})`);
    }
    try {
      return JSON.parse(text);
    } catch (_) {
      const snippet = text.replace(/\s+/g, " ").slice(0, 80);
      throw new Error(`服务器返回了非 JSON 响应 (${res.status}): ${snippet}`);
    }
  }

  async function login(username, password) {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
      credentials: "same-origin",
    });
    const body = await readJson(res);
    if (body.status !== "success") throw new Error(body.message || "登录失败");
    if (!body.data || !body.data.token || !body.data.user_id) {
      throw new Error("登录响应缺少 token/user_id");
    }
    setToken(body.data.token);
    currentUser = body.data;
    return body.data;
  }

  async function logout() {
    try {
      await fetch("/api/auth/logout", {
        method: "POST",
        headers: authHeaders(),
        credentials: "same-origin",
      });
    } catch (_) {}
    setToken("");
    currentUser = null;
  }

  async function me() {
    const token = getToken();
    if (!token) {
      currentUser = null;
      return null;
    }
    try {
      const res = await fetch("/api/auth/me", {
        headers: authHeaders(),
        credentials: "same-origin",
      });
      const body = await readJson(res);
      if (body.status !== "success") {
        setToken("");
        currentUser = null;
        return null;
      }
      currentUser = body.data;
      return currentUser;
    } catch (_) {
      setToken("");
      currentUser = null;
      return null;
    }
  }

  function user() {
    return currentUser;
  }

  function isLoggedIn() {
    return !!(currentUser && currentUser.user_id);
  }

  return { login, logout, me, user, isLoggedIn, getToken, authHeaders };
})();
