# Blockers

Things only Kyle can do. Everything else has been built and tested around them.

## 1. Whisper model could not be downloaded in the build sandbox
- The build environment's network policy blocks `huggingface.co`, so the
  real transcription test (`tests/test_stt.py::test_transcribe_generated_speech`)
  is **skipped** there. It is not faked: on your PC the model downloads on
  first use (~150 MB) and the test runs for real.
- **To do:** run `pytest` once on your PC; confirm 0 skips in `test_stt.py`.

## 2. Picovoice access key and custom "Hey Claude" wake word
- Porcupine needs a free account key, and a custom phrase must be trained in
  the Picovoice Console (only you can sign up).
- **To do:**
  1. Sign up at https://console.picovoice.ai, copy your AccessKey into `.env`
     as `PICOVOICE_ACCESS_KEY=...`.
  2. In the console go to Porcupine, type the phrase "Hey Claude", train it
     for **Windows** (and later again for **Raspberry Pi**), download the
     `.ppn` files, and put them in `keywords/` (e.g.
     `keywords/Hey-Claude_en_windows.ppn`). Point `--keyword-path` (or the
     config file) at it.
- Until then the pipeline was tested with a scripted stand-in detector, and
  the app falls back to the built-in keyword "computer" if no `.ppn` is found
  (it still needs the access key).
