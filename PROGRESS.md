# Progress

Run all tests with `pytest` (from the project root, venv active).

## Stage 1 - Core chat ✅
- `assistant/chat.py`: `ClaudeChat` keeps conversation history, adds a short
  spoken-style system prompt plus the current local date/time, and calls the
  Messages API. Model comes from `CLAUDE_MODEL` (default `claude-sonnet-4-5`).
  A failed request is rolled back so history stays valid; long histories are
  trimmed so they always start with a plain user turn.
- `assistant/cli.py`: text-mode loop (`python -m assistant --mode text`).
- Tested by `tests/test_chat.py` with a mocked Anthropic client (history,
  system prompt, error rollback, trimming, exit words, the text loop).
