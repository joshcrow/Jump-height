/*
  JumpHeight setup — the four screens' behaviour (server.py handles the
  three endpoints this posts to; docs/sync-agent-plan.md, "Setup").

  Every screen shows exactly one PRIMARY button (`.btn`) at a time — the
  same "one button, states its result" rule web/sync/sync.js follows for
  the rider's page. Screen 3 is the one stated exception, and it is
  exactly the shape CONTRACT.md already names for that page's own re-save
  link: `#btn-skip` is `.btn-link` (a secondary, link-styled action), never
  a second black button beside `#btn-garmin`.

  A screen's own primary button is reused across its two states (e.g.
  Connect -> Continue) rather than swapped for a second element, tracked
  in `dataset.state` — text comparison would be fragile the moment the
  displayed word ever needs translating; the dataset flag never does.
*/
(function () {
  "use strict";

  function byId(id) { return document.getElementById(id); }

  const SCREENS = [1, 2, 3, 4].map((n) => byId("screen-" + n));

  function showScreen(n) {
    SCREENS.forEach((el, i) => { el.hidden = (i + 1) !== n; });
    if (n === 4) requestNotificationPermission();
  }

  function setStatus(el, text, kind) {
    el.textContent = text || "";
    el.hidden = !text;
    el.className = "status muted" + (kind ? " is-" + kind : "");
  }

  async function postJSON(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    return res.json();
  }

  let notifyRequested = false;
  function requestNotificationPermission() {
    if (notifyRequested) return;
    notifyRequested = true;
    // Fire-and-forget: "the macOS notification prompt fires here; no copy
    // of ours" (docs/sync-agent-plan.md, screen 4) — nothing on this
    // screen reads the response either way.
    postJSON("/api/notify/request", {}).catch(() => {});
  }

  // -------------------------------------------------------- screen 1 -----
  byId("btn-continue-1").addEventListener("click", () => showScreen(2));

  // -------------------------------------------------------- screen 2 -----
  const btnGoogle = byId("btn-google");
  const googleStatus = byId("google-status");
  btnGoogle.dataset.state = "start";

  btnGoogle.addEventListener("click", async () => {
    if (btnGoogle.dataset.state === "connected") {
      showScreen(3);
      return;
    }
    btnGoogle.disabled = true;
    setStatus(googleStatus, "", null);
    let r;
    try {
      r = await postJSON("/api/google/start", {});
    } catch (e) {
      setStatus(googleStatus, String(e), "bad");
      btnGoogle.disabled = false;
      return;
    }
    if (r.ok) {
      // "Connected as nick@…" — the literal spec line, with the live
      // account filled in; not authored copy for the value itself.
      setStatus(googleStatus, "Connected as " + r.account, "ok");
      btnGoogle.textContent = "Continue";
      btnGoogle.dataset.state = "connected";
      btnGoogle.disabled = false;
    } else {
      setStatus(googleStatus, r.error || "", "bad");
      btnGoogle.disabled = false;
    }
  });

  // -------------------------------------------------------- screen 3 -----
  const garminForm = byId("garmin-form");
  const btnGarmin = byId("btn-garmin");
  const btnSkip = byId("btn-skip");
  const garminEmail = byId("garmin-email");
  const garminPassword = byId("garmin-password");
  const garminCode = byId("garmin-code");
  const garminStatus = byId("garmin-status");
  btnGarmin.dataset.state = "start";

  garminForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (btnGarmin.dataset.state === "signed-in") {
      showScreen(4);
      return;
    }
    btnGarmin.disabled = true;
    btnSkip.disabled = true;
    setStatus(garminStatus, "", null);
    let r;
    try {
      r = await postJSON("/api/garmin/login", {
        email: garminEmail.value,
        password: garminPassword.value,
        mfa_code: garminCode.hidden ? null : garminCode.value,
      });
    } catch (e) {
      setStatus(garminStatus, String(e), "bad");
      btnGarmin.disabled = false;
      btnSkip.disabled = false;
      return;
    }
    if (r.ok) {
      setStatus(garminStatus, "Signed in", "ok");
      garminEmail.hidden = true;
      garminPassword.hidden = true;
      garminCode.hidden = true;
      btnSkip.hidden = true;
      btnGarmin.textContent = "Continue";
      btnGarmin.dataset.state = "signed-in";
      btnGarmin.disabled = false;
    } else if (r.needs_mfa) {
      // "+ a code field only if Garmin asks" — its appearance IS the ask;
      // no sentence is added, per the sentence only if it changes what he
      // does.
      garminCode.hidden = false;
      garminCode.focus();
      btnGarmin.disabled = false;
      btnSkip.disabled = false;
    } else {
      setStatus(garminStatus, r.error || "", "bad");
      btnGarmin.disabled = false;
      btnSkip.disabled = false;
    }
  });

  btnSkip.addEventListener("click", () => showScreen(4));

  // -------------------------------------------------------- screen 4 -----
  byId("btn-close").addEventListener("click", () => {
    window.close();  // a no-op if this tab was not opened by script
  });

  // ------------------------------------------------------------ entry ----
  // "Re-runnable from the menu bar; each step individually" — a caller
  // opens e.g. ?step=3 to land straight on the Garmin screen.
  const requested = parseInt(new URLSearchParams(location.search).get("step"), 10);
  showScreen(requested >= 1 && requested <= 4 ? requested : 1);

  // Test seam, the same shape as web/sync/sync.js's window.__mock/__sync
  // (CONTRACT.md §3.3): a named hook instead of driving pixels to navigate.
  window.__setup = { showScreen };
})();
