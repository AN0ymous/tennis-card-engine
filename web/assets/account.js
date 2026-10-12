/* Accounts: an email address and a password for this site, a code sent to
   the email to reset a forgotten password, and saved cards that follow the
   person between devices and survive the page being closed.

   Supabase holds the accounts and the rows. This file talks to it with plain
   fetch calls against its documented endpoints, so the page loads nothing
   from a third-party script host and works the same hosted or on the PC. The
   key below is the project's public ("anon") key: it is meant to be in a
   page, and every table it can reach is guarded by row-level security, so it
   reads and writes only the rows of whoever is signed in.

   Nothing here is required. A visitor who never signs in uses the site
   exactly as before, with stars kept in this browser alone. */

const ACCOUNT = {
  url: "https://qmbyqyvxcqjdglxzdfsp.supabase.co",
  anon: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InFtYnlxeXZ4Y3FqZGdseHpkZnNwIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTE3NzY1MjksImV4cCI6MjEwNzM1MjUyOX0.pPAjnc8azT7ehmj4vsA-LmnhdcDVVy3ytk8KhjKySSs",
  sessionKey: "tce.session",
  minPassword: 8,
};

const Account = (() => {
  const $a = (id) => document.getElementById(id);
  let session = null;                  // {access_token, refresh_token, expires_at, user:{id,email}}
  let saved = null;                    // {get, set} handed in by app.js
  let listeners = [];
  let pendingEmail = "";               // the address a reset code was sent to

  /* ---- talking to Supabase ---- */
  function messageOf(data, status) {
    if (data && typeof data === "object") {
      const m = data.msg || data.error_description || data.message || data.error;
      if (typeof m === "string" && m) return m;
    }
    return `Supabase answered ${status}`;
  }

  async function call(path, { method = "GET", body, token, headers = {} } = {}) {
    const r = await fetch(ACCOUNT.url + path, {
      method,
      headers: {
        apikey: ACCOUNT.anon,
        Authorization: `Bearer ${token || ACCOUNT.anon}`,
        "Content-Type": "application/json",
        ...headers,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await r.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = null; }
    if (!r.ok) throw new Error(messageOf(data, r.status));
    return data;
  }

  /* ---- the session ---- */
  function keep(s) {
    session = s && s.access_token ? {
      access_token: s.access_token,
      refresh_token: s.refresh_token || (session && session.refresh_token) || "",
      expires_at: s.expires_at || Math.floor(Date.now() / 1000) + (s.expires_in || 3600),
      user: { id: (s.user || {}).id || (session && session.user.id), email: (s.user || {}).email || (session && session.user.email) },
    } : null;
    try {
      if (session) localStorage.setItem(ACCOUNT.sessionKey, JSON.stringify(session));
      else localStorage.removeItem(ACCOUNT.sessionKey);
    } catch { /* private mode: the session lasts the page */ }
    render();
    listeners.forEach((fn) => fn(session));
  }

  async function restore() {
    let s = null;
    try { s = JSON.parse(localStorage.getItem(ACCOUNT.sessionKey) || "null"); } catch { s = null; }
    if (!s || !s.access_token) return null;
    if (s.expires_at - 60 > Date.now() / 1000) { session = s; render(); return s; }
    if (!s.refresh_token) { keep(null); return null; }
    try {
      const fresh = await call("/auth/v1/token?grant_type=refresh_token",
        { method: "POST", body: { refresh_token: s.refresh_token } });
      session = s;                              // so keep() can fall back to its user
      keep(fresh);
      return session;
    } catch {
      keep(null);
      return null;
    }
  }

  const user = () => (session ? session.user : null);

  /* ---- sign up, sign in, out ---- */
  async function signUp(email, password) {
    const data = await call("/auth/v1/signup", { method: "POST", body: { email, password } });
    if (data && data.access_token) { keep(data); return "signed-in"; }
    return "confirm";                           // the project asks new accounts to confirm by email
  }

  async function signIn(email, password) {
    keep(await call("/auth/v1/token?grant_type=password", { method: "POST", body: { email, password } }));
  }

  async function signOut() {
    const token = session && session.access_token;
    keep(null);
    if (saved) saved.set({});                   // the stars were the account's
    if (token) { try { await call("/auth/v1/logout", { method: "POST", token }); } catch { /* already out */ } }
  }

  /* ---- forgot password: a code to the email, then a new password ---- */
  async function sendCode(email) {
    await call("/auth/v1/recover", { method: "POST", body: { email } });
    pendingEmail = email;
  }

  async function resetWithCode(email, code, password) {
    const data = await call("/auth/v1/verify", { method: "POST",
      body: { type: "recovery", email, token: String(code).trim() } });
    if (!data || !data.access_token) throw new Error("That code was not accepted.");
    await call("/auth/v1/user", { method: "PUT", token: data.access_token, body: { password } });
    keep(data);
  }

  /* A reset link (the other form Supabase can send) lands back here with the
     session in the hash. Take it before app.js reads the hash for routing. */
  function takeRecoveryFromHash() {
    const h = location.hash || "";
    if (!h.includes("access_token=") || !h.includes("type=recovery")) return false;
    const p = new URLSearchParams(h.slice(1));
    keep({ access_token: p.get("access_token"), refresh_token: p.get("refresh_token") || "",
           expires_in: Number(p.get("expires_in") || 3600), user: {} });
    history.replaceState("", document.title, location.pathname + location.search);
    return true;
  }

  async function setPassword(password) {
    if (!session) throw new Error("Sign in first.");
    const u = await call("/auth/v1/user", { method: "PUT", token: session.access_token, body: { password } });
    if (u && u.email) keep({ ...session, user: { id: u.id, email: u.email } });
  }

  async function deleteMe() {
    if (!session) return;
    await call("/rest/v1/rpc/delete_me", { method: "POST", token: session.access_token, body: {} });
    keep(null);
    if (saved) saved.set({});
  }

  /* ---- saved cards, kept on the account ---- */
  async function pullSaved() {
    if (!session || !saved) return;
    const rows = await call("/rest/v1/saved_listings?select=key,entry", { token: session.access_token });
    const mine = {};
    (rows || []).forEach((r) => { if (r.key && r.entry) mine[r.key] = r.entry; });
    const local = saved.get() || {};
    const toPush = Object.keys(local).filter((k) => !mine[k]).map((k) => ({ key: k, entry: local[k] }));
    if (toPush.length) await upsert(toPush);
    toPush.forEach((p) => { mine[p.key] = p.entry; });
    saved.set(mine);
  }

  function upsert(rows) {
    return call("/rest/v1/saved_listings", { method: "POST", token: session.access_token,
      headers: { Prefer: "resolution=merge-duplicates,return=minimal" }, body: rows });
  }

  function remove(key) {
    return call(`/rest/v1/saved_listings?key=eq.${encodeURIComponent(key)}`,
      { method: "DELETE", token: session.access_token, headers: { Prefer: "return=minimal" } });
  }

  /* app.js calls this whenever a star goes on or off, or a mark changes.
     Fire and forget: the browser copy is already updated, and a failure
     shows in the account panel rather than stopping the page. */
  function savedChanged(key, entry) {
    if (!session) return;
    const p = entry ? upsert([{ key, entry }]) : remove(key);
    p.catch((e) => note(`Could not save to your account: ${e.message}`));
  }

  /* ---- the panel ---- */
  const PANELS = ["signin", "signup", "forgot", "code", "newpass", "me"];
  function show(panel) {
    PANELS.forEach((p) => { const n = $a(`acct-${p}`); if (n) n.hidden = p !== panel; });
    note("");
    const first = document.querySelector(`#acct-${panel} input`);
    if (first) setTimeout(() => first.focus(), 30);
  }
  function note(text, ok) {
    const n = $a("acct-msg");
    if (!n) return;
    n.textContent = text || "";
    n.hidden = !text;
    n.classList.toggle("is-ok", !!ok);
  }
  function open(panel) {
    const o = $a("account");
    if (!o) return;
    o.hidden = false;
    document.body.classList.add("account-open");
    show(panel || (session ? "me" : "signin"));
  }
  function close() {
    const o = $a("account");
    if (!o) return;
    o.hidden = true;
    document.body.classList.remove("account-open");
  }

  function render() {
    const b = $a("account-btn");
    if (b) {
      b.textContent = session ? (session.user.email || "Account") : "Sign in";
      b.classList.toggle("is-in", !!session);
      b.title = session ? "Your account" : "Sign in or create an account";
    }
    const who = $a("acct-who");
    if (who) who.textContent = session ? session.user.email || "" : "";
  }

  function fieldsOf(form) {
    const o = {};
    form.querySelectorAll("input").forEach((i) => { o[i.name] = i.value.trim(); });
    return o;
  }

  function checkPassword(p) {
    if (!p || p.length < ACCOUNT.minPassword) throw new Error(`Use a password of at least ${ACCOUNT.minPassword} characters.`);
  }

  async function run(form, action) {
    const btn = form.querySelector("button[type=submit]");
    if (btn) btn.disabled = true;
    note("");
    try { await action(fieldsOf(form)); }
    catch (e) { note(e.message || String(e)); }
    finally { if (btn) btn.disabled = false; }
  }

  function wire() {
    const btn = $a("account-btn");
    if (!btn) return;
    btn.addEventListener("click", () => open());
    $a("acct-close").addEventListener("click", close);
    $a("account").addEventListener("click", (e) => { if (e.target === $a("account")) close(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$a("account").hidden) close(); });
    document.querySelectorAll("[data-acct-go]").forEach((a) => a.addEventListener("click", (e) => {
      e.preventDefault(); show(a.dataset.acctGo);
    }));

    $a("acct-signin").addEventListener("submit", (e) => { e.preventDefault(); run(e.target, async (f) => {
      await signIn(f.email, f.password);
      await pullSaved();
      close();
    }); });

    $a("acct-signup").addEventListener("submit", (e) => { e.preventDefault(); run(e.target, async (f) => {
      checkPassword(f.password);
      if (f.password !== f.again) throw new Error("The two passwords do not match.");
      const result = await signUp(f.email, f.password);
      if (result === "confirm") { show("signin"); note(`Check ${f.email} for a confirmation link, then sign in.`, true); return; }
      await pullSaved();
      close();
    }); });

    $a("acct-forgot").addEventListener("submit", (e) => { e.preventDefault(); run(e.target, async (f) => {
      await sendCode(f.email);
      $a("acct-code").querySelector("input[name=email]").value = f.email;
      show("code");
      note(`A code is on its way to ${f.email}. It is good for an hour.`, true);
    }); });

    $a("acct-code").addEventListener("submit", (e) => { e.preventDefault(); run(e.target, async (f) => {
      checkPassword(f.password);
      await resetWithCode(f.email || pendingEmail, f.code, f.password);
      await pullSaved();
      close();
    }); });

    $a("acct-newpass").addEventListener("submit", (e) => { e.preventDefault(); run(e.target, async (f) => {
      checkPassword(f.password);
      await setPassword(f.password);
      await pullSaved();
      close();
    }); });

    $a("acct-signout").addEventListener("click", () => { signOut(); close(); });
    $a("acct-delete").addEventListener("click", () => {
      if (!window.confirm("Delete your account and everything saved on it? This cannot be undone.")) return;
      deleteMe().then(close).catch((e) => note(e.message));
    });
  }

  /* app.js hands over how to read and replace its saved-card map, then this
     restores any session and merges the account's cards in. */
  async function init(savedHooks) {
    saved = savedHooks;
    wire();
    const fromLink = takeRecoveryFromHash();
    if (!fromLink) await restore();
    if (session) {
      try { await pullSaved(); } catch (e) { note(`Could not read your saved cards: ${e.message}`); }
    }
    if (fromLink) open("newpass");
  }

  return { init, user, open, close, savedChanged, signIn, signUp, signOut, sendCode, resetWithCode,
           onChange: (fn) => listeners.push(fn), _call: call };
})();
/* A const at the top of a classic script is not a property of window, and
   app.js looks for window.Account so that a page without this file still
   runs. Say it explicitly. */
window.Account = Account;
