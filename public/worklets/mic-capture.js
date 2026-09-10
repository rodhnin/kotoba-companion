// Mic-capture AudioWorklet for the LOCAL voice mode (lib/local-voice.ts): resamples the context-rate
// float32 mic signal (typically 44.1/48 kHz) down to the backend's PCM rate (16 kHz s16le mono) and posts
// fixed-size ~250 ms frames. Plain JS on purpose — loaded straight from /worklets/ via addModule(), no bundler.
class PcmDownsampler extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    const targetRate = opts.targetRate || 16000;
    this.frameLen = Math.round(targetRate * (opts.frameSeconds || 0.25));
    this.frame = new Int16Array(this.frameLen);
    this.framePos = 0;
    // `sampleRate` is the AudioWorkletGlobalScope's context rate — the REAL capture rate, whatever the device uses.
    this.ratio = sampleRate / targetRate;
    this.pos = 0; // fractional read cursor into the pending input
    this.pending = new Float32Array(0); // unconsumed input samples carried between process() calls
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch || ch.length === 0) return true;
    const prev = this.pending;
    const input = new Float32Array(prev.length + ch.length);
    input.set(prev, 0);
    input.set(ch, prev.length);

    // Linear-interpolation resample: adequate for speech STT at 3:1 decimation, zero dependencies.
    let pos = this.pos;
    while (pos + 1 < input.length) {
      const i = Math.floor(pos);
      const s = input[i] + (input[i + 1] - input[i]) * (pos - i);
      const v = Math.max(-1, Math.min(1, s));
      this.frame[this.framePos++] = v < 0 ? v * 0x8000 : v * 0x7fff;
      if (this.framePos === this.frameLen) {
        this.port.postMessage(this.frame.buffer, [this.frame.buffer]);
        this.frame = new Int16Array(this.frameLen); // the old buffer was transferred — allocate a fresh one
        this.framePos = 0;
      }
      pos += this.ratio;
    }
    // The cursor can exit past the input end (up to ratio-1 samples). slice() would clamp silently
    // while `pos - keep` would not, snapping the cursor backward every quantum — a 0.78% rate error
    // plus a phase glitch at the quantum rate. Clamp keep and carry the overshoot in pos instead.
    const keep = Math.min(Math.floor(pos), input.length);
    this.pending = input.slice(keep);
    this.pos = pos - keep;
    return true;
  }
}

registerProcessor("pcm-downsampler", PcmDownsampler);
