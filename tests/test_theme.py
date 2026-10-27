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
