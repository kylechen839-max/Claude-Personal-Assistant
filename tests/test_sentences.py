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
    s = SentenceSplitter()
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
