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
let processorNode = null;
let nextPlayTime = 0;
let activeSources = [];
let micAnalyser = null; // taps the mic capture graph, read while LISTENING -- see updateVoiceOrb()
let playAnalyser = null; // taps the playback graph, read while SPEAKING -- see updateVoiceOrb()
let orbAnimationId = null;
// Playback scheduling margin ahead of playCtx.currentTime (see playPCM16Chunk) and the
// fade applied at each chunk's edges -- both explained where they're used below.
const PLAYBACK_LOOKAHEAD_SEC = 0.08;
const CHUNK_FADE_SEC = 0.003;
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

// ---- playback: schedule streamed 24kHz PCM16 chunks back-to-back for gapless audio ----
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
// 2026-08-24: real-device testing found a regression from this same routing change --
// audible clicking/ticking noise ("哒哒哒哒哒") during assistant playback that wasn't
// there before. Likely cause: forcing the AudioContext itself to run at 24000Hz (to
// match the incoming PCM16 chunks 1:1, avoiding a resample inside the Web Audio graph)
// means the MediaStreamAudioDestinationNode's output -- and therefore what the <audio>
// element actually plays -- is also 24000Hz, a non-native rate for most audio hardware
// (commonly 48000Hz). Direct-to-.destination playback goes through Chromium's mature,
// glitch-free output resampler; MediaStreamTrack/<audio>-element playback goes through
// a different pipeline (built primarily for WebRTC audio) that's known to click/pop on
// non-native sample rates. Fix: let the context run at the browser's native rate (don't
// force sampleRate) -- createBuffer() still declares each chunk's real rate (24000)
// explicitly, and the Web Audio spec has the source node auto-resample on playback, so
// no behavior changes except which resampler does the work. Not acoustically verified
// in this sandbox (same caveat as the AEC fix above) -- needs real-device confirmation
// that the clicking is actually gone.
function setupPlayback() {
  playCtx = new (window.AudioContext || window.webkitAudioContext)();
  nextPlayTime = 0;
  playDestNode = playCtx.createMediaStreamDestination();
  if (!playElement) {
    playElement = document.createElement("audio");
    playElement.autoplay = true;
    playElement.style.display = "none";
    document.body.appendChild(playElement);
  }
  playElement.srcObject = playDestNode.stream;

  // Gives updateVoiceOrb() something to read while SPEAKING -- each chunk's source node
  // fans out to this in addition to playDestNode, doesn't affect actual playback.
  playAnalyser = playCtx.createAnalyser();
  playAnalyser.fftSize = 256;
}

// Real-device testing (2026-08-24) found clicking ("哒哒哒") recurring later in long
// assistant responses even after the native-sample-rate fix above. Root cause: this
// function used to schedule each chunk at `Math.max(nextPlayTime, currentTime)` -- fine
// while nextPlayTime stays comfortably ahead of currentTime, but network jitter or a GC
// pause can let currentTime catch up to nextPlayTime with zero margin left. From that
// point on, every chunk that arrives even slightly late snaps straight to currentTime,
// which means the previous chunk's buffer has *already* finished playing and the output
// has gone to silence before this one starts -- a hard silence-to-signal edge, which is
// exactly what a "click" is. It doesn't happen at the start of a response (there's
// nothing to fall behind yet) or on a fast/idle network (nextPlayTime never gets caught),
// which matches "shows up partway through longer turns" rather than every time.
//
// Two independent fixes, both cheap:
// - A small lookahead margin (PLAYBACK_LOOKAHEAD_SEC) whenever scheduling would otherwise
//   catch down to currentTime -- gives a late-arriving next chunk room to land on time
//   instead of underrunning. Applies uniformly to the first chunk of a response too
//   (nextPlayTime starts at/near 0 there), which doubles as the "buffer ~80ms before
//   playing" jitter-buffer behavior.
// - A short linear fade-in/out per chunk (CHUNK_FADE_SEC) via a GainNode, so even a
//   residual few-sample misalignment at a chunk boundary (adjacent chunks are arbitrary
//   cut points in a continuous waveform, not guaranteed to meet at a zero-crossing) is
//   smoothed instead of an audible discontinuity.
// Not acoustically re-verified in this sandbox (no real audio hardware here, same caveat
// as the fixes above) -- needs real-device confirmation.
function playPCM16Chunk(base64) {
  const int16 = base64ToInt16(base64);
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / (int16[i] < 0 ? 0x8000 : 0x7fff);

  const buffer = playCtx.createBuffer(1, float32.length, 24000);
  buffer.copyToChannel(float32, 0);

  const source = playCtx.createBufferSource();
  source.buffer = buffer;

  const gain = playCtx.createGain();
  source.connect(gain);
  gain.connect(playDestNode);
  if (playAnalyser) gain.connect(playAnalyser);

  const caughtUp = nextPlayTime <= playCtx.currentTime;
  const startAt = caughtUp ? playCtx.currentTime + PLAYBACK_LOOKAHEAD_SEC : nextPlayTime;

  const fade = Math.min(CHUNK_FADE_SEC, buffer.duration / 2);
  gain.gain.setValueAtTime(0, startAt);
  gain.gain.linearRampToValueAtTime(1, startAt + fade);
  gain.gain.setValueAtTime(1, startAt + buffer.duration - fade);
  gain.gain.linearRampToValueAtTime(0, startAt + buffer.duration);

  source.start(startAt);
  nextPlayTime = startAt + buffer.duration;

  activeSources.push(source);
  source.onended = () => {
    activeSources = activeSources.filter((s) => s !== source);
  };
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
  activeSources.forEach((s) => {
    try { s.stop(); } catch (e) { /* already stopped */ }
  });
  activeSources = [];
  nextPlayTime = playCtx ? playCtx.currentTime : 0;
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
const voiceSession = { getWs: () => ws, updater: voiceUpdater, pendingUserText: null };
const textSession = { getWs: () => textWs, updater: textUpdater, pendingUserText: null };

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
async function handleUserTurn(text, session) {
  // Pushing this turn to AgentNexus (with sync-status tracking + retry) is handled by
  // ConversationHistory.add(), triggered from addBubble("user", ...) at every call site
  // right before this function runs -- not duplicated here.

  // Paired with the assistant's reply once it's done (see finalizeAssistantTurn) to run
  // memory extraction on the complete exchange -- extraction needs both halves, not
  // just what the user said. Set unconditionally (even for a save-intent turn, which
  // finalizeAssistantTurn skips by re-checking SaveIntent.detect itself) so there's one
  // place deciding that, not two.
  session.pendingUserText = text;

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
        handleUserTurn(json.transcript, voiceSession);
      }
      break;
    case "input_audio_buffer.speech_started":
      // Barge-in: Qwen reports interrupt_response support server-side too. Stopping
      // local playback alone isn't enough -- without response.cancel the server keeps
      // generating/streaming after the user interrupts, and any response.audio.delta
      // that arrives after this point would just restart playback. Matches iOS's
      // onSpeechStarted (interruptPlayback + cancelResponse), which already did both.
      stopPlayback();
      sendEvent({ type: "response.cancel" });
      assistantBubbleEl = null;
      assistantHasDelta = false;
      setState(STATE.LISTENING);
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

    try {
      micStream = await navigator.mediaDevices.getUserMedia(MIC_CONSTRAINTS);
    } catch (e) {
      statusEl.textContent = `麦克风权限失败：${e.message}（仍可以打字对话）`;
      micStream = null;
    }

    setupPlayback();

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
        // threshold raised from the default 0.5 to 0.65 on 2026-08-24 -- real-device
        // testing found the assistant was very easily barge-in'd by background noise
        // (the user's own description: "随便一些其他的声音，可能它就停止说话或者中断
        // 了"). This only touches the voice session's turn_detection -- the one wired
        // to speech_started -> response.cancel (see handleServerEvent below) -- since
        // that's the only session where "being interrupted" is user-visible; the text
        // session and dictation's turn_detection govern something else (when to commit
        // the input buffer for transcription) and weren't touched. Not re-tuned against
        // real speech yet (docs/roadmap-todo.md's "VAD 阈值调优" item) -- this is a
        // single bump based on one report, not a scientifically chosen value; may need
        // another round if 0.65 turns out to be too high (real speech gets missed) or
        // still too low (still over-triggers).
        turn_detection: {
          type: "server_vad",
          threshold: 0.65,
          prefix_padding_ms: 300,
          silence_duration_ms: 500,
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
  localStorage.setItem(VOICE_STORAGE_KEY, voice);
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
