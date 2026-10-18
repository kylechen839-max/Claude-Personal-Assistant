# Vendored voice-activity-detection files

Served by the app itself at `/static/vad/` (no CDN, so the page's CSP stays
`'self'`). Loaded only when the voice conversation view starts.

| File | From | License |
|---|---|---|
| `ort.wasm.min.js`, `ort-wasm-simd-threaded.mjs`, `ort-wasm-simd-threaded.wasm` | `onnxruntime-web` 1.22.0 (npm), Microsoft | MIT |
| `silero_vad_v5.onnx` | `@ricky0123/vad-web` 0.0.31 (npm), the Silero VAD v5 model by Silero Team (snakers4/silero-vad) | MIT (model), ISC (vad-web) |

The small Silero wrapper in `assistant/web/voice.js` follows vad-web's
`Silero` class (512-sample frames at 16 kHz, a 64-sample context window).

SHA-256:

```
65e09376df69107e881b5c34d2d37aed333a366b6d941073ec518168e269b87d  ort.wasm.min.js
30dd851d9c00622940500f71ddd2ff8820c5cb65270816080175b958705385a8  ort-wasm-simd-threaded.mjs
71aef04959c5c1b6de461b6538e2058e306610034a85aad2742d0c7fd4533fe4  ort-wasm-simd-threaded.wasm
2623a2953f6ff3d2c1e61740c6cdb7168133479b267dfef114a4a3cc5bdd788f  silero_vad_v5.onnx
```

To update: `npm pack onnxruntime-web@<v> @ricky0123/vad-web@<v>`, copy the
same files from their `dist/`, update the hashes here and in
`tests/test_voice_ws.py`.
