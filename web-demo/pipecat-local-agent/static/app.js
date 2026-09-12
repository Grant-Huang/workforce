const statusEl = document.getElementById("status");
const metricsEl = document.getElementById("metrics");
const chatEl = document.getElementById("chat");
const emptyEl = document.getElementById("empty");
const connectBtn = document.getElementById("connectBtn");
const disconnectBtn = document.getElementById("disconnectBtn");

const BOT_WAIT_MS = 45000;
const BOT_POLL_MS = 1000;

let room = null;
let config = null;
let assistantBubble = null;
let botWaitTimer = null;
let botPollTimer = null;

function botIdentity() {
  return config?.agentIdentity || "Pipecat Local Agent";
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
  setMetrics(`Agent 已加入 (${participant.identity})，等待欢迎语…`);
}

function startWaitingForBot() {
  clearBotTimers();
  const identity = botIdentity();
  setMetrics(`等待 Mac 上的 Agent (${identity}) 加入房间…`);

  if (findBotParticipant()) {
    onBotDetected(findBotParticipant());
    return;
  }

  botPollTimer = setInterval(() => {
    const bot = findBotParticipant();
    if (bot) onBotDetected(bot);
  }, BOT_POLL_MS);

  botWaitTimer = setTimeout(() => {
    if (findBotParticipant()) return;
    clearBotTimers();
    const roomName = config?.defaultRoom || "voicechat-local";
    setMetrics(
      `超时：Agent 未加入。请确认 Mac 上已运行 ` +
        `python pipecat_agent.py --room ${roomName}，且 LIVEKIT_URL/凭证与 .env 一致。`
    );
  }, BOT_WAIT_MS);
}

async function loadConfig() {
  const resp = await fetch("/api/config");
  const body = await resp.json();
  config = body.data;
  if (!config.hasLiveKitCredentials) {
    setMetrics("警告：LIVEKIT_API_KEY / LIVEKIT_API_SECRET 未配置，无法获取 token");
  }
  const backends = [config.sttBackend, config.llmBackend, config.ttsBackend].join(" / ");
  setMetrics((metricsEl.textContent ? metricsEl.textContent + " · " : "") + `后端: ${backends}`);
}

async function connect() {
  connectBtn.disabled = true;
  setStatus("正在获取 LiveKit token…");

  try {
    if (!config) await loadConfig();

    const roomName = config.defaultRoom;
    const tokenResp = await fetch(`/api/livekit/token?room=${encodeURIComponent(roomName)}`);
    const tokenBody = await tokenResp.json();
    if (tokenBody.status !== "success") {
      throw new Error(tokenBody.message || "token 获取失败");
    }

    const { token, url, room: joinedRoom } = tokenBody.data;
    setStatus(`正在连接 LiveKit Cloud 房间 ${joinedRoom}…`);

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
          setMetrics("Agent 音频已连接，可以开始说话");
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
disconnectBtn.addEventListener("click", () => disconnectAll());

loadConfig().catch((err) => {
  setStatus(`配置加载失败: ${err.message}`);
});

if (window.VoiceModeSwitcher) {
  VoiceModeSwitcher.mountSwitcher(document.getElementById("modeSwitcher"), {
    currentMode: "localAgent",
    onBeforeLeave: async () => {
      await disconnectAll();
    },
  });
}
