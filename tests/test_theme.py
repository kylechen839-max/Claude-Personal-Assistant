"""The calm blue theme (kwil/web/*.css, the theme-color metas, the PWA
manifest and the generated icons): the old Claude orange is gone, the
installed app's colours match the page, and the palette keeps its WCAG
contrast in light and dark mode."""

import re
import struct
import zlib

import pytest

pytest.importorskip("fastapi")

from kwil import webapp  # noqa: E402

WEB = webapp.STATIC
# Claude's clay/terracotta family that the app and site used before.
OLD_ORANGE = ("#878580", "%23878580", "#c96442", "#d97757", "#e0876a", "#eb9a7c", "#a8492b", "#b4532f",
              "#f6b59a", "#f0a487", "#7a3520")
PAGES = ("index.html", "landing.html", "privacy.html", "terms.html", "waitlist.html")


def themed_files():
    return sorted([*WEB.glob("*.css"), *(WEB / "static").glob("*.css"),
                   *(WEB / name for name in PAGES)])


def tokens(css: str, dark: bool) -> dict:
    """The custom properties of the light :root block or the dark one."""
    blocks = re.findall(r":root\s*\{(.*?)\}", css, re.S)
    body = blocks[1] if dark else blocks[0]
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{3,8})\b", body))


def luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)

    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(int(h[i:i + 2], 16)) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def theme_colors(html: str) -> dict:
    return {scheme: colour for colour, scheme in re.findall(
        r'<meta name="theme-color" content="(#[0-9a-f]+)" media="\(prefers-color-scheme: (\w+)\)">', html)}


def test_no_old_orange_remains():
    for path in themed_files():
        text = path.read_text(encoding="utf-8").lower()
        assert [c for c in OLD_ORANGE if c in text] == [], path.name
    shipped = (webapp.ICON_SVG + str(webapp.MANIFEST)).lower()
    assert [c for c in OLD_ORANGE if c in shipped] == []


def test_manifest_and_theme_color_metas_match_the_page_background():
    app_css = (WEB / "app.css").read_text(encoding="utf-8")
    light, dark = tokens(app_css, False)["bg"], tokens(app_css, True)["bg"]
    index = theme_colors((WEB / "index.html").read_text(encoding="utf-8"))
    assert index == {"light": light, "dark": dark}
    assert webapp.MANIFEST["theme_color"] == index["light"]
    assert webapp.MANIFEST["background_color"] == index["light"]
    site_css = (WEB / "site.css").read_text(encoding="utf-8")
    for name in PAGES[1:]:
        metas = theme_colors((WEB / name).read_text(encoding="utf-8"))
        assert metas == {"light": tokens(site_css, False)["bg"],
                         "dark": tokens(site_css, True)["bg"]}, name


def test_icons_use_the_accent():
    # the logo sits on a gradient around the accent: lighter top left, deeper
    # bottom right; the SVG and the PNG agree on both ends
    accent = tokens((WEB / "app.css").read_text(encoding="utf-8"), False)["accent"]
    assert webapp.ICON_BG == accent
    assert webapp.ICON_TOP in webapp.ICON_SVG and webapp.ICON_BOTTOM in webapp.ICON_SVG
    size = 32
    png = webapp.icon_png(size)
    idat_len = struct.unpack(">I", png[33:37])[0]
    raw = zlib.decompress(png[41:41 + idat_len])
    row = 1 + size * 3

    def pixel(r, c):
        return raw[r * row + 1 + c * 3:r * row + 4 + c * 3]

    def near(px, colour, slack):
        return all(abs(a - int(colour[1 + 2 * i:3 + 2 * i], 16)) <= slack
                   for i, a in enumerate(px))
    assert near(pixel(0, 0), webapp.ICON_TOP, 4)
    assert near(pixel(size - 1, size - 1), webapp.ICON_BOTTOM, 4)
    # the middle barb meets the shaft in white, drawn where _icon_marks puts it
    bx, by = webapp._icon_marks()[5][1][1]
    assert min(pixel(int(by * size / 512), int(bx * size / 512))) > 200
    # the nib's centre is white too
    nib = webapp._icon_marks()[-1][1]
    nx, ny = (sum(p[0] for p in nib) / 3, sum(p[1] for p in nib) / 3)
    assert min(pixel(int(ny * size / 512), int(nx * size / 512))) > 200


def test_icon_svg_and_png_draw_the_same_marks():
    marks = webapp._icon_marks()
    # the shaft, ten swept barbs and the nib
    assert [kind for kind, *_ in marks] == ["line"] * 11 + ["fill"]
    assert webapp.ICON_SVG.count("<path") == len(marks)
    assert webapp.ICON_SVG.count('fill="none"') == 11


@pytest.mark.parametrize("sheet", ["app.css", "site.css"])
@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_palette_meets_wcag_contrast(sheet, dark):
    t = tokens((WEB / sheet).read_text(encoding="utf-8"), dark)
    if dark:  # the dark block only overrides; --on-accent stays white
        t = {**tokens((WEB / sheet).read_text(encoding="utf-8"), False), **t}
    assert t["on-accent"].lower() in ("#fff", "#ffffff")
    assert contrast(t["on-accent"], t["accent"]) >= 4.5
    assert contrast(t["on-accent"], t["accent-strong"]) >= 4.5
    assert contrast(t["accent-ink"], t["bg"]) >= 4.5
    assert contrast(t["accent-ink"], t["surface"]) >= 4.5
    assert contrast(t["text"], t["bg"]) >= 7
    assert contrast(t["text"], t["surface"]) >= 7
    assert contrast(t["muted"], t["bg"]) >= 4.5
    assert contrast(t["muted"], t["surface"]) >= 4.5


def test_data_uri_icon_grey_meets_3_to_1_in_both_modes():
    # a data URI can't follow the dark-mode query: one grey must serve both
    css = (WEB / "app.css").read_text(encoding="utf-8")
    greys = set(re.findall(r"stroke='%23([0-9a-f]{6})'", css))
    assert greys == {"738094"}
    light = tokens(css, False)
    dark = {**light, **tokens(css, True)}
    for t in (light, dark):
        for bg in ("bg", "surface", "surface-2"):
            assert contrast("#738094", t[bg]) >= 3, bg


def test_accent_graphic_meets_3_to_1_in_both_modes():
    css = (WEB / "app.css").read_text(encoding="utf-8")
    blocks = re.findall(r":root\s*\{(.*?)\}", css, re.S)
    for i, body in enumerate(blocks[:2]):
        ref = re.search(r"--accent-graphic:\s*var\(--([\w-]+)\)", body).group(1)
        t = {**tokens(css, False), **tokens(css, bool(i))}
        for bg in ("bg", "surface", "surface-2"):
            assert contrast(t[ref], t[bg]) >= 3, (i, bg)


def test_connections_rows_keep_wcag_contrast_in_both_modes():
    """The menu's Connections rows (connections.js): each service's white
    mark on its brand tile (a graphic: 3:1, the same tile in both modes), and
    the subtitle, status words and "Connect" on the row, its pressed state
    and the sheet (--surface, --surface-2, --bg)."""
    css = (WEB / "app.css").read_text(encoding="utf-8")
    tiles = re.findall(r"\.conn-row\[data-conn=(\w+)\]\s*\{\s*--conn-tile:\s*var\(--([\w-]+)\);"
                       r"\s*--conn-mark:\s*var\(--on-brand\);\s*\}", css)
    services = {"google", "notion", "canvas", "home_assistant", "spotify", "anthropic"}
    assert {s for s, _ in tiles} == services and len(tiles) == len(services)
    light = tokens(css, False)
    assert light["on-brand"] == "#ffffff"
    for name, token in tiles:
        assert token == "brand-" + name.replace("_", "-"), name
        assert contrast(light["on-brand"], light[token]) >= 3, name
    for is_dark in (False, True):
        t = {**tokens(css, False), **tokens(css, is_dark)}
        for fg in ("muted", "accent-ink", "bad-ink", "text"):
            for bg in ("surface", "surface-2", "bg"):
                assert contrast(t[fg], t[bg]) >= 4.5, (is_dark, fg, bg)
        # "Connected" (.state.on) only ever sits on a row; light mode's green
        # on the pressed row's --surface-2 is 4.2:1 for that moment
        assert contrast(t["ok"], t["surface"]) >= 4.5, is_dark


def test_connections_css_uses_tokens_motion_and_no_soft_shadow():
    """The designer's review: the primitives and the Connections component
    take every colour from a token (brand tiles and the scrim included);
    a group is a tone step, not a shadowed card (only the sheet has one);
    the sheet slides 240ms on the agreed curve over a 200ms scrim fade; a
    focus ring from --ring; and a reduced-motion path."""
    css = (WEB / "app.css").read_text(encoding="utf-8")
    block = css[css.index("/* ---------- design-system primitives"):
                css.index("/* reply-voice picker (voice-picker.js) */")]
    # no literal at all: ::backdrop reads the token (Safari 17.4 and later)
    assert ".conn-sheet::backdrop { background: var(--scrim);" in block
    assert re.findall(r"#[0-9a-fA-F]{3,8}\b", block) == []
    assert "html.conn-sheet-open, html.conn-sheet-open body" in block and "overflow: hidden" in block
    assert "rgb(" not in block and "rgba(" not in block
    rules = dict(re.findall(r"\n([^{}\n]+)\{([^{}]*)\}", block))
    assert "shadow" not in rules[".group "]
    shadows = [sel.strip() for sel, body in rules.items() if "--shadow" in body]
    assert shadows == [".conn-sheet"]
    light = tokens(css, False)
    assert {"scrim", "on-brand"} <= set(light) and "scrim" in tokens(css, True)
    assert "--t-sheet: 240ms; --ease-sheet: cubic-bezier(.32,.72,0,1); --t-scrim: 200ms;" in css
    assert "animation: sheet-up var(--t-sheet) var(--ease-sheet)" in block
    assert "animation: scrim-in 200ms ease-out" in block  # --t-scrim, literal for old Safari
    assert "box-shadow: var(--ring)" in block and "box-shadow: var(--ring-inset)" in block
    assert ".group .row:active:not(:disabled) { transform: none; background: var(--surface-2); }" in block
    reduced = block[block.index("@media (prefers-reduced-motion: reduce)"):]
    assert ".conn-sheet[open], .conn-sheet::backdrop { animation: none; }" in reduced


def test_maskable_icon_keeps_the_quill_in_the_safe_zone():
    entries = {e["src"]: e for e in webapp.MANIFEST["icons"]}
    assert entries["/icon-maskable.png"]["purpose"] == "maskable"
    assert "maskable" not in entries["/icon-512.png"].get("purpose", "")
    k = webapp.MASKABLE_SCALE
    for _, pts, width, _, _ in webapp._icon_marks():
        for px, py in pts:
            reach = ((px - 256) ** 2 + (py - 256) ** 2) ** 0.5 * k + width * k / 2
            assert reach <= 0.4 * 512


def test_settings_page_css_is_full_screen_tokens_only_and_calm():
    """The gear's menu is a full-screen Settings page: fixed over the app
    with the safe-area insets, the page colour behind grouped cards, no
    shadow or literal colour, a section with nothing visible hidden by
    :has, nothing scrolling behind it, 44px targets, a focus ring and a
    reduced-motion path."""
    css = (WEB / "app.css").read_text(encoding="utf-8")
    start = css.index("/* ---------- Settings page")
    block = css[start:css.index("\n.log {", start)]
    assert re.findall(r"#[0-9a-fA-F]{3,8}\b", block) == []
    assert "rgb(" not in block and "rgba(" not in block and "--shadow" not in block
    rules = dict(re.findall(r"\n([^{}\n@]+)\{([^{}]*)\}", block))
    page = rules[".menu[open] .menu-body "]
    assert "position: fixed; inset: 0;" in page and "background: var(--bg)" in page
    for side in ("top", "right", "bottom", "left"):
        assert f"env(safe-area-inset-{side})" in page, side
    assert "border" not in page and "shadow" not in page
    assert "max-width: 40rem; margin: 0 auto;" in rules[".settings-page "]
    assert re.search(r"\.set-sec:not\(:has\(> \.group > \.row:not\(\[hidden\]\)[^{]*\{ display: none; \}", block)
    assert "html:has(.menu[open])" in block and "overflow: hidden" in block
    # a Connections sheet still locks the page's own scroller, and the lock
    # comes after (and outranks) the page's overflow-y: auto
    lock = "html.conn-sheet-open .menu[open] .menu-body { overflow: hidden; }"
    assert lock in block and block.index(lock) > block.index(".menu[open] .menu-body {")
    assert "min-width: 44px" in rules["#settings-done "]
    assert "box-shadow: var(--ring)" in rules["#settings-done:focus-visible "]
    reduced = block[block.index("@media (prefers-reduced-motion: reduce)"):]
    assert ".menu[open] .menu-body { animation: none; }" in reduced
    # the old popover and its pop animation are gone, and so are the bands
    assert "animation: pop" not in css and "@keyframes pop" not in css
    assert ".menu-body > " not in css
    assert not re.search(r"#family \{[^}]*background", css) and ".conns {" not in css


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_secondary_text_meets_4_5_to_1_on_every_surface(dark):
    """Captions, group heads and empty states use --muted or --text-2; both
    sit on the page, a card and a pressed or grouped row."""
    css = (WEB / "app.css").read_text(encoding="utf-8")
    t = {**tokens(css, False), **tokens(css, dark)}
    for fg in ("text-2", "muted"):
        for bg in ("bg", "surface", "surface-2"):
            assert contrast(t[fg], t[bg]) >= 4.5, (fg, bg)


def test_scrim_and_on_accent_colours_come_from_tokens():
    """The scrim's raw colour lives only in the :root blocks (a sheet's
    backdrop reads var(--scrim)), and white text on a red or accent button
    reads var(--on-accent)."""
    css = (WEB / "app.css").read_text(encoding="utf-8")
    light, dark = tokens(css, False)["scrim"], tokens(css, True)["scrim"]
    outside = re.sub(r":root\s*\{.*?\}", "", css, flags=re.S)
    assert light not in outside and dark not in outside
    assert "dialog.sheet::backdrop { background: var(--scrim);" in css
    assert ".conn-sheet::backdrop { background: var(--scrim);" in css
    assert ".account-self form .danger { background: var(--bad); color: var(--on-accent); }" in css
    assert ".conv-actions .danger { background: var(--bad); color: var(--on-accent); }" in css


def test_polish_pass_selectors():
    """Phase 7's fixes: no hairline above "New project" when there are no
    projects; space between a Memory card's footnote and the next card;
    History's clock empty state stays on History (Memory's long-term
    notes say one quiet line like a group foot); a History row with no
    snippet centres its icon; day heads look like group heads."""
    css = (WEB / "app.css").read_text(encoding="utf-8")
    assert ".projects-list:not([hidden]) + .projects-new { border-top: 1px solid var(--line); }" in css
    assert ".projects-list + .projects-new" not in css
    assert ".mem-local .group-foot + .group { margin-top: var(--s4); }" in css
    assert ".conv-list:empty + .status.center" not in css
    assert "#history-list:empty + .status.center:not(:empty) {" in css
    assert "#history-list:empty + .status.center:not(:empty)::before {" in css
    rules = dict(re.findall(r"\n([^{}\n@]+)\{([^{}]*)\}", css))
    memo = rules["#memory-status "]
    assert "text-align: left" in memo and "font-size: var(--t-cap)" in memo and "color: var(--muted)" in memo
    assert "align-items: center" in rules[".conv-main:not(:has(.conv-snippet)) "]
    head = rules[".conv-group h2 "]
    assert "font-size: var(--t-cap)" in head and "font-weight: 500" in head and "color: var(--muted)" in head
