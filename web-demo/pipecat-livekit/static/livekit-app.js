const statusEl = document.getElementById("status");
const metricsEl = document.getElementById("metrics");
const chatEl = document.getElementById("chat");
const emptyEl = document.getElementById("empty");
const connectBtn = document.getElementById("connectBtn");
const disconnectBtn = document.getElementById("disconnectBtn");
const subtitleEl = document.querySelector(".subtitle");
const infoPanelEl = document.querySelector(".info-panel");

const urlParams = new URLSearchParams(location.search);
const UI_QUERY = urlParams.get("ui") === "local" ? "ui=local" : "";

const BOT_WAIT_MS = 45000;
const BOT_POLL_MS = 1000;

let room = null;
let config = null;
let assistantBubble = null;
let botWaitTimer = null;
let botPollTimer = null;

function isLocalAgentMode() {
  return config?.uiMode === "localAgent";
}

function botIdentity() {
  return config?.agentIdentity || (isLocalAgentMode() ? "Pipecat Local Agent" : "Pipecat Agent");
}

function apiQuery() {
  return UI_QUERY ? `?${UI_QUERY}` : "";
}

function setStatus(text) {
  statusEl.textContent = text;
}

function setMetrics(text) {
  metricsEl.textContent = text || "";
}

function clearBotTimers() {
  if (botWaitTimer) {
    clearTimeout(botWaitTimer);
    botWaitTimer = null;
  }
  if (botPollTimer) {
    clearInterval(botPollTimer);
    botPollTimer = null;
  }
}

function hideEmpty() {
  emptyEl.style.display = "none";
}

function findBotParticipant() {
  if (!room) return null;
  const identity = botIdentity();
  for (const participant of room.remoteParticipants.values()) {
    if (participant.identity === identity) return participant;
  }
  return null;
}

function appendBubble(role, text, { streaming = false } = {}) {
  hideEmpty();
  if (streaming && role === "assistant" && assistantBubble) {
    assistantBubble.querySelector(".text").textContent += text;
    chatEl.scrollTop = chatEl.scrollHeight;
    return;
  }

  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}`;
  bubble.innerHTML = `<span class="role">${role === "user" ? "你" : "助手"}</span><span class="text"></span>`;
  bubble.querySelector(".text").textContent = text;
  chatEl.appendChild(bubble);
  chatEl.scrollTop = chatEl.scrollHeight;

  if (role === "assistant" && streaming) {
    assistantBubble = bubble;
  } else if (role === "user") {
    assistantBubble = null;
  }
}

function handleDataMessage(payload) {
  try {
    const msg = JSON.parse(payload);
    if (msg.type !== "transcript" || !msg.text) return;
    if (msg.role === "user") {
      appendBubble("user", msg.text);
    } else {
      appendBubble("assistant", msg.text, { streaming: !msg.final });
      if (msg.final) assistantBubble = null;
    }
  } catch (_) {
    // ignore non-json payloads
  }
}

function onBotDetected(participant) {
  clearBotTimers();
  const label = isLocalAgentMode() ? "Agent" : "Bot";
  setMetrics(`${label} 已加入 (${participant.identity})，等待欢迎语…`);
}

async function pollBotStatus() {
  if (isLocalAgentMode()) return;
  try {
    const resp = await fetch("/api/bot/status");
    const body = await resp.json();
    const data = body.data || {};
    if (data.status === "error" || (data.status === "stopped" && data.error)) {
      setMetrics(`Bot 异常：${data.error || data.message || "已停止"}`);
    }
  } catch (_) {
    // ignore polling errors
  }
}

function startWaitingForBot() {
  clearBotTimers();
  const identity = botIdentity();

  if (isLocalAgentMode()) {
    setMetrics(`等待 Mac 上的 Agent (${identity}) 加入房间…`);
  } else {
    setMetrics("正在启动 Pipecat bot 并等待加入房间…");
  }

  if (findBotParticipant()) {
    onBotDetected(findBotParticipant());
    return;
  }

  botPollTimer = setInterval(() => {
    const bot = findBotParticipant();
    if (bot) {
      onBotDetected(bot);
      return;
    }
    pollBotStatus();
  }, BOT_POLL_MS);

  botWaitTimer = setTimeout(async () => {
    if (findBotParticipant()) return;
    clearBotTimers();
    let hint;
    if (isLocalAgentMode()) {
      const roomName = config?.defaultRoom || "voicechat-local";
      hint = `请确认 Mac 上已运行 python pipecat_agent.py --room ${roomName}，且 LIVEKIT_URL/凭证与 .env 一致。`;
    } else {
      hint = "请确认：1) LiveKit server 已启动；2) .env 里 QWEN_API_KEY 已配置；3) 查看 server.py 终端日志";
      try {
        const resp = await fetch("/api/bot/status");
        const body = await resp.json();
        const err = body.data?.error;
        if (err) hint = err;
      } catch (_) {}
    }
    const label = isLocalAgentMode() ? "Agent" : "Bot";
    setMetrics(`超时：${label} 未加入房间。${hint}`);
  }, BOT_WAIT_MS);
}

function applyUiMode() {
  if (!config) return;
  if (isLocalAgentMode()) {
    document.title = "Mac 本地 Agent · LiveKit";
    if (subtitleEl) {
      subtitleEl.textContent = "LiveKit Cloud WebRTC · Mac Mini 出站 Pipecat pipeline";
    }
    if (infoPanelEl) {
      infoPanelEl.innerHTML = `
        <h2>Mac 本地 Agent 测试</h2>
        <ol class="steps">
          <li>配置仓库根 <code>.env</code>（LiveKit Cloud + LOCAL_* 模型）</li>
          <li>终端 1：<code>llama-server</code> 启动本地 LLM（见 README）</li>
          <li>终端 2：<code>python pipecat_agent.py --room &lt;房间名&gt;</code></li>
          <li>本页连接同一 LiveKit 房间，等待 Agent 加入</li>
        </ol>
        <p class="note">LiveKit 必须直连 <code>wss://*.livekit.cloud</code>，不要经 Cloudflare 代理。</p>
      `;
    }
  }
}

async function loadConfig() {
  const resp = await fetch(`/api/config${apiQuery()}`);
  const body = await resp.json();
  config = body.data;
  applyUiMode();
  if (isLocalAgentMode()) {
    if (!config.hasLiveKitCredentials) {
      setMetrics("警告：LIVEKIT_API_KEY / LIVEKIT_API_SECRET 未配置");
    } else {
      const backends = [config.sttBackend, config.llmBackend, config.ttsBackend].join(" / ");
      setMetrics(`后端: ${backends}`);
    }
  } else if (!config.hasQwenKey) {
    setMetrics("警告：QWEN_API_KEY 未配置，连接时会失败");
  }
}

async function connect() {
  connectBtn.disabled = true;
  setStatus(isLocalAgentMode() ? "正在获取 LiveKit token…" : "正在启动 bot 并获取 token…");

  try {
    if (!config) await loadConfig();

    const roomName = config.defaultRoom;
    const tokenResp = await fetch(
      `/api/livekit/token?room=${encodeURIComponent(roomName)}${UI_QUERY ? "&ui=local" : ""}`
    );
    const tokenBody = await tokenResp.json();
    if (tokenBody.status !== "success") {
      throw new Error(tokenBody.message || "token 获取失败");
    }

    const { token, url, room: joinedRoom, bot } = tokenBody.data;
    if (bot?.status === "starting") {
      setMetrics(`Bot 正在启动 (pid ${bot.pid})…`);
    }

    setStatus(`正在连接 LiveKit 房间 ${joinedRoom}…`);

    room = new LivekitClient.Room({
      adaptiveStream: true,
      dynacast: true,
    });

    room.on(LivekitClient.RoomEvent.Disconnected, () => {
      clearBotTimers();
      setStatus("已断开");
      setMetrics("");
      connectBtn.disabled = false;
      disconnectBtn.disabled = true;
      room = null;
      assistantBubble = null;
    });

    room.on(LivekitClient.RoomEvent.ParticipantConnected, (participant) => {
      if (participant.identity === botIdentity()) {
        onBotDetected(participant);
      } else {
        setMetrics(`参与者加入: ${participant.identity}`);
      }
    });

    room.on(LivekitClient.RoomEvent.DataReceived, (payload) => {
      const text = new TextDecoder().decode(payload);
      handleDataMessage(text);
    });

    room.on(LivekitClient.RoomEvent.TrackSubscribed, (track, _pub, participant) => {
      if (track.kind === LivekitClient.Track.Kind.Audio) {
        const audioEl = track.attach();
        audioEl.autoplay = true;
        audioEl.dataset.lkAudio = "1";
        document.body.appendChild(audioEl);
        if (participant.identity === botIdentity()) {
          const label = isLocalAgentMode() ? "Agent" : "Bot";
          setMetrics(`${label} 音频已连接，可以开始说话`);
        }
      }
    });

    await room.connect(url, token);
    await room.localParticipant.setMicrophoneEnabled(true);
    setStatus(`已连接 · 麦克风已开启 · 房间 ${joinedRoom}`);
    disconnectBtn.disabled = false;
    startWaitingForBot();
  } catch (err) {
    console.error(err);
    clearBotTimers();
    setStatus(`连接失败: ${err.message}`);
    connectBtn.disabled = false;
    disconnectBtn.disabled = true;
    if (room) {
      room.disconnect();
      room = null;
    }
  }
}

async function disconnect() {
  await disconnectAll();
}

async function disconnectAll() {
  clearBotTimers();
  if (room) {
    try {
      await room.localParticipant.setMicrophoneEnabled(false);
    } catch (_) {
      // ignore if already disconnected
    }
    await room.disconnect();
    room = null;
  }
  document.querySelectorAll("audio[data-lk-audio]").forEach((el) => el.remove());
  assistantBubble = null;
  connectBtn.disabled = false;
  disconnectBtn.disabled = true;
}

connectBtn.addEventListener("click", connect);
disconnectBtn.addEventListener("click", disconnect);

loadConfig().catch((err) => {
  setStatus(`配置加载失败: ${err.message}`);
});

if (window.VoiceModeSwitcher) {
  VoiceModeSwitcher.mountSwitcher(document.getElementById("modeSwitcher"), {
    currentMode: urlParams.get("ui") === "local" ? "localAgent" : "livekit",
    onBeforeLeave: async () => {
      await disconnectAll();
    },
  });
}
