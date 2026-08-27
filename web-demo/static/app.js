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
//
// Two independent connections/state machines (docs/app-design.md section 8): the voice
// session (ws/STATE, mic + spoken replies, "point mic and talk") and the text session
// (textWs/TEXT_STATE, typing + dictation-to-text output, never touches the mic, reply
// is text-only via modalities: ["text"] — no "正在聆听" state ever shows for this one).
// They used to be the same connection; splitting them fixed a real bug where typing a
// message opened the microphone and played a spoken reply.

const chatEl = document.getElementById("chat");
const emptyEl = document.getElementById("empty");
const statusEl = document.getElementById("status");
const micBtn = document.getElementById("micBtn");
const micIcon = document.getElementById("micIcon");
const stopIcon = document.getElementById("stopIcon");
const composerRow = document.getElementById("composerRow");
const textForm = document.getElementById("textForm");
const textInput = document.getElementById("textInput");
const voiceOrbContainer = document.getElementById("voiceOrbContainer");
const voiceOrb = document.getElementById("voiceOrb");

const STATE = { IDLE: "idle", CONNECTING: "connecting", LISTENING: "listening", SPEAKING: "speaking" };
let state = STATE.IDLE;

let ws = null;
let micStream = null;
let captureCtx = null;
let playCtx = null;
let playDestNode = null; // MediaStreamAudioDestinationNode -- see setupPlayback()
let playElement = null; // <audio> element playback is routed through, for echo cancellation
let playWorkletNode = null; // AudioWorkletNode (pcm-player-worklet.js) -- see setupPlayback()
let processorNode = null;
let micAnalyser = null; // taps the mic capture graph, read while LISTENING -- see updateVoiceOrb()
let playAnalyser = null; // taps the playback graph, read while SPEAKING -- see updateVoiceOrb()
let orbAnimationId = null;
let assistantBubbleEl = null;
let assistantHasDelta = false;
let voice = "Serena";
let startPromise = null; // in-flight start(), so typed messages can await a session already starting

// Explicit request for the standard WebRTC-family constraints -- echoCancellation
// defaults to true in most browsers when unset, but that's an implicit default we
// shouldn't rely on; declaring it explicitly is part of the fix for the self-echo bug
// (assistant's own speech getting picked up as user input), see setupPlayback()'s
// comment for the other half of that fix.
const MIC_CONSTRAINTS = { audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } };

// ---- connection lifecycle ----
//
// Three rules, in order of how the demo satisfies them:
// (a) A connection attempt that never actually establishes a working session (socket
//     error, relay reports the upstream connect failed, or the socket opens but
//     session.created never arrives — the "哑掉" failure mode from the README) must
//     tell the user, not fail silently or leave the UI stuck in "连接中…".
// (b) Once connected, never release the connection on our own initiative — only the
//     user's stop button, a genuine connect failure, or (c) below closes it.
// (c) If the user goes quiet for a long time while connected, release the connection
//     proactively rather than holding it open indefinitely.
const IDLE_TIMEOUT_MS = 5 * 60 * 1000; // no user turn for this long while connected -> hang up
const CONNECT_TIMEOUT_MS = 8000; // socket opened but no session.created within this long -> treat as a failed connect
let idleTimer = null;
let connectTimeoutId = null;
let lastConnErrorMessage = null; // set by ws.onerror / relay.error just before the socket closes, so stop() can surface the real reason instead of the generic "未连接" label

function armIdleTimer() {
  clearIdleTimer();
  idleTimer = setTimeout(() => stop("长时间没有说话，已自动挂断"), IDLE_TIMEOUT_MS);
}

function clearIdleTimer() {
  if (idleTimer) {
    clearTimeout(idleTimer);
    idleTimer = null;
  }
}

function setState(next, statusOverride) {
  state = next;
  const label = {
    [STATE.IDLE]: "未连接",
    [STATE.CONNECTING]: "连接中…",
    [STATE.LISTENING]: "正在聆听…",
    [STATE.SPEAKING]: "助手正在说话…",
  }[next];
  statusEl.textContent = statusOverride || label;

  micBtn.classList.toggle("active", next !== STATE.IDLE);
  micBtn.classList.toggle("speaking", next === STATE.SPEAKING);
  micIcon.style.display = next === STATE.IDLE ? "block" : "none";
  stopIcon.style.display = next === STATE.IDLE ? "none" : "block";

  // LISTENING means "connected, waiting on the user" — the only state that should
  // count toward (c)'s idle clock. CONNECTING/SPEAKING/IDLE all clear it: still
  // connecting or the assistant is talking isn't "user went quiet", and IDLE means
  // there's nothing to release.
  if (next === STATE.LISTENING) armIdleTimer();
  else clearIdleTimer();

  renderDictationUI(); // keep the dictate button's enabled state in sync (can't run both mics at once)

  // Transcript is hidden behind the tech orb for the whole time the voice session is
  // connected (not just while actually listening/speaking) -- docs/app-design.md 8.3.
  // It's still being recorded into chatEl in real time underneath; this only toggles
  // which one is visible, so nothing about the history data model changes.
  if (next === STATE.IDLE) stopVoiceOrb();
  else startVoiceOrb();

  if (next !== STATE.CONNECTING && connectTimeoutId) {
    clearTimeout(connectTimeoutId);
    connectTimeoutId = null;
  }
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
  // User bubbles are always created with their full, final text in one call (unlike
  // assistant bubbles, which start empty and get filled in via appendToAssistantBubble/
  // setAssistantFinalText as deltas stream in) -- so this one spot covers every
  // user-turn source (voice transcript, typed, dictation) for both local history and
  // (via ConversationHistory.add(), see history.js) the AgentNexus push. The matching
  // assistant-turn persistence+push lives in finalizeAssistantTurn(), once the full
  // reply text is actually known.
  if (role === "user" && text) ConversationHistory.add("user", text);
  return bubble;
}

// Called once an assistant reply is actually complete (response.done) -- captures the
// full accumulated text before assistantBubbleEl gets reset, persists it locally and
// pushes it to AgentNexus (docs/app-design.md 8.4: previously only the user's half of
// the conversation was pushed/stored anywhere at all). Deliberately not called on
// barge-in (input_audio_buffer.speech_started) -- that's an interrupted, incomplete
// reply, not a finished turn, so it's just discarded same as before, not persisted
// half-formed.
//
// Also triggers memory extraction (docs/app-design.md 7.3) on the completed
// user+assistant pair -- fire-and-forget, doesn't block anything else here. Skipped
// for a save-intent turn ("记住…"): that already has its own explicit, curated write
// (see handleUserTurn), and running extraction on top would produce a near-duplicate
// entry paraphrasing the same content a second way.
function finalizeAssistantTurn(session) {
  if (session) session.responsePending = false;
  const text = assistantBubbleEl ? assistantBubbleEl.textContent : "";
  // ConversationHistory.add() also pushes to AgentNexus (with sync-status tracking +
  // retry) -- see history.js.
  if (text) ConversationHistory.add("assistant", text);

  const userText = session?.pendingUserText;
  if (session) session.pendingUserText = null;
  if (userText && text && !SaveIntent.detect(userText)) {
    MemoryExtraction.extractAndStore(userText, text);
  }

  assistantBubbleEl = null;
  assistantHasDelta = false;
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

// ---- playback: stream 24kHz PCM16 chunks through a continuous AudioWorklet ----
//
// Playback is deliberately routed through a MediaStreamAudioDestinationNode -> a hidden
// <audio> element, not connected directly to playCtx.destination. This isn't cosmetic:
// a real echo bug was reported (assistant's own speech getting picked back up as user
// input) and the likely root cause is that Chromium's AEC needs a reference signal for
// "what's currently being played out", and that reference is reliably wired up for
// <audio>/<video> element playback but is NOT guaranteed for raw Web Audio API output
// straight to .destination -- a known, documented gap, not a guess. Routing through an
// <audio> element is the standard workaround. Combined with explicitly requesting
// echoCancellation on the mic (see start()/startDictation() -- neither the text session
// nor its promoteDictationConnectionToTextSession() touch the mic at all).
// This couldn't be verified with a real speaker+mic acoustic loop in this sandbox (no
// real audio hardware here) -- it addresses the documented likely cause, but needs
// hands-on confirmation on a real device.
//
// 2026-08-24, round 1: real-device testing found clicking ("哒哒哒") after forcing the
// AudioContext to run at 24000Hz (to match the PCM chunks 1:1) -- MediaStreamTrack/
// <audio>-element playback clicks on non-native sample rates. Fixed by letting the
// context run at its native rate and resampling on playback instead.
//
// 2026-08-24, round 2: clicking persisted, recurring later in long responses, even after
// giving the old scheduling approach (creating one AudioBufferSourceNode per chunk,
// scheduled back-to-back via nextPlayTime) a lookahead margin and per-chunk fades. That
// approach is fragile by construction: every chunk boundary is a point where two
// independently-scheduled nodes' timing has to line up exactly, and anything that nudges
// one chunk's arrival relative to the schedule -- network jitter, a GC pause, or just
// accumulated imprecision over many chunks in a long response -- produces an audible
// edge there. Replaced with an AudioWorkletNode (pcm-player-worklet.js) backed by a
// small ring buffer: there's only one continuously-running audio callback for the whole
// session, chunks are just appended to its buffer whenever they arrive over the
// WebSocket, and an underrun (the buffer runs dry because the next chunk hasn't arrived
// yet) produces brief silence, not a click -- there's no per-chunk scheduling boundary
// left to misalign. This is the standard architecture for streamed PCM playback (the
// same shape any WebRTC/streaming-audio app uses), not a workaround layered on top of
// the discrete-node approach.
//
// The worklet processes audio at the AudioContext's native rate; resampleTo() below
// converts each 24kHz chunk to that rate on the main thread before handing it to the
// worklet, since AudioWorkletProcessor has no resampler of its own (this is what
// createBuffer()+AudioBufferSourceNode used to do for us automatically).
// Not acoustically verified in this sandbox (no real audio hardware here, same caveat as
// every playback fix in this file) -- needs real-device confirmation.
async function setupPlayback() {
  playCtx = new (window.AudioContext || window.webkitAudioContext)();
  await playCtx.audioWorklet.addModule("/static/pcm-player-worklet.js");
  playWorkletNode = new AudioWorkletNode(playCtx, "pcm-player", {
    numberOfInputs: 0,
    numberOfOutputs: 1,
    outputChannelCount: [1],
  });

  // See the tuning panel setup below (tuning.fadeMs/prebufferMs) -- falls back to the
  // worklet's own defaults if this message is somehow never delivered.
  playWorkletNode.port.postMessage({ type: "configure", fadeMs: tuning.fadeMs, prebufferMs: tuning.prebufferMs });

  playDestNode = playCtx.createMediaStreamDestination();
  playWorkletNode.connect(playDestNode);

  if (!playElement) {
    playElement = document.createElement("audio");
    playElement.autoplay = true;
    playElement.style.display = "none";
    document.body.appendChild(playElement);
  }
  playElement.srcObject = playDestNode.stream;

  // Gives updateVoiceOrb() something to read while SPEAKING.
  playAnalyser = playCtx.createAnalyser();
  playAnalyser.fftSize = 256;
  playWorkletNode.connect(playAnalyser);
}

// Linear-interpolation resample -- good enough for 24kHz -> ~48kHz speech playback, and
// far simpler than a proper polyphase resampler. Each chunk is resampled independently
// (the worklet's ring buffer is what stitches chunks together into continuous audio, not
// this function), so the very last output sample of a chunk can't interpolate against
// the next chunk's first sample yet and just clamps to the chunk's own last sample
// instead -- a tiny, sub-sample approximation at each chunk seam, not a discontinuity.
function resampleTo(float32, fromRate, toRate) {
  if (fromRate === toRate) return float32;
  const ratio = toRate / fromRate;
  const outLength = Math.round(float32.length * ratio);
  const out = new Float32Array(outLength);
  for (let i = 0; i < outLength; i++) {
    const srcPos = i / ratio;
    const i0 = Math.floor(srcPos);
    const i1 = Math.min(i0 + 1, float32.length - 1);
    const frac = srcPos - i0;
    out[i] = float32[i0] * (1 - frac) + float32[i1] * frac;
  }
  return out;
}

function playPCM16Chunk(base64) {
  const int16 = base64ToInt16(base64);
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / (int16[i] < 0 ? 0x8000 : 0x7fff);
  const resampled = resampleTo(float32, 24000, playCtx.sampleRate);
  playWorkletNode?.port.postMessage({ type: "push", samples: resampled }, [resampled.buffer]);
}

// ---- voice-session "tech orb" (docs/app-design.md 8.3) ----
//
// Replaces #chat while the voice session is connected. Light-blue glow that reacts to
// whichever audio is actually live right now -- the user's mic input while LISTENING,
// the assistant's playback while SPEAKING -- via the analysers wired up in start()'s
// mic-capture block and setupPlayback() above. A slow sine-driven "breathing" baseline
// keeps it visibly alive during CONNECTING or brief silence, instead of going static.

function readAnalyserLevel(analyser) {
  if (!analyser) return 0;
  const data = new Uint8Array(analyser.frequencyBinCount);
  analyser.getByteFrequencyData(data);
  let sum = 0;
  for (let i = 0; i < data.length; i++) sum += data[i];
  const avg = sum / data.length; // 0-255
  return Math.min(1, avg / 90); // 90, not 255: normal speech shouldn't need to peak the scale to register
}

// Defense-in-depth against the assistant's own played-back speech leaking into the mic
// (residual echo the browser's AEC didn't fully cancel) getting misread by the server's
// VAD as the user interrupting -- real-device report (2026-08-24). This does NOT fix the
// underlying acoustic issue (that needs headphones or better hardware AEC -- see
// docs/... discussion); it's a client-side confirmation gate on top of the server's
// speech_started signal, specifically for the one scenario where a false trigger is
// actually possible: while the assistant is SPEAKING (there's assistant audio for the
// mic to have picked back up). A normal turn-start, where the assistant is already
// silent, has nothing to false-trigger from and isn't gated -- see the speech_started
// handler below.
//
// Echo residual tends to be a brief spike rather than sustained energy the way genuine
// continued speech is, so this samples mic level for BARGE_IN_CONFIRM_MS and requires
// the *average* (not "every single sample", which would also reject real speech's
// natural brief dips between syllables) to clear BARGE_IN_CONFIRM_LEVEL before treating
// it as a real interruption. Trade-off: genuine barge-in now takes ~BARGE_IN_CONFIRM_MS
// longer to register. Neither constant is acoustically tuned (no real audio hardware in
// this sandbox, same caveat as every playback/echo fix in this file) -- needs real-device
// confirmation and likely adjustment.
const BARGE_IN_CONFIRM_MS = 250;
const BARGE_IN_CONFIRM_LEVEL = 0.12; // same 0-1 scale as readAnalyserLevel()

// Real-device report (2026-08-25) clarified that "前后重叠" didn't mean overlapping
// *audio* (the response-cancel fix already prevents that) -- it meant the assistant
// audibly cutting its own reply off mid-sentence and starting a new one. Root cause: the
// old flow called handleUserTurn (and therefore response.create) the instant a
// fragment's transcript arrived. If VAD splits one utterance into two segments (a
// mid-sentence pause outlasting silence_duration_ms), the assistant could already be
// speaking a reply to fragment A by the time fragment B's speech_started arrives, so the
// barge-in path cancels that in-progress reply -- audible as self-interruption, not a
// bug in the cancel logic itself, just a consequence of responding too eagerly.
//
// scheduleVoiceResponse below holds each transcript for RESPONSE_DEBOUNCE_MS before
// actually calling handleUserTurn; input_audio_buffer.speech_started arriving during
// that window cancels the pending timer instead of letting a reply start, so a
// near-immediate continuation gets caught (and merged -- see handleUserTurn's
// concatenation) before the assistant ever opens its mouth. This only catches
// continuations that resume within RESPONSE_DEBOUNCE_MS of the transcript arriving --
// longer thinking pauses still rely on silence_duration_ms itself not splitting the
// utterance in the first place. Not acoustically tuned (no real audio hardware in this
// sandbox) -- needs real-device confirmation and likely adjustment.
const RESPONSE_DEBOUNCE_MS = 500;
let voicePendingTranscript = null; // accumulated text waiting out the debounce window
let voiceResponseTimer = null;

function scheduleVoiceResponse(transcript) {
  voicePendingTranscript = voicePendingTranscript ? `${voicePendingTranscript} ${transcript}` : transcript;
  if (voiceResponseTimer) clearTimeout(voiceResponseTimer);
  voiceResponseTimer = setTimeout(() => {
    voiceResponseTimer = null;
    const text = voicePendingTranscript;
    voicePendingTranscript = null;
    handleUserTurn(text, voiceSession);
  }, RESPONSE_DEBOUNCE_MS);
}

function confirmSustainedMicLevel(analyser, durationMs, level) {
  return new Promise((resolve) => {
    if (!analyser) { resolve(true); return; }
    const samples = [];
    const start = performance.now();
    const sample = () => {
      samples.push(readAnalyserLevel(analyser));
      if (performance.now() - start >= durationMs) {
        const avg = samples.reduce((a, b) => a + b, 0) / samples.length;
        resolve(avg >= level);
        return;
      }
      requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });
}

function updateVoiceOrb() {
  let level = 0;
  if (state === STATE.LISTENING) level = readAnalyserLevel(micAnalyser);
  else if (state === STATE.SPEAKING) level = readAnalyserLevel(playAnalyser);

  const breathe = 0.05 * (1 + Math.sin(Date.now() / 900));
  const scale = 1 + breathe + level * 0.35;
  const glowSpread = 14 + level * 40;
  const glowOpacity = 0.35 + level * 0.35;

  voiceOrb.style.transform = `scale(${scale.toFixed(3)})`;
  voiceOrb.style.boxShadow = `0 0 ${glowSpread.toFixed(0)}px ${(glowSpread / 3).toFixed(0)}px rgba(79, 168, 255, ${glowOpacity.toFixed(2)})`;

  orbAnimationId = requestAnimationFrame(updateVoiceOrb);
}

function startVoiceOrb() {
  chatEl.style.display = "none";
  voiceOrbContainer.style.display = "flex";
  if (!orbAnimationId) updateVoiceOrb();
}

function stopVoiceOrb() {
  voiceOrbContainer.style.display = "none";
  chatEl.style.display = "";
  if (orbAnimationId) {
    cancelAnimationFrame(orbAnimationId);
    orbAnimationId = null;
  }
  voiceOrb.style.transform = "";
  voiceOrb.style.boxShadow = "";
  chatEl.scrollTop = chatEl.scrollHeight; // reveal the just-finished turn at the bottom
}

function stopPlayback() {
  playWorkletNode?.port.postMessage({ type: "clear" });
}

// ---- realtime session ----
//
// Two independent connections/state machines share this protocol layer: the voice
// session (ws/state, mic + TTS, unchanged) and the text session (textWs/textState,
// added for docs/app-design.md section 8 -- typing and dictation-to-text both land
// here, never touch the mic, never get a spoken reply). sendEventOn/handleUserTurn
// take an explicit target so the shared grounding logic (memory search, instructions
// patch, response.create) works for either without duplicating it.

function sendEventOn(targetWs, payload) {
  if (targetWs && targetWs.readyState === WebSocket.OPEN) {
    targetWs.send(JSON.stringify(payload));
  }
}

function sendEvent(payload) {
  sendEventOn(ws, payload);
}

const BASE_INSTRUCTIONS = `你是一个语音助手，正在和用户实时语音对话。

说话方式：
- 像日常聊天一样自然口语化，不要用书面语（比如不要说"因此""综上所述""值得注意的是"）。
- 不要用任何视觉格式：不用列表符号、编号、加粗，也不要读网址或代码。

回答长度：先判断这条问题属于哪一类，再按对应的长度来，不要机械地都说成一两句话或者都展开成一大段：
- **查询类**（问日期时间、单一事实、确认性问题）：1-3 句话说完，给答案不给报告，除非用户明确要求展开。
- **列举类**（问日程安排、待办事项、多条信息）：一口气最多说 3 条左右，说完问一句"还有几条要不要都说说"，不要一次性倒完一大串，人一次性靠听记不住那么多。
- **分析/解释类**（需要讲清楚原因、讲清楚一个技术/工程问题、帮用户理一件复杂的事）：可以说得详细，但先说一句"路线图"（比如"这个我从两方面说"），再按"第一……第二……"这样一段段说，段与段之间自然停顿，给用户留插话的空当；说了几点就是几点，中途不要冒出没预告过的第三点，语音没法让用户"往回听"，说漏了就是说漏了。
语音是念给人听的，不是照着文字稿念——同样的内容，念出来比读一遍慢得多，能一句话说清楚的不要拖成三句。

背景信息的使用：
- 如果背景信息里有跟当前问题相关的内容，用自己的话自然带出来，不要逐字复述，也不要提"背景信息"这个说法本身。
- 如果问题明显需要用户之前提到的具体信息（比如某个日程、决定、事实），但背景信息里完全没有相关内容，不要编造答案——诚实说明你目前没有这方面的记录，比如"这个我目前没有相关记录"或者"这个我还得再查一下"，可以顺带问用户要不要现在告诉你。
- 常识性、闲聊性的问题正常回答，不用刻意强调"没有记录"。`;

/**
 * Factory for "send a session.update, wait for its session.updated ack" -- one instance
 * per connection (voiceSession/textSession below), so the two connections' in-flight
 * patches never step on each other. Tested directly against Qwen: a
 * `conversation.item.create` with role "system" (or a fake "assistant" turn) is
 * silently ignored by the model — only content actually in
 * `session.update.session.instructions` gets used. Firing a second session.update
 * before the first is acked also produced an empty reply in testing, so this waits for
 * `session.updated` before doing anything else.
 *
 * That wait isn't fully reliable either, though: in further testing the ack for a
 * per-turn patch occasionally never arrived at all (server-side flakiness, not a
 * reproducible ordering bug) — so this has a timeout fallback. Missing the ack means
 * the model might answer on slightly stale instructions for that one turn rather than
 * the conversation hanging forever, which is the better failure mode.
 */
function makeSessionUpdateWaiter(getWs) {
  let pendingAck = null;

  function sendSessionUpdateAndWait(sessionPatch, timeoutMs = 4000) {
    return new Promise((resolve) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        if (pendingAck === finish) pendingAck = null;
        resolve();
      };
      pendingAck = finish;
      sendEventOn(getWs(), { type: "session.update", session: sessionPatch });
      setTimeout(finish, timeoutMs);
    });
  }

  function updateInstructionsAndWait(instructions, timeoutMs = 4000) {
    return sendSessionUpdateAndWait({ instructions }, timeoutMs);
  }

  /** Called from the connection's session.updated handler. */
  function resolveAck() {
    if (pendingAck) {
      pendingAck();
      pendingAck = null;
    }
  }

  return { sendSessionUpdateAndWait, updateInstructionsAndWait, resolveAck };
}

const voiceUpdater = makeSessionUpdateWaiter(() => ws);
const textUpdater = makeSessionUpdateWaiter(() => textWs);
// pendingUserText: set by handleUserTurn(), read (and cleared) by finalizeAssistantTurn()
// to pair a completed reply back with what the user said, for memory extraction
// (docs/app-design.md 7.3). Lives on these session objects rather than a module-level
// variable so voice and text turns can't cross-contaminate each other's pairing.
// responsePending: true from the moment we send response.create until response.done
// (or a cancel) lands -- see handleUserTurn's guard below for why this exists.
const voiceSession = { getWs: () => ws, updater: voiceUpdater, pendingUserText: null, responsePending: false };
const textSession = { getWs: () => textWs, updater: textUpdater, pendingUserText: null, responsePending: false };

/**
 * Every user turn — typed, dictated, or transcribed from live voice — comes through
 * here. Mirrors ConversationViewModel.groundAndRespond: search local memory, patch the
 * session's instructions with what's relevant, then explicitly trigger a reply (every
 * session runs with create_response:false, so nothing replies on its own).
 *
 * `session` bundles which connection this turn belongs to -- voiceSession for live
 * voice conversation, textSession for typed/dictated text (see docs/app-design.md
 * section 8: these are two independent connections now, not one shared one).
 *
 * An explicit "记住…" turn takes a different path: it writes a curated entry into
 * AgentNexus's structured memory layers (not just the raw message log every turn
 * gets) and skips memory retrieval — it's a command, not a question, so the model
 * just needs to briefly confirm rather than search-and-answer.
 */
async function handleUserTurn(rawText, session) {
  // Real-device report (2026-08-25): with threshold/silence_duration_ms tuned low
  // (0.50 / 750ms), a single continuous utterance can get split into two VAD segments
  // -- a mid-sentence pause outlasts silence_duration_ms, the server commits+transcribes
  // a fragment, then the user keeps talking and eventually triggers a second commit for
  // the rest. Each transcript reaches here and used to fire its own response.create
  // regardless of whether the previous one had finished -- with create_response:false
  // there's nothing server-side stopping two responses from streaming concurrently, and
  // this app never tracked which response a response.audio.delta belonged to, so the two
  // unrelated audio streams got interleaved into the same playback buffer. That's a
  // plausible cause of the reported "answered twice" and the raspy/garbled audio quality
  // both -- not just a buffer-underrun symptom. Treat a transcript that arrives while the
  // previous turn's response is still in flight as a continuation, the same way a real
  // barge-in would: cancel the stale response before starting the new one, instead of
  // letting both run at once.
  //
  // Follow-up report (still reproducible at the safer 0.55/900 defaults): even with the
  // cancel above, the assistant would sometimes ask about something the user had *just*
  // said in the fragment right before it ("是七点还是八点呢？" right after the user said
  // "八点钟开始") -- i.e. it wasn't just an audio-overlap problem, our own local
  // grounding (memory search, the instructions patch below) was regrounding on only the
  // *second* fragment's text, discarding whatever the first fragment said the instant
  // `session.pendingUserText` got overwritten. Concatenating the stale fragment onto the
  // new one before grounding gives both this app's local search and the save-intent
  // check the whole utterance, not half of it.
  const text = session.responsePending && session.pendingUserText
    ? `${session.pendingUserText} ${rawText}`
    : rawText;

  // Paired with the assistant's reply once it's done (see finalizeAssistantTurn) to run
  // memory extraction on the complete exchange -- extraction needs both halves, not
  // just what the user said. Set unconditionally (even for a save-intent turn, which
  // finalizeAssistantTurn skips by re-checking SaveIntent.detect itself) so there's one
  // place deciding that, not two.
  session.pendingUserText = text;

  if (session.responsePending) {
    if (session === voiceSession) stopPlayback();
    sendEventOn(session.getWs(), { type: "response.cancel" });
    session.responsePending = false;
  }

  const saveIntent = SaveIntent.detect(text);

  if (saveIntent) {
    // source defaults to "local" here (not "agentnexus") -- honestly reflects "not yet
    // confirmed synced" until createMemoryEntry below actually succeeds, per
    // docs/roadmap-todo.md's "记忆" section item 3. "过户" to agentnexus + the real
    // sourceId happens via markSynced once that's confirmed, not assumed up front.
    const localEntry = LocalMemory.add(saveIntent.content, { layer: "PROGRESS" });
    try {
      const created = await AgentNexusBridge.createMemoryEntry("PROGRESS", saveIntent.content);
      if (localEntry) LocalMemory.markSynced(localEntry.id, { source: "agentnexus", sourceId: created.entry_id });
    } catch (e) {
      console.warn("save-intent write to AgentNexus failed (stayed local only, source stays \"local\" for a retry later):", e);
    }
    const instructions = `${BASE_INSTRUCTIONS}\n\n用户刚才明确要求记住这件事："${saveIntent.content}"，你已经帮TA记下了。只需要简短确认一句就行，不要复述内容、不要追问。`;
    await session.updater.updateInstructionsAndWait(instructions);
    sendEventOn(session.getWs(), { type: "response.create" });
    session.responsePending = true;
    return;
  }

  // No longer stores the raw text here (used to be LocalMemory.add(text) on every
  // turn) -- that's now finalizeAssistantTurn's job, via MemoryExtraction, once the
  // assistant's reply is known too (docs/app-design.md 7.3). The raw transcript itself
  // isn't lost -- ConversationHistory (history.js) already keeps a verbatim copy.
  const relevant = LocalMemory.search(text, 5);

  if (relevant.length > 0) {
    const lines = relevant.map((e) => `- ${e.text}`);
    const instructions = `${BASE_INSTRUCTIONS}\n\n以下是用户过去说过、可能相关的内容，如果有帮助请参考：\n${lines.join("\n")}`;
    await session.updater.updateInstructionsAndWait(instructions);
  } else {
    await session.updater.updateInstructionsAndWait(BASE_INSTRUCTIONS); // clear out any previous turn's injected memory
  }

  sendEventOn(session.getWs(), { type: "response.create" });
  session.responsePending = true;
}

function handleBargeIn() {
  stopPlayback();
  sendEvent({ type: "response.cancel" });
  voiceSession.responsePending = false;
  assistantBubbleEl = null;
  assistantHasDelta = false;
  setState(STATE.LISTENING);
}

function handleServerEvent(json) {
  switch (json.type) {
    case "session.created":
      setState(STATE.LISTENING);
      break;
    case "session.updated":
      voiceUpdater.resolveAck();
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
        scheduleVoiceResponse(json.transcript);
      }
      break;
    case "input_audio_buffer.speech_started":
      // Barge-in: Qwen reports interrupt_response support server-side too. Stopping
      // local playback alone isn't enough -- without response.cancel the server keeps
      // generating/streaming after the user interrupts, and any response.audio.delta
      // that arrives after this point would just restart playback. Matches iOS's
      // onSpeechStarted (interruptPlayback + cancelResponse), which already did both.
      //
      // If a response is still waiting out its debounce window (see
      // scheduleVoiceResponse above), the user has already resumed talking before the
      // assistant ever opened its mouth -- let it ride: cancel the pending response and
      // wait for this new speech's own transcript, which scheduleVoiceResponse will
      // concatenate onto what's already pending and restart the debounce. No barge-in
      // needed since nothing is playing yet.
      if (voiceResponseTimer) {
        clearTimeout(voiceResponseTimer);
        voiceResponseTimer = null;
        break;
      }

      // Only gated by confirmSustainedMicLevel while the assistant is actually SPEAKING
      // -- see that function's doc comment. A normal turn-start (assistant already
      // silent) has no echo to false-trigger from, so it's confirmed immediately, same
      // as before this change.
      if (state === STATE.SPEAKING) {
        confirmSustainedMicLevel(micAnalyser, BARGE_IN_CONFIRM_MS, BARGE_IN_CONFIRM_LEVEL).then((confirmed) => {
          // Re-check state: the assistant may have already finished on its own (or the
          // user may have hung up) during the confirmation window.
          if (confirmed && state === STATE.SPEAKING) handleBargeIn();
        });
      } else {
        handleBargeIn();
      }
      break;
    case "response.done":
      finalizeAssistantTurn(voiceSession);
      if (state !== STATE.IDLE) setState(STATE.LISTENING);
      break;
    case "error":
      statusEl.textContent = `出错：${json.error?.message || "unknown"}`;
      break;
    case "relay.error":
      lastConnErrorMessage = `连接失败：${json.message}`;
      statusEl.textContent = lastConnErrorMessage;
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
    ConversationHistory.retryUnsynced();

    // setupPlayback() *before* getUserMedia() -- deliberately, not incidental order.
    // Real-device report (2026-08-26): with all four tuning-panel presets, the
    // assistant would interrupt its own reply and restart, repeatedly (3-8 times),
    // even while the user stayed completely silent -- ruling out VAD splitting a real
    // utterance (nothing to split) and matching the user's own guess: its own voice
    // being picked up as input. echoCancellation:true on the mic constraints is
    // supposed to prevent exactly this, using whatever's currently playing out of the
    // tab as the reference signal to cancel -- but getUserMedia() used to be called
    // *before* setupPlayback() ever created the <audio> element that routing depends
    // on (see that function's comment on why routing through a real <audio> element
    // matters for AEC at all). Requesting the mic stream before there's any playback
    // output for the browser to use as a reference is a plausible reason AEC wasn't
    // effectively canceling the assistant's own voice -- there was nothing to cancel
    // against yet at the moment the mic stream (and its AEC negotiation) was set up.
    // Not acoustically verified in this sandbox (no real audio hardware here) -- needs
    // real-device confirmation.
    await setupPlayback();

    try {
      micStream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);
    } catch (e) {
      statusEl.textContent = `麦克风权限失败：${e.message}（仍可以打字对话）`;
      micStream = null;
    }

    if (micStream) {
      captureCtx = new (window.AudioContext || window.webkitAudioContext)();
      // mic-capture-worklet.js -- see its header comment for why this replaced
      // ScriptProcessorNode: that ran its callback (downsample+PCM16+base64+WS-send) on
      // the main thread for the whole call, which is a plausible source of the main-
      // thread congestion that delays feeding the *separate* playback worklet's ring
      // buffer, causing persistent raspy/沙哑 audio no fade/prebuffer tuning could fix.
      await captureCtx.audioWorklet.addModule("/static/mic-capture-worklet.js");
      const source = captureCtx.createMediaStreamSource(micStream);
      processorNode = new AudioWorkletNode(captureCtx, "mic-capture", {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
      });
      const silentGain = captureCtx.createGain();
      silentGain.gain.value = 0; // keep the graph "live" without echoing mic audio to speakers

      processorNode.port.onmessage = (event) => {
        const downsampled = downsampleTo16k(event.data, captureCtx.sampleRate);
        const pcm16 = floatTo16BitPCM(downsampled);
        sendEvent({ type: "input_audio_buffer.append", audio: int16ToBase64(pcm16) });
      };

      source.connect(processorNode);
      processorNode.connect(silentGain);
      silentGain.connect(captureCtx.destination);

      // Fan the same mic source out to an analyser too -- doesn't affect the capture
      // pipeline above, just gives updateVoiceOrb() something to read while LISTENING.
      micAnalyser = captureCtx.createAnalyser();
      micAnalyser.fftSize = 256;
      source.connect(micAnalyser);
    }

    lastConnErrorMessage = null;
    await new Promise((resolveOpen, rejectOpen) => {
      const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(`${wsProto}//${location.host}/ws`);
      ws.onopen = resolveOpen;
      ws.onerror = () => {
        lastConnErrorMessage = "连接失败：WebSocket 出错，请检查网络后重试";
        statusEl.textContent = lastConnErrorMessage;
        rejectOpen(new Error("ws error"));
      };
      ws.onmessage = (event) => {
        try {
          handleServerEvent(JSON.parse(event.data));
        } catch (e) {
          console.error("bad server message", event.data);
        }
      };
      // Fires on any close, ours or the server's. stop() itself already sets state to
      // IDLE before calling ws.close(), so the `state !== IDLE` guard here only fires
      // for closes we didn't initiate (server dropped us, relay.error, network loss) —
      // those still need the same teardown + user-facing message stop() gives.
      ws.onclose = () => {
        if (state !== STATE.IDLE) stop(lastConnErrorMessage || "连接已断开");
        lastConnErrorMessage = null;
      };
    }).catch(() => {});

    if (ws && ws.readyState === WebSocket.OPEN) {
      // Belt-and-suspenders for (a): if the socket opens but the session never actually
      // comes up — no session.created, ever — the demo would otherwise sit at "连接中…"
      // forever. This is a real observed failure mode (see README's "连接多了之后会
      // '哑掉'"), not a hypothetical. session.created moves state to LISTENING, which
      // clears this timer; if that hasn't happened within CONNECT_TIMEOUT_MS, give up
      // and tell the user rather than hanging silently.
      connectTimeoutId = setTimeout(() => {
        if (state === STATE.CONNECTING) stop("连接超时，请重试");
      }, CONNECT_TIMEOUT_MS);

      // Wait for the initial session.update to be acked (session.updated), not just
      // sent — sending a second session.update (the per-turn memory patch) before
      // this one lands raced and produced an empty reply in testing. Has its own
      // timeout fallback (see sendSessionUpdateAndWait), so a missing ack here can't
      // hang start() forever either.
      await voiceUpdater.sendSessionUpdateAndWait({
        modalities: ["audio", "text"],
        instructions: BASE_INSTRUCTIONS,
        voice,
        input_audio_format: "pcm16",
        output_audio_format: "pcm16",
        // threshold history (2026-08-24): raised 0.5 -> 0.65 earlier the same day after
        // a report that the assistant was very easily barge-in'd by background noise.
        // That same day, a *different* report came in: the assistant would start
        // replying to a fragment ("我想问一下...") before the user actually finished
        // speaking -- a raised threshold means more of the quieter parts of the user's
        // own natural speech (unvoiced consonants, breaths between words) register as
        // "silence", which combined with silence_duration_ms made genuinely-continuous
        // speech look like it had ended. threshold is shared by both "should the
        // assistant treat this as a real interruption" and "has the user's turn ended" --
        // tuning it up for the first problem makes the second worse, and vice versa.
        //
        // Lowered back to 0.55 (partway back to the 0.5 default, not all the way) now
        // that confirmSustainedMicLevel() (see handleServerEvent's speech_started case)
        // exists as a client-side layer against false barge-in from echo -- the server
        // threshold no longer has to carry that whole burden by itself, so it can sit
        // closer to default and let silence_duration_ms (800, see below) do more of the
        // "avoid premature turn-ending" work instead. Not scientifically tuned (single
        // reports, no real audio hardware in this sandbox) -- may need further
        // adjustment either direction.
        //
        // silence_duration_ms: 500 -> 800 -> 900 across 2026-08-24 for the same
        // premature-turn-ending report above -- 500ms of silence is short relative to
        // normal mid-sentence pauses (thinking, breathing). 900ms is close to a practical
        // ceiling for this knob: the user's own description at 800ms was
        // "互相插话，大部分它也能接上" (mutual interruption sometimes, but mostly
        // recovers fine) -- that reads as ordinary conversational overlap rather than a
        // bug, and pushing this much further starts trading it for response latency
        // instead. If mutual interruption still needs to go lower than this after 900ms,
        // the fix is probably not another bump here -- it's inherent to turn-taking
        // based on silence detection.
        //
        // Both values now come from the tuning panel (see loadTuning() above) instead of
        // being hardcoded, so a further round of this doesn't need a redeploy -- the
        // numbers in the comments above are TUNING_DEFAULTS, not necessarily what's
        // actually in effect for this session.
        turn_detection: {
          type: "server_vad",
          threshold: tuning.threshold,
          prefix_padding_ms: 300,
          silence_duration_ms: tuning.silenceMs,
          create_response: false,
        },
      });
    } else {
      // Socket never opened (ws.onerror already set an explanatory message) — release
      // whatever we grabbed before attempting the connection and go back to idle so
      // the user can retry instead of being stuck mid-"连接中…".
      if (micStream) { micStream.getTracks().forEach((t) => t.stop()); micStream = null; }
      if (captureCtx) { captureCtx.close(); captureCtx = null; micAnalyser = null; }
      if (playCtx) { playCtx.close(); playCtx = null; playDestNode = null; playAnalyser = null; if (playElement) playElement.srcObject = null; }
      ws = null;
      setState(STATE.IDLE, lastConnErrorMessage || "连接失败，请稍后重试");
    }
  })();

  try {
    await startPromise;
  } finally {
    startPromise = null;
  }
}

function stop(reason) {
  // A patch we're mid-wait on (updateInstructionsAndWait/sendSessionUpdateAndWait) can
  // no longer be acked once we're tearing the connection down — resolve it now instead
  // of leaving it to time out on its own, so callers awaiting it aren't stuck holding
  // a stale in-flight start()/turn.
  voiceUpdater.resolveAck();

  // A response debounce (see scheduleVoiceResponse) still waiting out its window when
  // the user hangs up would otherwise fire after ws is already null -- sendEventOn's
  // guard keeps that harmless, but there's no reason to still run grounding/memory
  // search for a turn nobody's listening to anymore.
  if (voiceResponseTimer) {
    clearTimeout(voiceResponseTimer);
    voiceResponseTimer = null;
  }
  voicePendingTranscript = null;

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
    micAnalyser = null;
  }
  stopPlayback();
  if (playCtx) {
    playCtx.close();
    playCtx = null;
    playDestNode = null;
    playAnalyser = null;
    if (playElement) playElement.srcObject = null;
  }
  if (micStream) {
    micStream.getTracks().forEach((t) => t.stop());
    micStream = null;
  }

  assistantBubbleEl = null;
  assistantHasDelta = false;
  setState(STATE.IDLE, reason);
}

micBtn.addEventListener("click", () => {
  if (state === STATE.IDLE) start();
  else stop();
});

// ---- text session: typing + dictation-to-text output ----
//
// docs/app-design.md section 8: sendTextMessage used to fall through to start(), the
// same function that opens the live-voice connection -- confirmed via user testing
// that this meant typing a message actually opened the microphone and played a spoken
// reply. This is a fully independent connection/state machine from the voice session
// above: never touches the mic, and `modalities` is ["text"] only, so the server never
// generates audio for this connection at all -- verified live against
// qwen3.5-omni-flash-realtime (no audio-related events came back for a text-only
// session, only response.text.delta/response.text.done, a different event pair than
// the response.audio_transcript.* ones the voice session uses).
const TEXT_STATE = { IDLE: "idle", CONNECTING: "connecting", READY: "ready" };
let textState = TEXT_STATE.IDLE;
let textWs = null;
let textStartPromise = null;
let textIdleTimer = null;
let textConnectTimeoutId = null;
let textLastConnErrorMessage = null;

function armTextIdleTimer() {
  clearTextIdleTimer();
  textIdleTimer = setTimeout(() => stopTextSession("长时间没有新消息，已自动断开"), IDLE_TIMEOUT_MS);
}

function clearTextIdleTimer() {
  if (textIdleTimer) {
    clearTimeout(textIdleTimer);
    textIdleTimer = null;
  }
}

function setTextState(next) {
  textState = next;
  if (next === TEXT_STATE.READY) armTextIdleTimer();
  else clearTextIdleTimer();
  renderDictationUI(); // keeps mic/dictate buttons in sync -- see their disabled logic there
  if (next !== TEXT_STATE.CONNECTING && textConnectTimeoutId) {
    clearTimeout(textConnectTimeoutId);
    textConnectTimeoutId = null;
  }
}

function handleTextSessionEvent(json) {
  switch (json.type) {
    case "session.updated":
      textUpdater.resolveAck();
      break;
    case "response.text.delta":
      appendToAssistantBubble(json.delta);
      break;
    case "response.text.done":
      setAssistantFinalText(json.text || "");
      break;
    case "response.done":
      finalizeAssistantTurn(textSession);
      break;
    case "error":
      statusEl.textContent = `出错：${json.error?.message || "unknown"}`;
      break;
    case "relay.error":
      textLastConnErrorMessage = `连接失败：${json.message}`;
      statusEl.textContent = textLastConnErrorMessage;
      break;
    default:
      break;
  }
}

async function startTextSession() {
  if (textState !== TEXT_STATE.IDLE) return;
  if (textStartPromise) return textStartPromise;

  textStartPromise = (async () => {
    setTextState(TEXT_STATE.CONNECTING);
    await AgentNexusBridge.pullMemory();
    ConversationHistory.retryUnsynced();

    textLastConnErrorMessage = null;
    await new Promise((resolveOpen) => {
      const wsProto = location.protocol === "https:" ? "wss:" : "ws:";
      textWs = new WebSocket(`${wsProto}//${location.host}/ws`);
      textWs.onopen = resolveOpen;
      textWs.onerror = () => {
        textLastConnErrorMessage = "连接失败：WebSocket 出错，请检查网络后重试";
        statusEl.textContent = textLastConnErrorMessage;
        resolveOpen();
      };
      textWs.onmessage = (event) => {
        try {
          handleTextSessionEvent(JSON.parse(event.data));
        } catch (e) {
          console.error("bad server message", event.data);
        }
      };
      textWs.onclose = () => {
        if (textState !== TEXT_STATE.IDLE) stopTextSession(textLastConnErrorMessage || "连接已断开");
        textLastConnErrorMessage = null;
      };
    });

    if (textWs && textWs.readyState === WebSocket.OPEN) {
      textConnectTimeoutId = setTimeout(() => {
        if (textState === TEXT_STATE.CONNECTING) stopTextSession("连接超时，请重试");
      }, CONNECT_TIMEOUT_MS);

      await textUpdater.sendSessionUpdateAndWait({
        modalities: ["text"],
        instructions: BASE_INSTRUCTIONS,
        turn_detection: {
          type: "server_vad",
          threshold: 0.5,
          prefix_padding_ms: 300,
          silence_duration_ms: 500,
          create_response: false,
        },
      });
      setTextState(TEXT_STATE.READY);
    } else {
      textWs = null;
      setTextState(TEXT_STATE.IDLE);
      statusEl.textContent = textLastConnErrorMessage || "连接失败，请稍后重试";
    }
  })();

  try {
    await textStartPromise;
  } finally {
    textStartPromise = null;
  }
}

function stopTextSession(reason) {
  textUpdater.resolveAck();
  if (textWs) {
    textWs.close();
    textWs = null;
  }
  assistantBubbleEl = null;
  assistantHasDelta = false;
  setTextState(TEXT_STATE.IDLE);
  if (reason) statusEl.textContent = reason;
}

/** Shared by both the text input and the time-based suggestion chips. */
async function sendTextMessage(text) {
  text = text.trim();
  if (!text) return;

  if (textState === TEXT_STATE.IDLE) await startTextSession();
  addBubble("user", text);
  sendEventOn(textWs, {
    type: "conversation.item.create",
    item: { type: "message", role: "user", content: [{ type: "input_text", text }] },
  });
  handleUserTurn(text, textSession);
}

textForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = textInput.value;
  textInput.value = "";
  await sendTextMessage(text);
});

// ---- voice dictation → text, with AI cleanup (Typeless-style) ----
//
// Distinct from the live-conversation mic: this captures speech, transcribes it via
// the same Realtime protocol (reusing the transcript event the live-conversation path
// already relies on), but never triggers a spoken reply -- the transcript instead goes
// through a separate one-shot cleanup call (server-side, see server.py's
// /api/dictation-cleanup) that turns rambling spoken language into clean written text.
// See docs/app-design.md section 3 for the full design and what's untested (this
// sandbox has no real microphone, so this path has never been exercised with real
// speech -- only the cleanup call itself, with hand-written stand-in transcripts, has
// been verified).
const dictateBtn = document.getElementById("dictateBtn");
const dictationRow = document.getElementById("dictationRow");
const dictationStatus = document.getElementById("dictationStatus");
const dictationCancelBtn = document.getElementById("dictationCancelBtn");
const dictationStopBtn = document.getElementById("dictationStopBtn");
const dictationSendBtn = document.getElementById("dictationSendBtn");

const DICTATION_STATE = { IDLE: "idle", RECORDING: "recording", CLEANING: "cleaning" };
let dictationState = DICTATION_STATE.IDLE;
let dictationWs = null;
let dictationCaptureCtx = null;
let dictationProcessorNode = null;
let dictationMicStream = null;
let dictationRawText = "";

// Mutual exclusion across all three input modes (voice session / text session /
// dictation): only one may be active at a time. This isn't just UX tidiness -- text
// session and voice session share the same assistantBubbleEl/assistantHasDelta state
// used to build the streaming reply bubble (docs/app-design.md section 8's shared
// history space), so two of them replying concurrently would corrupt each other's
// bubble. Simplest correct fix is not letting that happen in the first place.
function renderDictationUI() {
  const isDictating = dictationState !== DICTATION_STATE.IDLE;
  composerRow.style.display = isDictating ? "none" : "flex";
  dictationRow.style.display = isDictating ? "flex" : "none";
  dictationStatus.textContent = dictationState === DICTATION_STATE.CLEANING ? "整理中…" : "正在聆听…";
  const busy = dictationState !== DICTATION_STATE.IDLE;
  dictationStopBtn.disabled = dictationState !== DICTATION_STATE.RECORDING;
  dictationSendBtn.disabled = dictationState !== DICTATION_STATE.RECORDING;
  // can't dictate while a live voice conversation OR a text session is connected
  dictateBtn.disabled = state !== STATE.IDLE || textState !== TEXT_STATE.IDLE;
  micBtn.disabled = busy || textState !== TEXT_STATE.IDLE; // can't start voice while dictating or texting
  textInput.disabled = state !== STATE.IDLE; // can't type while a live voice conversation is connected
}

async function startDictation() {
  if (dictationState !== DICTATION_STATE.IDLE) return;
  if (state !== STATE.IDLE || textState !== TEXT_STATE.IDLE) {
    statusEl.textContent = "先结束当前的对话，再用口述输入";
    return;
  }

  dictationRawText = "";
  dictationState = DICTATION_STATE.RECORDING;
  renderDictationUI();

  try {
    dictationMicStream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);
  } catch (e) {
    statusEl.textContent = `麦克风权限失败：${e.message}`;
    dictationState = DICTATION_STATE.IDLE;
    renderDictationUI();
    return;
  }

  dictationCaptureCtx = new (window.AudioContext || window.webkitAudioContext)();
  const source = dictationCaptureCtx.createMediaStreamSource(dictationMicStream);
  dictationProcessorNode = dictationCaptureCtx.createScriptProcessor(4096, 1, 1);
  const silentGain = dictationCaptureCtx.createGain();
  silentGain.gain.value = 0;

  const dictationWsProto = location.protocol === "https:" ? "wss:" : "ws:";
  dictationWs = new WebSocket(`${dictationWsProto}//${location.host}/ws`);
  dictationWs.onopen = () => {
    if (dictationWs.readyState !== WebSocket.OPEN) return;
    dictationWs.send(JSON.stringify({
      type: "session.update",
      session: {
        // modalities: ["text"], not ["audio", "text"] -- dictation never generates a
        // reply at all (create_response stays false the whole recording), spoken or
        // otherwise, and now that "send directly" promotes this connection into the
        // text session (see promoteDictationConnectionToTextSession) rather than the
        // voice session, starting it already in text-only mode means that promotion
        // needs zero modality change, just an instructions patch.
        modalities: ["text"],
        instructions: "",
        input_audio_format: "pcm16",
        output_audio_format: "pcm16",
        // create_response: false is the whole point here -- this session only ever
        // transcribes, it must never generate a reply.
        turn_detection: { type: "server_vad", threshold: 0.5, prefix_padding_ms: 300, silence_duration_ms: 500, create_response: false },
      },
    }));
  };
  dictationWs.onerror = () => {
    statusEl.textContent = "口述录音连接出错";
  };
  dictationWs.onmessage = (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch (e) {
      return;
    }
    if (msg.type === "conversation.item.input_audio_transcription.completed" && msg.transcript) {
      dictationRawText += (dictationRawText ? " " : "") + msg.transcript;
    }
  };

  dictationProcessorNode.onaudioprocess = (event) => {
    if (dictationState !== DICTATION_STATE.RECORDING) return;
    const input = event.inputBuffer.getChannelData(0);
    const downsampled = downsampleTo16k(input, dictationCaptureCtx.sampleRate);
    const pcm16 = floatTo16BitPCM(downsampled);
    if (dictationWs && dictationWs.readyState === WebSocket.OPEN) {
      dictationWs.send(JSON.stringify({ type: "input_audio_buffer.append", audio: int16ToBase64(pcm16) }));
    }
  };
  source.connect(dictationProcessorNode);
  dictationProcessorNode.connect(silentGain);
  silentGain.connect(dictationCaptureCtx.destination);
}

// Split in two so finishDictation can stop listening immediately without necessarily
// closing dictationWs -- the "send directly" path wants to keep that already-open,
// already-handshaken connection around to hand off to the text session instead of
// closing it and making startTextSession() open + handshake a brand new one (see
// promoteDictationConnectionToTextSession below).
function teardownDictationCapture() {
  if (dictationProcessorNode) {
    dictationProcessorNode.disconnect();
    dictationProcessorNode = null;
  }
  if (dictationCaptureCtx) {
    dictationCaptureCtx.close();
    dictationCaptureCtx = null;
  }
  if (dictationMicStream) {
    dictationMicStream.getTracks().forEach((t) => t.stop());
    dictationMicStream = null;
  }
}

function closeDictationWs() {
  if (dictationWs) {
    dictationWs.onopen = null;
    dictationWs.onmessage = null;
    dictationWs.onerror = null;
    dictationWs.close();
    dictationWs = null;
  }
}

function teardownDictationAudio() {
  teardownDictationCapture();
  closeDictationWs();
}

function cancelDictation() {
  teardownDictationAudio();
  dictationRawText = "";
  dictationState = DICTATION_STATE.IDLE;
  renderDictationUI();
}

/**
 * Reuses the dictation module's already-open, already-handshaken WebSocket for the
 * "send directly" path instead of closing it and letting startTextSession() open +
 * handshake a brand new one. Measured when this optimization was first built (back
 * when "direct send" promoted into the voice session, not the text session as it does
 * now): opening a fresh connection was ~2.1s of the ~4.9s total send-directly
 * pipeline -- the single biggest chunk (see docs/app-design.md section 3.4).
 *
 * Promotes into the TEXT session (docs/app-design.md section 8), not the voice
 * session -- a better fit than the original design, since the dictation connection
 * never had live-conversation semantics (mic-hot, spoken replies) to begin with, and
 * as of the modalities: ["text"] change in startDictation(), it's already configured
 * exactly like a text session is. That means promoting it needs *zero* modality
 * change, just the same lightweight instructions patch as before.
 *
 * Only called when dictationWs is still open at this point; callers fall back to the
 * normal startTextSession()-based path otherwise (see sendCleanedDictationText).
 */
async function promoteDictationConnectionToTextSession() {
  setTextState(TEXT_STATE.CONNECTING);

  // startTextSession() always awaits this before connecting -- dictation never has
  // (it doesn't need memory grounding to just transcribe), so if this is the user's
  // very first action in the session, do it now too, in parallel with the instructions
  // patch below, so handleUserTurn's memory search isn't working off a never-synced
  // local cache.
  const pullMemoryPromise = AgentNexusBridge.pullMemory();

  textWs = dictationWs;
  dictationWs = null;

  textLastConnErrorMessage = null;
  textWs.onmessage = (event) => {
    try {
      handleTextSessionEvent(JSON.parse(event.data));
    } catch (e) {
      console.error("bad server message", event.data);
    }
  };
  textWs.onerror = () => {
    textLastConnErrorMessage = "连接失败：WebSocket 出错，请检查网络后重试";
    statusEl.textContent = textLastConnErrorMessage;
  };
  textWs.onclose = () => {
    if (textState !== TEXT_STATE.IDLE) stopTextSession(textLastConnErrorMessage || "连接已断开");
    textLastConnErrorMessage = null;
  };

  // The dictation session is already configured with modalities: ["text"] and the
  // same formats/turn_detection a text session uses -- only `instructions` differs
  // (empty vs BASE_INSTRUCTIONS) -- so this one-field patch is all promoting it needs.
  await textUpdater.updateInstructionsAndWait(BASE_INSTRUCTIONS);
  await pullMemoryPromise;
  ConversationHistory.retryUnsynced();
  setTextState(TEXT_STATE.READY);
}

/** Like sendTextMessage, but reuses dictationWs (still open post-cleanup) when possible. */
async function sendCleanedDictationText(text) {
  text = text.trim();
  if (!text) {
    closeDictationWs();
    return;
  }

  if (textState === TEXT_STATE.IDLE && dictationWs && dictationWs.readyState === WebSocket.OPEN) {
    await promoteDictationConnectionToTextSession();
  } else {
    // dictationWs died in between (server dropped it during cleanup) or a text
    // session is somehow already connected -- fall back to the normal path.
    closeDictationWs();
    if (textState === TEXT_STATE.IDLE) await startTextSession();
  }

  addBubble("user", text);
  sendEventOn(textWs, {
    type: "conversation.item.create",
    item: { type: "message", role: "user", content: [{ type: "input_text", text }] },
  });
  handleUserTurn(text, textSession);
}

/** mode: "edit" fills the text box for review; "send" sends the cleaned text directly. */
async function finishDictation(mode) {
  if (dictationState !== DICTATION_STATE.RECORDING) return;
  // Stop listening right away either way; keep dictationWs open a little longer so
  // the "send" path below can hand it off to the conversation module instead of
  // closing it and reconnecting from scratch.
  teardownDictationCapture();
  const raw = dictationRawText.trim();
  dictationRawText = "";

  if (!raw) {
    closeDictationWs();
    dictationState = DICTATION_STATE.IDLE;
    renderDictationUI();
    return;
  }

  dictationState = DICTATION_STATE.CLEANING;
  renderDictationUI();
  try {
    // Measured latency against this call is highly variable (~17s in some tests,
    // exceeding even the server's own 60s timeout in another) -- a client-side
    // abort is a belt-and-suspenders backstop so a hung request can't leave the UI
    // stuck on "整理中…" forever, same principle as the connection-lifecycle
    // timeouts elsewhere in this file. Slightly longer than the server's own
    // timeout so a real server-side 504 has a chance to arrive normally first.
    const controller = new AbortController();
    const abortTimer = setTimeout(() => controller.abort(), 65000);
    let res;
    try {
      res = await fetch("/api/dictation-cleanup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: raw }),
        signal: controller.signal,
      });
    } finally {
      clearTimeout(abortTimer);
    }
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "整理失败");
    const cleaned = (data.cleaned || "").trim();
    if (mode === "send") {
      await sendCleanedDictationText(cleaned);
    } else {
      closeDictationWs(); // no reuse needed for the edit path -- nothing gets sent
      textInput.value = cleaned;
      textInput.focus();
    }
  } catch (e) {
    closeDictationWs();
    const reason = e.name === "AbortError" ? "响应时间过长（超过 65 秒）" : e.message;
    statusEl.textContent = `口述整理失败：${reason}，请重试`;
  } finally {
    dictationState = DICTATION_STATE.IDLE;
    renderDictationUI();
  }
}

dictateBtn.addEventListener("click", startDictation);
dictationCancelBtn.addEventListener("click", cancelDictation);
dictationStopBtn.addEventListener("click", () => finishDictation("edit"));
dictationSendBtn.addEventListener("click", () => finishDictation("send"));

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

// Voice is chosen per-browser: server tells us the default and the full option list,
// but once the user picks one here it's remembered in localStorage across reloads
// (a per-browser convenience, not synced anywhere).
const VOICE_STORAGE_KEY = "voiceChat.voice";
const voiceSelect = document.getElementById("voiceSelect");

function populateVoiceSelect(options, selected) {
  voiceSelect.innerHTML = "";
  for (const opt of options) {
    const el = document.createElement("option");
    el.value = opt.id;
    el.textContent = opt.label;
    voiceSelect.appendChild(el);
  }
  voiceSelect.value = selected;
}

voiceSelect.addEventListener("change", () => {
  voice = voiceSelect.value;
  try {
    localStorage.setItem(VOICE_STORAGE_KEY, voice);
  } catch (e) {
    // Same storage-unavailable fallback as saveTuning() above.
  }
});

// Barge-in/turn-taking/click tuning knobs, exposed here so testing a new value doesn't
// need a code change + redeploy -- these three (threshold, silence_duration_ms, the
// playback fade) were each hand-tuned from single real-device reports on 2026-08-24 and
// explicitly flagged as needing further adjustment; this panel is for that follow-up
// tuning, not an end-user-facing setting. Applied on the *next* session start (read
// fresh in start()'s session.update and setupPlayback()'s worklet "configure" message),
// not to a session already in progress -- simpler and safer than pushing a live
// session.update or renegotiating the worklet mid-playback, and "change setting, tap
// start again" is a perfectly fast loop for this kind of tuning.
const TUNING_STORAGE_KEY = "voiceChat.tuning";
// threshold 0.55->0.6 and prebufferMs 100->150 (2026-08-27): real-device A/B/C/D test
// across four presets, after the AEC-ordering fix (setupPlayback() before
// getUserMedia()) -- this combination (0.6/900/15/150) came back as the best-performing
// with headphones, basically no self-interruption. Loudspeaker/car-Bluetooth playback
// (this app's actual target scenario) hasn't been retested against the AEC fix
// specifically yet -- headphones structurally avoid the acoustic-echo class of bug
// regardless of whether AEC works, so a clean headphone result doesn't by itself confirm
// the AEC fix helped over a real speaker.
const TUNING_DEFAULTS = { threshold: 0.6, silenceMs: 900, fadeMs: 15, prebufferMs: 150 };

// Explanation text for the "?" tip buttons next to each field above -- threshold/
// silenceMs wording mirrors VoiceChat/Settings/SettingsView.swift's SettingsTip enum
// so both platforms explain the shared server-side params the same way; fadeMs/
// prebufferMs are web-only (no iOS equivalent -- see pcm-player-worklet.js's history
// comments for why AVAudioPlayerNode doesn't need them).
const TUNING_TIPS = {
  threshold:
    "服务端判断“用户正在说话”的灵敏度，范围 0–1。数值越低，越容易把小声音也当成“有人在说话”（更容易打断 AI，但环境噪音也更容易被误判成插话）；数值越高，需要更明显的声音才会被判定为说话（不容易被打断，但小声说话可能被漏判）。推荐范围 0.5–0.6。",
  silenceMs:
    "用户停止说话后，需要静音多久（毫秒）服务端才判定“这一轮说完了”。数值越小，AI 回复更快，但容易在用户换气、思考停顿时就抢话；数值越大，越不容易抢话，但每一轮的等待延迟也会变长。推荐范围 700–1000ms。",
  fadeMs:
    "播放缓冲区在“有声音”和“没声音”之间切换时的淡入淡出时长（毫秒），用来消除生硬跳变产生的“咔哒”声。太短起不到消音效果，太长会让声音听起来发糊。推荐范围 10–20ms。",
  prebufferMs:
    "开始/恢复播放前，先攒够多少毫秒的数据再出声，用来防止网络抖动导致缓冲区反复跑空（表现为“沙哑”“毛躁”的声音质感）。太小起不到防抖作用，太大会让语音播放的启动延迟变得明显。推荐范围 80–150ms。",
};

function loadTuning() {
  try {
    const saved = JSON.parse(localStorage.getItem(TUNING_STORAGE_KEY) || "{}");
    return { ...TUNING_DEFAULTS, ...saved };
  } catch (e) {
    return { ...TUNING_DEFAULTS };
  }
}

let tuning = loadTuning();

const tuningToggle = document.getElementById("tuningToggle");
const tuningPanel = document.getElementById("tuningPanel");
const tuneThreshold = document.getElementById("tuneThreshold");
const tuneSilenceMs = document.getElementById("tuneSilenceMs");
const tuneFadeMs = document.getElementById("tuneFadeMs");
const tunePrebufferMs = document.getElementById("tunePrebufferMs");

function renderTuningInputs() {
  tuneThreshold.value = tuning.threshold;
  tuneSilenceMs.value = tuning.silenceMs;
  tuneFadeMs.value = tuning.fadeMs;
  tunePrebufferMs.value = tuning.prebufferMs;
}

function saveTuning() {
  try {
    localStorage.setItem(TUNING_STORAGE_KEY, JSON.stringify(tuning));
  } catch (e) {
    // Storage full/unavailable (e.g. Safari private mode with "Block All Cookies") --
    // tuning just won't persist across reloads this session, same fallback as
    // history.js/memory.js's persist().
  }
}

renderTuningInputs();

tuningToggle.addEventListener("click", () => {
  tuningPanel.hidden = !tuningPanel.hidden;
});

tuneThreshold.addEventListener("change", () => {
  const v = parseFloat(tuneThreshold.value);
  if (!Number.isNaN(v)) { tuning.threshold = v; saveTuning(); }
});
tuneSilenceMs.addEventListener("change", () => {
  const v = parseInt(tuneSilenceMs.value, 10);
  if (!Number.isNaN(v)) { tuning.silenceMs = v; saveTuning(); }
});
tuneFadeMs.addEventListener("change", () => {
  const v = parseInt(tuneFadeMs.value, 10);
  if (!Number.isNaN(v)) { tuning.fadeMs = v; saveTuning(); }
});
tunePrebufferMs.addEventListener("change", () => {
  const v = parseInt(tunePrebufferMs.value, 10);
  if (!Number.isNaN(v)) { tuning.prebufferMs = v; saveTuning(); }
});

document.getElementById("tuningReset").addEventListener("click", () => {
  tuning = { ...TUNING_DEFAULTS };
  saveTuning();
  renderTuningInputs();
});

// Delegated listener (not one per button) so this keeps working unchanged if the
// panel's markup ever grows more fields -- see the "?" buttons in index.html.
tuningPanel.addEventListener("click", (event) => {
  const btn = event.target.closest(".tipBtn");
  if (!btn) return;
  const tip = TUNING_TIPS[btn.dataset.tip];
  if (tip) alert(tip);
});

(async () => {
  try {
    const res = await fetch("/api/config");
    const config = await res.json();
    voice = localStorage.getItem(VOICE_STORAGE_KEY) || config.voice || voice;
    populateVoiceSelect(config.voices || [{ id: voice, label: voice }], voice);
    if (!config.hasKey) statusEl.textContent = "⚠️ 服务器没有读到 QWEN_API_KEY，请检查 .env";
    else if (!config.hasWorkspaceId) statusEl.textContent = "⚠️ 未配置 QWEN_WORKSPACE_ID，使用共享域名（稳定性较差，见 README）";
  } catch (e) {
    statusEl.textContent = "⚠️ 无法连接本地服务";
  }
})();

renderSuggestions();
setState(STATE.IDLE);

// Refresh the local memory cache when the tab regains focus, on top of the existing
// pull-on-conversation-start -- covers "memory changed on another device/tab while this
// one sat idle in the background" without needing to poll on a timer (docs/roadmap-todo.md,
// "拉取时机加一条 app 回到前台时也拉一次").
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    AgentNexusBridge.pullMemory();
    ConversationHistory.retryUnsynced();
  }
});
