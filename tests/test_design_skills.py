"""The vendored design skills in .claude/skills stay pinned, licensed and
offline: each has a SKILL.md whose name matches its folder and a license
file, and none tells Claude to fetch its rules from the web at run time."""

import re
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parent.parent / ".claude" / "skills"
DESIGN = (
    "frontend-design",
    "web-design-guidelines",
    "accessibility",
    "ui-ux-pro-max",
    "taste-skill",
    "animation-vocabulary",
)


@pytest.mark.parametrize("name", DESIGN)
def test_design_skill_is_complete(name):
    folder = SKILLS / name
    text = (folder / "SKILL.md").read_text(encoding="utf-8")
    assert re.search(rf"^name: {re.escape(name)}$", text, re.M), name
    assert re.search(r"^description: \S", text, re.M), name
    assert any((folder / f).is_file() for f in ("LICENSE", "LICENSE.txt")), name


@pytest.mark.parametrize("name", DESIGN)
def test_design_skill_reads_no_rules_from_the_web(name):
    for path in (SKILLS / name).rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert "raw.githubusercontent.com" not in text, path
        assert "WebFetch" not in text, path


def test_guidelines_wrapper_points_at_the_vendored_rules():
    text = (SKILLS / "web-design-guidelines" / "SKILL.md").read_text(encoding="utf-8")
    assert "references/command.md" in text
    assert (SKILLS / "web-design-guidelines" / "references" / "command.md").is_file()


def test_design_skills_note_pins_every_source():
    note = (SKILLS / "DESIGN_SKILLS.md").read_text(encoding="utf-8")
    for name in DESIGN:
        assert f"`{name}`" in note, name
    assert len(re.findall(r"@ [0-9a-f]{40}", note)) == len(DESIGN)


@pytest.mark.parametrize("name", DESIGN)
def test_design_skill_ships_no_scripts(name):
    # Upstream bundles search and inspection scripts (search.py, draw.mjs,
    # assets.mjs); only reference text and data are vendored here.
    for path in (SKILLS / name).rglob("*"):
        assert path.suffix not in {".py", ".js", ".mjs", ".cjs", ".sh", ".ts"}, path


@pytest.mark.parametrize("name", DESIGN)
def test_design_skill_loads_no_outside_fonts_or_installs(name):
    for path in (SKILLS / name).rglob("*"):
        if path.suffix not in {".md", ".csv"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert "fonts.googleapis.com" not in text, path
        assert "allowed-tools" not in text, path
        assert "npx skills" not in text and "shadcn@latest" not in text, path
