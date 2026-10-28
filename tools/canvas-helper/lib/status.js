// The popup's words for the worker's status (helper.status()): paired or
// not, where, the last sync, and what happened last. Plain text only.

const RESULTS = {
  ok: "",
  partial: "Part of your courses were read; the rest come next time.",
  no_canvas: "Open your school's Canvas in this browser once, so the helper can find it.",
  canvas_signed_out: "Sign in to Canvas in this browser; the helper carries on after that.",
  canvas_rate_limited: "Canvas asked the helper to slow down; it tries again later.",
  canvas_unreachable: "Couldn't reach Canvas; the helper tries again later.",
  canvas_error: "Canvas didn't answer as expected; the helper tries again later.",
  unpaired: "Kwil no longer accepts this helper. Set it up again in Kwil.",
  kwil_off: "The Canvas helper is off in your Kwil.",
  kwil_busy: "Kwil was busy; the helper tries again later.",
  kwil_rate_limited: "Kwil asked the helper to slow down; it tries again later.",
  kwil_unreachable: "Couldn't reach Kwil; the helper tries again later.",
  kwil_rejected: "Kwil didn't accept what was sent; the helper tries again later.",
  kwil_too_large: "Too much at once for Kwil; the helper sends less next time.",
  needs_permission: "The helper isn't allowed on your Canvas or Kwil site any more. " +
                    "Open that site and click the helper's icon there.",
};

export function hostOf(origin) {
  try { return new URL(origin).host; } catch (e) { return ""; }
}

export function when(isoText, nowMs = Date.now()) {
  const t = Date.parse(isoText || "");
  if (!Number.isFinite(t)) return "never";
  const min = Math.round((nowMs - t) / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  if (h < 48) return `${h} h ago`;
  return new Date(t).toISOString().slice(0, 10);
}

// {title, lines: [...]} for the popup.
export function describe(st, nowMs = Date.now()) {
  if (!st || !st.paired) {
    return { title: "Not paired",
             lines: ["In Kwil, open the menu, then School deadlines, and tap " +
                     "Set up Canvas helper. The helper picks the code up by itself."] };
  }
  const lines = [`Kwil: ${hostOf(st.kwil)}`,
                 `Canvas: ${st.canvas ? hostOf(st.canvas) : "not found yet"}`,
                 `Last sync: ${st.running ? "syncing now" : when(st.lastSync, nowMs)}`];
  if (st.counts && st.result === "ok") {
    lines.push(`${st.counts.courses} course(s), ${st.counts.files} file(s) sent`);
  }
  const why = RESULTS[st.result];
  if (why) lines.push(why);
  else if (st.canvasSignedOut) lines.push(RESULTS.canvas_signed_out);
  return { title: "Paired", lines };
}
