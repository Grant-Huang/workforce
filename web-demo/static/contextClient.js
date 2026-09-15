// Context API 客户端：bootstrap / context.query / memory.event
const ContextClient = (() => {
  async function post(path, payload, timeoutMs = 8000) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(path, {
        method: "POST",
        headers: Auth.authHeaders(),
        credentials: "same-origin",
        body: JSON.stringify(payload || {}),
        signal: ctrl.signal,
      });
      const text = await res.text();
      let body;
      try {
        body = text ? JSON.parse(text) : { status: "error", message: "空响应" };
      } catch (_) {
        body = { status: "error", message: "非 JSON 响应" };
      }
      if (!res.ok || body.status !== "success") {
        const err = new Error(body.message || `请求失败 ${res.status}`);
        err.status = res.status;
        err.body = body;
        throw err;
      }
      return body.data;
    } catch (e) {
      if (e && e.name === "AbortError") {
        throw new Error("请求超时");
      }
      throw e;
    } finally {
      clearTimeout(timer);
    }
  }

  function bootstrap() {
    return post("/api/session/bootstrap", {});
  }

  function queryContext(payload) {
    const budget = (payload && payload.options && payload.options.latency_budget_ms) || 1500;
    return post("/api/context/query", payload, Math.max(budget + 1500, 4000));
  }

  function memoryEvent(payload) {
    return post("/api/memory/event", payload);
  }

  return { bootstrap, queryContext, memoryEvent };
})();
