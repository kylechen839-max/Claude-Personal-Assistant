"""The Connections rows' brand icons (kwil/web/static/icons/): every row on
the page, and every service the owner's sign-ins can list, has a vendored
Simple Icons file; the page's inline sprite draws exactly that file's path;
and the folder's README names every file, the pinned source and the CC0
licence. Offline: nothing is fetched."""

import re
from pathlib import Path

from kwil import owner_connect

WEB = Path(__file__).resolve().parent.parent / "kwil" / "web"
ICONS = WEB / "static" / "icons"
PAGE = (WEB / "index.html").read_text(encoding="utf-8")
README = (ICONS / "README.md").read_text(encoding="utf-8")


def rows():
    return re.findall(r'<li class="conn-row" id="conn-([a-z_]+)" data-conn="\1" hidden>', PAGE)


def icon_path(svg: str) -> str:
    m = re.fullmatch(r'<svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
                     r'<title>[A-Za-z ]+</title><path d="([0-9A-Za-z .,\-]+)"/></svg>\n', svg)
    assert m, "not a plain Simple Icons file"
    return m.group(1)


def test_every_row_has_an_icon_file_drawn_from_the_sprite():
    ids = rows()
    assert ids == ["google", "notion", "canvas", "home_assistant", "spotify", "anthropic"]
    # connect.js lists the owner's services; each one has its row on the page
    assert set(owner_connect.NAMES) <= set(ids)
    for id_ in ids:
        f = ICONS / f"{id_}.svg"
        assert f.is_file(), id_
        d = icon_path(f.read_text(encoding="utf-8"))
        assert f'<symbol id="icon-{id_}" viewBox="0 0 24 24"><path d="{d}"/></symbol>' in PAGE, id_
        assert PAGE.count(f'<use href="#icon-{id_}"/>') == 2, id_  # the row and its sheet


def test_every_icon_file_is_listed_in_the_readme_with_its_source():
    files = sorted(p.name for p in ICONS.glob("*.svg"))
    assert files and set(files) == {f"{id_}.svg" for id_ in rows()}
    for name in files:
        assert f"| `{name}` |" in README, name
    assert "simple-icons/simple-icons@2f67e5d1a7e5cba43c79a0ebc00abcd95fb0ea30" in README
    assert "CC0 1.0" in README and "trademarks of their owners" in README


def test_icons_are_inline_never_fetched():
    # no remote or runtime fetch: the sprite is in the page; the CSP stays
    # 'self' only, and no icon URL appears in the page or the stylesheet
    css = (WEB / "app.css").read_text(encoding="utf-8")
    for text in (PAGE, css):
        assert "simpleicons" not in text.lower() and "cdn." not in text.lower()
        assert "/static/icons/" not in text
