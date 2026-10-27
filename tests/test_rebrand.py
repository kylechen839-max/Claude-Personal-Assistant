"""The Kwil rebrand (2026-10-22): the new names, and the old ones that keep
working for at least one stable release (PC tasks, Pi units and the Render
start command run `python -m assistant` until they are re-registered)."""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "kwil" / "web"


# ---------- the package: kwil, with assistant as an alias ----------

def test_the_old_package_name_is_the_same_module():
    import assistant.config
    import assistant.tools.calendar
    import kwil.config
    import kwil.tools.calendar

    assert assistant.config is kwil.config
    assert assistant.tools.calendar is kwil.tools.calendar
    assert kwil.config.__spec__.name == "kwil.config"  # its real spec, not the alias
    assert kwil.config.__name__ == "kwil.config"


def test_monkeypatching_either_name_patches_both(monkeypatch):
    import assistant.version
    import kwil.version

    monkeypatch.setattr("assistant.version.ROOT", Path("/nowhere"))
    assert kwil.version.ROOT == Path("/nowhere")
    monkeypatch.setattr(kwil.version, "ROOT", Path("/elsewhere"))
    assert assistant.version.ROOT == Path("/elsewhere")


def test_old_imports_of_names_and_attributes_work():
    import assistant
    from assistant.cli import main
    from kwil import cli

    assert main is cli.main
    assert assistant.__version__ == __import__("kwil").__version__
    with pytest.raises(AttributeError):
        assistant.no_such_module  # noqa: B018


def test_a_module_that_doesnt_exist_isnt_found_by_either_name():
    import importlib.util

    assert importlib.util.find_spec("kwil.no_such_module") is None
    assert importlib.util.find_spec("assistant.no_such_module") is None
    with pytest.raises(ModuleNotFoundError):
        __import__("assistant.no_such_module")


@pytest.mark.parametrize("name", ["kwil", "assistant"])
def test_python_dash_m_runs_under_both_names(name):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    p = subprocess.run([sys.executable, "-m", name, "--help"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120, check=False)
    assert p.returncode == 0, p.stderr
    assert "--mode" in p.stdout and "Kwil" in p.stdout


@pytest.mark.parametrize("module", ["host", "watch", "deploy", "tools.google_auth"])
def test_python_dash_m_runs_old_submodule_names(module):
    """The \\HeyClaude\\ tasks and the Pi's hey-claude units run
    `python -m assistant.host` / `assistant.watch` (and the old host runs
    `-m assistant.watch --functional`): runpy needs the alias's get_code."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    old = subprocess.run([sys.executable, "-m", f"assistant.{module}", "--help"], cwd=ROOT,
                         env=env, capture_output=True, text=True, timeout=120, check=False)
    new = subprocess.run([sys.executable, "-m", f"kwil.{module}", "--help"], cwd=ROOT,
                         env=env, capture_output=True, text=True, timeout=120, check=False)
    assert "Traceback" not in old.stderr, old.stderr
    assert old.returncode == new.returncode and old.stdout + old.stderr == new.stdout + new.stderr
    assert "usage" in (old.stdout + old.stderr).lower()


# 610017b's modules, every one of which an old process may still import lazily
OLD_MODULES = """account attachments audio backup canvas chat cli cloud config deadlines
deploy devteam doctor earcons errortracking failover failover.edge failover.eventlog
failover.leader failover.peers failover.sync failover.tokens family google_web history
history_cli history_web host i18n lecture mathspeech memory memory_review memory_web
notion_web preflight proactive public_site push quotas router sentences smoke spotify_web
stt stt_cloud student_web timing tools tools.calendar tools.deadlines tools.gmail
tools.google_auth tools.history tools.home_assistant tools.memory_tools tools.notes
tools.notion tools.spotify tools.teamchat tools.teamchat_discord tools.teamchat_github
tools.teamchat_slack tools.timeutil tools.web tts tts_elevenlabs tts_kokoro tutor updater
usage userkeys userkeys_web version voice_picker voice_session voice_ws wakeword watch
webapp webfeatures""".split()


def test_every_old_module_has_a_file_shim():
    for name in OLD_MODULES:
        parts = name.split(".")
        path = ROOT / "assistant" / Path(*parts).with_suffix(".py")
        if not path.is_file():
            path = ROOT / "assistant" / Path(*parts) / "__init__.py"
        assert path.is_file(), name
        assert "shim(__name__)" in path.read_text(encoding="utf-8"), name
        assert importlib_util_find(f"kwil.{name}"), name


def importlib_util_find(name):
    import importlib.util

    return importlib.util.find_spec(name) is not None


def test_a_file_shim_and_the_alias_give_the_same_module():
    import importlib

    import kwil.smoke
    from assistant import _alias

    sys.modules.pop("assistant.smoke", None)
    _alias.shim("assistant.smoke")  # what assistant/smoke.py does
    assert sys.modules["assistant.smoke"] is kwil.smoke
    sys.modules.pop("assistant.smoke")
    assert importlib.import_module("assistant.smoke") is kwil.smoke  # the alias


OLD_PROCESS = '''
import importlib, shutil, sys
from pathlib import Path

root, new = Path(sys.argv[1]), Path(sys.argv[2])
from assistant import deploy           # the old deploy, as started by the old deploy.ps1
assert not any(type(f).__name__ == "Alias" for f in sys.meta_path)
# git pull: the old package folder is replaced by the new layout
shutil.rmtree(root / "assistant")
skip = shutil.ignore_patterns("__pycache__")
shutil.copytree(new / "assistant", root / "assistant", ignore=skip)
shutil.copytree(new / "kwil", root / "kwil", ignore=skip)
importlib.invalidate_caches()
got = deploy.gate()                    # its lazy imports, after the pull
import kwil.host, kwil.smoke, kwil.tools.google_auth, kwil.watch, kwil.devteam
assert got["console_python"] is kwil.host.console_python
assert got["smoke"] is kwil.smoke and sys.modules["assistant.smoke"] is kwil.smoke
assert got["EXTERNAL_REASONS"] is kwil.smoke.EXTERNAL_REASONS
assert got["write_private"] is kwil.tools.google_auth.write_private
assert got["link_from_config"] is kwil.devteam.link_from_config
assert got["updater_gate"] is kwil.watch.updater_gate
assert sys.modules["assistant.tools.google_auth"] is kwil.tools.google_auth
print("OK")
'''

# The lazy imports of 610017b's deploy/watch/host (watch.py:345-690, host.py:237)
OLD_DEPLOY = '''
def gate():
    from assistant.host import console_python
    from assistant import smoke
    from assistant.smoke import EXTERNAL_REASONS
    from assistant.tools.google_auth import write_private
    from assistant.devteam import link_from_config
    from assistant.watch import updater_gate
    from assistant.updater import take_lock, gate_backoff
    from assistant.config import load_config
    return dict(locals())
'''


def test_an_old_process_finds_its_lazy_imports_after_the_pull(tmp_path):
    """Kyle's next deploy.ps1 runs the OLD `python -m assistant.deploy`: the
    old package (no alias) is in memory when `git pull` swaps the tree, and
    its lazy imports must then find the new modules, not crash halfway."""
    old = tmp_path / "old"
    (old / "assistant" / "tools").mkdir(parents=True)
    (old / "assistant" / "__init__.py").write_text('__version__ = "1.0.2"\n')
    (old / "assistant" / "tools" / "__init__.py").write_text("")
    (old / "assistant" / "deploy.py").write_text(OLD_DEPLOY)
    (old / "old_process.py").write_text(OLD_PROCESS)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    p = subprocess.run([sys.executable, "old_process.py", str(old), str(ROOT)], cwd=old,
                       env=env, capture_output=True, text=True, timeout=120, check=False)
    assert p.returncode == 0 and p.stdout.strip() == "OK", p.stdout + p.stderr


def test_package_name_follows_the_checkout(tmp_path):
    """A rollback past the rebrand runs `python -m assistant` again."""
    from kwil.version import package

    assert package(ROOT) == "kwil"
    old = tmp_path / "old"
    (old / "assistant").mkdir(parents=True)
    (old / "assistant" / "cli.py").write_text("")
    assert package(old) == "assistant"
    (old / "kwil" / "__pycache__").mkdir(parents=True)  # left behind by the rollback
    assert package(old) == "assistant"
    assert package(tmp_path / "empty") == "kwil"


# ---------- environment variables: KWIL_*, the old HEY_CLAUDE_* still read ----------

@pytest.fixture
def version_env(monkeypatch):
    from kwil import version as v

    for var in ("KWIL_VERSION", "HEY_CLAUDE_VERSION", "RENDER_GIT_COMMIT"):
        monkeypatch.delenv(var, raising=False)
    v.version.cache_clear()
    yield monkeypatch, v
    v.version.cache_clear()


def test_the_new_version_variable_is_read(version_env):
    monkeypatch, v = version_env
    monkeypatch.setenv("KWIL_VERSION", "abcdef0123")
    assert v.version() == "abcdef0"


def test_the_old_version_variable_is_still_read(version_env):
    monkeypatch, v = version_env
    monkeypatch.setenv("HEY_CLAUDE_VERSION", "1234567890")
    assert v.version() == "1234567"


def test_the_new_variable_wins_over_the_old(version_env):
    monkeypatch, v = version_env
    monkeypatch.setenv("HEY_CLAUDE_VERSION", "1111111")
    monkeypatch.setenv("KWIL_VERSION", "2222222")
    assert v.version() == "2222222"


# ---------- updates: the renamed GitHub repository and signers folder ----------

@pytest.mark.parametrize("origin", [
    "https://github.com/kylechen839-max/Claude-Personal-Assistant.git",
    "git@github.com:kylechen839-max/Kwil.git",
    "https://github.com/kylechen839-max/kwil"])
@pytest.mark.parametrize("expected", [
    "github.com/kylechen839-max/Kwil",
    "github.com/kylechen839-max/Claude-Personal-Assistant"])
def test_either_repository_name_is_the_expected_origin(origin, expected):
    from kwil import updater

    assert updater.same_repo(origin, expected)


@pytest.mark.parametrize("origin", [
    "https://github.com/evil/Kwil", "https://github.com/evil/Claude-Personal-Assistant",
    "https://github.com/kylechen839-max/kwil-fork", "https://gitlab.com/kylechen839-max/Kwil"])
def test_other_repositories_are_still_refused(origin):
    from kwil import updater

    assert not updater.same_repo(origin, "github.com/kylechen839-max/Kwil")
    assert not updater.same_repo(origin, "github.com/kylechen839-max/Claude-Personal-Assistant")


@pytest.mark.parametrize("url", ["https://github.com/kylechen839-max/Claude-Personal-Assistant.git",
                                 "https://github.com/kylechen839-max/Kwil.git"])
def test_a_clone_keeps_updating_after_the_repo_rename(tmp_path, url):
    from kwil import updater

    def run(cmd, cwd, timeout):
        assert cmd[:3] == ["git", "remote", "get-url"]
        return 0, url + "\n"

    for repo in ("github.com/kylechen839-max/Kwil",
                 "github.com/kylechen839-max/Claude-Personal-Assistant"):
        up = updater.Updater(tmp_path, run=run, repo=repo, trust_unsigned=True)
        assert up.origin_problem() is None, (url, repo)
    up = updater.Updater(tmp_path, run=lambda c, d, t: (0, "https://github.com/evil/Kwil\n"),
                         repo="github.com/kylechen839-max/Kwil", trust_unsigned=True)
    assert "not github.com/kylechen839-max/kwil" in up.origin_problem()


def test_the_default_repo_is_the_new_name():
    from kwil.config import DEFAULTS

    assert DEFAULTS["update"]["repo"] == "github.com/kylechen839-max/Kwil"


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    from kwil import updater

    monkeypatch.setattr(updater.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_signers_from_before_the_rebrand_are_still_used(config_home):
    from kwil import updater

    assert updater.default_signers_path() == config_home / "kwil" / "allowed_signers"
    old = config_home / "hey-claude" / "allowed_signers"
    old.parent.mkdir()
    old.write_text("kyle namespaces=\"git\" ssh-ed25519 AAAA\n")
    assert updater.default_signers_path() == old
    new = config_home / "kwil" / "allowed_signers"
    new.parent.mkdir()
    new.write_text("kyle namespaces=\"git\" ssh-ed25519 BBBB\n")
    assert updater.default_signers_path() == new  # the new folder wins once it has one


def test_windows_signers_folder(tmp_path, monkeypatch):
    from kwil import updater

    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert updater.default_signers_path() == tmp_path / "Kwil" / "allowed_signers"
    (tmp_path / "HeyClaude").mkdir()
    (tmp_path / "HeyClaude" / "allowed_signers").write_text("x")
    assert updater.default_signers_path() == tmp_path / "HeyClaude" / "allowed_signers"


# ---------- what people see: Kwil, not Hey Claude ----------

# "Hey Claude" may stay only where it is the wake word (the trained model
# is still keywords/hey_claude.onnx) or names the pre-rebrand thing itself.
ALLOWED = [re.compile(p, re.IGNORECASE) for p in (
    r"say[^.\n]{0,20}hey claude",            # the wake-phrase note
    r"hey claude[^.\n]{0,40}until",          # "... until a Hey Kwil model ships"
)]
OLD_NAME = re.compile(r"hey[ _-]?claude", re.IGNORECASE)
SPLIT_NAME = re.compile(r"hey[ \t]*\n\s*claude", re.IGNORECASE)  # wrapped across two lines


def stale(text: str) -> list[str]:
    hits = []
    for line in text.splitlines():
        if OLD_NAME.search(line) and not any(a.search(line) for a in ALLOWED):
            hits.append(line.strip())
    hits += [" ".join(m.group(0).split()) for m in SPLIT_NAME.finditer(text)]
    return hits


@pytest.mark.parametrize("name", ["index.html", "landing.html", "privacy.html", "terms.html",
                                  "waitlist.html", "i18n.js", "sw.js", "app.js"])
def test_web_pages_say_kwil(name):
    text = (WEB / name).read_text(encoding="utf-8")
    assert stale(text) == [], name
    if name.endswith(".html"):
        assert "Kwil" in text, name


def test_the_manifest_and_titles_say_kwil():
    from kwil import i18n, proactive, public_site, webapp

    assert webapp.MANIFEST["name"] == "Kwil" and webapp.MANIFEST["short_name"] == "Kwil"
    assert stale(json.dumps(webapp.MANIFEST)) == []
    assert proactive.TITLE == "Kwil"  # push notification titles
    assert "What is Kwil?" in public_site.LOGIN_LINK
    assert stale(json.dumps([i18n.EN, i18n.ZH], ensure_ascii=False)) == []
    i18n_js = (WEB / "i18n.js").read_text(encoding="utf-8")
    assert '"What is Kwil?": "Kwil 是什么？"' in i18n_js  # untranslated in Chinese


def test_the_home_screen_label_and_console_say_kwil():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert '<meta name="apple-mobile-web-app-title" content="Kwil">' in html
    source = (ROOT / "kwil" / "cli.py").read_text(encoding="utf-8")
    assert 'output_fn(f"Claude: ' not in source and 'output_fn(f"Kwil: {reply}")' in source


def test_the_assistant_calls_itself_kwil():
    from kwil.chat import SYSTEM_PROMPT, SYSTEM_PROMPT_ZH

    assert "Kwil" in SYSTEM_PROMPT and "Kwil" in SYSTEM_PROMPT_ZH
    assert stale(SYSTEM_PROMPT + SYSTEM_PROMPT_ZH) == []


def test_the_service_worker_cache_was_renamed():
    sw = (WEB / "sw.js").read_text(encoding="utf-8")
    assert re.search(r'const CACHE = "kwil-shell-v\d+";', sw)
    assert "hey-claude-shell" not in sw  # every phone reloads its assets once


def test_page_globals_were_renamed():
    for path in WEB.rglob("*.js"):
        if "vad" in path.parts or "katex" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\b[Hh]eyClaude\w*", text), path.name


# ---------- what must NOT be renamed ----------

def test_secret_labels_keep_their_names():
    """HKDF labels of secrets already stored: a new label couldn't read them."""
    from kwil import canvas, push, userkeys

    assert userkeys.ANTHROPIC_LABEL == b"hey-claude/anthropic-api-key/v1"
    assert userkeys.NOTION_LABEL == b"hey-claude/notion-token/v1"
    assert push.PUSH_LABEL == b"hey-claude/push-subscriptions/v1"
    assert canvas.CANVAS_LABEL == b"hey-claude/canvas/v1"


def test_cookie_names_are_kept():
    text = (ROOT / "kwil" / "webapp.py").read_text(encoding="utf-8")
    assert 'COOKIE = "hc_session"' in text  # nobody is signed out by the rebrand


def test_render_service_and_disk_keep_their_names():
    """Renaming them would make Render create a new service with an empty disk."""
    import yaml

    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    web = next(s for s in blueprint["services"] if s.get("runtime") == "docker")
    assert web["name"] == "hey-claude"
    assert web["disk"]["name"] == "hey-claude-data"


def test_the_wake_word_model_is_still_hey_claude():
    from kwil.config import DEFAULTS  # noqa: F401 - [wake] defaults load

    toml = (ROOT / "config.toml").read_text(encoding="utf-8")
    assert 'keyword_path = "keywords/hey_claude.onnx"' in toml
    assert (ROOT / "keywords" / "hey_claude.onnx").is_file()


# ---------- the wake phrase: Hey Kwil (Whisper: "Hey Quill") and Hey Claude ----------

@pytest.mark.parametrize("heard", ["Hey Kwil, what time is it?", "hey quill. What time is it?",
                                   "Hey Claude, what time is it?"])
def test_either_name_is_stripped_from_a_follow_up(heard):
    from kwil.cli import strip_wake_phrase

    assert strip_wake_phrase(heard, "hey claude").lower() == "what time is it?"


def test_only_a_leading_wake_phrase_is_stripped():
    from kwil.cli import strip_wake_phrase

    assert strip_wake_phrase("I said hey kwil", "hey claude") == "I said hey kwil"
    assert strip_wake_phrase("Hey Kwilliam", "hey claude") == "Hey Kwilliam"
    assert strip_wake_phrase("Hey Kwil, hi", None) == "Hey Kwil, hi"  # None strips nothing


def test_the_phone_voice_mode_strips_hey_kwil():
    import inspect

    from kwil.voice_session import VoiceConversation

    assert inspect.signature(VoiceConversation).parameters["wake_phrase"].default == "Hey Kwil"


# ---------- deploy units and the scripts that replace the old ones ----------

def test_systemd_units_were_renamed():
    deploy = ROOT / "deploy"
    assert {p.name for p in deploy.glob("*.service")} | {p.name for p in deploy.glob("*.timer")} \
        == {"kwil.service", "kwil-watch.service", "kwil-watch.timer"}
    assert "ExecStart=@REPO_DIR@/.venv/bin/python -m kwil.host wake" in \
        (deploy / "kwil.service").read_text(encoding="utf-8")


def test_install_scripts_remove_the_old_units_and_tasks():
    scripts = ROOT / "scripts"
    service = (scripts / "install_service.sh").read_text(encoding="utf-8")
    assert 'OLD_UNIT="hey-claude.service"' in service and 'disable --now "$OLD_UNIT"' in service
    watch = (scripts / "install_watch.sh").read_text(encoding="utf-8")
    assert "disable --now hey-claude-watch.timer" in watch
    for name in ("install_windows_service.ps1", "install_family_tasks.ps1",
                 "install_watch_task.ps1"):
        text = (scripts / name).read_text(encoding="utf-8")
        assert "$taskPath = '\\Kwil\\'" in text, name
        assert "$oldTaskPath = '\\HeyClaude\\'" in text, name
    restart = (scripts / "restart_instance.ps1").read_text(encoding="utf-8")
    # the next deploy on the PC still finds the tasks registered before the rebrand
    for where in ("Path = '\\Kwil\\'", "Path = '\\HeyClaude\\'", "Name = 'Hey Claude Web'"):
        assert where in restart, where
    assert "(kwil|assistant)" in restart


def test_the_image_has_both_package_names():
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY kwil ./kwil" in docker and "COPY assistant ./assistant" in docker
    assert 'CMD ["python", "-m", "kwil", "--mode", "web"]' in docker


def test_backup_sweeps_temp_folders_of_either_name(tmp_path):
    from kwil import backup

    old = tmp_path / (backup.OLD_TEMP_PREFIX + "crashed")
    new = tmp_path / (backup.TEMP_PREFIX + "crashed")
    for folder in (old, new):
        folder.mkdir()
        stale = time.time() - backup.STALE_SECONDS - 60
        os.utime(folder, (stale, stale))
    backup.Backup(temp_dir=tmp_path).sweep()
    assert not old.exists() and not new.exists()


def test_the_rebrand_left_requirements_txt_alone():
    """Any change to requirements.txt makes the old deploy and the updater
    ask for --requirements, which stops the automatic update that carries
    the rebrand. Compared with 610017b (the commit before it) when this
    clone has that commit (CI's shallow checkout doesn't)."""
    have = subprocess.run(["git", "cat-file", "-e", "610017b^{commit}"], cwd=ROOT,
                          capture_output=True, check=False)
    if have.returncode != 0:
        pytest.skip("610017b isn't in this clone")
    old = subprocess.run(["git", "show", "610017b:requirements.txt"], cwd=ROOT,
                         capture_output=True, check=True).stdout
    assert (ROOT / "requirements.txt").read_bytes().replace(b"\r\n", b"\n") == old
