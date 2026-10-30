// Attachments for the chat: the "+" button left of the text field opens a
// sheet (Take photo / Photo library / Choose file); what is picked shows as
// removable chips above the field and goes with the next typed message or
// push-to-talk recording as "attachment" parts of /api/turn's form
// (kwil/attachments.py checks every file again). Photos are shrunk to
// 1600 px and re-encoded as JPEG here first (which drops EXIF/GPS, and saves
// data); the tutor (static/features.js) uses the same shrinkImage(). At most
// MAX files: at the limit the sheet's pickers are disabled, while "+" still
// opens it for its other rows (Think harder, Study mode; a row with
// data-close just closes the sheet after its own click). Loaded before
// app.js, which asks window.KwilAttach.tray for the pending files and is
// told when they change (opts.changed: the composer's Send button).
"use strict";

(function (root) {
  const MAX = 4;
  const MAX_SIDE = 1600;
  const IMAGE = /^image\/(jpeg|png|webp|gif|heic|heif)$/i;
  const EXT = { jpg: "image", jpeg: "image", png: "image", webp: "image", gif: "image",
                heic: "image", heif: "image", pdf: "pdf", txt: "text", md: "text", markdown: "text" };

  // "image", "pdf", "text", or null (not allowed) for a picked File.
  function kindOf(file) {
    const type = String((file && file.type) || "").toLowerCase();
    if (IMAGE.test(type)) return "image";
    if (type === "application/pdf") return "pdf";
    if (type === "text/plain" || type === "text/markdown") return "text";
    const ext = String((file && file.name) || "").toLowerCase().split(".").pop();
    return EXT[ext] || null;
  }

  // The pending list after adding `files`: a new array (the old one is left
  // as it is) plus what was refused and why ("type" or "limit").
  function addFiles(list, files) {
    const out = list.slice();
    const rejected = [];
    for (const file of Array.from(files || [])) {
      const kind = kindOf(file);
      if (!kind) rejected.push({ name: file.name, reason: "type" });
      else if (out.length >= MAX) rejected.push({ name: file.name, reason: "limit" });
      else out.push({ file, kind, name: file.name || (kind === "image" ? "photo.jpg" : "file") });
    }
    return { list: out, rejected };
  }

  function removeAt(list, index) {
    return list.filter((_, i) => i !== index);
  }

  const canAdd = (list) => list.length < MAX;

  // What the chat shows for the sent files (history keeps the same marker).
  function summary(list) {
    return list.map((a) => (a.kind === "image" ? "[photo]" : `[file: ${a.name}]`)).join(" ");
  }

  // Adds the files to a turn's FormData (photos as their shrunk JPEG).
  function appendTo(form, list) {
    for (const a of list) {
      const name = a.kind === "image" ? String(a.name).replace(/\.[^.]*$/, "") + ".jpg" : a.name;
      form.append("attachment", a.blob || a.file, name);
    }
    return form;
  }

  // Draw a photo upright, at most `max` px, on a canvas; resolves with
  // { blob (JPEG), canvas }.
  async function shrinkImage(file, max = MAX_SIDE, doc = root.document) {
    let source;
    try {
      source = await createImageBitmap(file, { imageOrientation: "from-image" });
    } catch (e) {
      source = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => { const img = new Image(); img.onload = () => resolve(img);
                                img.onerror = reject; img.src = reader.result; };
        reader.onerror = reject;
        reader.readAsDataURL(file);
      });
    }
    const w = source.width, h = source.height, scale = Math.min(1, max / Math.max(w, h));
    const canvas = doc.createElement("canvas");
    canvas.width = Math.round(w * scale);
    canvas.height = Math.round(h * scale);
    canvas.getContext("2d").drawImage(source, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
    if (!blob) throw new Error("could not encode the photo");
    return { blob, canvas };
  }

  // A small data: URL preview (the page's CSP allows data: images, not blob:).
  function thumbnail(canvas, doc = root.document, size = 96) {
    const scale = Math.min(1, size / Math.max(canvas.width, canvas.height));
    const t = doc.createElement("canvas");
    t.width = Math.max(1, Math.round(canvas.width * scale));
    t.height = Math.max(1, Math.round(canvas.height * scale));
    t.getContext("2d").drawImage(canvas, 0, 0, t.width, t.height);
    return t.toDataURL("image/jpeg", 0.7);
  }

  // Wire the "+" button, its sheet, the file inputs and the chips.
  function setupTray(doc, opts = {}) {
    const $ = (id) => doc.getElementById(id);
    const plus = $("attach"), sheet = $("attach-sheet"), chips = $("attach-chips");
    const inputs = { camera: $("attach-camera"), library: $("attach-library"), file: $("attach-file") };
    if (!plus || !sheet || !chips) return null;
    const note = opts.note || (() => {});
    const changed = opts.changed || (() => {});
    const picks = Array.from(sheet.querySelectorAll("button[data-pick]"));
    let list = [];
    let work = Promise.resolve();  // photos still being shrunk

    function render() {
      chips.textContent = "";
      list.forEach((a, i) => {
        const chip = doc.createElement("div");
        chip.className = `attach-chip ${a.kind}`;
        if (a.thumb) {
          const img = doc.createElement("img");
          img.src = a.thumb;
          img.alt = "";
          chip.appendChild(img);
        } else {
          const label = doc.createElement("span");
          label.className = "attach-name";
          label.textContent = a.name;
          chip.appendChild(label);
        }
        const x = doc.createElement("button");
        x.type = "button";
        x.className = "attach-remove";
        x.setAttribute("aria-label", "Remove");
        x.textContent = "×";
        x.addEventListener("click", () => { list = removeAt(list, i); render(); });
        chip.appendChild(x);
        chips.appendChild(chip);
      });
      chips.hidden = list.length === 0;
      for (const btn of picks) btn.disabled = !canAdd(list);
      changed();
    }

    async function picked(input) {
      const files = Array.from(input.files || []);
      input.value = "";
      const { list: next, rejected } = addFiles(list, files);
      if (rejected.some((r) => r.reason === "limit")) note(`You can attach up to ${MAX} files.`);
      if (rejected.some((r) => r.reason === "type")) note("Only photos, PDFs and text files can be attached.");
      const added = next.slice(list.length);
      list = next;
      render();
      work = work.then(async () => {
        for (const a of added) {
          if (a.kind !== "image") continue;
          try {
            const { blob, canvas } = await shrinkImage(a.file, MAX_SIDE, doc);
            a.blob = blob;
            a.thumb = thumbnail(canvas, doc);
          } catch (e) {
            list = list.filter((x) => x !== a);
            note("That photo couldn't be opened. Try a JPEG or PNG.");
          }
          render();
        }
      });
      await work;
    }

    for (const input of Object.values(inputs)) {
      if (input) input.addEventListener("change", () => picked(input));
    }
    plus.addEventListener("click", () => {
      if (typeof sheet.showModal === "function") sheet.showModal();
      else if (canAdd(list) && inputs.file) inputs.file.click();
    });
    // The picker opens from the button's own tap (iOS needs the gesture).
    for (const btn of picks) {
      btn.addEventListener("click", () => {
        sheet.close();
        if (!canAdd(list)) return;
        const input = inputs[btn.dataset.pick];
        if (input) input.click();
      });
    }
    const cancel = sheet.querySelector("button.cancel");
    if (cancel) cancel.addEventListener("click", () => sheet.close());
    for (const btn of sheet.querySelectorAll("[data-close]")) {
      btn.addEventListener("click", () => sheet.close());
    }
    render();

    return {
      count: () => list.length,
      // resolves once every picked photo has been shrunk (or dropped)
      whenReady: () => work,
      summary: () => summary(list),
      appendTo: (form) => appendTo(form, list),
      clear: () => { list = []; render(); },
    };
  }

  const api = { MAX, MAX_SIDE, kindOf, addFiles, removeAt, canAdd, summary, appendTo,
                shrinkImage, thumbnail, setupTray, tray: null };
  root.KwilAttach = api;
  if (typeof root.document !== "undefined" && root.document.getElementById) {
    // app.js's addMessage (a global function of that classic script)
    api.tray = setupTray(root.document, {
      note: (text) => (typeof root.addMessage === "function" ? root.addMessage("note", text) : null),
      // app.js's composer (Send shows for files alone); not there yet at load
      changed: () => {
        const app = root.KwilApp;
        if (app && typeof app.syncComposer === "function") app.syncComposer();
      },
    });
  }
})(typeof window !== "undefined" ? window : globalThis);
