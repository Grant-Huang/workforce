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
//
// 2026-08-24, round 2: real-device testing found a click specifically at the *end* of
// each assistant turn (not mid-stream). Root cause: this file originally jumped straight
// from real samples to hard 0 the moment the buffer ran dry -- fine as a description of
// "silence", but a hard jump from a nonzero sample to exactly 0 is itself a waveform
// discontinuity, i.e. a click. Mid-stream this was rare (the lookahead margin upstream
// mostly prevents the buffer ever running dry before the next chunk arrives), but the
// *end* of a response is a guaranteed underrun -- there's no next chunk coming -- so a
// click there was effectively unavoidable with a hard cutoff. FADE_SAMPLES below ramps
// the last real sample down to silence instead (and symmetrically ramps back up when
// data resumes after any underrun, mid-stream or otherwise, for the same reason in
// reverse).
//
// 2026-08-24, round 3: clicking persisted (confirmed not a caching artifact -- still
// present in an incognito tab) at both the end of a response AND mid-stream, matching a
// real-device observation that the assistant sometimes speaks a reply as what sounds
// like two segments with a brief pause between them (whether that's the model actually
// generating in separate bursts, or just a natural inter-sentence pause outrunning
// generation speed, the effect on this buffer is the same either way: a genuine
// underrun, not just at the very end). The original FADE_SAMPLES (64, ~1.3ms at 48kHz)
// was a guess flagged as unverified from the start, and 1.3ms turned out to be too
// short to reliably read as smooth rather than a soft click -- typical anti-click fades
// in audio practice run 10-20ms. Computed from the worklet's actual sample rate (not a
// hardcoded sample count) so it's ~15ms regardless of whether the context runs at
// 44100/48000/other native rates.
const DEFAULT_FADE_MS = 15;
// 2026-08-24, round 4: "沙哑" (raspy/hoarse), not discrete clicks, reported during
// otherwise-continuous speech. Root cause: this worklet never had a safety margin --
// the discrete-AudioBufferSourceNode-scheduling approach this replaced buffered
// PLAYBACK_LOOKAHEAD_SEC (80ms) before starting playback (see git history), and that
// got dropped in the rewrite instead of carried over. Without it, playback starts
// consuming the instant *any* data arrives, sitting right at the edge of underrun for
// the whole session -- ordinary network jitter is enough to trigger the fade-out/silence
// /fade-in cycle from round 2/3 repeatedly, in quick succession. One such cycle sounds
// like a soft click; many of them close together, several times a second, sound like
// texture/roughness in the voice instead of discrete pops -- same mechanism, different
// perceived result at different trigger rates. DEFAULT_PREBUFFER_MS restores that
// safety margin, just inside the worklet instead of the old scheduling code.
const DEFAULT_PREBUFFER_MS = 100;

class PCMPlayerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = new Float32Array(0);
    this._readIndex = 0;
    this._lastSample = 0;
    this._wasSilent = true; // fade the very first chunk in too, not just mid-stream recoveries
    // Overridable via app.js's tuning panel (see the "configure" message below) instead
    // of being fixed at module-load time -- lets a new fade duration be tested without a
    // redeploy. Falls back to this default if "configure" is never sent (or hasn't
    // arrived yet when playback starts).
    this._fadeSamples = Math.round(sampleRate * (DEFAULT_FADE_MS / 1000));
    this._prebufferSamples = Math.round(sampleRate * (DEFAULT_PREBUFFER_MS / 1000));
    this.port.onmessage = (event) => {
      if (event.data.type === "push") {
        this._append(event.data.samples);
      } else if (event.data.type === "clear") {
        // Barge-in (input_audio_buffer.speech_started) -- discard whatever's still
        // queued instead of letting it keep playing after the user starts talking.
        this._buffer = new Float32Array(0);
        this._readIndex = 0;
        this._wasSilent = true; // re-arm the prebuffer gate below for whatever plays next
      } else if (event.data.type === "configure") {
        const { fadeMs, prebufferMs } = event.data;
        if (typeof fadeMs === "number" && fadeMs > 0) {
          this._fadeSamples = Math.max(1, Math.round(sampleRate * (fadeMs / 1000)));
        }
        if (typeof prebufferMs === "number" && prebufferMs >= 0) {
          this._prebufferSamples = Math.round(sampleRate * (prebufferMs / 1000));
        }
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

    if (this._wasSilent && available < this._prebufferSamples) {
      // Building up a safety margin before (re)starting playback -- see
      // DEFAULT_PREBUFFER_MS above. Only gates right after a drain-to-silence moment
      // (_wasSilent); once real playback is underway this doesn't hold up ordinary
      // frames waiting for a full prebuffer on every call.
      output.fill(0);
      return true;
    }

    const toCopy = Math.min(available, output.length);

    const fadeSamples = this._fadeSamples;
    for (let i = 0; i < output.length; i++) {
      if (i < toCopy) {
        let sample = this._buffer[this._readIndex + i];
        if (this._wasSilent) {
          const fadeIn = Math.min(i + 1, fadeSamples) / fadeSamples;
          sample *= fadeIn;
          if (i + 1 >= fadeSamples) this._wasSilent = false;
        }
        output[i] = sample;
        this._lastSample = sample;
      } else {
        // Underrun -- most commonly the guaranteed one at the very end of a response,
        // but also any brief mid-stream gap. Fade the last real sample down instead of
        // jumping straight to 0.
        const samplesIntoUnderrun = i - toCopy;
        const fadeOut = Math.max(0, 1 - (samplesIntoUnderrun + 1) / fadeSamples);
        output[i] = this._lastSample * fadeOut;
        if (samplesIntoUnderrun + 1 >= fadeSamples) {
          this._lastSample = 0;
          this._wasSilent = true;
        }
      }
    }

    this._readIndex += toCopy;
    return true; // keep this processor alive for the life of the AudioContext
  }
}

registerProcessor("pcm-player", PCMPlayerProcessor);
