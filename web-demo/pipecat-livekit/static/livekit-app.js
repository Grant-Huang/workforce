const statusEl = document.getElementById("status");
const metricsEl = document.getElementById("metrics");
const chatEl = document.getElementById("chat");
const emptyEl = document.getElementById("empty");
const connectBtn = document.getElementById("connectBtn");
const disconnectBtn = document.getElementById("disconnectBtn");

let room = null;
let config = null;
let connectedAt = null;
let assistantBubble = null;

function setStatus(text) {
  statusEl.textContent = text;
}

function setMetrics(text) {
  metricsEl.textContent = text || "";
}

function hideEmpty() {
  emptyEl.style.display = "none";
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

async function loadConfig() {
  const resp = await fetch("/api/config");
  const body = await resp.json();
  config = body.data;
}

async function connect() {
  connectBtn.disabled = true;
  setStatus("正在获取 token…");

  try {
    if (!config) await loadConfig();

    const roomName = config.defaultRoom;
    const tokenResp = await fetch(`/api/livekit/token?room=${encodeURIComponent(roomName)}`);
    const tokenBody = await tokenResp.json();
    if (tokenBody.status !== "success") {
      throw new Error(tokenBody.message || "token 获取失败");
    }

    const { token, url, room: joinedRoom } = tokenBody.data;
    setStatus(`正在连接 LiveKit 房间 ${joinedRoom}…`);

    room = new LivekitClient.Room({
      adaptiveStream: true,
      dynacast: true,
    });

    room.on(LivekitClient.RoomEvent.Connected, () => {
      connectedAt = Date.now();
      setStatus(`已连接 · 房间 ${joinedRoom}`);
      setMetrics("等待 Pipecat bot 加入并开始说话…");
      disconnectBtn.disabled = false;
    });

    room.on(LivekitClient.RoomEvent.Disconnected, () => {
      setStatus("已断开");
      setMetrics("");
      connectBtn.disabled = false;
      disconnectBtn.disabled = true;
      room = null;
      assistantBubble = null;
    });

    room.on(LivekitClient.RoomEvent.ParticipantConnected, (participant) => {
      setMetrics(`Bot 已加入: ${participant.identity}`);
    });

    room.on(LivekitClient.RoomEvent.DataReceived, (payload) => {
      const text = new TextDecoder().decode(payload);
      handleDataMessage(text);
    });

    room.on(LivekitClient.RoomEvent.TrackSubscribed, (track, _pub, participant) => {
      if (track.kind === LivekitClient.Track.Kind.Audio) {
        const audioEl = track.attach();
        audioEl.autoplay = true;
        document.body.appendChild(audioEl);
        setMetrics(`正在播放 ${participant.identity} 的音频`);
      }
    });

    await room.connect(url, token);
    await room.localParticipant.setMicrophoneEnabled(true);
    setStatus(`已连接 · 麦克风已开启`);
  } catch (err) {
    console.error(err);
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
  if (room) {
    await room.disconnect();
  }
}

connectBtn.addEventListener("click", connect);
disconnectBtn.addEventListener("click", disconnect);

loadConfig().catch((err) => {
  setStatus(`配置加载失败: ${err.message}`);
});
