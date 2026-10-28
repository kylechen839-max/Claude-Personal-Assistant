# Kwil Canvas helper (Chrome and Edge)

For schools that don't let students make a Canvas access token. The helper
reads your own Canvas courses in your own signed-in browser and sends them
to your Kwil, which stores them exactly as the token sync does (search,
"read the syllabus", deadline drafts). No token, nothing for school IT to
approve, nothing to type.

It reads: your active courses' syllabus, pages, module lists, assignment
descriptions and due dates, announcements from the last 120 days, the
course files list, and the files Kwil asks for (PDF, Word, PowerPoint,
HTML, text; Kwil skips images, video, audio, spreadsheets and zips). It
never reads or sends grades, scores, submissions, classmates, your
password or your cookies, and never posts anything to Canvas.

The helper reads only your own courses, with your own sign-in, like
opening the pages yourself. Some schools' rules limit automated access;
check yours. It is read-only and rate-limited.

## Install (Load unpacked, for now)
A Chrome Web Store / Edge Add-ons listing may come later; until then:

**Chrome**
1. Get this folder (`tools/canvas-helper/` of the Kwil repo) onto the
   computer, e.g. download the repo as a zip and unzip it.
2. Open `chrome://extensions`, turn on **Developer mode** (top right).
3. Click **Load unpacked** and pick the `canvas-helper` folder.
4. Pin it (puzzle icon, then the pin) so its popup is one click away.

**Edge**
1. Same folder. Open `edge://extensions`, turn on **Developer mode**
   (left side).
2. Click **Load unpacked** and pick the `canvas-helper` folder.

Updating: replace the folder's files and click the reload arrow on the
extension's card. Chrome may show a "developer mode extensions" notice at
start-up; that is normal for an unpacked extension.

## Set up (one tap)
1. In the same browser, open Kwil (`https://heykwil.com`), sign in, open
   the menu, then **School deadlines**, and tap **Set up Canvas helper**.
   The helper picks the one-time code up by itself; within a few seconds
   the card says "Canvas helper connected."
2. Open your school's Canvas in that browser (signed in) once.
   - On `*.instructure.com` nothing else is needed.
   - On a school domain of its own (e.g. `canvas.school.edu`): click the
     helper's icon there and **Use as my school's Canvas**; Chrome asks to
     allow that one site.

The first sync starts about half a minute later, then every 6 hours while
the browser is open. The popup shows whether it is paired, the Kwil and
Canvas it uses, and the last sync.

If your Kwil runs on its own domain (not `heykwil.com`): open it, tap
**Set up Canvas helper**, then click the helper's icon, **This is my
Kwil**, and confirm "Send your Canvas courses to <host>?" within the 10
minutes.

Already paired with one Kwil and setting up another? On the new Kwil's
page the popup offers **Switch to <host>** (naming both); without that
click the helper ignores the new code.

If Chrome stops letting the helper on your Canvas or Kwil (Site access in
the extension's details), the popup says so on that site: **Allow on this
site** for a domain you enabled, or where to find Site access for
heykwil.com and instructure.com.

## Stop it
- **Unpair this browser** (popup, click twice) forgets the key here.
- **Revoke in Kwil** opens Kwil; tap **Revoke helper** in School deadlines
  so the key stops working everywhere. **Disconnect Canvas** there also
  deletes what was sent.
- Signing out of Kwil does not revoke the helper (it has its own key).
- Removing the extension deletes its key from the browser.

## Permissions, and why
| Permission | Why |
| --- | --- |
| `storage` | the sync key, the Kwil and Canvas addresses, the last sync |
| `alarms` | the sync every 6 hours, and one soon after pairing |
| `activeTab` | the popup reads the current tab's address, only when you click the icon, to offer the buttons below |
| `scripting` | for a site you enabled: run that role's one small page script there (Canvas: `canvas-seen.js`; Kwil: `kwil-pair.js`) |
| `https://heykwil.com/*` | pick up the pairing code; send your courses to Kwil |
| `https://*.instructure.com/*` | read your Canvas (most schools) |
| `https://*.inscloudgate.net/*` | Canvas hands course files out from its file store there |
| optional `https://*/*` | asked for one site at a time, only when you click **Use as my school's Canvas** or confirm **This is my Kwil** |

No `<all_urls>`, no `cookies` or `tabs` permission, no remote code, no
`eval`; the extension pages' Content-Security-Policy allows only its own
scripts.

## How it works (for reviewers)
- `content/kwil-pair.js` (on the Kwil page): reads the code from the
  `data-kwil-pair` element, POSTs it same-origin to
  `/api/student/canvas/pair` (after asking the worker whether this page
  may pair), and hands the returned `kcs1.` key to the service worker. The
  key never goes into the page, a URL or a log. It never runs on a Canvas
  site.
- `content/canvas-seen.js` (on Canvas pages): tells the worker you are on
  Canvas; the worker takes the address from Chrome, not the page, and
  checks it is a signed-in Canvas.
- `background.js` + `lib/`: the service worker. `lib/canvas.js` reads the
  Canvas REST API with `credentials: "include"` (the browser's own
  session; GET only; Link pagination, at most 10 pages and 1000 items a
  listing; Canvas's rate limit waited out 1, 2, 4 s, then the run stops
  and backs off 15 min doubling to 6 h; a 401 that means signed out stops
  the run until you visit Canvas again). `lib/manifest.js` keeps only the
  fields Kwil reads and splits big manifests by course under Kwil's size
  limit. `lib/kwil.js` posts to the paired Kwil origin only, with the key
  as a Bearer header, no cookies, no redirects. `lib/sync.js` pushes as it
  reads: a manifest whenever a batch of courses fills, then only the files
  Kwil's answer asked for (at most 40 a run); the worker keeps itself
  alive during a run (a `chrome.runtime` call every 20 s, a progress note
  after each course).
- Server side: `kwil/canvas_helper.py` (the contract) and SECURITY.md
  "The Canvas helper's pushes".
- `package.json` only tells node to load `lib/*.js` as ES modules for the
  tests; there is no build step and no dependency.

Tests: `node --test tests/js/canvas-helper.test.mjs
tests/js/canvas-helper-ui.test.mjs` and
`python -m pytest tests/test_canvas_helper_extension.py` (a round trip
through the real push routes).
