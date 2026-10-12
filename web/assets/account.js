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
    profile = null; searches = []; renderSearches(); renderProfile();
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

  /* ---- the profile: the email switches, and the address the mailer uses ---- */
  let profile = null;
  async function ensureProfile() {
    if (!session || !session.user || !session.user.id || !session.user.email) return null;
    const rows = await call(`/rest/v1/profiles?select=user_id,email,daily_email,alerts&user_id=eq.${session.user.id}`,
      { token: session.access_token });
    profile = (rows || [])[0] || null;
    if (!profile || profile.email !== session.user.email) {
      profile = { user_id: session.user.id, email: session.user.email,
                  daily_email: profile ? profile.daily_email : false, alerts: profile ? profile.alerts : true };
      await call("/rest/v1/profiles", { method: "POST", token: session.access_token,
        headers: { Prefer: "resolution=merge-duplicates,return=minimal" }, body: [profile] });
    }
    renderProfile();
    return profile;
  }
  async function setDaily(on) {
    if (!session) return;
    await call(`/rest/v1/profiles?user_id=eq.${session.user.id}`, { method: "PATCH", token: session.access_token,
      headers: { Prefer: "return=minimal" }, body: { daily_email: !!on } });
    if (profile) profile.daily_email = !!on;
  }
  function renderProfile() {
    const d = $a("acct-daily");
    if (d) d.checked = !!(profile && profile.daily_email);
  }

  /* ---- saved searches ---- */
  let filters = null;                  // {current, apply, describe} handed in by app.js
  let searches = [];
  async function loadSearches() {
    if (!session) { searches = []; renderSearches(); return; }
    searches = (await call("/rest/v1/saved_searches?select=id,name,filters,alerts,created_at&order=created_at.desc",
      { token: session.access_token })) || [];
    renderSearches();
  }
  async function saveSearch() {
    if (!session) { open("signin"); note("Sign in to keep a search on your account.", true); return; }
    if (!filters) return;
    const name = window.prompt("Name this search", "");
    if (!name || !name.trim()) return;
    await call("/rest/v1/saved_searches", { method: "POST", token: session.access_token,
      headers: { Prefer: "return=minimal" }, body: [{ name: name.trim(), filters: filters.current(), alerts: true }] });
    await loadSearches();
    open("me");
    note(`Saved "${name.trim()}". A scan that finds a card fitting it will email you.`, true);
  }
  async function removeSearch(id) {
    await call(`/rest/v1/saved_searches?id=eq.${encodeURIComponent(id)}`,
      { method: "DELETE", token: session.access_token, headers: { Prefer: "return=minimal" } });
    await loadSearches();
  }
  async function setSearchAlerts(id, on) {
    await call(`/rest/v1/saved_searches?id=eq.${encodeURIComponent(id)}`, { method: "PATCH", token: session.access_token,
      headers: { Prefer: "return=minimal" }, body: { alerts: !!on } });
    const s = searches.find((x) => x.id === id);
    if (s) s.alerts = !!on;
  }
  function renderSearches() {
    const list = $a("acct-searches");
    if (!list) return;
    list.replaceChildren();
    $a("acct-searches-note").hidden = searches.length > 0;
    searches.forEach((s) => {
      const li = document.createElement("li");
      const name = document.createElement("span"); name.className = "name"; name.textContent = s.name;
      const acts = document.createElement("span"); acts.className = "acts";
      const apply = document.createElement("a"); apply.href = "#"; apply.textContent = "Apply";
      apply.addEventListener("click", (e) => { e.preventDefault(); if (filters) filters.apply(s.filters); close(); });
      const alerts = document.createElement("label");
      const box = document.createElement("input"); box.type = "checkbox"; box.checked = !!s.alerts;
      box.addEventListener("change", () => setSearchAlerts(s.id, box.checked).catch((e) => note(e.message)));
      alerts.append(box, document.createTextNode(" Email me"));
      const del = document.createElement("a"); del.href = "#"; del.textContent = "Delete"; del.className = "danger";
      del.addEventListener("click", (e) => { e.preventDefault(); removeSearch(s.id).catch((err) => note(err.message)); });
      acts.append(apply, alerts, del);
      const what = document.createElement("span"); what.className = "what";
      what.textContent = filters ? filters.describe(s.filters || {}) : "";
      li.append(name, acts, what);
      list.append(li);
    });
  }

  /* everything the account holds, read after a sign-in or a restore */
  async function pullAll() {
    await pullSaved();
    try { await ensureProfile(); } catch (e) { note(`Could not read your settings: ${e.message}`); }
    try { await loadSearches(); } catch (e) { note(`Could not read your saved searches: ${e.message}`); }
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
      await pullAll();
      close();
    }); });

    $a("acct-signup").addEventListener("submit", (e) => { e.preventDefault(); run(e.target, async (f) => {
      checkPassword(f.password);
      if (f.password !== f.again) throw new Error("The two passwords do not match.");
      const result = await signUp(f.email, f.password);
      if (result === "confirm") { show("signin"); note(`Check ${f.email} for a confirmation link, then sign in.`, true); return; }
      await pullAll();
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
      await pullAll();
      close();
    }); });

    $a("acct-newpass").addEventListener("submit", (e) => { e.preventDefault(); run(e.target, async (f) => {
      checkPassword(f.password);
      await setPassword(f.password);
      await pullAll();
      close();
    }); });

    $a("acct-daily").addEventListener("change", (e) => setDaily(e.target.checked).catch((err) => note(err.message)));
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
    filters = savedHooks.filters || null;
    wire();
    const fromLink = takeRecoveryFromHash();
    if (!fromLink) await restore();
    if (fromLink && session) {
      // the link carries the session but not who it belongs to; ask
      try {
        const u = await call("/auth/v1/user", { token: session.access_token });
        if (u && u.id) keep({ ...session, user: { id: u.id, email: u.email } });
      } catch { /* the new-password screen still works; the profile waits */ }
    }
    if (session) {
      try { await pullAll(); } catch (e) { note(`Could not read your saved cards: ${e.message}`); }
    }
    if (fromLink) open("newpass");
  }

  return { init, user, open, close, savedChanged, saveSearch, signIn, signUp, signOut, sendCode, resetWithCode,
           onChange: (fn) => listeners.push(fn), _call: call };
})();
/* A const at the top of a classic script is not a property of window, and
   app.js looks for window.Account so that a page without this file still
   runs. Say it explicitly. */
window.Account = Account;
