// Bridges LocalMemory to AgentNexus's memory API — personal memory / schedule / todos.
// Default: local mock at /agentnexus-mock/* (see web-demo/agentnexus/).
// Switch via /api/config → agentnexus.baseURL when AGENTNEXUS_MODE=real.
//
// Ops facts (lines/orders/machines) are NOT here — see NexusOps + docs/capability-map.md.
const AgentNexusBridge = (() => {
  const config = {
    baseURL: "/agentnexus-mock",
    channelId: "demo-channel",
    token: "pt_mock_demo_token",
  };

  let configLoaded = false;

  async function ensureConfig() {
    if (configLoaded) return;
    try {
      const res = await fetch("/api/config");
      if (res.ok) {
        const data = await res.json();
        const an = data.agentnexus || {};
        if (an.baseURL) config.baseURL = an.baseURL;
        if (an.channelId) config.channelId = an.channelId;
        if (an.token) config.token = an.token;
      }
    } catch (e) {
      console.warn("AgentNexus config load failed, using defaults:", e);
    }
    configLoaded = true;
  }

  function headers() {
    return {
      Authorization: `Bearer ${config.token}`,
      "Content-Type": "application/json",
    };
  }

  async function pullMemory() {
    await ensureConfig();
    try {
      const res = await fetch(`${config.baseURL}/api/v1/channels/${config.channelId}/memory/`, {
        headers: headers(),
      });
      if (!res.ok) throw new Error(`pull failed: ${res.status}`);
      const remoteEntries = await res.json();
      LocalMemory.merge(
        remoteEntries.map((e) => ({
          id: `agentnexus:${e.entry_id}`,
          text: e.title ? `${e.title}：${e.content}` : e.content,
          timestamp: e.updated_at ? Date.parse(e.updated_at) : Date.now(),
          source: "agentnexus",
          sourceId: e.entry_id,
          layer: e.layer,
        }))
      );
      return remoteEntries.length;
    } catch (e) {
      console.warn("AgentNexus pull-sync failed (continuing with local cache only):", e);
      return 0;
    }
  }

  async function pushMessage(text, senderType = "user") {
    await ensureConfig();
    const res = await fetch(`${config.baseURL}/api/v1/channels/${config.channelId}/messages`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ content: text, sender_type: senderType }),
    });
    if (!res.ok) throw new Error(`push failed: ${res.status}`);
    return res.json();
  }

  async function createMemoryEntry(layer, content) {
    await ensureConfig();
    const res = await fetch(`${config.baseURL}/api/v1/channels/${config.channelId}/memory/`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ layer, content }),
    });
    if (!res.ok) throw new Error(`create memory entry failed: ${res.status}`);
    return res.json();
  }

  return { config, ensureConfig, pullMemory, pushMessage, createMemoryEntry };
})();
