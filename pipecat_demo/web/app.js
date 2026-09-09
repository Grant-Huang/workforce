// Browser client for the LiveKit + Pipecat arm.
//
// Deliberately thin compared with web-demo/static/app.js, and that contrast is part of
// what this demo is showing. Over there the browser owns the whole realtime protocol:
// mic capture worklet, 16kHz downsampling, PCM framing, a scheduled 24kHz playback
// queue, session.update/response.create bookkeeping, barge-in, reconnects. Here the
// browser publishes a microphone track and plays a remote track -- WebRTC does capture,
// echo cancellation, jitter buffering and packet loss recovery, and every pipeline
// decision lives in the bot process.

const statusEl = document.getElementById("status");
const chatEl = document.getElementById("chat");
const emptyEl = document.getElementById("empty");
const timingEl = document.getElementById("timing");
const memoryEl = document.getElementById("memory");
const toggleEl = document.getElementById("toggle");
const errorEl = document.getElementById("error");
const summaryEl = document.getElementById("pipelineSummary");

let room = null;
let session = null;
let connecting = false;
const decoder = new TextDecoder();

async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    const body = await res.json();
    const c = body.data || {};
    const parts = [
      `ASR: ${c.asr_model}`,
      `LLM: ${c.llm_model}`,
      `TTS: ${c.tts_model} / ${c.tts_voice}`,
      `VAD 停顿 ${c.vad_stop_secs}s + 防抖 ${c.response_debounce_secs}s`,
      c.workspace_domain ? "专属域名" : "共享域名",
    ];
    if (c.mock) parts.push("模拟模式（未调用云端）");
    if (!c.has_key && !c.mock) parts.push("未配置 QWEN_API_KEY");
    summaryEl.textContent = parts.join(" · ");
  } catch (e) {
    summaryEl.textContent = "配置读取失败";
  }
}

function setStatus(text) {
  statusEl.textContent = text;
}

function setError(text) {
  errorEl.textContent = text || "";
}

function addBubble(role, text, interrupted) {
  emptyEl.style.display = "none";
  const el = document.createElement("div");
  el.className = `bubble ${role}${interrupted ? " interrupted" : ""}`;
  el.textContent = text;
  chatEl.appendChild(el);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function renderTiming(payload) {
  for (const el of timingEl.querySelectorAll("b")) {
    const value = payload[el.dataset.k];
    el.textContent = value === null || value === undefined ? "—" : `${value}ms`;
  }
}

function renderMemory(payload) {
  if (!payload.hits || payload.hits.length === 0) {
    memoryEl.textContent = `「${payload.query}」没有命中任何记忆`;
    return;
  }
  memoryEl.textContent = payload.hits.map((hit, i) => `${i + 1}. ${hit}`).join("\n");
}

function handleBotEvent(payload) {
  switch (payload.type) {
    case "ready":
      setStatus(`已连接（记忆 ${payload.memory_entries} 条）`);
      break;
    case "user":
      addBubble("user", payload.text);
      break;
    case "assistant":
      addBubble("assistant", payload.text, payload.interrupted);
      break;
    case "timing":
      renderTiming(payload);
      break;
    case "memory":
      renderMemory(payload);
      break;
    default:
      break;
  }
}

async function start() {
  if (connecting || room) return;
  connecting = true;
  setError("");
  setStatus("连接中…");
  toggleEl.disabled = true;

  try {
    await window.loadLiveKitSdk();

    const res = await fetch("/api/session", { method: "POST" });
    const body = await res.json();
    if (body.status !== "success") throw new Error(body.message || "创建会话失败");
    session = body.data;

    const { Room, RoomEvent, Track } = window.LivekitClient;
    room = new Room({
      adaptiveStream: true,
      dynacast: true,
      // Explicit, for the same reason web-demo learned to be explicit: the assistant's
      // own voice getting picked up as user speech is a real bug, not a theoretical
      // one, and browsers only guarantee AEC when it is asked for. Unlike the other
      // arm, playback here goes through a WebRTC audio element, which is the path
      // Chromium's echo canceller actually treats as a reference signal.
      audioCaptureDefaults: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    room.on(RoomEvent.TrackSubscribed, (track) => {
      if (track.kind === Track.Kind.Audio) {
        const el = track.attach();
        el.autoplay = true;
        document.body.appendChild(el);
      }
    });

    room.on(RoomEvent.DataReceived, (payload) => {
      try {
        handleBotEvent(JSON.parse(decoder.decode(payload)));
      } catch (e) {
        console.warn("无法解析 bot 数据消息", e);
      }
    });

    room.on(RoomEvent.Disconnected, () => {
      stop({ notifyServer: false });
    });

    await room.connect(session.url, session.token);
    // Browsers block autoplay until a gesture; the click that got us here is it.
    await room.startAudio();
    await room.localParticipant.setMicrophoneEnabled(true);

    setStatus("已连接，等待 bot 加入…");
    toggleEl.textContent = "结束对话";
    toggleEl.classList.add("active");
  } catch (e) {
    console.error(e);
    setError(`连接失败：${e.message || e}`);
    setStatus("未连接");
    await stop({ notifyServer: true });
  } finally {
    connecting = false;
    toggleEl.disabled = false;
  }
}

async function stop({ notifyServer = true } = {}) {
  const endedRoom = session && session.room;
  if (room) {
    try {
      await room.disconnect();
    } catch (e) {
      console.warn("断开连接时出错", e);
    }
    room = null;
  }
  if (notifyServer && endedRoom) {
    try {
      await fetch("/api/session/end", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ room: endedRoom }),
      });
    } catch (e) {
      console.warn("结束会话通知失败", e);
    }
  }
  session = null;
  setStatus("未连接");
  toggleEl.textContent = "开始对话";
  toggleEl.classList.remove("active");
}

toggleEl.addEventListener("click", () => {
  if (room) {
    stop();
  } else {
    start();
  }
});

window.addEventListener("beforeunload", () => {
  if (session) {
    // Best effort: keepalive lets this survive the page teardown so the bot process
    // for this room gets cleaned up even if LiveKit's disconnect doesn't land.
    fetch("/api/session/end", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ room: session.room }),
      keepalive: true,
    });
  }
});

loadConfig();
