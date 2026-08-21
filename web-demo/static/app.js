// Voice chat demo — browser side. Talks to the local relay at /ws (see server.py),
// which forwards to Qwen's Realtime API with the API key attached server-side.
//
// Protocol is the same Realtime-API event shape used by VoiceChat/Realtime/*.swift
// in the iOS app (session.update / input_audio_buffer.append / response.audio.delta / …).

const chatEl = document.getElementById("chat");
const emptyEl = document.getElementById("empty");
const statusEl = document.getElementById("status");
const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");

let ws = null;
let micStream = null;
let captureCtx = null;
let playCtx = null;
let processorNode = null;
let nextPlayTime = 0;
let activeSources = [];
let assistantBubbleEl = null;
let assistantHasDelta = false;
let voice = "Chelsie";

async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    const config = await res.json();
    voice = config.voice || voice;
    if (!config.hasKey) {
      setStatus("⚠️ 服务器没有读到 QWEN_API_KEY，请检查 .env");
    }
  } catch (e) {
    setStatus("⚠️ 无法连接本地服务");
  }
}

function setStatus(text) {
  statusEl.textContent = text;
}

function addBubble(role, text) {
  emptyEl.style.display = "none";
  const row = document.createElement("div");
  row.className = `bubble-row ${role}`;
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role}`;
  bubble.textContent = text;
  row.appendChild(bubble);
  chatEl.appendChild(row);
  chatEl.scrollTop = chatEl.scrollHeight;
  return bubble;
}

function appendToAssistantBubble(delta) {
  if (!assistantBubbleEl) {
    assistantBubbleEl = addBubble("assistant", "");
  }
  assistantHasDelta = true;
  assistantBubbleEl.textContent += delta;
  chatEl.scrollTop = chatEl.scrollHeight;
}

function setAssistantFinalText(text) {
  // Fallback for providers that only send response.audio_transcript.done, no deltas.
  if (assistantHasDelta) return;
  if (!assistantBubbleEl) {
    assistantBubbleEl = addBubble("assistant", "");
  }
  assistantBubbleEl.textContent = text;
  chatEl.scrollTop = chatEl.scrollHeight;
}

// ---- mic capture: downsample to 16kHz mono PCM16, base64, send as input_audio_buffer.append ----

function downsampleTo16k(float32Array, inputSampleRate) {
  if (inputSampleRate === 16000) return float32Array;
  const ratio = inputSampleRate / 16000;
  const outLength = Math.floor(float32Array.length / ratio);
  const result = new Float32Array(outLength);
  for (let i = 0; i < outLength; i++) {
    const srcIndex = i * ratio;
    const i0 = Math.floor(srcIndex);
    const i1 = Math.min(i0 + 1, float32Array.length - 1);
    const frac = srcIndex - i0;
    result[i] = float32Array[i0] * (1 - frac) + float32Array[i1] * frac;
  }
  return result;
}

function floatTo16BitPCM(float32Array) {
  const out = new Int16Array(float32Array.length);
  for (let i = 0; i < float32Array.length; i++) {
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function int16ToBase64(int16Array) {
  const bytes = new Uint8Array(int16Array.buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

function base64ToInt16(base64) {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Int16Array(bytes.buffer);
}

// ---- playback: schedule streamed 24kHz PCM16 chunks back-to-back for gapless audio ----

function playPCM16Chunk(base64) {
  const int16 = base64ToInt16(base64);
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / (int16[i] < 0 ? 0x8000 : 0x7fff);

  const buffer = playCtx.createBuffer(1, float32.length, 24000);
  buffer.copyToChannel(float32, 0);

  const source = playCtx.createBufferSource();
  source.buffer = buffer;
  source.connect(playCtx.destination);

  const startAt = Math.max(nextPlayTime, playCtx.currentTime);
  source.start(startAt);
  nextPlayTime = startAt + buffer.duration;

  activeSources.push(source);
  source.onended = () => {
    activeSources = activeSources.filter((s) => s !== source);
  };
}

function stopPlayback() {
  activeSources.forEach((s) => {
    try { s.stop(); } catch (e) { /* already stopped */ }
  });
  activeSources = [];
  nextPlayTime = playCtx ? playCtx.currentTime : 0;
}

// ---- realtime session ----

function sendEvent(payload) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(payload));
  }
}

function handleServerEvent(json) {
  switch (json.type) {
    case "session.created":
      setStatus("已连接，可以说话了");
      break;
    case "response.audio.delta":
      playPCM16Chunk(json.delta);
      setStatus("助手正在说话…");
      break;
    case "response.audio_transcript.delta":
      appendToAssistantBubble(json.delta);
      break;
    case "response.audio_transcript.done":
      setAssistantFinalText(json.transcript || "");
      break;
    case "conversation.item.input_audio_transcription.completed":
      if (json.transcript) addBubble("user", json.transcript);
      break;
    case "input_audio_buffer.speech_started":
      setStatus("正在听你说话…");
      stopPlayback(); // barge-in: Qwen reports interrupt_response support server-side too
      break;
    case "response.done":
      setStatus("轮到你说话了");
      assistantBubbleEl = null;
      assistantHasDelta = false;
      break;
    case "error":
      setStatus(`出错：${json.error?.message || "unknown"}`);
      break;
    case "relay.error":
      setStatus(`连接失败：${json.message}`);
      break;
    default:
      break; // ignore anything we don't handle
  }
}

async function start() {
  startBtn.disabled = true;
  setStatus("连接中…");

  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    setStatus(`麦克风权限失败：${e.message}`);
    startBtn.disabled = false;
    return;
  }

  captureCtx = new (window.AudioContext || window.webkitAudioContext)();
  playCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
  nextPlayTime = 0;

  const source = captureCtx.createMediaStreamSource(micStream);
  processorNode = captureCtx.createScriptProcessor(4096, 1, 1);
  const silentGain = captureCtx.createGain();
  silentGain.gain.value = 0; // keep the graph "live" without echoing mic audio to speakers

  processorNode.onaudioprocess = (event) => {
    const input = event.inputBuffer.getChannelData(0);
    const downsampled = downsampleTo16k(input, captureCtx.sampleRate);
    const pcm16 = floatTo16BitPCM(downsampled);
    sendEvent({ type: "input_audio_buffer.append", audio: int16ToBase64(pcm16) });
  };

  source.connect(processorNode);
  processorNode.connect(silentGain);
  silentGain.connect(captureCtx.destination);

  ws = new WebSocket(`ws://${location.host}/ws`);

  ws.onopen = () => {
    sendEvent({
      type: "session.update",
      session: {
        modalities: ["audio", "text"],
        instructions: "你是一个友好、简洁的语音助手，用自然口语中文回答问题。",
        voice,
        input_audio_format: "pcm16",
        output_audio_format: "pcm16",
        turn_detection: {
          type: "server_vad",
          threshold: 0.5,
          prefix_padding_ms: 300,
          silence_duration_ms: 500,
          create_response: true,
        },
      },
    });
    stopBtn.disabled = false;
    startBtn.classList.add("listening");
  };

  ws.onmessage = (event) => {
    try {
      handleServerEvent(JSON.parse(event.data));
    } catch (e) {
      console.error("bad server message", event.data);
    }
  };

  ws.onerror = () => setStatus("WebSocket 出错");
  ws.onclose = () => {
    if (!stopBtn.disabled) stop();
  };
}

function stop() {
  if (ws) {
    ws.close();
    ws = null;
  }
  if (processorNode) {
    processorNode.disconnect();
    processorNode = null;
  }
  if (captureCtx) {
    captureCtx.close();
    captureCtx = null;
  }
  stopPlayback();
  if (playCtx) {
    playCtx.close();
    playCtx = null;
  }
  if (micStream) {
    micStream.getTracks().forEach((t) => t.stop());
    micStream = null;
  }

  assistantBubbleEl = null;
  assistantHasDelta = false;
  startBtn.disabled = false;
  stopBtn.disabled = true;
  startBtn.classList.remove("listening");
  setStatus("未连接");
}

startBtn.addEventListener("click", start);
stopBtn.addEventListener("click", stop);
loadConfig();
