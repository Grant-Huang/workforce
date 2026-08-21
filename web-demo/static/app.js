// Voice + text chat demo — browser side. Talks to the local relay at /ws (see server.py),
// which forwards to Qwen's Realtime API with the API key attached server-side, and to
// the mock AgentNexus endpoints at /agentnexus-mock/* (see agentnexus.js).
//
// Protocol is the same Realtime-API event shape used by VoiceChat/Realtime/*.swift
// in the iOS app (session.update / input_audio_buffer.append / response.audio.delta / …).
// Memory grounding mirrors VoiceChat/ConversationViewModel.swift's groundAndRespond:
// turn_detection.create_response is false, so nothing auto-replies — every user turn
// (typed or transcribed) goes through handleUserTurn, which searches local memory,
// injects what's relevant as background context, then explicitly requests a reply.

const chatEl = document.getElementById("chat");
const emptyEl = document.getElementById("empty");
const statusEl = document.getElementById("status");
const micBtn = document.getElementById("micBtn");
const micIcon = document.getElementById("micIcon");
const stopIcon = document.getElementById("stopIcon");
const textForm = document.getElementById("textForm");
const textInput = document.getElementById("textInput");

const STATE = { IDLE: "idle", CONNECTING: "connecting", LISTENING: "listening", SPEAKING: "speaking" };
let state = STATE.IDLE;

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
let startPromise = null; // in-flight start(), so typed messages can await a session already starting

function setState(next) {
  state = next;
  const label = {
    [STATE.IDLE]: "未连接",
    [STATE.CONNECTING]: "连接中…",
    [STATE.LISTENING]: "正在聆听…",
    [STATE.SPEAKING]: "助手正在说话…",
  }[next];
  statusEl.textContent = label;

  micBtn.classList.toggle("active", next !== STATE.IDLE);
  micBtn.classList.toggle("speaking", next === STATE.SPEAKING);
  micIcon.style.display = next === STATE.IDLE ? "block" : "none";
  stopIcon.style.display = next === STATE.IDLE ? "none" : "block";
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
  if (!assistantBubbleEl) assistantBubbleEl = addBubble("assistant", "");
  assistantHasDelta = true;
  assistantBubbleEl.textContent += delta;
  chatEl.scrollTop = chatEl.scrollHeight;
}

function setAssistantFinalText(text) {
  if (assistantHasDelta) return; // already built up via deltas
  if (!assistantBubbleEl) assistantBubbleEl = addBubble("assistant", "");
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

const BASE_INSTRUCTIONS = "你是一个友好、简洁的语音助手，用自然口语中文回答问题。";

// Resolver for whichever session.update we're currently waiting to be acked (see
// updateInstructionsAndWait). Tested directly against Qwen: a `conversation.item.create`
// with role "system" (or a fake "assistant" turn) is silently ignored by the model —
// only content actually in `session.update.session.instructions` gets used. Firing a
// second session.update before the first is acked also produced an empty reply in
// testing, so this waits for `session.updated` before doing anything else.
let pendingInstructionsAck = null;

function updateInstructionsAndWait(instructions) {
  return new Promise((resolve) => {
    pendingInstructionsAck = resolve;
    sendEvent({ type: "session.update", session: { instructions } });
  });
}

/**
 * Every user turn — typed or transcribed from voice — comes through here.
 * Mirrors ConversationViewModel.groundAndRespond: search local memory, patch the
 * session's instructions with what's relevant, then explicitly trigger a reply (the
 * session runs with create_response:false, so nothing replies on its own).
 */
async function handleUserTurn(text) {
  const relevant = LocalMemory.search(text, 5);
  LocalMemory.add(text);
  AgentNexusBridge.pushMessage(text, "user");

  if (relevant.length > 0) {
    const lines = relevant.map((e) => `- ${e.text}`);
    const instructions = `${BASE_INSTRUCTIONS}\n\n以下是用户过去说过、可能相关的内容，如果有帮助请参考：\n${lines.join("\n")}`;
    await updateInstructionsAndWait(instructions);
  } else {
    await updateInstructionsAndWait(BASE_INSTRUCTIONS); // clear out any previous turn's injected memory
  }

  sendEvent({ type: "response.create" });
}

function handleServerEvent(json) {
  switch (json.type) {
    case "session.created":
      setState(STATE.LISTENING);
      break;
    case "session.updated":
      if (pendingInstructionsAck) {
        pendingInstructionsAck();
        pendingInstructionsAck = null;
      }
      break;
    case "response.audio.delta":
      playPCM16Chunk(json.delta);
      setState(STATE.SPEAKING);
      break;
    case "response.audio_transcript.delta":
      appendToAssistantBubble(json.delta);
      break;
    case "response.audio_transcript.done":
      setAssistantFinalText(json.transcript || "");
      break;
    case "conversation.item.input_audio_transcription.completed":
      if (json.transcript) {
        addBubble("user", json.transcript);
        handleUserTurn(json.transcript);
      }
      break;
    case "input_audio_buffer.speech_started":
      stopPlayback(); // barge-in: Qwen reports interrupt_response support server-side too
      setState(STATE.LISTENING);
      break;
    case "response.done":
      assistantBubbleEl = null;
      assistantHasDelta = false;
      if (state !== STATE.IDLE) setState(STATE.LISTENING);
      break;
    case "error":
      statusEl.textContent = `出错：${json.error?.message || "unknown"}`;
      break;
    case "relay.error":
      statusEl.textContent = `连接失败：${json.message}`;
      break;
    default:
      break; // ignore anything we don't handle
  }
}

async function start() {
  if (state !== STATE.IDLE) return;
  if (startPromise) return startPromise;

  startPromise = (async () => {
    setState(STATE.CONNECTING);

    // Pull-sync from AgentNexus before the conversation starts — bounded by the
    // fetch itself; on failure we just proceed with whatever's in local cache.
    await AgentNexusBridge.pullMemory();

    try {
      micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      statusEl.textContent = `麦克风权限失败：${e.message}（仍可以打字对话）`;
      micStream = null;
    }

    playCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 24000 });
    nextPlayTime = 0;

    if (micStream) {
      captureCtx = new (window.AudioContext || window.webkitAudioContext)();
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
    }

    await new Promise((resolve) => {
      ws = new WebSocket(`ws://${location.host}/ws`);

      ws.onopen = () => {
        // Resolve once the initial session.update is actually acked (session.updated),
        // not just sent — sending a second session.update (the per-turn memory patch)
        // before this one lands raced and produced an empty reply in testing.
        pendingInstructionsAck = resolve;
        sendEvent({
          type: "session.update",
          session: {
            modalities: ["audio", "text"],
            instructions: BASE_INSTRUCTIONS,
            voice,
            input_audio_format: "pcm16",
            output_audio_format: "pcm16",
            turn_detection: {
              type: "server_vad",
              threshold: 0.5,
              prefix_padding_ms: 300,
              silence_duration_ms: 500,
              create_response: false,
            },
          },
        });
      };

      ws.onmessage = (event) => {
        try {
          handleServerEvent(JSON.parse(event.data));
        } catch (e) {
          console.error("bad server message", event.data);
        }
      };

      ws.onerror = () => { statusEl.textContent = "WebSocket 出错"; resolve(); };
      ws.onclose = () => { if (state !== STATE.IDLE) stop(); };
    });
  })();

  try {
    await startPromise;
  } finally {
    startPromise = null;
  }
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
  setState(STATE.IDLE);
}

micBtn.addEventListener("click", () => {
  if (state === STATE.IDLE) start();
  else stop();
});

textForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = textInput.value.trim();
  if (!text) return;
  textInput.value = "";

  if (state === STATE.IDLE) await start();
  addBubble("user", text);
  sendEvent({
    type: "conversation.item.create",
    item: { type: "message", role: "user", content: [{ type: "input_text", text }] },
  });
  handleUserTurn(text);
});

(async () => {
  try {
    const res = await fetch("/api/config");
    const config = await res.json();
    voice = config.voice || voice;
    if (!config.hasKey) statusEl.textContent = "⚠️ 服务器没有读到 QWEN_API_KEY，请检查 .env";
  } catch (e) {
    statusEl.textContent = "⚠️ 无法连接本地服务";
  }
})();

setState(STATE.IDLE);
