/* Tennis Card Engine -- browser UI.
   Talks to the local server in server.py; never sees your eBay credentials. */

const $ = (id) => document.getElementById(id);
const API = {
  config: "api/config",
  events: (since) => `api/events?since=${since}`,
  scan: "api/scan",
  stop: "api/stop",
  matches: "api/matches",
  board: "api/board",
  spreadsheet: "api/spreadsheet",
  portrait: (player) => `api/portrait?player=${encodeURIComponent(player)}`,
};

const portraits = new Map();          // player -> portrait payload, fetched once

let HOSTED = false;                  // true when there is no server: GitHub Pages

const state = {
  config: null,
  matches: [],
  board: [],
  price: { min: 0, max: null },
  listing: "all",
  sort: "newest",
  bookend: "all",
  window: "any",
  cardtype: "all",
  condition: "all",
  colourmatch: "all",
  saved: {},                 // link -> card, the viewer's starred cards
  statuses: {},              // item id -> {status, checkedAt, price, bids}
  savedstatus: "all",
  statusCheckedAt: null,
  showingExamples: false,
  since: 0,
  polling: null,
  running: false,
  totalQueries: 0,
  doneQueries: 0,
  listingsSeen: 0,
};

/* Shown before the first scan so the page arrives with something to look at.
   Clearly flagged in the UI -- these are not listings that exist. */
const EXAMPLES = [
  {
    player: "Roger Federer", manufacturer: "NetPro", set_name: "Glossy",
    title: "2003 NetPro Glossy Roger Federer Rookie Refractor #1 /100 PSA 9",
    parallel: "", outfit: "", colourMatch: "no",
    serial: "1/100", bookend: "001 of 100", price: "2450.00 USD", _example: true,
    listed: new Date(Date.now() - 2 * 864e5).toISOString(), listing: "Buy It Now",
  },
  {
    player: "Coco Gauff", manufacturer: "Topps", set_name: "Topps Chrome",
    title: "2021 Topps Chrome Coco Gauff Gold Refractor Auto 50/50",
    parallel: "gold", outfit: "yellow", colourMatch: "yes",
    serial: "50/50", bookend: "last of 50 (50/50)", price: "1180.00 USD", _example: true,
    listed: new Date(Date.now() - 22 * 6e4).toISOString(), listing: "Auction", bids: 4,
  },
  {
    player: "Carlos Alcaraz", manufacturer: "Panini", set_name: "Panini Instant",
    title: "2022 Panini Instant Carlos Alcaraz US Open Champion Patch Auto 1/1",
    serial: "1/1", bookend: "True 1/1 (both bookends)", price: "3900.00 USD", _example: true,
    listed: new Date(Date.now() - 5 * 36e5).toISOString(), listing: "Auction + Buy It Now",
  },
];

/* ---------------------------------------------------------------- helpers */

function clock(ts) {
  return new Date((ts || Date.now() / 1000) * 1000).toLocaleTimeString([], {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function ebaySearchUrl(match) {
  const terms = [match.player, match.manufacturer, match.set_name, "tennis card"]
    .filter(Boolean).join(" ");
  return `https://www.ebay.com/sch/i.html?_nkw=${encodeURIComponent(terms)}`;
}

function brandLine(match) {
  return match.set_name && match.set_name !== match.manufacturer
    ? `${match.manufacturer} / ${match.set_name}`
    : match.manufacturer || "Unknown manufacturer";
}

/* ----------------------------------------------------------- listing type */
const LISTING = { key: "tce.listing" };
const LISTING_WORDS = { buy_now: "Buy It Now", auction: "Auction" };

function inListingType(card) {
  if (state.listing === "all") return true;
  if (!card.listing) return true;                       // rows from before the column: never hide
  return card.listing.includes(LISTING_WORDS[state.listing]);
}

function listingLine(card) {
  if (!card.listing) return "";
  const bids = card.bids != null && card.listing.includes("Auction")
    ? ` \u00b7 ${card.bids} bid${card.bids === 1 ? "" : "s"}` : "";
  return card.listing + bids;
}

function setListing(value) {
  state.listing = LISTING_WORDS[value] ? value : "all";
  try { localStorage.setItem(LISTING.key, state.listing); } catch { /* private mode */ }
  document.querySelectorAll("#listing-seg .seg-btn").forEach((b) => {
    const on = b.dataset.listing === state.listing;
    b.classList.toggle("is-on", on);
    b.setAttribute("aria-checked", on ? "true" : "false");
  });
  $("listing-note").textContent = {
    all: "Fixed-price and auction listings alike.",
    buy_now: "Only fixed-price listings you can buy outright.",
    auction: "Only listings up for bidding. The price shown is the current bid.",
  }[state.listing];
  renderMatches();
  renderBoard();
}

function initListing() {
  document.querySelectorAll("#listing-seg .seg-btn").forEach((b) =>
    b.addEventListener("click", () => setListing(b.dataset.listing)));
  let saved = "all";
  try { saved = localStorage.getItem(LISTING.key) || "all"; } catch { /* ignore */ }
  setListing(saved);
}

/* ------------------------------------------- order, bookend, listed-within */
/* One segmented control each, remembered, applied to the board and matches. */
const SEGS = {
  sort: {
    key: "tce.sort", fallback: "newest",
    notes: {
      newest: "The board and the matches run newest listing first.",
      price_desc: "Highest asking price first. Auctions count their current bid.",
      price_asc: "Lowest asking price first. Cards with no price sit at the end.",
    },
    titles: { newest: "Just listed", price_desc: "Highest prices", price_asc: "Lowest prices" },
  },
  bookend: {
    key: "tce.bookend", fallback: "all",
    notes: {
      all: "Cards numbered #1, the final card of the run, and true 1/1s.",
      first: "Only the first card of each run, numbered 001 or 1 of N. True 1/1s count.",
      last: "Only the final card of each run, where the serial equals the print run. True 1/1s count.",
      one: "Only true 1/1s, the single card that is both bookends.",
    },
  },
  cardtype: {
    key: "tce.cardtype", fallback: "all",
    notes: {
      all: "Base cards, relic and patch cards, autographs, and patch autographs alike.",
      base: "Only plain cards: no autograph, no relic or patch.",
      patch: "Only relic, jersey and patch cards without an autograph.",
      auto: "Only autographed cards without a relic or patch.",
      patch_auto: "Only cards with both an autograph and a relic or patch.",
    },
  },
  colourmatch: {
    key: "tce.colourmatch", fallback: "all",
    notes: {
      all: "Every card, whether or not the outfit matches the parallel.",
      only: "Only cards where the colour of the player's outfit matches the colour parallel, like a blue kit on a Blue Refractor.",
    },
  },
  condition: {
    key: "tce.condition", fallback: "all",
    notes: {
      all: "Slabbed and ungraded cards alike.",
      graded: "Only cards in a grading slab: PSA, BGS, SGC, CGC and the rest.",
      raw: "Only ungraded cards.",
    },
  },
  window: {
    key: "tce.window", fallback: "any",
    notes: {
      any: "Every listing, however long it has been up.",
      1: "Only listings that went up in the last 24 hours.",
      7: "Only listings that went up in the last 7 days.",
      30: "Only listings that went up in the last 30 days.",
    },
  },
};

function setSeg(name, value) {
  const seg = SEGS[name];
  state[name] = seg.notes[value] ? String(value) : seg.fallback;
  try { localStorage.setItem(seg.key, state[name]); } catch { /* private mode */ }
  document.querySelectorAll(`#${name}-seg .seg-btn`).forEach((b) => {
    const on = b.dataset.value === state[name];
    b.classList.toggle("is-on", on);
    b.setAttribute("aria-checked", on ? "true" : "false");
  });
  $(`${name}-note`).textContent = seg.notes[state[name]];
  if (seg.titles) $("board-title").textContent = seg.titles[state[name]];
  renderMatches();
  renderBoard();
}

function initSegs() {
  Object.keys(SEGS).forEach((name) => {
    document.querySelectorAll(`#${name}-seg .seg-btn`).forEach((b) =>
      b.addEventListener("click", () => setSeg(name, b.dataset.value)));
    let saved = SEGS[name].fallback;
    try { saved = localStorage.getItem(SEGS[name].key) || saved; } catch { /* ignore */ }
    setSeg(name, saved);
  });
}

/* what kind of card: the engine's reading when it has one, else the title */
const CARD_TYPE_LABELS = { base: "Base", patch: "Patch", auto: "Auto", patch_auto: "Patch auto" };
const AUTO_RE = /\b(auto|autos|autograph|autographed|autographs|signed|signature|signatures)\b/i;
const PATCH_RE = /\b(patch|patches|relic|relics|jersey|jerseys|memorabilia|swatch|swatches|game[ -]used|match[ -]used|match[ -]worn|player[ -]worn|event[ -]worn|tournament[ -]worn|materials?|shirt)\b/i;

function cardType(card) {
  if (CARD_TYPE_LABELS[card.cardType]) return card.cardType;
  const text = String(card.title || "");
  const auto = AUTO_RE.test(text), patch = PATCH_RE.test(text);
  return auto && patch ? "patch_auto" : auto ? "auto" : patch ? "patch" : "base";
}

/* colour match: the engine's reading (the parallel from the title or specifics,
   the outfit from the photo). Nothing is guessed on the page. */
function colourMatchLine(card) {
  if (card.colourMatch === "yes") return `${cap(card.parallel)} parallel, ${card.outfit} outfit`;
  if (card.colourMatch === "no" && card.parallel) return `${cap(card.parallel)} parallel, ${card.outfit || "outfit differs"}`;
  if (card.colourMatch === "unknown" && card.parallel) return `${cap(card.parallel)} parallel, outfit not read yet`;
  return "";
}
function cap(s) { return s ? s[0].toUpperCase() + s.slice(1) : ""; }
function inColourMatch(card) {
  return state.colourmatch === "all" || card.colourMatch === "yes";
}

/* graded or raw: the engine's reading when it has one, else the title */
const GRADE_RE = /\b(psa|bgs|sgc|cgc|csg|hga|isa|gma|ksa|beckett|tag)\b[\s:-]*(?:gem\s*(?:mint|mt)|pristine|black\s*label|mint|nm-mt|nm)?[\s:-]*(10|[1-9](?:\.5)?)\b(?!\s*\/)/i;
const GRADER_RE = /\b(psa|bgs|sgc|cgc|csg|hga|beckett)\b/i;
const GRADED_WORDS_RE = /\b(graded|slab|slabbed|gem\s*(?:mint|mt)\s*10)\b/i;

function grading(card) {
  if (card.grading) return card.grading;
  const t = String(card.title || "");
  let m = t.match(GRADE_RE);
  if (m) return `${m[1].toUpperCase()} ${m[2]}`;
  m = t.match(GRADER_RE);
  if (m) return m[1].toUpperCase();
  return GRADED_WORDS_RE.test(t) ? "Graded" : "Raw";
}

function inCondition(card) {
  if (state.condition === "all") return true;
  const raw = grading(card) === "Raw";
  return state.condition === "raw" ? raw : !raw;
}

function inCardType(card) {
  return state.cardtype === "all" || cardType(card) === state.cardtype;
}

/* which bookend a card is, read off the engine's label and the serial */
function bookendKind(card) {
  const label = String(card.bookend || "").toLowerCase();
  const m = String(card.serial || "").match(/^(\d+)\s*\/\s*(\d+)$/);
  const n = m ? Number(m[1]) : NaN, run = m ? Number(m[2]) : NaN;
  if (label.includes("true 1/1") || (n === 1 && run === 1)) return "one";
  if (label.startsWith("001 of") || n === 1) return "first";
  if (label.startsWith("last of") || (m && n === run)) return "last";
  return "";
}

function inBookend(card) {
  if (state.bookend === "all") return true;
  const kind = bookendKind(card);
  if (!kind) return true;                                // unreadable label: never hide
  if (kind === "one") return true;                       // a 1/1 is both bookends
  return kind === state.bookend;
}

function inWindow(card) {
  if (state.window === "any") return true;
  const d = listedAt(card);
  if (!d) return true;                                   // no date known: never hide
  return Date.now() - d.getTime() <= Number(state.window) * 864e5;
}

function sortCards(list) {
  const byPrice = (c) => priceValue(c.price);
  const sorted = [...list];
  if (state.sort === "price_desc") sorted.sort((a, b) => byPrice(b) - byPrice(a));
  else if (state.sort === "price_asc") sorted.sort((a, b) => (byPrice(a) || Infinity) - (byPrice(b) || Infinity));
  else sorted.sort((a, b) => listedKey(b) - listedKey(a));
  return sorted;
}

/* every display filter in one place */
function passesFilters(card) {
  return inPriceRange(card) && inListingType(card) && inBookend(card) && inWindow(card)
    && inCardType(card) && inCondition(card) && inColourMatch(card);
}

/* ------------------------------------------------------------ saved cards */
/* Stars live in this browser. Status (active / sold / ended) comes from the
   local server's live check, or from results/status.json on the hosted site. */
const SAVED = { key: "tce.saved", statusKey: "tce.status" };
const STATUS_LABELS = { active: "Active", sold: "Sold", ended: "Ended", unknown: "Not checked" };

function savedKey(card) { return card.link || `example:${card.title}`; }
function isSaved(card) { return !!state.saved[savedKey(card)]; }

function itemIdOf(card) {
  if (card.itemId) return card.itemId;
  const m = String(card.link || "").match(/\/itm\/(?:[^/?#]+\/)?(\d{9,})/);
  return m ? `v1|${m[1]}|0` : "";
}

function persistSaved() {
  try { localStorage.setItem(SAVED.key, JSON.stringify(state.saved)); } catch { /* private mode */ }
  const n = Object.keys(state.saved).length;
  $("saved-count").textContent = String(n);
  $("saved-page-count").textContent = `${n} saved`;
}

function toggleSaved(card) {
  const key = savedKey(card);
  if (state.saved[key]) delete state.saved[key];
  else state.saved[key] = { ...card, savedAt: new Date().toISOString(), manual: null };
  persistSaved();
  renderBoard();
  renderMatches();
  renderSaved();
  syncHoloStar();
  if (state.saved[key] && !HOSTED) refreshStatuses([key]);
}

/* the little star on lots and match cards; a span, since they are buttons */
function starFor(card) {
  const star = el("span", "star" + (isSaved(card) ? " is-on" : ""), isSaved(card) ? "\u2605" : "\u2606");
  star.setAttribute("role", "button");
  star.setAttribute("tabindex", "0");
  star.setAttribute("aria-pressed", isSaved(card) ? "true" : "false");
  star.setAttribute("aria-label", isSaved(card) ? "Remove from saved cards" : "Save this card");
  star.title = isSaved(card) ? "Saved" : "Save";
  const go = (e) => { e.stopPropagation(); e.preventDefault(); toggleSaved(card); };
  star.addEventListener("click", go);
  star.addEventListener("pointerdown", (e) => e.stopPropagation());
  star.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") go(e); });
  return star;
}

function syncHoloStar() {
  const card = state.holoCard;
  if (!card) return;
  const on = isSaved(card);
  const btn = $("holo-star");
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.querySelector(".star-glyph").textContent = on ? "\u2605" : "\u2606";
  $("holo-star-label").textContent = on ? "Saved to your list" : "Save this card";
}

/* effective status of a saved card: the owner's own mark wins over eBay's */
function statusOf(card) {
  const entry = state.saved[savedKey(card)] || {};
  if (entry.manual) return { status: entry.manual, manual: true };
  const s = state.statuses[itemIdOf(card)];
  return s ? { ...s } : { status: "unknown" };
}

function ago(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const mins = Math.max(0, Math.round((Date.now() - d.getTime()) / 60000));
  if (mins < 60) return `${mins || 1} min ago`;
  const h = Math.round(mins / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

async function refreshStatuses(keys) {
  const cards = (keys || Object.keys(state.saved)).map((k) => state.saved[k]).filter(Boolean);
  const ids = [...new Set(cards.map(itemIdOf).filter(Boolean))];
  const note = $("saved-checked");
  if (!ids.length) { note.textContent = ""; return; }
  note.textContent = "Checking eBay\u2026";
  try {
    let data;
    if (HOSTED) {
      data = await (await fetch("results/status.json", { cache: "no-store" })).json();
      state.statusCheckedAt = data.checkedAt || null;
    } else {
      const r = await fetch(`api/status?ids=${encodeURIComponent(ids.join(","))}`);
      data = await r.json();
      if (!r.ok) throw new Error(data.error || r.status);
      state.statusCheckedAt = new Date().toISOString();
    }
    Object.assign(state.statuses, data.statuses || {});
    try {
      localStorage.setItem(SAVED.statusKey, JSON.stringify({ at: state.statusCheckedAt, statuses: state.statuses }));
    } catch { /* ignore */ }
    const missing = ids.filter((i) => !state.statuses[i]).length;
    note.textContent = HOSTED
      ? `Statuses come from the scheduled GitHub run${state.statusCheckedAt ? `, last checked ${ago(state.statusCheckedAt)}` : ""}.`
        + (missing ? ` ${missing} saved card${missing === 1 ? " is" : "s are"} not in its results yet.` : "")
      : `Checked with eBay ${ago(state.statusCheckedAt)}.`;
  } catch (e) {
    note.textContent = HOSTED
      ? "No status file yet: the scheduled GitHub run writes one after its next scan."
      : `Couldn't check eBay (${e.message}). Mark cards sold or active yourself below.`;
  }
  renderSaved();
}

function renderSaved() {
  const area = $("saved-area");
  const all = Object.values(state.saved).sort((a, b) => (b.savedAt || "").localeCompare(a.savedAt || ""));
  const want = state.savedstatus;
  const list = all.filter((c) => {
    if (want === "all") return true;
    const s = statusOf(c).status;
    return want === "active" ? (s === "active" || s === "unknown") : s === want;
  });
  area.innerHTML = "";
  if (!all.length) {
    const note = el("div", "empty");
    note.append(el("p", null, "Nothing saved yet. Open any card and press Save, or tap the star on a card."));
    area.append(note);
    return;
  }
  if (!list.length) {
    const note = el("div", "empty");
    note.append(el("p", null, `None of your saved cards are ${STATUS_LABELS[want].toLowerCase()} right now.`));
    area.append(note);
    return;
  }
  const grid = el("div", "saved-grid");
  list.forEach((card, i) => {
    const item = el("div", "saved-item");
    const st = statusOf(card);
    const cardEl = buildCard(card, i);
    if (st.status === "sold") cardEl.classList.add("is-sold");
    item.append(cardEl);

    const bar = el("div", "saved-bar");
    const badge = el("span", `status is-${st.status}`, STATUS_LABELS[st.status] || st.status);
    if (st.manual) badge.title = "Marked by you";
    bar.append(badge);
    if (st.price && st.price !== card.price) bar.append(el("span", null, `now ${st.price}`));
    if (st.bids) bar.append(el("span", null, `${st.bids} bid${st.bids === 1 ? "" : "s"}`));
    if (st.manual) bar.append(el("span", null, "your mark"));
    else if (st.checkedAt) bar.append(el("span", null, `checked ${ago(st.checkedAt)}`));
    bar.append(el("span", "spacer"));
    const mark = el("button", "linkbtn", st.status === "sold" ? "Mark active" : "Mark sold");
    mark.type = "button";
    mark.addEventListener("click", () => {
      const entry = state.saved[savedKey(card)];
      entry.manual = st.status === "sold" ? (st.manual ? null : "active") : "sold";
      persistSaved(); renderSaved();
    });
    const remove = el("button", "linkbtn", "Remove");
    remove.type = "button";
    remove.addEventListener("click", () => toggleSaved(card));
    bar.append(mark, remove);
    item.append(bar);
    grid.append(item);
  });
  area.append(grid);
}

function showSavedView(on) {
  document.body.classList.toggle("is-saved-view", on);
  $("saved-page").hidden = !on;
  if (on) {
    window.scrollTo({ top: 0 });
    renderSaved();
    refreshStatuses();
  }
}

function initSaved() {
  try { state.saved = JSON.parse(localStorage.getItem(SAVED.key) || "{}") || {}; } catch { state.saved = {}; }
  try {
    const s = JSON.parse(localStorage.getItem(SAVED.statusKey) || "{}");
    state.statuses = s.statuses || {};
    state.statusCheckedAt = s.at || null;
  } catch { /* ignore */ }
  persistSaved();
  $("holo-star").addEventListener("click", () => { if (state.holoCard) toggleSaved(state.holoCard); });
  $("saved-refresh").addEventListener("click", () => refreshStatuses());
  document.querySelectorAll("#savedstatus-seg .seg-btn").forEach((b) => b.addEventListener("click", () => {
    state.savedstatus = b.dataset.value;
    document.querySelectorAll("#savedstatus-seg .seg-btn").forEach((o) => {
      const on = o === b;
      o.classList.toggle("is-on", on);
      o.setAttribute("aria-checked", on ? "true" : "false");
    });
    $("savedstatus-note").textContent = {
      all: "Every card you have starred.",
      active: "Saved cards whose listing is still up, plus any not checked yet.",
      sold: "Saved cards whose listing sold, or that you marked sold.",
      ended: "Saved cards whose listing ended without a sale or has gone from eBay.",
    }[state.savedstatus];
    renderSaved();
  }));
  const route = () => showSavedView(location.hash === "#saved");
  window.addEventListener("hashchange", route);
  route();
}

/* ------------------------------------------------------------ price range */
/* Typed bounds and a two-handle slider on a cubic scale, so the low end of
   0 to 50,000 gets most of the track. A max at or over the ceiling means open. */
const PRICE = { key: "tce.price", ceiling: 50000, currency: "USD", steps: 1000 };
const CURRENCY_SIGNS = { USD: "$", CAD: "C$", AUD: "A$", GBP: "\u00a3", EUR: "\u20ac" };

function snapPrice(v) {
  const step = v < 100 ? 5 : v < 500 ? 10 : v < 2000 ? 25 : v < 10000 ? 100 : 500;
  return Math.round(v / step) * step;
}
function priceFromT(t) { return snapPrice(PRICE.ceiling * Math.pow(t, 3)); }
function tFromPrice(v) { return Math.cbrt(Math.min(v, PRICE.ceiling) / PRICE.ceiling); }
function money(v) {
  const sign = CURRENCY_SIGNS[PRICE.currency] || `${PRICE.currency} `;
  return sign + Math.round(v).toLocaleString();
}

function inPriceRange(card) {
  const v = priceValue(card.price);
  if (!v) return true;                                  // no price known: never hide
  const { min, max } = state.price;
  return v >= min && (max == null || v <= max);
}

function setPrice(min, max, source) {
  min = Math.max(0, Number(min) || 0);
  max = max == null || max === "" || Number(max) >= PRICE.ceiling ? null : Math.max(0, Number(max));
  if (max != null && max < min) {
    if (source === "min") max = min; else min = max;
  }
  state.price = { min, max };
  try { localStorage.setItem(PRICE.key, JSON.stringify(state.price)); } catch { /* private mode */ }

  if (source !== "min") $("price-min").value = min ? String(min) : "";
  if (source !== "max") $("price-max").value = max != null ? String(max) : "";
  const lo = Math.round(tFromPrice(min) * PRICE.steps);
  const hi = max == null ? PRICE.steps : Math.round(tFromPrice(max) * PRICE.steps);
  $("price-lo").value = lo;
  $("price-hi").value = hi;
  const fill = $("price-fill");
  fill.style.left = `${(lo / PRICE.steps) * 100}%`;
  fill.style.right = `${100 - (hi / PRICE.steps) * 100}%`;

  const open = !min && max == null;
  $("price-note").textContent = open
    ? "Any price. Drag the handles or type a range to narrow the scan and what is shown below."
    : max == null
      ? `${money(min)} and up.`
      : `${money(min)} to ${money(max)}.`;
  $("price-reset").hidden = open;

  renderMatches();
  renderBoard();
}

function initPrice(c) {
  if (c && c.priceCeiling) PRICE.ceiling = c.priceCeiling;
  if (c && c.priceCurrency) PRICE.currency = c.priceCurrency;
  document.querySelectorAll("[data-currency]").forEach((n) => {
    n.textContent = CURRENCY_SIGNS[PRICE.currency] || PRICE.currency;
  });
  $("price-max").placeholder = `${Math.round(PRICE.ceiling).toLocaleString()}+`;

  const ticks = $("price-ticks");
  ticks.innerHTML = "";
  [0, 500, 2500, 10000, PRICE.ceiling].forEach((v) => {
    const t = el("span", null, v >= PRICE.ceiling ? `${money(v)}+` : money(v));
    t.style.left = `${tFromPrice(v) * 100}%`;
    ticks.append(t);
  });

  let saved = { min: 0, max: null };
  try { saved = { ...saved, ...JSON.parse(localStorage.getItem(PRICE.key) || "{}") }; } catch { /* ignore */ }

  const lo = $("price-lo"), hi = $("price-hi");
  const fromSliders = (which) => {
    let a = Number(lo.value), b = Number(hi.value);
    if (a > b) { if (which === "lo") b = a; else a = b; }
    setPrice(priceFromT(a / PRICE.steps), b >= PRICE.steps ? null : priceFromT(b / PRICE.steps));
  };
  lo.addEventListener("input", () => fromSliders("lo"));
  hi.addEventListener("input", () => fromSliders("hi"));
  // the handle nearer the pointer takes the drag when both sit at one end
  $("price-range").addEventListener("pointerdown", (e) => {
    const r = lo.getBoundingClientRect();
    const t = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) * PRICE.steps;
    const nearLo = Math.abs(t - Number(lo.value)) <= Math.abs(t - Number(hi.value));
    lo.style.zIndex = nearLo ? 3 : 2;
    hi.style.zIndex = nearLo ? 2 : 3;
  }, true);

  $("price-min").addEventListener("change", (e) => setPrice(e.target.value, state.price.max, "min"));
  $("price-max").addEventListener("change", (e) => setPrice(state.price.min, e.target.value, "max"));
  ["price-min", "price-max"].forEach((id) => $(id).addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); e.target.blur(); }
  }));
  $("price-reset").addEventListener("click", () => setPrice(0, null));

  setPrice(saved.min, saved.max);
}

/* ----------------------------------------------------------------- config */

async function loadConfig() {
  const pill = $("status-pill");
  try {
    const r = await fetch(API.config);
    if (!r.ok) throw new Error(r.status);
    state.config = await r.json();
  } catch {
    // No server answering. On GitHub Pages the scheduled run leaves its
    // results beside the page instead; switch to reading those.
    try {
      const r = await fetch("results/config.json", { cache: "no-store" });
      if (!r.ok) throw new Error(r.status);
      state.config = await r.json();
      HOSTED = true;
      API.board = "results/board.json";
      API.matches = "results/new_matches.json";
      API.spreadsheet = "results/tennis_cards_verified.xlsx";
    } catch {
      pill.textContent = "Server unreachable";
      pill.className = "pill is-warn";
      initPrice(null);
      initListing();
      initSegs();
      renderMatches();
      return;
    }
  }
  const c = state.config;

  if (!c.engineAvailable) {
    pill.textContent = "Engine not loaded";
    pill.className = "pill is-warn";
    $("setup-notice").hidden = false;
    $("setup-title").textContent = "Install the engine's two dependencies";
    $("setup-lede").textContent =
      `The engine couldn't be imported (${c.importError || "unknown error"}).`;
    $("setup-steps").innerHTML =
      "<li>Run <code>pip install requests openpyxl</code></li>" +
      "<li>Restart the server: <code>python server.py</code></li>";
    $("run-btn").disabled = true;
    renderMatches();
    return;
  }

  if (c.credentials) {
    pill.textContent = "Ready to scan";
    pill.className = "pill is-ok";
  } else {
    pill.textContent = "eBay keys missing";
    pill.className = "pill is-warn";
    $("setup-notice").hidden = false;
    $("run-btn").disabled = true;
  }

  initPrice(c);
  initListing();
  initSegs();
  buildChips("players-chips", c.players, "player");
  restoreRemembered("player");
  buildChips("brands-chips", c.brands, "brand");
  restoreRemembered("brand");

  $("all-players").checked = !!c.scanAllPlayers;
  syncPlayerChips();
  $("stat-players").textContent = c.scanAllPlayers ? "All" : c.players.length;
  $("stat-brands").textContent = c.brands.length;
  $("stat-queries").innerHTML = c.scanAllPlayers
    ? `${(c.maxResultsPerBrand * c.brands.length).toLocaleString()}<small>up to</small>`
    : `${(c.players.length * c.brands.length * c.maxResultsPerBrand).toLocaleString()}<small>up to</small>`;
  $("stat-ceiling").innerHTML =
    `${c.printRunInclusive ? "≤" : "<"}${c.maxPrintRun}<small>copies</small>`;

  $("max-print-run").value = c.maxPrintRun;
  $("inclusive").checked = !!c.printRunInclusive;
  $("ref-category").textContent = c.categoryId;
  $("ref-marketplace").textContent = c.marketplace;
  $("ref-per-query").textContent = (c.maxResultsPerBrand || c.resultsPerQuery).toLocaleString();
  $("download-btn").hidden = !c.spreadsheetExists;

  const body = $("allowlist-body");
  body.innerHTML = "";
  (c.allowedManufacturers || []).forEach((row) => {
    const tr = el("tr");
    tr.append(el("td", null, row.manufacturer));
    tr.append(el("td", "mono", row.requiresSetKeyword
      ? row.requiresSetKeyword.join(" or ") : "—"));
    body.append(tr);
  });

  const blocked = [
    ...(c.checkedAutographs || []).map((m) =>
      `${m}: on-card autographs and numbering on a circle sticker rejected` +
      (c.colourMatch ? " (checked on the photos)." : " (from the listing text; photos are checked once ANTHROPIC_API_KEY is set, and until then such cards carry a check-by-eye note).")),
    ...(c.blockedSets || []).map((b) =>
      `${b.manufacturer} sets skipped: ${b.skip.join(", ")}` +
      (b.unless.length ? ` (kept when marked ${b.unless.join(", ")})` : "") + "."),
    (c.blockedManufacturers || []).length
      ? `Manufacturer strings rejected on sight: ${c.blockedManufacturers.join(", ")}.`
      : "",
    (c.blockedSellers || []).length
      ? `Sellers rejected on sight: ${c.blockedSellers.join(", ")}.`
      : "",
  ].filter(Boolean).join(" ");
  $("blocklist-note").textContent = blocked;
  const setup = $("colourmatch-setup");
  setup.hidden = !!c.colourMatch;
  setup.textContent = c.colourMatch ? "" : (HOSTED
    ? "Outfit colours are read from photos by Claude once an ANTHROPIC_API_KEY secret is added to the repo; until then no card can be marked a colour match."
    : "Outfit colours are read from photos by Claude once ANTHROPIC_API_KEY is in .env (and pip install anthropic); until then no card can be marked a colour match.");
  if (HOSTED) enterHostedMode(c);
}

function syncPlayerChips() {
  const all = $("all-players").checked;
  document.querySelectorAll('input[data-group="player"]').forEach((i) => { i.disabled = all; });
  $("players-chips").style.opacity = all ? ".45" : "";
}

function buildChip(box, value, name, checked) {
  const id = `${name}-${box.children.length}`;
  const label = el("label", "chip");
  label.htmlFor = id;
  const input = el("input");
  input.type = "checkbox";
  input.id = id;
  input.value = value;
  input.checked = checked;
  input.dataset.group = name;
  label.append(input, el("span", null, value));
  box.append(label);
  return input;
}

function buildChips(containerId, values, name) {
  const box = $(containerId);
  box.innerHTML = "";
  values.forEach((value) => buildChip(box, value, name, true));
}

/* ---- a specific player or set: typed in, kept as a chip, remembered ---- */

const SEARCHES = {
  player: { box: "players-chips", input: "player-search", key: "tce.addedPlayers",
            narrowed: () => { $("all-players").checked = false; syncPlayerChips(); } },
  brand:  { box: "brands-chips",  input: "set-search",    key: "tce.addedSets",
            narrowed: () => {} },
};

function remembered(group) {
  try { return JSON.parse(localStorage.getItem(SEARCHES[group].key) || "[]"); } catch { return []; }
}

function remember(group, name) {
  try {
    const list = remembered(group);
    if (!list.some((n) => n.toLowerCase() === name.toLowerCase())) {
      localStorage.setItem(SEARCHES[group].key, JSON.stringify([...list, name].slice(-40)));
    }
  } catch { /* storage unavailable; the chip still works this session */ }
}

function chipsOf(group) {
  return [...document.querySelectorAll(`input[data-group="${group}"]`)];
}

function findChip(group, name) {
  return chipsOf(group).find((i) => i.value.toLowerCase() === name.toLowerCase()) || null;
}

/* ---- forgiving matching: a typo lands on the nearest known name ---- */

function words(s) {
  return String(s).toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .split(/[^a-z0-9]+/).filter(Boolean);
}

function editDistance(a, b) {
  const prev = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    let diag = prev[0]; prev[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const tmp = prev[j];
      prev[j] = Math.min(prev[j] + 1, prev[j - 1] + 1, diag + (a[i - 1] === b[j - 1] ? 0 : 1));
      diag = tmp;
    }
  }
  return prev[b.length];
}

/* is the typed word close enough to this word of a known name? */
function wordClose(typed, known) {
  if (typed === known) return 1;
  if (typed.length >= 3 && known.startsWith(typed)) return 0.85;    // "coco", "graphite"
  const slack = typed.length >= 6 ? 2 : typed.length >= 4 ? 1 : 0;   // typos, scaled to length
  return slack && editDistance(typed, known) <= slack ? 0.7 : 0;
}

/* The chip whose name every typed word lands on, best fit first; null when
   nothing known is close, in which case the name is taken as typed. */
function closestChip(group, raw) {
  const typed = words(raw);
  if (!typed.length) return null;
  let best = null, bestScore = 0;
  chipsOf(group).forEach((chip) => {
    const known = words(chip.value);
    let score = 0;
    for (const t of typed) {
      const fit = Math.max(0, ...known.map((k) => wordClose(t, k)));
      if (!fit) return;                                     // one word off the mark: not this chip
      score += fit;
    }
    score = score / typed.length + (known.length === typed.length ? 0.05 : 0);
    if (score > bestScore) { best = chip; bestScore = score; }
  });
  return best;
}

function showNote(group, text) {
  const note = $(group === "player" ? "player-note" : "set-note");
  note.textContent = text;
  note.hidden = false;
  clearTimeout(note._timer);
  note._timer = setTimeout(() => { note.hidden = true; }, 7000);
}

function restoreRemembered(group) {
  remembered(group).forEach((name) => {
    if (!findChip(group, name)) {
      buildChip($(SEARCHES[group].box), name, group, false).parentElement.classList.add("is-added");
    }
  });
}

/* Adds (or ticks) the chip for a typed name and narrows the run to it. A
   search means this one: the defaults untick, though other names you searched
   for and left ticked stay in. Returns the chip, or null if nothing usable. */
function addSearched(group, raw) {
  const cfg = SEARCHES[group];
  const name = String(raw || "").replace(/\s+/g, " ").trim();
  if (!name) return null;
  const existing = closestChip(group, name);
  const chip = existing || buildChip($(cfg.box), name, group, true);
  if (!existing) chip.parentElement.classList.add("is-added");
  if (existing && existing.value.toLowerCase() !== name.toLowerCase()) {
    showNote(group, `Took \u201c${name}\u201d as ${existing.value}.`);
  } else {
    $(group === "player" ? "player-note" : "set-note").hidden = true;
  }
  document.querySelectorAll(`input[data-group="${group}"]`).forEach((i) => {
    if (i !== chip && !i.parentElement.classList.contains("is-added")) i.checked = false;
  });
  chip.checked = true;
  if (!existing) remember(group, name);              // only genuinely new names are kept
  cfg.narrowed();
  $(cfg.input).value = "";
  return chip;
}

function wireSearch(group, addBtnId) {
  const cfg = SEARCHES[group];
  $(addBtnId).addEventListener("click", () => { addSearched(group, $(cfg.input).value); $(cfg.input).focus(); });
  $(cfg.input).addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    if (addSearched(group, $(cfg.input).value) && !$("run-btn").disabled && !state.running) runScan();
  });
}

function selected(name) {
  return [...document.querySelectorAll(`input[data-group="${name}"]:checked`)]
    .map((i) => i.value);
}

/* ---------------------------------------------------------------- results */

function renderMatches() {
  const area = $("results-area");
  const all = state.matches.length ? state.matches : EXAMPLES;
  const list = sortCards(all.filter(passesFilters));
  state.showingExamples = state.matches.length === 0;

  $("match-count").textContent = state.showingExamples
    ? "example cards"
    : list.length === all.length ? `${all.length} found` : `${list.length} of ${all.length} match the filters`;

  area.innerHTML = "";

  if (state.showingExamples) {
    const note = el("div", "empty");
    note.style.padding = "16px 16px 0";
    note.append(el("p", null,
      "No scan results yet. These three are examples of what a match looks like — " +
      "tap one to open the card. They are not real listings."));
    area.append(note);
  }

  if (!list.length) {
    const note = el("div", "empty");
    note.style.padding = "16px";
    note.append(el("p", null, "Nothing matches the filters in the scan setup. Widen the card type, graded or raw, price, listing type, bookend or listed-within choice."));
    area.append(note);
    return;
  }
  const grid = el("div", "matchgrid");
  list.forEach((match, i) => grid.append(buildCard(match, i)));
  area.append(grid);
}

function initials(name) {
  return name.split(/\s+/).map((w) => w[0] || "").join("").slice(0, 3).toUpperCase();
}

function buildCard(match, index) {
  const card = el("button", "matchcard" + (match._example ? " is-example" : ""));
  card.type = "button";
  card.dataset.index = index;
  card.style.animationDelay = `${Math.min(index, 8) * 45}ms`;
  card.setAttribute("aria-label", `${match.player}, ${match.serial}. Open card.`);

  const photo = el("div", "mc-photo");
  if (match.image) {
    const img = el("img");
    img.src = match.image;
    img.alt = `Listing photo: ${match.title}`;
    img.loading = "lazy";
    img.addEventListener("error", () => {
      photo.innerHTML = "";
      photo.append(el("span", "mc-crest", initials(match.player)));
      photo.append(serialTag);
      photo.append(starFor(match));
    });
    photo.append(img);
  } else {
    photo.append(el("span", "mc-crest", initials(match.player)));
  }
  const serialTag = el("span", "mc-serial-tag", match.serial);
  photo.append(serialTag);
  photo.append(starFor(match));
  card.append(photo);

  const body = el("div", "mc-body");
  body.append(el("div", "mc-player", match.player));
  body.append(el("div", "mc-bookend", match._example ? "Example card" : match.bookend));
  body.append(el("div", "mc-title", match.title));
  const typeLine = el("div", "mc-type", [grading(match), CARD_TYPE_LABELS[cardType(match)], listingLine(match)].filter(Boolean).join(" \u00b7 "));
  if (match.colourMatch === "yes") typeLine.append(el("span", "cm-tag", "Colour match"));
  if (match.caution) { const t = el("span", "caution-tag", "Check by eye"); t.title = match.caution; typeLine.append(t); }
  body.append(typeLine);

  const foot = el("div", "mc-foot");
  foot.append(el("span", "mc-brand", brandLine(match)));
  foot.append(el("span", "mc-price", match.price || "\u2014"));
  body.append(foot);
  card.append(body);

  card.addEventListener("click", () => openHologram(match));
  return card;
}

async function loadSavedMatches() {
  try {
    const data = await (await fetch(API.matches, { cache: "no-store" })).json();
    const list = Array.isArray(data) ? data : data.matches;
    if (Array.isArray(list) && list.length) state.matches = list;
  } catch { /* server not up yet; examples will show */ }
  renderMatches();
}

/* ------------------------------------------------------------------ board */

function priceValue(text) {
  const m = String(text || "").match(/\d[\d,]*(?:\.\d+)?/);
  return m ? parseFloat(m[0].replace(/,/g, "")) : 0;
}

function priceParts(text) {
  const value = priceValue(text);
  const cur = (String(text || "").match(/[A-Z]{3}/) || [""])[0];
  return {
    amount: value ? value.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 }) : "\u2014",
    currency: cur,
  };
}

/* "Listed (UTC)" from eBay is ISO; "Date Found" from the scan is "YYYY-MM-DD HH:MM UTC" */
function listedAt(card) {
  const raw = card.listed || card.found || "";
  const d = new Date(raw.replace(/ UTC$/, "Z").replace(" ", "T"));
  return isNaN(d) ? null : d;
}

function listedKey(card) {
  const d = listedAt(card);
  return d ? d.getTime() : 0;
}

function listedLabel(card) {
  const d = listedAt(card);
  if (!d) return "";
  const mins = Math.max(0, Math.round((Date.now() - d.getTime()) / 60000));
  if (mins < 60) return `Listed ${mins || 1} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `Listed ${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `Listed ${days} day${days === 1 ? "" : "s"} ago`;
  return "Listed " + d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function buildLot(card, rank) {
  const lot = el("button", "lot" + (card._example ? " is-example" : ""));
  lot.type = "button";
  lot.setAttribute("aria-label", `${rank}. ${card.player}, ${card.price}. Open card.`);

  const photo = el("div", "lot-photo");
  if (card.image) {
    const img = el("img");
    img.src = card.image;
    img.alt = `Listing photo: ${card.title}`;
    img.loading = "lazy";
    img.addEventListener("error", () => { img.remove(); photo.prepend(el("span", "mc-crest", initials(card.player))); });
    photo.append(img);
  } else {
    photo.append(el("span", "mc-crest", initials(card.player)));
  }
  photo.append(el("span", "lot-rank", String(rank).padStart(2, "0")));
  photo.append(el("span", "lot-serial", card.serial));
  if (card.colourMatch === "yes") photo.append(el("span", "cm-tag", "Colour match"));
  if (card.caution) { const t = el("span", "caution-tag", "Check by eye"); t.title = card.caution; photo.append(t); }
  photo.append(starFor(card));
  lot.append(photo);

  const body = el("div", "lot-body");
  const { amount, currency } = priceParts(card.price);
  const price = el("div", "lot-price", amount);
  if (currency) price.append(el("small", null, currency));
  body.append(price);
  body.append(el("div", "lot-label", card._example ? "Example card" : (card.bookend || "Asking")));
  const when = listedLabel(card);
  const type = listingLine(card);
  if (when || type) {
    const line = el("div", "lot-when");
    if (type) line.append(el("span", "lot-type", type));
    if (type && when) line.append(" \u00b7 ");
    if (when) line.append(when);
    body.append(line);
  }
  body.append(el("div", "lot-player", card.player));
  body.append(el("div", "lot-set", brandLine(card)));
  lot.append(body);

  lot.addEventListener("click", () => { if (!rail.suppressClick) openHologram(card); });
  return lot;
}

function renderBoard() {
  const railEl = $("rail");
  const all = state.board.length ? state.board : EXAMPLES;
  const cards = sortCards(all.filter(passesFilters));
  const examples = !state.board.length;

  $("board-count").textContent = examples
    ? "example cards"
    : cards.length === all.length ? `${all.length} on the board` : `${cards.length} of ${all.length} match the filters`;
  railEl.innerHTML = "";
  cards.forEach((c, i) => {
    const lot = buildLot(c, i + 1);
    lot.style.animationDelay = `${Math.min(i, 10) * 40}ms`;
    railEl.append(lot);
  });
  updateRailButtons();
}

async function loadBoard() {
  try {
    const data = await (await fetch(API.board)).json();
    if (Array.isArray(data.cards)) state.board = data.cards;
  } catch { /* offline or preview: examples will show */ }
  renderBoard();
}

/* a newly found card takes its place on the board immediately, newest first */
function placeOnBoard(match) {
  const card = { ...match, priceValue: priceValue(match.price) };
  state.board = [...state.board.filter((c) => c.link !== card.link), card]
    .sort((a, b) => listedKey(b) - listedKey(a))
    .slice(0, 24);
  renderBoard();
}

const rail = { dragging: false, startX: 0, startLeft: 0, moved: 0, suppressClick: false };

function updateRailButtons() {
  const r = $("rail");
  const max = r.scrollWidth - r.clientWidth - 1;
  $("rail-prev").disabled = r.scrollLeft <= 0;
  $("rail-next").disabled = r.scrollLeft >= max;
}

function initRail() {
  const r = $("rail");
  const step = () => Math.max(260, r.clientWidth * 0.8);
  $("rail-prev").addEventListener("click", () => r.scrollBy({ left: -step(), behavior: "smooth" }));
  $("rail-next").addEventListener("click", () => r.scrollBy({ left: step(), behavior: "smooth" }));
  r.addEventListener("scroll", updateRailButtons, { passive: true });
  window.addEventListener("resize", updateRailButtons);

  // drag to scroll with a mouse; touch already scrolls natively
  r.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "mouse") return;
    rail.dragging = true; rail.moved = 0;
    rail.startX = e.clientX; rail.startLeft = r.scrollLeft;
  });
  r.addEventListener("pointermove", (e) => {
    if (!rail.dragging) return;
    const dx = e.clientX - rail.startX;
    rail.moved = Math.max(rail.moved, Math.abs(dx));
    if (rail.moved > 6 && !r.hasPointerCapture(e.pointerId)) {
      // capturing at pointerdown would steal the click from the lot buttons,
      // so it starts only once this is unmistakably a drag
      r.classList.add("is-dragging");
      r.setPointerCapture(e.pointerId);
    }
    if (rail.moved > 6) r.scrollLeft = rail.startLeft - dx;
  });
  const release = (e) => {
    if (!rail.dragging) return;
    rail.dragging = false;
    r.classList.remove("is-dragging");
    if (e && r.hasPointerCapture(e.pointerId)) r.releasePointerCapture(e.pointerId);
    rail.suppressClick = rail.moved > 6;          // a drag is not a click
    setTimeout(() => { rail.suppressClick = false; }, 0);
  };
  r.addEventListener("pointerup", release);
  r.addEventListener("pointercancel", release);

  r.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") { e.preventDefault(); r.scrollBy({ left: 280, behavior: "smooth" }); }
    if (e.key === "ArrowLeft")  { e.preventDefault(); r.scrollBy({ left: -280, behavior: "smooth" }); }
  });
}


/* ---- hosted on GitHub Pages: the scan runs on GitHub, not here ---- */

function enterHostedMode(c) {
  const pill = $("status-pill");
  pill.textContent = c.lastRun ? `Last scan ${c.lastRun}` : "Hosted";
  pill.className = "pill is-ok";
  document.body.classList.add("is-hosted");

  const actions = c.repo
    ? `https://github.com/${c.repo}/actions/workflows/${c.workflow || "scan.yml"}`
    : "";
  const run = $("run-btn");
  run.textContent = "Run a scan on GitHub";
  run.disabled = !actions;
  run.onclick = (e) => { e.stopImmediatePropagation(); if (actions) window.open(actions, "_blank", "noopener"); };
  $("stop-btn").hidden = true;
  $("hosted-note").hidden = false;
  $("hosted-note").innerHTML = actions
    ? `Scans run on GitHub every six hours and this page updates from them. ` +
      `To start one now, press the button, then <strong>Run workflow</strong> on the page that opens.`
    : `Scans run on GitHub every six hours and this page updates from them.`;
  $("download-btn").hidden = !c.spreadsheetExists;
}

/* ------------------------------------------------------------- contents */

function initContents() {
  const btn = $("contents-btn");
  const panel = $("contents");
  const veil = $("contents-veil");
  const links = [...panel.querySelectorAll(".contents-list a")];
  let opener = null;
  let closeTimer = null;

  function open() {
    clearTimeout(closeTimer);
    opener = document.activeElement;
    panel.classList.remove("is-closing");
    panel.hidden = false;
    veil.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    document.body.style.overflow = "hidden";
    $("contents-close").focus();
  }

  function close(restoreFocus) {
    if (panel.hidden) return;
    btn.setAttribute("aria-expanded", "false");
    panel.classList.add("is-closing");
    veil.hidden = true;
    document.body.style.overflow = "";
    const settle = () => {
      if (panel.classList.contains("is-closing")) {   // not reopened meanwhile
        panel.hidden = true;
        panel.classList.remove("is-closing");
      }
    };
    panel.addEventListener("animationend", settle, { once: true });
    clearTimeout(closeTimer);
    closeTimer = setTimeout(settle, 420);             // in case animations are off
    if (restoreFocus !== false && opener) opener.focus();
  }

  btn.addEventListener("click", () => (panel.hidden ? open() : close()));
  $("contents-close").addEventListener("click", () => close());
  veil.addEventListener("click", () => close());
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panel.hidden) close();
  });
  // jumping to a section closes the index and leaves focus at the destination
  links.forEach((a) => a.addEventListener("click", () => close(false)));

  // mark where the reader currently is
  const targets = links
    .map((a) => ({ a, section: document.querySelector(a.getAttribute("href")) }))
    .filter((t) => t.section);
  if ("IntersectionObserver" in window) {
    const spy = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        targets.forEach((t) => t.a.classList.toggle("is-here", t.section === entry.target));
      });
    }, { rootMargin: "-90px 0px -62% 0px" });
    targets.forEach((t) => spy.observe(t.section));
  }
}

/* -------------------------------------------------------------- hologram */

let lastFocused = null;

/* The card is a real object: drag spins it on both axes, let go and it coasts
   to a stop, and turning past 90 degrees shows the printed back. */
const rotor = {
  node: null, rx: 0, ry: 0, vx: 0, vy: 0, zoom: 1,
  dragging: false, frame: null, lastX: 0, lastY: 0,
};

const CLAMP_X = 78;                    // past this it just looks broken

function applyRotor() {
  const n = rotor.node;
  n.style.setProperty("--rx", `${rotor.rx.toFixed(2)}deg`);
  n.style.setProperty("--ry", `${rotor.ry.toFixed(2)}deg`);
  n.style.setProperty("--zoom", rotor.zoom.toFixed(3));
}

/* Which face is toward the viewer, read off the transform the browser is
   actually drawing (so it stays right mid-transition). Runs while open. */
function watchFacing() {
  const n = rotor.node;
  if (!n || $("holo").hidden) { rotor.facingFrame = null; return; }
  const t = getComputedStyle(n).transform;
  let front = true;
  if (t && t.startsWith("matrix3d(")) {
    const m = t.slice(9, -1).split(",").map(Number);
    front = m[10] >= 0;                        // z of the transformed z axis
  } else if (t && t.startsWith("matrix(")) {
    front = true;
  } else {
    const rad = Math.PI / 180;
    front = Math.cos(rotor.ry * rad) * Math.cos(rotor.rx * rad) >= 0;
  }
  n.classList.toggle("is-back", !front);
  rotor.facingFrame = requestAnimationFrame(watchFacing);
}

function coast() {
  rotor.vx *= 0.94;
  rotor.vy *= 0.94;
  rotor.rx = Math.max(-CLAMP_X, Math.min(CLAMP_X, rotor.rx + rotor.vx));
  rotor.ry += rotor.vy;
  applyRotor();
  if (Math.abs(rotor.vx) > 0.02 || Math.abs(rotor.vy) > 0.02) {
    rotor.frame = requestAnimationFrame(coast);
  } else {
    rotor.frame = null;
    rotor.node.classList.remove("is-coasting");
  }
}

function stopCoast() {
  if (rotor.frame) cancelAnimationFrame(rotor.frame);
  rotor.frame = null;
  rotor.vx = rotor.vy = 0;
}

function turnTo(rx, ry, zoom) {
  stopCoast();
  rotor.node.classList.remove("is-coasting", "is-dragging");
  rotor.rx = rx; rotor.ry = ry;
  if (zoom !== undefined) rotor.zoom = zoom;
  applyRotor();                        // the CSS transition carries it smoothly
}

function trackFoil(clientX, clientY) {
  const box = rotor.node.getBoundingClientRect();
  const x = Math.max(0, Math.min(1, (clientX - box.left) / box.width));
  const y = Math.max(0, Math.min(1, (clientY - box.top) / box.height));
  rotor.node.style.setProperty("--px", `${x * 100}%`);
  rotor.node.style.setProperty("--py", `${y * 100}%`);
}

function initRotor() {
  const n = $("holo-rotor");
  rotor.node = n;

  n.addEventListener("pointerdown", (e) => {
    stopCoast();
    rotor.dragging = true;
    rotor.lastX = e.clientX;
    rotor.lastY = e.clientY;
    n.classList.add("is-dragging");
    n.setPointerCapture(e.pointerId);
  });

  n.addEventListener("pointermove", (e) => {
    trackFoil(e.clientX, e.clientY);
    if (!rotor.dragging) return;
    const dx = e.clientX - rotor.lastX;
    const dy = e.clientY - rotor.lastY;
    rotor.lastX = e.clientX;
    rotor.lastY = e.clientY;
    rotor.vy = dx * 0.45;
    rotor.vx = -dy * 0.45;
    rotor.ry += rotor.vy;
    rotor.rx = Math.max(-CLAMP_X, Math.min(CLAMP_X, rotor.rx + rotor.vx));
    applyRotor();
  });

  const release = (e) => {
    if (!rotor.dragging) return;
    rotor.dragging = false;
    n.classList.remove("is-dragging");
    if (e && e.pointerId !== undefined && n.hasPointerCapture(e.pointerId)) {
      n.releasePointerCapture(e.pointerId);
    }
    if (Math.abs(rotor.vx) > 0.05 || Math.abs(rotor.vy) > 0.05) {
      n.classList.add("is-coasting");
      rotor.frame = requestAnimationFrame(coast);
    }
  };
  n.addEventListener("pointerup", release);
  n.addEventListener("pointercancel", release);
  n.addEventListener("dblclick", () => turnTo(0, 0, 1));

  n.addEventListener("wheel", (e) => {
    e.preventDefault();
    stopCoast();
    rotor.zoom = Math.max(0.75, Math.min(1.7, rotor.zoom - e.deltaY * 0.0012));
    applyRotor();
  }, { passive: false });

  n.addEventListener("keydown", (e) => {
    const step = 15;
    const moves = {
      ArrowLeft:  [0, -step], ArrowRight: [0, step],
      ArrowUp:    [step, 0],  ArrowDown:  [-step, 0],
    };
    if (moves[e.key]) {
      e.preventDefault();
      turnTo(Math.max(-CLAMP_X, Math.min(CLAMP_X, rotor.rx + moves[e.key][0])),
             rotor.ry + moves[e.key][1]);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      turnTo(rotor.rx, rotor.ry + 180);
    }
  });

  $("holo-flip").addEventListener("click", () => turnTo(rotor.rx, rotor.ry + 180));
  $("holo-reset").addEventListener("click", () => turnTo(0, 0, 1));
}

function showFace(photoUrl, altText, credit) {
  const wrap = $("holo-photo");
  const img = $("holo-img");
  const creditLine = $("holo-credit");

  if (photoUrl) {
    img.src = photoUrl;
    img.alt = altText;
    wrap.hidden = false;
    $("holo-scrim").hidden = false;
    $("holo-monogram").style.display = "none";
  } else {
    wrap.hidden = true;
    $("holo-scrim").hidden = true;
    img.removeAttribute("src");
    $("holo-monogram").style.display = "";
  }

  creditLine.innerHTML = "";
  if (credit) {
    creditLine.hidden = false;
    creditLine.append(document.createTextNode(`Photo: ${credit.credit} \u00b7 ${credit.licence} \u00b7 `));
    const a = el("a", null, "Wikimedia Commons");
    a.href = credit.source || credit.licenceUrl || "#";
    a.target = "_blank";
    a.rel = "noopener";
    creditLine.append(a);
  } else {
    creditLine.hidden = true;
  }
}

async function loadPortrait(player) {
  if (!$("portraits").checked) return;
  if (!portraits.has(player)) {
    try {
      portraits.set(player, await (await fetch(API.portrait(player))).json());
    } catch {
      portraits.set(player, { available: false });
    }
  }
  const p = portraits.get(player);
  // only paint it if the same card is still open and has no listing photo of its own
  if (p && p.available && $("holo-name").textContent === player && !$("holo-img").src) {
    showFace(p.url, `Portrait of ${player}`, p);
  }
}

function openHologram(match) {
  lastFocused = document.activeElement;
  state.holoCard = match;
  syncHoloStar();
  const holo = $("holo");
  const isExample = !!match._example;

  $("holo-badge").textContent = isExample ? "Example" : "Bookend";
  $("holo-name").textContent = match.player;
  $("holo-brand").textContent = brandLine(match);
  $("holo-serial").textContent = match.serial;
  $("holo-price").textContent = match.price || "\u2014";
  $("holo-monogram").textContent = initials(match.player);

  $("holo-back-crest").textContent = initials(match.player);
  $("holo-back-name").textContent = match.player;
  $("holo-back-meta").textContent = brandLine(match);
  $("holo-back-serial").textContent = match.serial;

  $("holo-detail-title").textContent = isExample
    ? "Example card" : (match.bookend || "Qualifying card");
  $("holo-full-title").textContent = match.title;
  $("holo-d-player").textContent = match.player;
  $("holo-d-manufacturer").textContent = match.manufacturer || "\u2014";
  $("holo-d-set").textContent = match.set_name || "\u2014";
  $("holo-d-serial").textContent = match.serial;
  $("holo-d-bookend").textContent = match.bookend || "\u2014";
  $("holo-d-price").textContent = match.price || "\u2014";
  $("holo-d-listing").textContent = listingLine(match) || "\u2014";
  $("holo-d-cardtype").textContent = CARD_TYPE_LABELS[cardType(match)];
  $("holo-d-grading").textContent = grading(match);
  $("holo-d-parallel").textContent = match.parallel ? `${cap(match.parallel)}` : "None named";
  $("holo-d-caution").textContent = match.caution || "";
  $("holo-d-caution").hidden = $("holo-dt-caution").hidden = !match.caution;
  $("holo-d-colourmatch").textContent = match.colourMatch === "yes" ? `Yes: ${match.outfit} outfit`
    : match.colourMatch === "no" ? (match.parallel ? `No${match.outfit ? `: ${match.outfit} outfit` : ""}` : "No colour parallel")
    : match.parallel ? "Not read yet" : "\u2014";

  const url = match.link || ebaySearchUrl(match);
  $("holo-link").href = url;
  $("holo-link-label").textContent = match.link
    ? "View listing on eBay" : "Search eBay for this card";
  $("holo-url").textContent = url;

  // the seller's photo of the actual card is the best image there is
  showFace(match.image || "", `Listing photo: ${match.title}`, null);
  if (!match.image) loadPortrait(match.player);
  setupBackPhotos(match);

  holo.hidden = false;
  document.body.style.overflow = "hidden";
  if (!rotor.facingFrame) rotor.facingFrame = requestAnimationFrame(watchFacing);

  // deal it in: park it turned, then let the transition settle it square
  stopCoast();
  rotor.node.classList.add("is-dragging");
  rotor.rx = 15; rotor.ry = -24; rotor.zoom = 0.93;
  applyRotor();
  requestAnimationFrame(() => requestAnimationFrame(() => turnTo(0, 0, 1)));

  $("holo-close").focus();
}

/* The listing's other photos go on the back face (sellers shoot the back
   second), with a strip to pick any of them. */
function showBack(url) {
  const face = $("holo-back");
  const wrap = $("holo-back-photo");
  const img = $("holo-back-img");
  if (url) {
    img.src = url;
    img.alt = "Listing photo of the card back";
    wrap.hidden = false;
    face.classList.add("has-photo");
  } else {
    img.removeAttribute("src");
    wrap.hidden = true;
    face.classList.remove("has-photo");
  }
  document.querySelectorAll(".holo-thumb").forEach((t) => t.classList.toggle("is-on", t.dataset.url === (url || "")));
}

function setupBackPhotos(match) {
  const strip = $("holo-thumbs");
  strip.innerHTML = "";
  const extras = (match.images || []).filter((u) => u && u !== match.image);
  showBack(extras[0] || "");
  if (!match.image || !extras.length) { strip.hidden = true; return; }
  strip.hidden = false;
  strip.append(el("span", "holo-thumb-label", "Photos"));
  [match.image, ...extras].forEach((url, i) => {
    const b = el("button", "holo-thumb" + (i === 1 ? " is-on" : ""));
    b.type = "button";
    b.dataset.url = i === 0 ? "" : url;
    b.title = i === 0 ? "Front (seller's first photo)" : `Photo ${i + 1}`;
    b.setAttribute("aria-label", b.title);
    const img = el("img");
    img.src = url; img.alt = ""; img.loading = "lazy";
    b.append(img);
    b.addEventListener("click", () => {
      if (i === 0) {                                     // the front: turn to it
        turnTo(rotor.rx, Math.round(rotor.ry / 360) * 360, 1);
        document.querySelectorAll(".holo-thumb").forEach((t) => t.classList.toggle("is-on", t === b));
        return;
      }
      showBack(url);                                      // any other: on the back, turned to it
      const toBack = Math.round((rotor.ry - 180) / 360) * 360 + 180;
      turnTo(rotor.rx, toBack, 1);
    });
    strip.append(b);
  });
}

function closeHologram() {
  $("holo").hidden = true;
  document.body.style.overflow = "";
  stopCoast();
  turnTo(0, 0, 1);
  if (lastFocused) lastFocused.focus();
}

/* ------------------------------------------------------------------- scan */

function logLine(kind, text, ts) {
  const box = $("logbox");
  const line = el("div", `logline k-${kind}`);
  line.append(el("span", "t", clock(ts)));
  line.append(el("span", null, text));
  box.append(line);
  box.scrollTop = box.scrollHeight;
  while (box.children.length > 400) box.removeChild(box.firstChild);
}

function describe(e) {
  switch (e.kind) {
    case "start":
      return ["query", e.players === "all"
        ? `Scanning every player across ${e.brands} set(s), up to ${e.perBrand} listings each.`
        : `Scanning ${e.players} player(s) across ${e.brands} set(s) — ${e.queries} queries, up to ${e.perBrand} listings each.`];
    case "query":
      return ["query", `[${e.index}/${e.total}] ${e.player} — ${e.brand}`];
    case "results":
      return ["reject", (e.query ? `   “${e.query}”: ` : "   ") + (e.total
        ? `${e.count} listing(s) from ${e.offset + 1} of ${e.total.toLocaleString()}.`
        : `${e.count} listing(s) returned.`)];
    case "reject":
      return ["reject", `   skipped: ${e.reason} — ${e.title}`];
    case "known":
      return ["info", `   already in your spreadsheet from an earlier run — ${e.title}`];
    case "match":
      return ["match", `   MATCH ${e.serial} — ${e.title}`];
    case "error":
      return ["error", e.message];
    case "info":
      return ["info", e.message];
    case "done":
      return ["match", (e.cancelled ? "Stopped. " : "Finished. ")
        + `${e.matches} new match(es) from ${e.checked} listings`
        + (e.known ? `, ${e.known} already recorded earlier` : "")
        + (e.judged ? `, ${e.judged} turned down on an earlier run` : "")
        + (e.failed ? `, ${e.failed} eBay would not return` : "") + "."];
    default:
      return ["info", e.kind];
  }
}

function applyEvent(e) {
  const [kind, text] = describe(e);
  logLine(kind, text, e.at);

  if (e.kind === "start") {
    state.totalQueries = e.queries;
    state.doneQueries = 0;
    state.listingsSeen = 0;
  }
  if (e.kind === "query") {
    state.doneQueries = e.index;
    $("progress-now").textContent = `${e.player} · ${e.brand}`;
  }
  if (e.kind === "results") state.listingsSeen += e.count;
  if (e.kind === "match") {
    if (state.showingExamples) state.matches = [];
    state.matches.unshift(e);
    renderMatches();
    placeOnBoard(e);
  }
  if (e.kind === "done") {
    $("progress-now").textContent = e.cancelled ? "Stopped." : "Finished.";
    $("download-btn").hidden = false;
    loadBoard();
  }
  updateProgress();
}

function updateProgress() {
  const pct = state.totalQueries
    ? Math.round((state.doneQueries / state.totalQueries) * 100) : 0;
  $("progress-bar").style.width = `${pct}%`;
  $("progress-count").textContent = `${state.doneQueries} / ${state.totalQueries} queries`;
  $("progress-checked").textContent = `${state.listingsSeen} listings`;
}

function setRunning(running) {
  state.running = running;
  $("run-btn").disabled = running || !(state.config && state.config.credentials);
  $("stop-btn").disabled = !running;
  $("run-btn").textContent = running ? "Scanning…" : "Run scan";
  const pill = $("log-pill");
  pill.textContent = running ? "Scanning" : "Idle";
  pill.className = running ? "pill is-live" : "pill";
}

async function poll() {
  let snap;
  try {
    snap = await (await fetch(API.events(state.since))).json();
  } catch {
    return;
  }
  (snap.events || []).forEach((e) => {
    state.since = Math.max(state.since, e.seq);
    applyEvent(e);
  });
  if (!snap.running && state.running) {
    setRunning(false);
    clearInterval(state.polling);
    state.polling = null;
  }
}

async function runScan() {
  if (HOSTED) return;
  const everyone = $("all-players").checked;
  const players = everyone ? [] : selected("player");
  const brands = selected("brand");
  if ((!everyone && !players.length) || !brands.length) {
    logLine("error", "Pick at least one set, and either every player or some names.");
    return;
  }

  $("logbox").innerHTML = "";
  state.since = 0;
  setRunning(true);
  updateProgress();

  const body = {
    players, brands,
    maxPrintRun: Number($("max-print-run").value) || undefined,
    printRunInclusive: $("inclusive").checked,
    writeOutputs: $("write-outputs").checked,
    minPrice: state.price.min || undefined,
    maxPrice: state.price.max == null ? undefined : state.price.max,
    listingTypes: state.listing === "all" ? undefined : [state.listing],
    cardTypes: state.cardtype === "all" ? undefined : [state.cardtype],
    conditions: state.condition === "all" ? undefined : [state.condition],
  };

  let res;
  try {
    res = await (await fetch(API.scan, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })).json();
  } catch {
    logLine("error", "Couldn't reach the server. Is python server.py still running?");
    setRunning(false);
    return;
  }

  if (!res.started) {
    logLine("error", res.error || "The scan couldn't start.");
    setRunning(false);
    return;
  }
  state.polling = setInterval(poll, 900);
  poll();
}

async function stopScan() {
  $("stop-btn").disabled = true;
  try { await fetch(API.stop, { method: "POST" }); } catch { /* ignore */ }
}

/* ------------------------------------------------------------------ wire */

document.addEventListener("DOMContentLoaded", () => {
  initSaved();
  loadConfig().then(loadSavedMatches).then(loadBoard);
  initRail();

  $("stop-btn").addEventListener("click", stopScan);
  $("download-btn").addEventListener("click", () => { window.location = API.spreadsheet; });
  $("run-btn").addEventListener("click", runScan);

  $("all-players").addEventListener("change", syncPlayerChips);
  wireSearch("player", "player-add");
  wireSearch("brand", "set-add");
  document.querySelectorAll("[data-toggle-all]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const group = btn.dataset.toggleAll === "players" ? "player" : "brand";
      if (group === "player" && $("all-players").checked) {
        $("all-players").checked = false;              // choosing names means narrowing
        syncPlayerChips();
      }
      const boxes = [...document.querySelectorAll(`input[data-group="${group}"]`)];
      const turnOn = boxes.some((b) => !b.checked);
      boxes.forEach((b) => { b.checked = turnOn; });
    });
  });

  $("holo-close").addEventListener("click", closeHologram);
  $("holo").addEventListener("click", (e) => {
    if (e.target === $("holo")) closeHologram();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("holo").hidden) closeHologram();
  });

  initRotor();
  initContents();
});
