// AudioWorklet for the voice view: downsample the mic to 16 kHz mono and
// post 512-sample frames (32 ms, what Silero v5 and the endpointer expect).
"use strict";

class VoiceCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.frame = new Float32Array(512);
    this.filled = 0;
    this.acc = 0; // fractional input position carried between blocks
    this.sum = 0;
    this.count = 0;
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    for (let i = 0; i < input.length; i++) {
      // box-filter average over each output sample's input span
      this.sum += input[i];
      this.count++;
      this.acc += 1;
      if (this.acc >= this.ratio) {
        this.acc -= this.ratio;
        this.frame[this.filled++] = this.sum / this.count;
        this.sum = 0;
        this.count = 0;
        if (this.filled === this.frame.length) {
          this.port.postMessage(this.frame.buffer, [this.frame.buffer]);
          this.frame = new Float32Array(512);
          this.filled = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("voice-capture", VoiceCapture);
