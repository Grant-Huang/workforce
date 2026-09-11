const statusEl = document.getElementById("status");
const metricsEl = document.getElementById("metrics");
const chatEl = document.getElementById("chat");
const emptyEl = document.getElementById("empty");
const connectBtn = document.getElementById("connectBtn");
const disconnectBtn = document.getElementById("disconnectBtn");

const BOT_IDENTITY = "Pipecat Agent";
const BOT_WAIT_MS = 30000;
const BOT_POLL_MS = 1000;

let room = null;
let config = null;
let assistantBubble = null;
let botWaitTimer = null;
let botPollTimer = null;

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
  for (const participant of room.remoteParticipants.values()) {
    if (participant.identity === BOT_IDENTITY) return participant;
  }
  return null;
}

function appendBubble(role, text, { streaming = false } = {}) {
  hideEmpty();
  if (streaming && role === "assistant" && assistantBubble) {
    assistantBubble.querySelector(".text").textContent += text;
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
  setMetrics(`Bot 已加入 (${participant.identity})，等待欢迎语…`);
}

async function pollBotStatus() {
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
  setMetrics("正在启动 Pipecat bot 并等待加入房间…");

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
    let hint = "请确认：1) LiveKit server 已启动；2) .env 里 QWEN_API_KEY 已配置；3) 查看 server.py 终端日志";
    try {
      const resp = await fetch("/api/bot/status");
      const body = await resp.json();
      const err = body.data?.error;
      if (err) hint = err;
    } catch (_) {}
    setMetrics(`超时：Bot 未加入房间。${hint}`);
  }, BOT_WAIT_MS);
}

async function loadConfig() {
  const resp = await fetch("/api/config");
  const body = await resp.json();
  config = body.data;
  if (!config.hasQwenKey) {
    setMetrics("警告：QWEN_API_KEY 未配置，连接时会失败");
  }
}

async function connect() {
  connectBtn.disabled = true;
  setStatus("正在启动 bot 并获取 token…");

  try {
    if (!config) await loadConfig();

    const roomName = config.defaultRoom;
    const tokenResp = await fetch(`/api/livekit/token?room=${encodeURIComponent(roomName)}`);
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
      if (participant.identity === BOT_IDENTITY) {
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
        if (participant.identity === BOT_IDENTITY) {
          setMetrics("Bot 音频已连接，可以开始说话");
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
    currentMode: "livekit",
    onBeforeLeave: async () => {
      await disconnectAll();
    },
  });
}
