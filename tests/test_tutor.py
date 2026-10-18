"""Homework tutor: image checks (type, size, EXIF), the vision call with a
mocked client, and reading LaTeX aloud."""

import base64
import io

import pytest
from PIL import Image

from assistant.mathspeech import speak_latex, speakable
from assistant.tutor import (ImageRejected, TUTOR_PROMPT, TutorSession,
                             prepare_image)
from tests.conftest import make_response, text_block


def photo(fmt="JPEG", size=(800, 600), exif=True, color=(200, 30, 30)):
    img = Image.new("RGB", size, color)
    out = io.BytesIO()
    kwargs = {}
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "PhoneMaker"          # Make
        ex[0x0131] = "SecretSoftware 1.0"  # Software
        ex[0x0112] = 6                     # Orientation: rotate 90
        ex[0x8825] = {2: (33.0, 45.0, 0.0)}  # GPS latitude
        kwargs["exif"] = ex.tobytes()
    img.save(out, fmt, **kwargs)
    return out.getvalue()


# ---------- images ----------
def test_exif_is_stripped_and_photo_turned_upright():
    raw = photo(size=(800, 600))
    assert b"SecretSoftware" in raw
    clean, media_type = prepare_image(raw, "image/jpeg")
    assert media_type == "image/jpeg" and b"SecretSoftware" not in clean
    with Image.open(io.BytesIO(clean)) as img:
        assert not img.getexif()
        assert img.size == (600, 800)  # orientation 6 applied, then dropped


def test_large_photos_are_shrunk_to_1600():
    clean, _ = prepare_image(photo(size=(4000, 3000), exif=False), "image/jpeg")
    with Image.open(io.BytesIO(clean)) as img:
        assert max(img.size) == 1600


@pytest.mark.parametrize("fmt,media", [("PNG", "image/png"), ("WEBP", "image/jpeg")])
def test_png_and_webp_are_accepted(fmt, media):
    clean, media_type = prepare_image(photo(fmt, exif=False), f"image/{fmt.lower()}")
    assert media_type == media


@pytest.mark.parametrize("data,declared,message", [
    (b"", "image/jpeg", "empty"),
    (photo(exif=False), "image/gif", "Only JPEG"),
    (photo(exif=False), "application/pdf", "Only JPEG"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "image/png", "couldn't be read"),
    (b"%PDF-1.7 not an image", None, "couldn't be read"),
    (photo(exif=False)[:200], "image/jpeg", "couldn't be read"),
])
def test_bad_uploads_are_rejected(data, declared, message):
    with pytest.raises(ImageRejected, match=message):
        prepare_image(data, declared)


def test_gif_content_with_a_jpeg_label_is_rejected():
    out = io.BytesIO()
    Image.new("RGB", (10, 10)).save(out, "GIF")
    with pytest.raises(ImageRejected, match="Only JPEG"):
        prepare_image(out.getvalue(), "image/jpeg")


def test_size_cap():
    with pytest.raises(ImageRejected, match="over 1 MB"):
        prepare_image(b"x" * 1_000_001, "image/jpeg", max_bytes=1_000_000)


def test_pixel_bomb_is_rejected(monkeypatch):
    monkeypatch.setattr("assistant.tutor.MAX_PIXELS", 100)
    with pytest.raises(ImageRejected, match="too many pixels"):
        prepare_image(photo(size=(20, 20), exif=False), "image/jpeg")


# ---------- the vision call ----------
def test_tutor_sends_photo_and_question_socratically(fake_client):
    fake_client.queue = [make_response(text_block(
        "What happens if you divide both sides by $2$? Then $x = \\frac{6}{2}$."))]
    session = TutorSession(client=fake_client, model="m")
    image = prepare_image(photo(exif=False), "image/jpeg")
    reply = session.ask("How do I solve $2x = 6$?", image=image)
    call = fake_client.calls[0]
    assert call["system"] == TUTOR_PROMPT and "Socratic" in call["system"]
    assert "full solution only when he explicitly asks" in call["system"].replace(
        "full worked solution", "full solution")
    content = call["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/jpeg"
    assert base64.b64decode(content[0]["source"]["data"]) == image[0]
    assert content[1] == {"type": "text", "text": "How do I solve $2x = 6$?"}
    assert reply.text.endswith("$x = \\frac{6}{2}$.")
    assert reply.spoken == ("What happens if you divide both sides by 2? Then "
                            "x equals 6 over 2.")


def test_full_solution_only_when_asked_and_follow_ups_keep_context(fake_client):
    fake_client.queue = [make_response(text_block("Try isolating x.")),
                         make_response(text_block("Close: check the sign.")),
                         make_response(text_block("Full: $x = 3$."))]
    session = TutorSession(client=fake_client)
    session.ask("Help", image=prepare_image(photo(exif=False), "image/jpeg"))
    session.ask("Is it x = -3?")
    session.ask("Just show me the full solution", full_solution=True)
    assert fake_client.calls[1]["system"] == TUTOR_PROMPT
    assert "complete worked solution" in fake_client.calls[2]["system"]
    msgs = fake_client.calls[2]["messages"]
    assert msgs[0]["content"][0]["type"] == "image"  # the photo stays in context
    assert [m["role"] for m in msgs] == ["user", "assistant", "user",
                                         "assistant", "user"]


def test_a_new_photo_replaces_the_old_one(fake_client):
    fake_client.queue = [make_response(text_block("a")), make_response(text_block("b"))]
    session = TutorSession(client=fake_client)
    session.ask("one", image=(b"1", "image/jpeg"))
    session.ask("two", image=(b"2", "image/png"))
    images = [b for m in fake_client.calls[1]["messages"] if m["role"] == "user"
              for b in m["content"] if b["type"] == "image"]
    assert len(images) == 1 and images[0]["source"]["media_type"] == "image/png"


def test_failed_call_leaves_history_usable(fake_client):
    fake_client.messages.create.side_effect = RuntimeError("down")
    session = TutorSession(client=fake_client)
    with pytest.raises(RuntimeError):
        session.ask("hi")
    assert session.history == []


# ---------- LaTeX read aloud ----------
@pytest.mark.parametrize("latex,spoken", [
    (r"\frac{x^2}{2}", "x squared over 2"),
    (r"x^3", "x cubed"),
    (r"x^{n}", "x to the n"),
    (r"a^{n+1}", "a to the power of n plus 1"),
    (r"2^{-1}", "2 to the negative 1"),
    (r"\sqrt{x}", "the square root of x"),
    (r"\sqrt[3]{8} = 2", "the cube root of 8 equals 2"),
    (r"\sqrt{b^2 - 4ac}", "the square root of b squared minus 4 a c"),
    (r"x = \frac{-b \pm \sqrt{b^2-4ac}}{2a}",
     "x equals negative b plus or minus the square root of b squared minus 4 a c, "
     "all over 2 a"),
    (r"\frac{x+1}{x-1}", "the quantity x plus 1, over the quantity x minus 1"),
    (r"(x+1)^2", "the quantity x plus 1 squared"),
    (r"x_1 + x_2", "x sub 1 plus x sub 2"),
    (r"\int_0^1 x^2\,dx", "the integral from 0 to 1 of x squared d x"),
    (r"\sum_{i=1}^{n} i^2", "the sum from i equals 1 to n of i squared"),
    (r"\lim_{x \to 0} \frac{\sin x}{x}",
     "the limit as x approaches 0 of sine of x, all over x"),
    (r"\frac{d}{dx} x^3", "the derivative with respect to x of x cubed"),
    (r"\sin^2\theta + \cos^2\theta = 1",
     "sine squared of theta plus cosine squared of theta equals 1"),
    (r"\log_2 8 = 3", "log base 2 of 8 equals 3"),
    (r"\Delta x \geq 0", "delta x is greater than or equal to 0"),
    (r"|x| < 5", "the absolute value of x is less than 5"),
    (r"30^\circ", "30 degrees"),
    (r"e^{i\pi} + 1 = 0", "e to the i pi plus 1 equals 0"),
    (r"F = ma", "F equals m a"),
    (r"\left( \frac{1}{2} \right)", "the quantity 1 over 2"),
    (r"2(x+1) = 8", "2 times the quantity x plus 1, equals 8"),
    (r"\vec{F} = m\vec{a}", "vector F equals m vector a"),
    (r"3 \times 10^{8}", "3 times 10 to the 8"),
])
def test_latex_to_speech(latex, spoken):
    assert speak_latex(latex) == spoken


def test_speakable_reads_math_and_drops_markdown():
    text = ("**Step one:** divide by $2$, so $x = \\frac{6}{2}$.\n"
            "$$\\int_0^1 x\\,dx$$\n1. Check \\(a^2\\)\n- It costs $5 and $6.")
    assert speakable(text) == (
        "Step one: divide by 2, so x equals 6 over 2.\n"
        "the integral from 0 to 1 of x d x\n"
        "Step 1: Check a squared\n"
        "It costs $5 and $6.")


def test_speak_latex_never_hangs_on_odd_input():
    for junk in ["\\", "{", "}", "^", "_", "\\frac{", "\\sqrt[", "x^", "(", "|x",
                 "1.", "\\unknowncommand{x}", "$$", "\\left("]:
        assert isinstance(speak_latex(junk), str)
