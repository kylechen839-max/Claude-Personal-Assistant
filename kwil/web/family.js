// Family invites on the cloud server (kwil/family.py).
// - Someone opening an invite link (https://<site>/#invite=hcinv....): the
//   token is read from the #fragment (never sent to a server by the
//   browser), cleared from the address bar, and POSTed once to
//   /api/invite/accept, which signs this phone in as that person.
// - The owner's menu: "Invite someone" (name, language) makes a one-time
//   link and opens the share sheet (or copies it); the list shows who is
//   invited or active, with "New link" and "Remove".
// - A family member's own Settings: "Download my data" (GET /api/me/export,
//   saved as a file) and "Delete my account" (typed confirmation, then
//   POST /api/me/delete; it signs this phone out). account.py.
// app.js calls configure({ request }) and acceptInvite() first thing, and
// onState(state) on every /api/state. The owner's calls go through that
// request() (with failover: the router, signed in with a bearer token, to
// whichever backend is active); the invite link's address comes from the
// server (its own public address), never this page's. Loaded before app.js.
// - A family member's or beta tester's Settings: "Your Anthropic API key"
//   (state.api_key "required" for beta, "optional" for family): save,
//   replace or remove their own key (/api/me/anthropic-key,
//   userkeys_web.py). Only the last four characters ever come back. A beta
//   tester on the starter credit (credit.py) sees what is left of it, and
//   a link to the key guide (/help/anthropic-key) where it is served
//   (state.key_help); the owner's People list shows what each used.
// - Beta feedback (feedback.py): "Send feedback" in Settings opens a sheet
//   of three questions; the one-time survey card when state.feedback_due;
//   the owner's "Feedback" row opens everyone's notes (text only, never HTML).
"use strict";

(function (root) {
  const ACCOUNT_KEY = "account";
  // app.js's request(url, options) -> Response; until configured, a plain
  // same-origin fetch (cookie sign-in, no failover)
  let request = null;

  function configure(opts = {}) {
    if (typeof opts.request === "function") request = opts.request;
  }

  // The beta's fixed text (i18n.js has each one in Chinese; the dollar
  // lines are its PATTERNS).
  const BETA_TEXT = {
    onCredit: "You're on the starter credit. Add your own key any time.",
    creditGone: "Your starter credit is used up. Add your key to keep talking with Claude.",
    starter: "Starter credit",
    messageReady: "Invite message ready.",
    messageCopied: "Invite message copied.",
  };
  const finite = (v) => v !== null && v !== undefined && v !== "" && Number.isFinite(Number(v));
  const cents = (v) => `$${Math.max(0, Number(v)).toFixed(2)}`;
  // a cap: "$5", or "$2.50" when it isn't whole dollars
  const dollars = (v) => (Number.isInteger(Number(v)) ? `$${Number(v)}` : cents(v));
  // "$4.37 of $5 starter credit left", or "" off the credit
  function creditLine(st) {
    if (!st || !finite(st.credit_usd) || !finite(st.credit_left_usd) || !(Number(st.credit_usd) > 0)) return "";
    return `${cents(st.credit_left_usd)} of ${dollars(st.credit_usd)} starter credit left`;
  }

  // The invite token in a location's #fragment, or null.
  function readInvite(loc) {
    const m = String((loc && loc.hash) || "").match(/[#&]invite=([^&]+)/);
    if (!m) return null;
    try { return decodeURIComponent(m[1]); } catch (e) { return null; }
  }

  function storage(win) {
    try { return win.localStorage || null; } catch (e) { return null; }
  }

  // A family member's phone: no failover router (that is the owner's Pi/PC).
  function isFamilyAccount(win) {
    const s = storage(win);
    try { return Boolean(s && s.getItem(ACCOUNT_KEY) === "family"); } catch (e) { return false; }
  }

  // True when an invite was accepted (the page is reloading); false when
  // there was none, or it failed (the message is in #login-error).
  async function acceptInvite(win) {
    const token = readInvite(win.location);
    if (!token) return false;
    // out of the address bar (and history) before anything else
    win.history.replaceState(null, "", win.location.pathname || "/");
    let message = "This invite link has expired or was already used. Ask for a new one.";
    try {
      const res = await win.fetch("/api/invite/accept", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ invite: token }),
      });
      if (res.ok) {
        const s = storage(win);
        try { if (s) s.setItem(ACCOUNT_KEY, "family"); } catch (e) { /* private mode */ }
        win.location.replace("/");  // the page again, in their language
        return true;
      }
      if (res.status === 429) message = "too many attempts; try again later";
    } catch (e) {
      message = "Couldn't reach the server. Open the link again.";
    }
    const err = win.document && win.document.getElementById("login-error");
    if (err) err.textContent = message;
    return false;
  }

  // JSON from `url` through app.js's request (or a same-origin fetch);
  // throws the server's error message.
  async function callJSON(win, url, options = {}) {
    const res = request ? await request(url, options)
      : await win.fetch(url, { credentials: "same-origin", ...options });
    let body = {};
    try { body = await res.json(); } catch (e) { /* not JSON */ }
    if (!res.ok) throw new Error(body.error || `Error ${res.status}`);
    return body;
  }

  // --- the owner's menu ---
  function setupOwner(win, doc) {
    const $ = (id) => doc.getElementById(id);
    const box = $("family");
    if (!box) return null;
    const openBtn = $("invite-open"), form = $("invite-form"), nameIn = $("invite-name");
    const langIn = $("invite-language"), tzIn = $("invite-tz"), status = $("invite-status");
    const tierIn = $("invite-tier");  // "beta" (the default) or "family"
    const linkIn = $("invite-link"), list = $("family-list");
    const total = $("family-credit");  // everyone's starter credit together
    const credit = { on: false };
    let shown = false;

    const call = (url, options) => callJSON(win, url, options);
    const post = (url, data) => call(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data || {}) });

    // the link, with the server's ready-to-send invite text (family.py
    // invite_message: the link, and for a beta tester the key guide, the
    // credit and the beta's links) when it sent one
    async function share(url, message) {
      linkIn.value = url;
      linkIn.hidden = false;
      const text = typeof message === "string" && message.trim() ? message : "";
      const ready = text ? BETA_TEXT.messageReady : "Invite link ready.";
      const nav = win.navigator || {};
      if (typeof nav.share === "function") {
        try { await nav.share(text ? { text, url } : { url }); status.textContent = ready; return; }
        catch (e) { if (e && e.name === "AbortError") { status.textContent = ready; return; } }
      }
      try {
        await nav.clipboard.writeText(text || url);
        status.textContent = text ? BETA_TEXT.messageCopied : "Invite link copied.";
      } catch (e) {
        status.textContent = "Copy the invite link below.";
      }
    }
    const shareBody = (body) => share(body.url, body.message);

    function row(user) {
      const li = doc.createElement("li");
      const label = doc.createElement("span");
      label.textContent = user.name;
      const state = doc.createElement("span");
      state.className = "hint";
      state.textContent = (user.status === "active" ? "Active" : "Invited") +
        (user.tier === "beta" ? " · Beta" : "");
      if (user.tier === "beta" && finite(user.credit_used_usd) && credit.on) {
        // what they've used of the starter credit (its own node: i18n.js
        // translates "Active · Beta" and "$1.20 used" separately)
        const used = doc.createElement("span");
        used.className = "credit-used";
        used.textContent = `${cents(user.credit_used_usd)} used`;
        state.append(" · ", used);
      }
      const again = doc.createElement("button");
      again.type = "button";
      again.textContent = "New link";
      again.addEventListener("click", async () => {
        try { await shareBody(await post(`/api/users/${encodeURIComponent(user.id)}/invite`)); }
        catch (e) { status.textContent = e.message; }
      });
      const remove = doc.createElement("button");
      remove.type = "button";
      remove.className = "danger";
      remove.textContent = "Remove";
      remove.addEventListener("click", async () => {
        if (!win.confirm(`Remove ${user.name}? This deletes their conversations.`)) return;
        try {
          // POST, not DELETE: with failover this goes cross-origin, and the
          // backends' preflight allows only GET and POST
          await post(`/api/users/${encodeURIComponent(user.id)}/remove`);
          refresh();
        } catch (e) { status.textContent = e.message; }
      });
      li.append(label, " ", state, " ", again, " ", remove);
      return li;
    }

    async function refresh() {
      if (!shown) return;
      try {
        const body = await call("/api/users");
        // the credit is on when it has a cap (credit.py totals)
        credit.on = finite(body.credit_total_cap) && Number(body.credit_total_cap) > 0;
        list.textContent = "";
        for (const u of body.users || []) list.appendChild(row(u));
        if (total) {
          total.hidden = !credit.on;
          total.textContent = credit.on ? `Starter credit: ${cents(body.credit_total_used || 0)} of ` +
            `${dollars(body.credit_total_cap)} used` : "";
        }
      } catch (e) { /* offline: keep the list */ }
    }

    openBtn.addEventListener("click", () => {
      form.hidden = !form.hidden;
      if (!form.hidden && tzIn && !tzIn.value) {
        try { tzIn.value = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { /* none */ }
      }
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      status.textContent = "";
      try {
        const body = await post("/api/invites", {
          name: nameIn.value.trim(), language: langIn.value, timezone: tzIn ? tzIn.value.trim() : "",
          tier: tierIn && tierIn.value ? tierIn.value : "beta" });
        nameIn.value = "";
        form.hidden = true;
        await shareBody(body);
        refresh();
      } catch (err) { status.textContent = err.message; }
    });
    const menu = doc.querySelector("details.menu");
    if (menu) menu.addEventListener("toggle", () => { if (menu.open) refresh(); });

    return {
      show(on) {
        shown = Boolean(on);
        box.hidden = !shown;
        if (shown) refresh();
      },
      refresh,
    };
  }

  // --- a family member's own account (kwil/account.py) ---
  // Every message this section shows (i18n.js has each one in Chinese).
  const ACCOUNT_TEXT = {
    preparing: "Preparing your download…",
    started: "Your download has started.",
    busy: "A download is already being made. Wait for it.",
    tooMany: "Too many downloads. Try again in an hour.",
    tooLarge: "Your data is too large to download here. Ask the person who invited you for a copy.",
    failed: "Couldn't download. Try again.",
    typeIt: "Type DELETE (or your name) to confirm.",
    ask: "Delete your account and everything in it? This can't be undone.",
    deleting: "Deleting…",
    notDeleted: "Couldn't delete your account. Try again.",
  };

  function setupAccount(win, doc) {
    const $ = (id) => doc.getElementById(id);
    const box = $("account-self");
    if (!box) return null;
    const exportBtn = $("account-export"), openBtn = $("account-delete-open");
    const form = $("account-delete-form"), confirmIn = $("account-delete-confirm");
    const status = $("account-status");
    const say = (text) => { if (status) status.textContent = text; };
    const send = (url, options = {}) => (request ? request(url, options)
      : win.fetch(url, { credentials: "same-origin", cache: "no-store", ...options }));

    exportBtn.addEventListener("click", async () => {
      exportBtn.disabled = true;
      say(ACCOUNT_TEXT.preparing);
      try {
        const res = await send("/api/me/export");
        if (!res.ok) {
          say(res.status === 409 ? ACCOUNT_TEXT.busy : res.status === 429 ? ACCOUNT_TEXT.tooMany
            : res.status === 413 ? ACCOUNT_TEXT.tooLarge : ACCOUNT_TEXT.failed);
          return;
        }
        // a file save, not a navigation: an error never replaces the page
        const url = win.URL.createObjectURL(await res.blob());
        const a = doc.createElement("a");
        a.href = url;
        a.download = "kwil-data.zip";
        doc.body.appendChild(a);
        a.click();
        a.remove();
        win.setTimeout(() => win.URL.revokeObjectURL(url), 60000);
        say(ACCOUNT_TEXT.started);
      } catch (e) {
        say(ACCOUNT_TEXT.failed);
      } finally {
        exportBtn.disabled = false;
      }
    });
    openBtn.addEventListener("click", () => {
      form.hidden = !form.hidden;
      say("");
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const typed = String(confirmIn.value || "").trim();
      if (!typed) { say(ACCOUNT_TEXT.typeIt); return; }
      if (typeof win.confirm === "function" && !win.confirm(ACCOUNT_TEXT.ask)) return;
      say(ACCOUNT_TEXT.deleting);
      let res = null;
      try {
        res = await send("/api/me/delete", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ confirm: typed }) });
      } catch (err) { /* shown below */ }
      if (res && res.ok) {
        const s = storage(win);
        try { if (s) s.removeItem(ACCOUNT_KEY); } catch (err) { /* ignore */ }
        win.location.replace("/");  // signed out: the sign-in page
        return;
      }
      say(res && res.status === 400 ? ACCOUNT_TEXT.typeIt : ACCOUNT_TEXT.notDeleted);
    });
    return {
      show(on) {
        box.hidden = !on;
        if (!on) form.hidden = true;
      },
    };
  }

  // --- a family member's own Anthropic key ---
  const KEY_URL = "/api/me/anthropic-key";

  // The key's row in Connections (connections.js): a beta tester needs one
  // to talk at all, so a missing or unreadable key there is flagged.
  function keyRow(st, mode) {
    if (!mode) return { state: "off" };
    if (st.set) return { state: "connected", status: "Key saved", action: "Manage",
                         detail: `•••• ${String(st.last4 || "").slice(0, 4)}` };
    if (st.available === false) return { state: "available", status: "Not available" };
    // on the starter credit with some left: no key needed yet
    if (creditLine(st) && Number(st.credit_left_usd) > 0) {
      return { state: "available", status: BETA_TEXT.starter, action: "Add key", detail: creditLine(st) };
    }
    if (st.unreadable || mode === "required") {
      return { state: "available", status: "Key needed", action: "Add key", alert: true };
    }
    return { state: "available", status: "Optional", action: "Add key" };
  }
  function setupKey(win, doc) {
    const $ = (id) => doc.getElementById(id);
    const box = $("api-key");
    if (!box) return null;
    const form = $("api-key-form"), input = $("api-key-input"), save = $("api-key-save");
    const remove = $("api-key-remove"), state = $("api-key-state"), cost = $("api-key-cost");
    const left = $("api-key-credit"), help = $("api-key-help");
    let mode = null;  // "required" (beta), "optional" (family) or null (hidden)
    let helpPath = "";  // state.key_help: the key guide, where it is served

    function render(st) {
      const set = Boolean(st.set);
      save.textContent = set ? "Replace key" : "Save key";
      remove.hidden = !set;
      if (set) state.textContent = `Your key ending in ${String(st.last4 || "")} is saved.`;
      else if (st.available === false) state.textContent = "This server can't store keys yet.";
      else if (st.unreadable) state.textContent = "Your saved key can't be read any more. Please enter it again.";
      else if (mode === "required" && creditLine(st)) {
        state.textContent = Number(st.credit_left_usd) > 0 ? BETA_TEXT.onCredit : BETA_TEXT.creditGone;
      } else state.textContent = mode === "required" ? "Add your key to start talking with Claude."
        : "Optional: without one, the family's key is used.";
      if (left) {
        left.textContent = set ? "" : creditLine(st);
        left.hidden = !left.textContent;
      }
      if (help) {
        // a same-site path only (the server's credit.HELP_PATH)
        const ok = !set && mode === "required" && /^\/[a-z0-9/-]+$/.test(helpPath);
        if (ok) help.setAttribute("href", helpPath);
        help.hidden = !ok;
      }
      const usd = Number(st.month_usd);
      cost.hidden = !(set && st.month_usd != null && Number.isFinite(usd));
      cost.textContent = cost.hidden ? "" : `About $${usd.toFixed(2)} this month on your key.`;
      win.KwilConnections?.set("anthropic", keyRow(st, mode));  // connections.js
    }

    async function refresh() {
      if (!mode) return;
      try { render(await callJSON(win, KEY_URL)); } catch (e) { /* offline: keep it */ }
    }

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const key = String(input.value || "").trim();
      if (!key) return;
      save.disabled = true;
      state.textContent = "Checking the key with Anthropic…";
      try {
        await callJSON(win, KEY_URL, {
          method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key }) });
        input.value = "";  // never kept in the page once saved
        await refresh();
      } catch (err) {
        state.textContent = err.message;
      } finally {
        save.disabled = false;
      }
    });
    remove.addEventListener("click", async () => {
      if (typeof win.confirm === "function" &&
          !win.confirm("Remove your Anthropic API key from this app?")) return;
      try {
        await callJSON(win, KEY_URL, {
          method: "DELETE", headers: { "Content-Type": "application/json" }, body: "{}" });
        await refresh();
      } catch (err) { state.textContent = err.message; }
    });
    const menu = doc.querySelector("details.menu");
    if (menu) menu.addEventListener("toggle", () => { if (menu.open) refresh(); });

    return {
      show(next, guide) {
        helpPath = typeof guide === "string" ? guide : "";
        if (help && (!helpPath || !mode)) help.hidden = true;
        next = next === "required" || next === "optional" ? next : null;
        const changed = next !== mode;
        mode = next;
        box.hidden = !mode;
        if (!mode) win.KwilConnections?.set("anthropic", { state: "off" });
        if (changed) refresh();
      },
      refresh,
    };
  }

  // --- beta feedback (feedback.py) ---
  const FEEDBACK_TEXT = {
    title: "Send feedback",
    survey: "How's the beta going?",
    sending: "Sending…",
    sent: "Thanks. Your feedback was sent.",
    empty: "Write something first.",
    tooMany: "That's plenty for this hour, thank you. Try again later.",
    full: "You've sent a lot of feedback already. Thank you.",
    tooLong: "That's too long. Shorten an answer.",
    noStore: "Feedback can't be saved on this server yet.",
    failed: "Couldn't send it. Try again.",
    loading: "Loading…",
    none: "No feedback yet.",
    loadFailed: "Couldn't load feedback. Try again.",
    general: "Feedback",
    surveyKind: "Survey",
    you: "You",
    broke: "What broke?",
    confused: "What confused you?",
    pay: "What would you pay for?",
  };
  const FIELDS = ["broke", "confused", "pay"];

  function setupFeedback(win, doc) {
    const $ = (id) => doc.getElementById(id);
    const sheet = $("feedback-sheet");
    if (!sheet) return null;
    const sendRow = $("feedback-send"), form = $("feedback-form"), title = $("feedback-title");
    const submit = $("feedback-submit"), status = $("feedback-status");
    const fields = Object.fromEntries(FIELDS.map((k) => [k, $(`feedback-${k}`)]));
    const due = $("feedback-due"), dueOpen = $("feedback-due-open"), dueNo = $("feedback-due-dismiss");
    const readRow = $("feedback-read"), listSheet = $("feedback-list-sheet");
    const list = $("feedback-list"), listStatus = $("feedback-list-status");
    const send = (url, options = {}) => (request ? request(url, options)
      : win.fetch(url, { credentials: "same-origin", cache: "no-store", ...options }));
    let kind = "general";
    let dueShown = false;  // state.feedback_due, until answered or dismissed here
    let returnTo = null;
    const say = (text) => { if (status) status.textContent = text; };

    // a bottom sheet (DESIGN_SYSTEM.md): modal, Esc and Close shut it, and
    // focus goes back to what opened it
    function openSheet(dlg, first) {
      returnTo = doc.activeElement || null;
      if (typeof dlg.showModal === "function" && !dlg.open) dlg.showModal(); else dlg.open = true;
      doc.documentElement?.classList?.add("conn-sheet-open");
      if (first && typeof first.focus === "function") first.focus();
    }
    function closeSheet(dlg) {
      if (typeof dlg.close === "function" && dlg.open) dlg.close(); else dlg.open = false;
    }
    for (const dlg of [sheet, listSheet]) {
      if (!dlg) continue;
      dlg.addEventListener("close", () => {
        doc.documentElement?.classList?.remove("conn-sheet-open");
        const back = returnTo;
        returnTo = null;
        if (back && typeof back.focus === "function") back.focus();
      });
      // a tap on the backdrop (the dialog itself, outside its content)
      dlg.addEventListener("click", (e) => { if (e && e.target === dlg) closeSheet(dlg); });
      const close = dlg.querySelector ? dlg.querySelector(".conn-close") : null;
      if (close) close.addEventListener("click", () => closeSheet(dlg));
    }

    function open(which) {
      kind = which === "survey" ? "survey" : "general";
      if (title) title.textContent = kind === "survey" ? FEEDBACK_TEXT.survey : FEEDBACK_TEXT.title;
      say("");
      openSheet(sheet, fields.broke);
    }

    async function onSubmit(e) {
      if (e && e.preventDefault) e.preventDefault();
      const body = { kind };
      for (const k of FIELDS) body[k] = fields[k] ? String(fields[k].value || "").trim() : "";
      if (!FIELDS.some((k) => body[k])) { say(FEEDBACK_TEXT.empty); return; }
      if (submit) submit.disabled = true;
      say(FEEDBACK_TEXT.sending);
      let res = null;
      try {
        res = await send("/api/me/feedback", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      } catch (err) { /* shown below */ }
      if (submit) submit.disabled = false;
      if (res && res.ok) {
        for (const k of FIELDS) if (fields[k]) fields[k].value = "";
        say(FEEDBACK_TEXT.sent);
        if (kind === "survey") showDue(false);  // answered: never due again
        return;
      }
      const code = res ? res.status : 0;
      say(code === 429 ? FEEDBACK_TEXT.tooMany : code === 409 ? FEEDBACK_TEXT.full
        : code === 413 ? FEEDBACK_TEXT.tooLong : code === 503 ? FEEDBACK_TEXT.noStore
        : FEEDBACK_TEXT.failed);
    }

    function showDue(on) {
      dueShown = Boolean(on);
      if (due) due.hidden = !dueShown;
    }
    let dismissed = false;
    async function dismiss() {
      dismissed = true;  // gone from this page now, whatever the server says
      showDue(false);
      try {
        await send("/api/me/feedback/dismiss", {
          method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      } catch (err) { /* offline: it comes back on another load, and can be dismissed again */ }
    }

    // the owner's list: every value is someone else's text, set as text
    function entryItem(name, entry) {
      const li = doc.createElement("li");
      li.className = "fb-item";
      const head = doc.createElement("p");
      head.className = "fb-head";
      const who = doc.createElement("span");
      who.className = "fb-who";
      who.textContent = name;
      const what = doc.createElement("span");
      what.className = "fb-kind";
      what.textContent = entry.kind === "survey" ? FEEDBACK_TEXT.surveyKind : FEEDBACK_TEXT.general;
      const when = doc.createElement("span");
      when.className = "fb-when";
      const at = Number(entry.at);
      try { when.textContent = Number.isFinite(at) ? new Date(at * 1000).toLocaleDateString() : ""; }
      catch (err) { when.textContent = ""; }
      head.append(who, " · ", what, " · ", when);
      li.appendChild(head);
      for (const k of FIELDS) {
        const text = typeof entry[k] === "string" ? entry[k] : "";
        if (!text) continue;
        const q = doc.createElement("p");
        q.className = "fb-q";
        q.textContent = FEEDBACK_TEXT[k];
        const a = doc.createElement("p");
        a.className = "fb-a";
        a.textContent = text;
        li.append(q, a);
      }
      return li;
    }
    async function openList() {
      if (!listSheet || !list) return;
      list.textContent = "";
      if (listStatus) listStatus.textContent = FEEDBACK_TEXT.loading;
      openSheet(listSheet, listSheet.querySelector ? listSheet.querySelector(".conn-close") : null);
      let body = null;
      try { body = await callJSON(win, "/api/feedback"); } catch (err) { body = null; }
      if (!body) { if (listStatus) listStatus.textContent = FEEDBACK_TEXT.loadFailed; return; }
      const all = [];
      for (const e of Array.isArray(body.owner) ? body.owner : []) all.push([FEEDBACK_TEXT.you, e]);
      for (const p of Array.isArray(body.people) ? body.people : []) {
        for (const e of Array.isArray(p && p.entries) ? p.entries : []) all.push([String(p.name || p.id || ""), e]);
      }
      const items = all.filter(([, e]) => e && typeof e === "object")
        .sort((x, y) => (Number(y[1].at) || 0) - (Number(x[1].at) || 0));
      for (const [name, e] of items) list.appendChild(entryItem(name, e));
      if (listStatus) listStatus.textContent = items.length ? "" : FEEDBACK_TEXT.none;
    }

    if (sendRow) sendRow.addEventListener("click", () => open("general"));
    if (form) form.addEventListener("submit", onSubmit);
    if (dueOpen) dueOpen.addEventListener("click", () => open("survey"));
    if (dueNo) dueNo.addEventListener("click", dismiss);
    if (readRow) readRow.addEventListener("click", openList);

    return {
      onState(state) {
        const member = state.owner === false;
        if (sendRow) sendRow.hidden = !member;
        if (readRow) readRow.hidden = !(state.owner && state.family);
        showDue(member && state.feedback_due === true && !dismissed);
      },
      open, openList, dismiss,
      get due() { return dueShown; },
    };
  }

  let owner = null;
  let account = null;
  let keys = null;
  let feedback = null;
  // From app.js after each /api/state: the owner's menu only for the owner
  // with [family] on; a family member's page hides the owner-only tabs and
  // shows their own key's settings and "Download my data" / "Delete my account".
  function onState(state, win = root, doc = root.document) {
    if (!doc) return;
    const app = doc.getElementById("app");
    if (app && app.dataset) app.dataset.account = state.owner === false ? "family" : "owner";
    const s = storage(win);
    if (state.owner) {
      try { if (s) s.removeItem(ACCOUNT_KEY); } catch (e) { /* ignore */ }
    }
    if (!owner) owner = setupOwner(win, doc);
    if (owner) owner.show(Boolean(state.owner && state.family));
    if (!keys) keys = setupKey(win, doc);
    if (keys) keys.show(state.owner === false ? state.api_key : null, state.key_help);
    if (!account) account = setupAccount(win, doc);
    if (account) account.show(state.owner === false);
    if (!feedback) feedback = setupFeedback(win, doc);
    if (feedback) feedback.onState(state);
    // a beta tester's first-run checklist (i18n.js)
    if (win.KwilI18n && typeof win.KwilI18n.renderChecklist === "function") {
      win.KwilI18n.renderChecklist(win, doc, state);
    }
  }

  root.KwilFamily = { readInvite, acceptInvite, isFamilyAccount, onState, setupOwner,
                           setupKey, setupAccount, setupFeedback, configure, ACCOUNT_TEXT,
                           BETA_TEXT, FEEDBACK_TEXT, creditLine };
})(typeof window !== "undefined" ? window : globalThis);
