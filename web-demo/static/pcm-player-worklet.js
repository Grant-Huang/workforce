// AudioWorkletProcessor for streamed PCM playback -- a small ring buffer that a
// continuous audio callback reads from at the hardware's own clock, instead of
// scheduling a separate AudioBufferSourceNode per network chunk.
//
// Real-device testing (2026-08-24) found clicking ("哒哒哒") persisting even after
// giving the discrete-scheduling approach (in app.js's old playPCM16Chunk) a lookahead
// margin and per-chunk fades. That approach is fundamentally fragile to this class of
// bug: every chunk boundary is a moment where two separately-scheduled nodes' timing has
// to line up exactly, and any imprecision -- network jitter, GC pauses, or just
// non-atomic scheduling of many nodes over a long response -- produces an audible edge.
// A worklet sidesteps the whole problem: there's only ever one continuously-running
// node, chunks are just appended to its buffer whenever they arrive, and an underrun
// (no data available yet) produces brief silence rather than a click, because there's no
// scheduling boundary to misalign in the first place.
//
// See app.js's setupPlayback()/playPCM16Chunk() for how chunks get here (base64 PCM16 ->
// Float32 -> resampled to the AudioContext's native rate on the main thread, since this
// processor has no resampler of its own).
class PCMPlayerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = new Float32Array(0);
    this._readIndex = 0;
    this.port.onmessage = (event) => {
      if (event.data.type === "push") {
        this._append(event.data.samples);
      } else if (event.data.type === "clear") {
        // Barge-in (input_audio_buffer.speech_started) -- discard whatever's still
        // queued instead of letting it keep playing after the user starts talking.
        this._buffer = new Float32Array(0);
        this._readIndex = 0;
      }
    };
  }

  _append(samples) {
    // Drop the already-consumed prefix rather than ever compacting/shifting a growing
    // buffer -- keeps this cheap across a long response with many small chunks.
    const remaining = this._buffer.subarray(this._readIndex);
    const merged = new Float32Array(remaining.length + samples.length);
    merged.set(remaining, 0);
    merged.set(samples, remaining.length);
    this._buffer = merged;
    this._readIndex = 0;
  }

  process(_inputs, outputs) {
    const output = outputs[0][0]; // mono
    if (!output) return true;
    const available = this._buffer.length - this._readIndex;
    const toCopy = Math.min(available, output.length);
    for (let i = 0; i < toCopy; i++) {
      output[i] = this._buffer[this._readIndex + i];
    }
    for (let i = toCopy; i < output.length; i++) {
      output[i] = 0; // underrun -- silence, not a click
    }
    this._readIndex += toCopy;
    return true; // keep this processor alive for the life of the AudioContext
  }
}

registerProcessor("pcm-player", PCMPlayerProcessor);
