// When the server has been updated (its /healthz "version" changes), reload
// the page once to get the new code, at a quiet moment only: never while a
// request runs, a recording or confirmation is open, the voice conversation
// is on screen, or there's unsent text. Hidden pages reload when shown again.
(() => {
  let current = null;

  function quiet() {
    if (document.visibilityState !== "visible") return false;
    if (typeof busy !== "undefined" && busy) return false;
    if (typeof confirming !== "undefined" && confirming) return false;
    if (typeof recorder !== "undefined" && recorder && recorder.state === "recording") return false;
    const voice = document.getElementById("voice");
    if (voice && !voice.hidden) return false;
    return ![...document.querySelectorAll("textarea, input[type=text]")].some((el) => el.value.trim());
  }

  async function check() {
    let version;
    try {
      const res = await fetch("/healthz", { cache: "no-store" });
      version = (await res.json()).version;
    } catch (e) { return; }  // offline: try again later
    if (!version || version === "unknown") return;
    if (current === null) { current = version; return; }
    if (version === current || !quiet()) return;
    try {  // at most once per version, even if the reload lands on old code
      if (sessionStorage.getItem("hc-reloaded-for") === version) return;
      sessionStorage.setItem("hc-reloaded-for", version);
    } catch (e) { /* private mode: reload anyway */ }
    location.reload();
  }

  check();
  setInterval(check, 60000);
  document.addEventListener("visibilitychange", check);
})();
