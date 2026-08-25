// AudioWorkletProcessor for mic capture -- replaces the old ScriptProcessorNode(4096, 1,
// 1) in app.js's start(). ScriptProcessorNode's onaudioprocess callback runs directly on
// the main thread (a documented reason it's deprecated in favor of AudioWorkletNode), and
// it was doing real work every ~85ms for the entire duration of every call: downsample to
// 16kHz, convert to PCM16, base64-encode, WebSocket.send. Real-device report (2026-08-25):
// after switching playback to an AudioWorklet ring buffer (pcm-player-worklet.js) and
// tuning its fadeMs/prebufferMs extensively, the playback was still persistently raspy/
// 沙哑 with "基本听不出来改善" even at fadeMs=30 -- i.e. the fade *shape* wasn't the
// problem. The remaining plausible explanation is underrun *frequency*: any main-thread
// congestion (this mic callback's own downsample/encode/send work included) delays how
// promptly newly-arrived response.audio.delta chunks get pushed into the playback
// worklet's ring buffer via port.postMessage, which is exactly what would cause frequent,
// closely-spaced underruns no fade-duration tuning could mask -- the underruns just keep
// happening faster than any fade window, sounding like continuous roughness instead of
// discrete clicks.
//
// This processor only moves *where render-quantum-by-render-quantum samples get pulled
// off the mic* onto the dedicated audio-rendering thread -- the downsample/PCM16/base64/
// send work stays on the main thread (kept as-is deliberately, to keep this change small
// and testable), just triggered by a batched postMessage every BATCH_SAMPLES instead of
// running inline inside an audio-thread-adjacent callback.
//
// Not acoustically verified in this sandbox (no real audio hardware here, same caveat as
// every playback fix in this file) -- needs real-device confirmation.
const BATCH_SAMPLES = 4096; // same cadence as the ScriptProcessorNode this replaces

class MicCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._batch = new Float32Array(BATCH_SAMPLES);
    this._filled = 0;
  }

  process(inputs) {
    const input = inputs[0][0]; // mono; may be undefined for a quantum with no input yet
    if (!input) return true;

    let offset = 0;
    while (offset < input.length) {
      const room = BATCH_SAMPLES - this._filled;
      const take = Math.min(room, input.length - offset);
      this._batch.set(input.subarray(offset, offset + take), this._filled);
      this._filled += take;
      offset += take;

      if (this._filled === BATCH_SAMPLES) {
        const batch = this._batch;
        this._batch = new Float32Array(BATCH_SAMPLES);
        this._filled = 0;
        this.port.postMessage(batch, [batch.buffer]); // transfer, not copy
      }
    }
    return true; // keep this processor alive for the life of the AudioContext
  }
}

registerProcessor("mic-capture", MicCaptureProcessor);
