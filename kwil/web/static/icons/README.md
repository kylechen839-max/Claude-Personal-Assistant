# Service icons

The brand marks on the Settings menu's Connections rows (`index.html`
draws them from an inline `<symbol>` sprite with the same paths;
`tests/test_connection_icons.py` checks the two agree).

- **Source:** [Simple Icons](https://github.com/simple-icons/simple-icons),
  `simple-icons/simple-icons@2f67e5d1a7e5cba43c79a0ebc00abcd95fb0ea30`
  (tag 16.36.0), files `icons/<slug>.svg`. Each file here keeps only the
  original `viewBox`, `<title>` and single `<path>`.
- **Licence:** the Simple Icons files are CC0 1.0 Universal (public domain
  dedication): <https://github.com/simple-icons/simple-icons/blob/develop/LICENSE.md>.
- **Trademarks:** the marks themselves are trademarks of their owners
  (Google LLC, Notion Labs, Instructure, Nabu Casa / the Home Assistant
  project, Spotify AB, Anthropic PBC). Kwil uses each only to name the
  service a row connects to; no endorsement is implied.

| File | Simple Icons slug | Brand hex | Tile in the app |
| --- | --- | --- | --- |
| `google.svg` | `google` | `#4285F4` | `#4285F4` |
| `notion.svg` | `notion` | `#000000` | `#000000` |
| `canvas.svg` | `canvas` | `#E72429` | `#E72429` |
| `home_assistant.svg` | `homeassistant` | `#18BCF2` | `#0F86C2` |
| `spotify.svg` | `spotify` | `#1ED760` | `#179443` |
| `anthropic.svg` | `anthropic` | `#191919` | `#191919` |

Every icon is white on a tile of its brand colour. Home Assistant's and
Spotify's light brand colours are darkened only as far as a white mark
needs for 3:1 (`tests/test_theme.py`). Simple Icons' Google mark is
single-colour; it has no four-colour "G".

A service with no file here gets a letter tile instead.
