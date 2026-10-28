"""Canvas course content, PR (c): the Kwil Canvas helper extension
(tools/canvas-helper/), checked from Python. Its logic is tested in node
(tests/js/canvas-helper*.test.mjs); here: the manifest asks for the least
it can (no <all_urls>, no cookies permission, no remote code, a strict
CSP), the code has no eval or HTML injection, and a round trip: the
extension's real sync, run in node against a fake Canvas, produces a
manifest and a multipart file push that the real push routes
(kwil/canvas_helper.py) accept, store and make searchable. Offline: node
fakes Canvas; the routes run in a TestClient."""

import base64
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXT = ROOT / "tools" / "canvas-helper"
PDF_PATH = ROOT / "tests" / "fixtures" / "course_syllabus.pdf"


def _manifest() -> dict:
    return json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))


def _code() -> dict[str, str]:
    return {p.relative_to(EXT).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(EXT.rglob("*")) if p.suffix in (".js", ".html", ".json", ".css")}


def test_the_manifest_asks_for_the_least_it_can():
    m = _manifest()
    assert m["manifest_version"] == 3
    assert m["background"] == {"service_worker": "background.js", "type": "module"}
    assert sorted(m["permissions"]) == ["activeTab", "alarms", "scripting", "storage"]
    assert m["host_permissions"] == ["https://heykwil.com/*", "https://*.instructure.com/*",
                                     "https://*.inscloudgate.net/*"]
    assert m["optional_host_permissions"] == ["https://*/*"]   # one school origin, on a click
    everything = json.dumps(m)
    for never in ("<all_urls>", '"cookies"', '"tabs"', '"webRequest"', "http://",
                  '"externally_connectable"', '"web_accessible_resources"', '"nativeMessaging"',
                  "*://*/*"):
        assert never not in everything, never
    scripts = m["content_scripts"]
    assert [s["matches"] for s in scripts] == [["https://heykwil.com/*"],
                                               ["https://*.instructure.com/*"]]
    assert all(not s.get("all_frames") for s in scripts)
    csp = m["content_security_policy"]["extension_pages"]
    directives = dict(d.strip().split(" ", 1) for d in csp.split(";"))
    assert directives["script-src"] == "'self'"
    assert directives["object-src"] == "'none'"
    assert directives["default-src"] == "'none'"
    assert "unsafe" not in csp and "wasm" not in csp
    assert set(m) <= {"manifest_version", "name", "short_name", "version", "description",
                      "minimum_chrome_version", "background", "permissions", "host_permissions",
                      "optional_host_permissions", "content_scripts", "action",
                      "content_security_policy"}


def test_every_file_the_manifest_names_exists():
    m = _manifest()
    named = [m["background"]["service_worker"], m["action"]["default_popup"]]
    named += [js for s in m["content_scripts"] for js in s["js"]]
    for name in named:
        assert (EXT / name).is_file(), name
    # the scripts the worker registers or injects for an enabled site
    helper = (EXT / "lib" / "helper.js").read_text(encoding="utf-8")
    for name in re.findall(r'"(content/[\w-]+\.js)"', helper):
        assert (EXT / name).is_file(), name


def test_no_eval_remote_code_or_html_injection():
    for name, text in _code().items():
        for never in ("eval(", "new Function", "innerHTML", "outerHTML", "insertAdjacentHTML",
                      "document.write", "importScripts", "setTimeout(\"", "http://"):
            assert never not in text, f"{name}: {never}"
        for src in re.findall(r"""(?:src|href)=["']([^"']+)""", text):
            assert not re.match(r"(https?:)?//", src), f"{name}: remote {src}"
        for spec in re.findall(r"""(?:import|from)\s*\(?\s*["']([^"']+)""", text):
            assert spec.startswith("./") or spec.startswith("../"), f"{name}: {spec}"
    # no logging of the key, a URL or a title (counts and codes only)
    for name, text in _code().items():
        for line in re.findall(r"(?:console\.\w+|log)\((.*)", text):
            assert not re.search(r"\b(key|url|title|name|body)\b", line), f"{name}: {line}"


# --- the round trip through the real push routes ---------------------------------------

pytest.importorskip("fastapi")
pytest.importorskip("cryptography")
from fastapi.testclient import TestClient  # noqa: E402

from kwil import canvas_helper  # noqa: E402
from tests.test_canvas_helper import bearer, h, helper_client, pair  # noqa: E402,F401
from tests.test_coursefiles import search  # noqa: E402
from tests.test_student_web import s  # noqa: E402,F401 - the fixture
from tests.test_webapp import BASE  # noqa: E402

NODE = shutil.which("node")


def _extension_run() -> dict:
    out = subprocess.run([NODE, str(ROOT / "tests" / "js" / "canvas-helper-roundtrip.mjs"),
                          str(PDF_PATH)], capture_output=True, text=True, timeout=60,
                         cwd=ROOT, check=True)
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="needs node (the JS test job has it)")
def test_what_the_extension_sends_is_what_the_push_routes_take(h):
    run = _extension_run()
    assert run["result"] == "ok" and len(run["manifests"]) == 1 and len(run["files"]) == 1
    manifest = run["manifests"][0]
    # clean_manifest keeps all of it: nothing the extension sends is dropped
    cleaned = canvas_helper.clean_manifest(manifest)
    assert [c["id"] for c in cleaned["courses"]] == [c["id"] for c in manifest["courses"]]
    for sent, kept in zip(manifest["courses"], cleaned["courses"]):
        for name in canvas_helper.LISTINGS:
            assert (kept[name] is None) == (sent[name] is None), name
            if sent[name] is not None:
                assert len(kept[name]) == len(sent[name]), name
    assert len(cleaned["announcements"]) == len(manifest["announcements"])
    # what the settings page said of tokens (read with a GET) is taken as sent
    assert cleaned["canvas"] == manifest["canvas"] == {
        "host": "school.instructure.com", "can_create_tokens": False, "only_student": True}
    # the real routes: the manifest stores the text and asks for the PDF
    key, _ = pair(h, h.sam)
    r = helper_client(h).post(canvas_helper.MANIFEST_PATH, json=manifest, headers=bearer(key))
    assert r.status_code == 200, r.text
    assert r.json()["want"] == [{"course_id": 111, "file_id": 501}]
    # ...and the school is learned as blocking students' tokens
    assert h.app.state.student.policies.policy("school.instructure.com", own=True) == "blocked"
    assert h.sam.get("/api/student/items").json()["canvas"]["policy"] == {
        "host": "school.instructure.com", "tokens": "blocked"}
    # the extension's own multipart body, byte for byte
    f = run["files"][0]
    body = base64.b64decode(f["body_b64"])
    r = TestClient(h.app, base_url=BASE).post(
        canvas_helper.FILE_PATH, content=body,
        headers={**bearer(key), "Content-Type": f["content_type"],
                 "Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop"})
    assert r.status_code == 200, r.text
    assert r.json() == {"result": "added", "note": None}
    tools = h.fam.context(h.uid).registry
    pdf = [x for x in search(tools, "late policy")["results"] if x["kind"] == "file"]
    assert pdf and pdf[0]["title"] == "Syllabus.pdf"
    kinds = {x["kind"] for x in search(tools, "Altgeld Loomis Foellinger Grading work Week")
             ["results"]}
    assert kinds >= {"page", "announcement", "syllabus", "assignment", "module"}
    rows = h.fam.coursefiles.export(h.uid)
    module = next(r for r in rows if r["kind"] == "module")
    assert "- Week 1 (Page)" in module["text"]   # a module's items, as Kwil reads them
    blob = json.dumps(rows)
    for never in ("verifier", "download_frd", "9.5", "computed_current", "Prof. X"):
        assert never not in blob, never
