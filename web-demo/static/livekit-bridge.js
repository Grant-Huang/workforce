/** LiveKit / Mac local agent bridge for the unified app.js.
 *
 * Activated when the URL is `/?mode=livekit`. Renders inside the existing
 * index.html DOM:
 *   - hides #voiceSelect + #tuningPanel (those are Qwen-specific)
 *   - replaces the mic button text/icon to "连接" / "断开"
 *   - uses #chat as transcript bubble area (reused from Qwen layout)
 *   - shows #voiceOrbContainer while waiting for the bot to join the room
 *   - listens for `transcript` data-channel messages from pipecat_agent.py
 *     via transcript_bridge.py, renders them with the same addBubble()
 *     shape that Qwen mode uses
 *
 * Same data-channel JSON schema as livekit-app.js (now removed):
 *   {type: "transcript", role: "user", text: "..."}
 *   {type: "transcript", role: "assistant", text: "...", final: false}
 *
 * `LiveKitRoom` is loaded from https://cdn.jsdelivr.net/npm/livekit-client@2
 * — same CDN livekit.html used. It's exposed as window.LivekitClient / window.LiveKitClient.
 */
(function () {
  const params = new URLSearchParams(location.search);
  if (params.get("mode") !== "livekit") return;

  const lk = window.LivekitClient || window.LiveKitClient;
  if (!lk) {
    console.error("LiveKit client CDN failed to load");
    document.getElementById("status").textContent = "LiveKit 客户端加载失败";
    return;
  }
  const { Room, RoomEvent, ConnectionState } = lk;

  const statusEl = document.getElementById("status");
  const chatEl = document.getElementById("chat");
  const emptyEl = document.getElementById("empty");
  const micBtn = document.getElementById("micBtn");
  const voiceSelect = document.getElementById("voiceSelect");
  const tuningToggle = document.getElementById("tuningToggle");
  const voiceOrbContainer = document.getElementById("voiceOrbContainer");

  // Hide Qwen-only UI — LiveKit mode has no voice picker / VAD tuning
  if (voiceSelect) voiceSelect.style.display = "none";
  if (tuningToggle) tuningToggle.style.display = "none";

  let room = null;
  let livekitConfig = null;
  let connecting = false;
  let assistantBubble = null;

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text;
  }

  function appendBubble(role, text, { streaming = false } = {}) {
    if (emptyEl) emptyEl.style.display = "none";

    if (streaming && role === "assistant" && assistantBubble) {
      const span = assistantBubble.querySelector(".bubble-text");
      if (span) span.textContent += text;
      chatEl.scrollTop = chatEl.scrollHeight;
      return;
    }

    // Replace-in-place: end-of-response cleanup overwrites the streaming
    // bubble with the sanitized final text. Without this, the Qwen 2.5
    // occasional leaked ``<|im_end|>`` / ``system\n`` / stray `` would
    // stay visible in the transcript even though the agent moved on.
    if (role === "assistant" && assistantBubble) {
      const span = assistantBubble.querySelector(".bubble-text");
      if (span) span.textContent = text;
      chatEl.scrollTop = chatEl.scrollHeight;
      return;
    }

    const bubble = document.createElement("div");
    bubble.className = `bubble ${role}`;
    const roleEl = document.createElement("span");
    roleEl.className = "role";
    roleEl.textContent = role === "user" ? "你" : "助手";
    const textEl = document.createElement("span");
    textEl.className = "bubble-text";
    textEl.textContent = text;
    bubble.appendChild(roleEl);
    bubble.appendChild(textEl);
    chatEl.appendChild(bubble);
    chatEl.scrollTop = chatEl.scrollHeight;

    if (role === "assistant" && streaming) {
      assistantBubble = bubble;
    } else if (role === "user") {
      assistantBubble = null;
    }
  }

  function setMicIcon(state) {
    // state ∈ {idle, connecting, listening, speaking, connected}
    // Mirrors Qwen app.js's setState() visual contract:
    //   idle       → grey mic icon, no orb
    //   connecting → grey mic icon + orb animating (waiting for room)
    //   listening  → red mic icon + orb animating (user's turn)
    //   speaking   → blue mic icon + orb animating (assistant turn)
    //   connected  → alias for listening, kept for backwards compat
    if (!micBtn) return;
    micBtn.classList.toggle("active", state === "listening" || state === "speaking" || state === "connected");
    micBtn.classList.toggle("speaking", state === "speaking");
    if (state === "idle" || state === "connecting") {
      micBtn.textContent = state === "connecting" ? "连接中…" : "连接并开始";
      micBtn.disabled = state === "connecting";
      micBtn.dataset.livekitState = state;
    } else {
      // listening/speaking/connected — show "断开" as the action label
      micBtn.textContent = "断开";
      micBtn.disabled = false;
      micBtn.dataset.livekitState = "connected";
    }
    // Orb visibility: visible whenever the room session is active (i.e. not idle)
    if (voiceOrbContainer) {
      voiceOrbContainer.style.display = state === "idle" ? "none" : "";
    }
  }
  // Backwards-compat alias used in the existing flow above
  function updateMicBtn(state) {
    setMicIcon(state);
  }

  async function loadLiveKitConfig() {
    const resp = await fetch("/api/config?mode=livekit");
    const body = await resp.json();
    if (body.status !== "success") throw new Error(body.message || "/api/config failed");
    livekitConfig = body.data;
    if (!livekitConfig.hasLiveKitCredentials) {
      throw new Error("LIVEKIT_API_KEY/LIVEKIT_API_SECRET 未配置（看 unified_server 启动 banner）");
    }
  }

  async function fetchToken() {
    const resp = await fetch(`/api/livekit/token?room=${encodeURIComponent(livekitConfig.defaultRoom)}&participant=${encodeURIComponent("user-" + Math.random().toString(36).slice(2, 10))}`);
    const body = await resp.json();
    if (body.status !== "success") throw new Error(body.message || "/api/livekit/token failed");
    return body.data; // {token, url, room, ...}
  }

  function findBotParticipant() {
    if (!room) return null;
    const wanted = livekitConfig.agentIdentity;
    for (const participant of room.remoteParticipants.values()) {
      if (participant.identity === wanted) return participant;
    }
    return null;
  }

  function onDataReceived(payload, participant) {
    try {
      const msg = JSON.parse(typeof payload === "string" ? payload : new TextDecoder().decode(payload));
      if (msg.type !== "transcript" || !msg.text) return;
      if (msg.role === "user") {
        // User spoke → switch orb to "speaking" (the user's bubble is shown) briefly,
        // then back to "listening" once the assistant starts responding.
        setMicIcon("speaking");
        appendBubble("user", msg.text);
        setMicIcon("listening");
      } else {
        // Assistant streaming — flip to "speaking" while text is mid-stream, back to
        // "listening" once final=true (or on completion).
        setMicIcon("speaking");
        appendBubble("assistant", msg.text, { streaming: !msg.final });
        if (msg.final) {
          assistantBubble = null;
          setMicIcon("listening");
        }
      }
    } catch (e) {
      console.warn("LiveKit data parse error:", e);
    }
  }

  async function connect() {
    if (connecting || (room && room.state === ConnectionState.Connected)) return;
    connecting = true;
    setMicIcon("connecting");
    setStatus("正在获取 LiveKit token + 拉起本地 agent…");

    try {
      if (!livekitConfig) await loadLiveKitConfig();
      const tokenData = await fetchToken();
      const { token, url } = tokenData;

      // Hide the empty placeholder; reveal the chat area
      if (emptyEl) emptyEl.style.display = "none";
      // Voice orb stays visible (already set to "connecting" at the start of connect())

      setStatus("正在连接 LiveKit 房间…");
      room = new Room({
        adaptiveStream: true,
        dynacast: true,
      });
      room.on(RoomEvent.DataReceived, onDataReceived);
      room.on(RoomEvent.ParticipantConnected, (p) => {
        if (p.identity === livekitConfig.agentIdentity) {
          setStatus("本地 agent 已加入房间");
        }
      });
      room.on(RoomEvent.ParticipantDisconnected, (p) => {
        if (p.identity === livekitConfig.agentIdentity) {
          setStatus("本地 agent 已离开房间");
        }
      });
      room.on(RoomEvent.Disconnected, () => {
        setStatus("未连接");
        setMicIcon("idle");
      });
      room.on(RoomEvent.ConnectionStateChanged, (state) => {
        if (state === ConnectionState.Connecting) setStatus("连接中…");
        if (state === ConnectionState.Connected) {
          setStatus("已连接，等待本地 agent…");
          const bot = findBotParticipant();
          if (bot) setStatus("本地 agent 已就绪，可以说话");
        }
        if (state === ConnectionState.Disconnected) {
          setStatus("已断开");
          setMicIcon("idle");
        }
      });

      await room.connect(url, token);
      // livekit-client@2.x removed enableMicrophone() — setMicrophoneEnabled() takes its place.
      // Don't block on mic enable: in headless / no-mic environments it hangs forever waiting
      // for getUserMedia to resolve/reject. Fire-and-forget; failure logs but doesn't fail connect.
      try {
        const enable = room.localParticipant.setMicrophoneEnabled
          || room.localParticipant.enableMicrophone;
        if (enable) {
          Promise.resolve(enable.call(room.localParticipant, true))
            .then(() => setStatus("已连接，本地 agent 已就绪，可以说话"))
            .catch((e) => console.warn("setMicrophoneEnabled failed (no mic?):", e?.message || e));
        }
      } catch (e) {
        console.warn("mic enable threw:", e?.message || e);
      }
      setStatus("已连接，正在等待本地 agent…");
      setMicIcon("listening");
    } catch (e) {
      console.error("LiveKit connect failed:", e);
      setStatus(`连接失败: ${e.message}`);
      setMicIcon("idle");
    } finally {
      connecting = false;
    }
  }

  async function disconnect() {
    if (!room) return;
    try {
      await room.disconnect();
    } finally {
      room = null;
      setMicIcon("idle");
      setStatus("未连接");
    }
  }

  // Bind mic button (which was originally for Qwen mic toggle) to LiveKit connect/disconnect
  if (micBtn) {
    micBtn.addEventListener("click", async () => {
      if (micBtn.dataset.livekitState === "connected") {
        await disconnect();
      } else {
        await connect();
      }
    });
  }

  // Initial state — orb visible while waiting for first connect
  if (voiceOrbContainer) voiceOrbContainer.style.display = "";
  setMicIcon("idle");

  // Auto-disconnect on tab close
  window.addEventListener("beforeunload", () => {
    if (room) room.disconnect();
  });

  // Tell Qwen's stopAllSessions() to also disconnect LiveKit when the user
  // navigates to the other pill (Qwen Realtime).
  window.addEventListener("pagehide", () => {
    if (room) room.disconnect();
  });

  // Expose for onBeforeLeave hook in mode-switcher
  window.__livekitBridge = { connect, disconnect, getRoom: () => room };
})();