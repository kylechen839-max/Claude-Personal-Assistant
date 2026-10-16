# Blockers

Things only Kyle can do. Everything else has been built and tested around them.

## 1. Whisper model could not be downloaded in the build sandbox
- The build environment's network policy blocks `huggingface.co`, so the
  real transcription test (`tests/test_stt.py::test_transcribe_generated_speech`)
  is **skipped** there. It is not faked: on your PC the model downloads on
  first use (~150 MB) and the test runs for real.
- **To do:** run `pytest` once on your PC; confirm 0 skips in `test_stt.py`.
