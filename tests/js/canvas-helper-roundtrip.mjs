// node tests/js/canvas-helper-roundtrip.mjs <file.pdf>
//
// For tests/test_canvas_helper_extension.py: runs the extension's real
// sync (tools/canvas-helper/lib/sync.js) against the fake Canvas in
// canvas-helper-fakes.mjs, with <file.pdf> as the bytes of file 501, and
// prints what it would send Kwil as JSON: the manifest(s) and the file
// push's exact multipart body (base64) and Content-Type. Python then
// sends those to the real push routes. No network.

import { readFileSync } from 'node:fs';

import { runSync } from '../../tools/canvas-helper/lib/sync.js';
import { CANVAS, KEY, KWIL, canvasFile, school, settingsPage, world } from
  './canvas-helper-fakes.mjs';

const pdf = new Uint8Array(readFileSync(process.argv[2]));
const data = school();
data.files[111][0] = canvasFile(501, 'Syllabus.pdf', 'application/pdf', 'pdf', pdf.length);
data.bytes[501] = pdf;
data.settings = settingsPage({ canUpdate: false });   // this school blocks students' tokens
const forms = [];
const w = world({ data, kwil: {
  '/api/student/canvas/push/manifest': () => ({ body: { counts: {}, max_file_bytes: 3_934_464,
    want: [{ course_id: 111, file_id: 501 }] } }),
  '/api/student/canvas/push/file': (opts) => { forms.push(opts.body); return { body: {} }; },
} });
const out = await runSync({ pairing: { key: KEY, origin: KWIL, maxFiles: 40 },
                            canvasOrigin: CANVAS, fetch: w.fetch, sleep: async () => {} });
const files = [];
for (const form of forms) {
  const req = new Request(`${KWIL}/x`, { method: 'POST', body: form });
  files.push({ content_type: req.headers.get('content-type'),
               body_b64: Buffer.from(await req.arrayBuffer()).toString('base64') });
}
process.stdout.write(JSON.stringify({ result: out.result, manifests: w.pushed.manifests, files }));
