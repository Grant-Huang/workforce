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

const BASE_INSTRUCTIONS = `你是一个语音助手，正在和用户实时语音对话。

说话方式：
- 像日常聊天一样自然口语化，不要用书面语（比如不要说"因此""综上所述""值得注意的是"）。
- 不要用任何视觉格式：不用列表符号、编号、加粗，也不要读网址或代码。

回答长度：根据问题本身决定，不要机械地限制在一两句话。
- 简单的问题、闲聊、确认类的话，几句话说完就行，不用刻意拉长。
- 如果用户是让你整理工作内容、待办事项，或者问一个需要讲清楚的技术/工程问题，内容可以详细，但要按口语习惯组织——比如"主要有这么几件事，第一……第二……"这样一段段说，不要用书面的分点列表腔调；内容特别多的时候，可以先说完整体，再问要不要展开某一部分。

背景信息的使用：
- 如果背景信息里有跟当前问题相关的内容，用自己的话自然带出来，不要逐字复述，也不要提"背景信息"这个说法本身。
- 如果问题明显需要用户之前提到的具体信息（比如某个日程、决定、事实），但背景信息里完全没有相关内容，不要编造答案——诚实说明你目前没有这方面的记录，比如"这个我目前没有相关记录"或者"这个我还得再查一下"，可以顺带问用户要不要现在告诉你。
- 常识性、闲聊性的问题正常回答，不用刻意强调"没有记录"。`;

// Resolver for whichever session.update we're currently waiting to be acked (see
// updateInstructionsAndWait). Tested directly against Qwen: a `conversation.item.create`
// with role "system" (or a fake "assistant" turn) is silently ignored by the model —
// only content actually in `session.update.session.instructions` gets used. Firing a
// second session.update before the first is acked also produced an empty reply in
// testing, so this waits for `session.updated` before doing anything else.
//
// That wait isn't fully reliable either, though: in further testing the ack for a
// per-turn patch occasionally never arrived at all (server-side flakiness, not a
// reproducible ordering bug) — so this has a timeout fallback. Missing the ack means
// the model might answer on slightly stale instructions for that one turn rather than
// the conversation hanging forever, which is the better failure mode.
let pendingInstructionsAck = null;

function sendSessionUpdateAndWait(sessionPatch, timeoutMs = 4000) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      if (pendingInstructionsAck === finish) pendingInstructionsAck = null;
      resolve();
    };
    pendingInstructionsAck = finish;
    sendEvent({ type: "session.update", session: sessionPatch });
    setTimeout(finish, timeoutMs);
  });
}

function updateInstructionsAndWait(instructions, timeoutMs = 4000) {
  return sendSessionUpdateAndWait({ instructions }, timeoutMs);
}

/**
 * Every user turn — typed or transcribed from voice — comes through here.
 * Mirrors ConversationViewModel.groundAndRespond: search local memory, patch the
 * session's instructions with what's relevant, then explicitly trigger a reply (the
 * session runs with create_response:false, so nothing replies on its own).
 *
 * An explicit "记住…" turn takes a different path: it writes a curated entry into
 * AgentNexus's structured memory layers (not just the raw message log every turn
 * gets) and skips memory retrieval — it's a command, not a question, so the model
 * just needs to briefly confirm rather than search-and-answer.
 */
async function handleUserTurn(text) {
  const saveIntent = SaveIntent.detect(text);
  AgentNexusBridge.pushMessage(text, "user");

  if (saveIntent) {
    LocalMemory.add(saveIntent.content, { source: "agentnexus", layer: "PROGRESS" });
    try {
      await AgentNexusBridge.createMemoryEntry("PROGRESS", saveIntent.content);
    } catch (e) {
      console.warn("save-intent write to AgentNexus failed (stayed local only):", e);
    }
    const instructions = `${BASE_INSTRUCTIONS}\n\n用户刚才明确要求记住这件事："${saveIntent.content}"，你已经帮TA记下了。只需要简短确认一句就行，不要复述内容、不要追问。`;
    await updateInstructionsAndWait(instructions);
    sendEvent({ type: "response.create" });
    return;
  }

  const relevant = LocalMemory.search(text, 5);
  LocalMemory.add(text);

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

    await new Promise((resolveOpen, rejectOpen) => {
      ws = new WebSocket(`ws://${location.host}/ws`);
      ws.onopen = resolveOpen;
      ws.onerror = () => { statusEl.textContent = "WebSocket 出错"; rejectOpen(new Error("ws error")); };
      ws.onmessage = (event) => {
        try {
          handleServerEvent(JSON.parse(event.data));
        } catch (e) {
          console.error("bad server message", event.data);
        }
      };
      ws.onclose = () => { if (state !== STATE.IDLE) stop(); };
    }).catch(() => {});

    if (ws && ws.readyState === WebSocket.OPEN) {
      // Wait for the initial session.update to be acked (session.updated), not just
      // sent — sending a second session.update (the per-turn memory patch) before
      // this one lands raced and produced an empty reply in testing. Has its own
      // timeout fallback (see sendSessionUpdateAndWait), so a missing ack here can't
      // hang start() forever either.
      await sendSessionUpdateAndWait({
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
      });
    }
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

/** Shared by both the text input and the time-based suggestion chips. */
async function sendTextMessage(text) {
  text = text.trim();
  if (!text) return;

  if (state === STATE.IDLE) await start();
  addBubble("user", text);
  sendEvent({
    type: "conversation.item.create",
    item: { type: "message", role: "user", content: [{ type: "input_text", text }] },
  });
  handleUserTurn(text);
}

textForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = textInput.value;
  textInput.value = "";
  await sendTextMessage(text);
});

// ---- time-based suggestion chips ----
//
// Deliberately restrained: no auto-connect, no auto-speak on open. Just 0-3 tappable
// suggestions in the empty state, chosen by time of day, that kick off a normal typed
// turn when tapped — same path as typing it yourself. Nothing shown outside these
// windows rather than forcing an always-on suggestion.
function getTimeSuggestions() {
  const hour = new Date().getHours();
  if (hour < 12) return [{ label: "查一下今天的日程安排", query: "查一下我今天的日程安排" }];
  if (hour >= 17) return [{ label: "总结复盘一下今天的工作", query: "帮我总结复盘一下今天的工作" }];
  return [];
}

function renderSuggestions() {
  const container = document.getElementById("suggestions");
  const suggestions = getTimeSuggestions();
  container.innerHTML = "";
  for (const s of suggestions) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "suggestionChip";
    chip.textContent = s.label;
    chip.addEventListener("click", () => sendTextMessage(s.query));
    container.appendChild(chip);
  }
}

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

renderSuggestions();
setState(STATE.IDLE);
