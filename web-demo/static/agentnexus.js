// Bridges LocalMemory to AgentNexus's memory API — pointed at the local mock by
// default (see server.py's /agentnexus-mock/* routes), which stands in for what
// AgentNexus looks like *after* the changes proposed in
// docs/agentnexus-memory-integration-proposal.md: a long-lived bearer token works
// on the plain memory/messages REST API, no special auth dance needed.
//
// Short HTTPS calls only, no persistent connection — pull once when a conversation
// starts, push new turns in the background. LocalMemory stays the only thing the
// live conversation reads from.
const AgentNexusBridge = (() => {
  const config = {
    baseURL: "/agentnexus-mock",
    channelId: "demo-channel",
    // Mock accepts any non-empty bearer token — stands in for a pt_... personal
    // token once AgentNexus's generic auth accepts those (proposal §2).
    token: "pt_mock_demo_token",
  };

  function headers() {
    return {
      Authorization: `Bearer ${config.token}`,
      "Content-Type": "application/json",
    };
  }

  async function pullMemory() {
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
          layer: e.layer,
        }))
      );
      return remoteEntries.length;
    } catch (e) {
      console.warn("AgentNexus pull-sync failed (continuing with local cache only):", e);
      return 0;
    }
  }

  /** Fire-and-forget push of a raw conversation turn as a channel message. */
  function pushMessage(text, senderType = "user") {
    fetch(`${config.baseURL}/api/v1/channels/${config.channelId}/messages`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ content: text, sender_type: senderType }),
    }).catch((e) => console.warn("AgentNexus push failed (will just stay local for now):", e));
  }

  /**
   * Writes a curated memory entry (not raw dialogue) into one of the structured
   * layers — used when the user explicitly asks to remember something, per
   * docs/agentnexus-memory-integration-proposal.md's "raw dialogue vs curated
   * memory" split. Awaited (unlike pushMessage) so the caller can confirm success
   * before telling the user it's saved.
   */
  async function createMemoryEntry(layer, content) {
    const res = await fetch(`${config.baseURL}/api/v1/channels/${config.channelId}/memory/`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({ layer, content }),
    });
    if (!res.ok) throw new Error(`create memory entry failed: ${res.status}`);
    return res.json();
  }

  return { config, pullMemory, pushMessage, createMemoryEntry };
})();
