"""Sentence splitting for streamed speech (voice conversation mode)."""

import pytest

from assistant.sentences import SentenceSplitter, split_sentences


@pytest.mark.parametrize("text, expected", [
    ("Sure. Your dentist is at 3:30 PM. Then lunch.",
     ["Sure.", "Your dentist is at 3:30 PM.", "Then lunch."]),
    ("It's at 3:30 p.m. tomorrow, okay?", ["It's at 3:30 p.m. tomorrow, okay?"]),
    ("Lunch is at 1 p.m. Then you're free.", ["Lunch is at 1 p.m.", "Then you're free."]),
    ("Dr. Lee and Mr. Smith are in St. Louis.", ["Dr. Lee and Mr. Smith are in St. Louis."]),
    ("Bring snacks, e.g. chips. Also water.", ["Bring snacks, e.g. chips.", "Also water."]),
    ("The U.S. Open starts Oct. 18 at 9 a.m. sharp.",
     ["The U.S. Open starts Oct. 18 at 9 a.m. sharp."]),
    ("It costs $3.50 or 2.5 euros. Cheap!", ["It costs $3.50 or 2.5 euros.", "Cheap!"]),
    ("J. R. R. Tolkien wrote it. Really?! Yes.",
     ["J. R. R. Tolkien wrote it.", "Really?!", "Yes."]),
    ("Wait... Okay. Go to example.com now.", ["Wait...", "Okay.", "Go to example.com now."]),
    ('He said "hi." Then he left.', ['He said "hi."', "Then he left."]),
    ("I counted 42. Then I stopped", ["I counted 42.", "Then I stopped"]),
    ("Line one\nLine two", ["Line one", "Line two"]),
    ("**Bold** answer. ## Done.", ["Bold answer.", "Done."]),
])
def test_split(text, expected):
    assert split_sentences(text) == expected


def test_streaming_waits_for_the_next_character():
    s = SentenceSplitter()
    assert s.feed("Your meeting is at 3") == []
    assert s.feed(" p.m.") == []  # could be the end, or "p.m. tomorrow"
    assert s.feed(" ") == []
    assert s.feed("tomorrow. ") == []  # "tomorrow." needs the next word
    assert s.feed("Bring") == ["Your meeting is at 3 p.m. tomorrow."]
    assert s.feed(" the notes") == []
    assert s.flush() == ["Bring the notes"]
    assert s.flush() == []


def test_token_by_token_matches_whole_text():
    text = ("Tomorrow you have three things. Dentist at 9:30 a.m. with Dr. Patel, "
            "then lunch with Sam at noon. Finally, the U.S. history essay is due "
            "at 11:59 PM. Want me to move anything?")
    s = SentenceSplitter(first_clause_words=0)
    out = []
    for i in range(0, len(text), 3):
        out += s.feed(text[i:i + 3])
    out += s.flush()
    assert out == split_sentences(text)
    assert out[0] == "Tomorrow you have three things."
    assert len(out) == 4


def test_long_run_is_cut_at_a_comma():
    s = SentenceSplitter(max_chars=60)
    out = s.feed("one two three four five six seven, eight nine ten eleven twelve "
                 "thirteen fourteen fifteen")
    assert out == ["one two three four five six seven,"]
    assert s.flush()[0].startswith("eight")


def test_punctuation_only_is_dropped():
    assert split_sentences("... !") == []


def test_first_piece_ends_at_a_clause_for_faster_first_audio():
    """Voice mode: the reply's first piece also ends at a comma, semicolon or
    dash once it has 4+ words, so the first audio renders sooner."""
    s = SentenceSplitter()
    out = []
    for ch in "Tomorrow you have the dentist at three thirty, and after that you're free. Then lunch, with Sam.":
        out += s.feed(ch)
    out += s.flush()
    assert out == ["Tomorrow you have the dentist at three thirty,",
                   "and after that you're free.", "Then lunch, with Sam."]  # first piece only
    for text, first in [
            ("Sure, I can do that. Okay.", "Sure, I can do that."),  # < 4 words before
            ("It costs 3,000 dollars, which is a lot.", "It costs 3,000 dollars,"),
            ("Your meetings are at 9, 10, and 11 tomorrow.",
             "Your meetings are at 9, 10, and 11 tomorrow."),  # a list of numbers
            ("Well — the forecast says rain all day; bring a coat.",
             "Well — the forecast says rain all day;"),
            ("The forecast for today - rain, mostly.", "The forecast for today -")]:
        s = SentenceSplitter()
        assert (s.feed(text) + s.flush())[0] == first, text
    s = SentenceSplitter(first_clause_words=0)
    assert s.feed("Tomorrow you have the dentist at three thirty, and then. X") == [
        "Tomorrow you have the dentist at three thirty, and then."]


@pytest.mark.parametrize("text, expected", [
    ("Here it is: https://example.com/a_b?x=1 so take a look.",
     ["Here it is: so take a look."]),
    ("Nice work \U0001F389\U0001F44D see you then.", ["Nice work see you then."]),
    ("- **Gym** at six.", ["Gym at six."]),
    ("✅", []),
])
def test_unspeakable_bits_are_stripped(text, expected):
    assert split_sentences(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("好的。明天下午三点半有个会！要我提醒你吗？",
     ["好的。", "明天下午三点半有个会！", "要我提醒你吗？"]),
    ("他说「好的。」然后走了；我们也走吧", ["他说「好的。」", "然后走了；", "我们也走吧"]),
    ("真的？！太好了", ["真的？！", "太好了"]),
    ("好!明天见", ["好!", "明天见"]),
])
def test_split_chinese(text, expected):
    assert split_sentences(text) == expected


def test_chinese_waits_for_a_closing_quote_and_cuts_long_runs():
    splitter = SentenceSplitter()
    assert splitter.feed("他说「好。") == []  # 」 may still come
    assert splitter.feed("」好") == ["他说「好。」"]
    long = "，".join(["今天的天气非常好"] * 40)  # no sentence end at all
    pieces = SentenceSplitter(max_chars=60, first_clause_words=0).feed(long)
    assert pieces and all(p.endswith("，") and len(p) <= 60 for p in pieces)


def test_chinese_first_clause_comes_early():
    splitter = SentenceSplitter()
    out = []
    for ch in "好的，我帮你查一下明天的日历，稍等。":
        out += splitter.feed(ch)
    assert out == ["好的，我帮你查一下明天的日历，"]  # "好的，" alone is too short
    assert splitter.flush() == ["稍等。"]
