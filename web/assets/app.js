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

/* ---- where a reload leaves you ----
   The browser puts a reloaded page back where it was scrolled to, which is
   measured against the old page's height. This page is taller a second later
   than it is at load -- the board, the matches and their photos all arrive
   after it -- so restoring the offset landed somewhere arbitrary, and a
   finished hosted scan reloads the page every time. So the browser is told
   not to guess, and the page settles itself once the cards are in:

     a plain load                     -> the top
     a reload a finished scan asked for -> the Matches panel, which is why it reloaded
     a link with a #section           -> that section

   Deterministic in all three cases, which is the point. */
const LAND_KEY = "tce.landOn";
try { history.scrollRestoration = "manual"; } catch { /* older browsers: as before */ }

function landOnAfterReload(where) {
  try { sessionStorage.setItem(LAND_KEY, where); } catch { /* ignore */ }
}

let settled = false;
function settleScroll() {
  if (settled) return;
  settled = true;
  let want = "";
  try {
    want = sessionStorage.getItem(LAND_KEY) || "";
    sessionStorage.removeItem(LAND_KEY);         // this load only
  } catch { /* ignore */ }
  const hash = location.hash || "";
  if (VIEWS[hash]) { window.scrollTo({ top: 0 }); return; }   // a page, not an anchor
  const target = document.getElementById(hash.slice(1))
    || (want && document.getElementById(want));
  if (target) target.scrollIntoView({ block: "start" });
  else window.scrollTo({ top: 0 });
}

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
  pageSize: 24,              // cards per page in the Matches panel; 0 means all
  page: { fresh: 1, board: 1 },
  filterSig: null,           // what the filters were last render, to spot a change
  hostedScan: null,          // {startedAt, phase, phaseAt} while a GitHub scan is watched
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
/* ---- the scan setup is a filter too: players, sets, print-run ceiling ----
   These three go to the engine to decide what gets recorded, and until now
   that was all they did: "Everything found so far" ignored them, so narrowing
   the scan to one set and one player still showed the whole record. Now they
   narrow what is shown exactly as price, listing type, card type and condition
   already did. A card the page cannot place -- no brand, no readable serial --
   is shown rather than hidden, as everywhere else on this page. */

/* the ticked chips of a group, lower-cased; null when the group is not narrowing */
function chosen(group) {
  const boxes = chipsOf(group);
  const on = boxes.filter((b) => b.checked);
  if (!on.length || on.length === boxes.length) return null;
  return on.map((b) => b.value);
}

/* "Daniil Medvedev" is the same person as "DANIIL MEDVEDEV", and a card
   recorded as "Erika Andreeva, Mirra Andreeva" is Mirra's; "Serena Williams"
   is not "Venus Williams". Whole names contain each other, one way or the other. */
function samePlayer(wanted, recorded) {
  const a = words(wanted), b = words(recorded);
  if (!a.length || !b.length) return false;
  const within = (x, y) => x.every((w) => y.includes(w));
  return within(a, b) || within(b, a);
}

function inPlayers(card) {
  if ($("all-players").checked) return true;
  const names = chosen("player");
  if (!names) return true;
  return names.some((name) => samePlayer(name, card.player || ""));
}

function inBrands(card) {
  const sets = chosen("brand");
  if (!sets || !card.brand) return true;
  return sets.some((name) => name.toLowerCase() === card.brand.toLowerCase());
}

function printRunOf(card) {
  const m = String(card.serial || "").match(/^\s*\d+\s*\/\s*(\d+)\s*$/);
  return m ? Number(m[1]) : null;
}

function underCeiling(card) {
  const run = printRunOf(card);
  if (run == null) return true;                          // unreadable serial: never hide
  const ceiling = Number($("max-print-run").value)
    || (state.config && state.config.maxPrintRun) || 500;
  return run < ceiling || ($("inclusive").checked && run === ceiling);
}

function passesFilters(card) {
  return inPriceRange(card) && inListingType(card) && inBookend(card) && inWindow(card)
    && inCardType(card) && inCondition(card) && inColourMatch(card)
    && inPlayers(card) && inBrands(card) && underCeiling(card);
}

/* ---- and it survives a reload ----
   A finished hosted scan reloads the page, and loadConfig used to put every
   scan-setup control back to the engine's default on the way in. So the setup
   you had just scanned with was gone by the time the results appeared. */
const SCAN_SETUP_KEY = "tce.scanSetup";

function saveScanSetup() {
  try {
    localStorage.setItem(SCAN_SETUP_KEY, JSON.stringify({
      allPlayers: $("all-players").checked,
      players: selected("player"),
      brands: selected("brand"),
      ceiling: $("max-print-run").value,
      inclusive: $("inclusive").checked,
    }));
  } catch { /* private mode: the setup lasts the session */ }
}

function restoreScanSetup() {
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem(SCAN_SETUP_KEY) || "null"); } catch { /* ignore */ }
  if (!saved) return;
  const tick = (group, names) => {
    const wanted = new Set((names || []).map((n) => n.toLowerCase()));
    chipsOf(group).forEach((b) => { b.checked = wanted.has(b.value.toLowerCase()); });
  };
  if (Array.isArray(saved.players)) tick("player", saved.players);
  if (Array.isArray(saved.brands)) tick("brand", saved.brands);
  if (typeof saved.allPlayers === "boolean") $("all-players").checked = saved.allPlayers;
  if (saved.ceiling && Number(saved.ceiling) > 0) $("max-print-run").value = saved.ceiling;
  if (typeof saved.inclusive === "boolean") $("inclusive").checked = saved.inclusive;
  syncPlayerChips();
}

/* The "Bypass ceiling" toggle beside the eBay calls meter. On, a scan started
   from this device runs without the engine's local daily ceiling: hosted, as
   the bypass_budget workflow input; locally, as bypassBudget in the request.
   Remembered per device (tce.bypassBudget) like the rest of the setup, since a
   finished hosted scan reloads the page. The scheduled run never bypasses. */
const BYPASS_KEY = "tce.bypassBudget";

function bypassBudget() {
  return $("bypass-budget").checked;
}

function renderBypass() {
  const on = bypassBudget();
  $("allowance").classList.toggle("is-bypassed", on);
  $("bypass-note").hidden = !on;
}

function wireBypass() {
  let saved = false;
  try { saved = localStorage.getItem(BYPASS_KEY) === "1"; } catch { /* ignore */ }
  $("bypass-budget").checked = saved;
  renderBypass();
  $("bypass-budget").addEventListener("change", () => {
    try { localStorage.setItem(BYPASS_KEY, bypassBudget() ? "1" : "0"); } catch { /* ignore */ }
    renderBypass();
  });
}

/* every change to the setup: remember it, and narrow what is shown right away */
function scanSetupChanged() {
  saveScanSetup();
  renderMatches();
  renderBoard();
}

function wireScanSetup() {
  // chips are built and rebuilt after load, so listen on their containers
  ["players-chips", "brands-chips"].forEach((id) =>
    $(id).addEventListener("change", scanSetupChanged));
  $("all-players").addEventListener("change", scanSetupChanged);
  $("inclusive").addEventListener("change", scanSetupChanged);
  $("max-print-run").addEventListener("input", scanSetupChanged);
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

/* Cards the most recent scan turned up. state.matches is exactly that list --
   new_matches.json on the hosted page, the live event stream on a local one --
   so this is derived from it at render time rather than kept in step by hand.

   A card stops being new an hour after the scan recorded it, whichever device
   is looking and whether or not another scan has run since: the clock is the
   "Date Found" the scan wrote, so every device agrees and nothing has to be
   remembered per browser. A card whose date cannot be read keeps its flag
   until the next scan replaces the list, the same as before. */
const NEW_FOR_MS = 60 * 60 * 1000;
const newlyFound = new Set();
let newFlagTimer = null;

function foundAt(card) {
  const d = new Date(String(card.found || "").replace(/ UTC$/, "Z").replace(" ", "T"));
  return isNaN(d) ? null : d;
}

/* when this card's flag goes, or null when it has no readable date */
function newFlagGoesAt(card) {
  const at = foundAt(card);
  return at ? at.getTime() + NEW_FOR_MS : null;
}

function markNewlyFound() {
  newlyFound.clear();
  const now = Date.now();
  state.matches.forEach((m) => {
    if (!m.link) return;
    const goes = newFlagGoesAt(m);
    if (goes === null || goes > now) newlyFound.add(m.link);
  });
  scheduleNewFlagSweep();
}

/* Take the flags off by themselves, without waiting for a reload. Timers in a
   hidden tab are throttled or stopped, so coming back to the tab sweeps too --
   see initWakeChecks. */
function scheduleNewFlagSweep() {
  clearTimeout(newFlagTimer);
  newFlagTimer = null;
  const now = Date.now();
  let soonest = Infinity;
  state.matches.forEach((m) => {
    const goes = m.link ? newFlagGoesAt(m) : null;
    if (goes !== null && goes > now && goes < soonest) soonest = goes;
  });
  if (soonest === Infinity) return;
  newFlagTimer = setTimeout(sweepNewFlags, Math.max(soonest - now, 1000));
}

function sweepNewFlags() {
  markNewlyFound();
  renderBoard();
  renderMatches();
}

function isNewCard(card) {
  return !!card.link && newlyFound.has(card.link);
}

function isSoldCard(card) {
  return statusOf(card).status === "sold";
}

function flagTag(className, words, title) {
  const tag = el("span", className, words);
  tag.title = title;
  tag.setAttribute("aria-label", title);
  return tag;
}

/* One flag rides beside the star, and sold outranks new: a card that cannot
   be bought is the more useful thing to know at a glance, and both in the same
   corner would be a scrum. Sold comes from results/status.json on the hosted
   page, or from whatever the saved page last checked locally -- and from the
   owner's own mark ahead of either, since statusOf honours that first. */
function flagFor(card) {
  if (isSoldCard(card)) return flagTag("sold-tag", "Sold", "This listing has sold");
  if (isNewCard(card)) return flagTag("new-tag", "New", "Found in the last hour");
  return null;
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

/* The board and the matches need statuses too, not just the saved page, so
   the published file is read once at startup. Hosted only on purpose: the file
   is static and free, while the local path asks eBay per listing and every one
   of those comes out of the same daily allowance a scan spends. Locally the
   board uses whatever the saved page last checked, kept in this browser. */
async function loadStatuses() {
  if (!HOSTED) return;
  try {
    const data = await (await fetch("results/status.json", { cache: "no-store" })).json();
    if (!data || !data.statuses) return;
    Object.assign(state.statuses, data.statuses);
    state.statusCheckedAt = data.checkedAt || state.statusCheckedAt;
    try {
      localStorage.setItem(SAVED.statusKey,
        JSON.stringify({ at: state.statusCheckedAt, statuses: state.statuses }));
    } catch { /* private mode: this page still has them in memory */ }
    renderBoard();
    renderMatches();
  } catch { /* no status file yet; cards simply carry no sold flag */ }
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

/* ---- the two pages that are not the main one ----
   Saved cards, and the method and process reference. Both used to be sections
   of one very long page; both are now routed off the hash, one router so they
   can never both be showing. #method was an anchor you scrolled to -- every
   link to it still works, it just opens the page now. */
const VIEWS = {
  "#saved":  { id: "saved-page", cls: "is-saved-view" },
  "#method": { id: "method", cls: "is-method-view" },
};

/* A link to a part of the reference -- #limits, #allowlist -- used to be a
   link to a place on this page. It still works: it opens the page that part
   now lives on and goes to it. */
function partOfAView(hash) {
  const target = hash.length > 1 && document.getElementById(hash.slice(1));
  if (!target) return null;
  const view = Object.values(VIEWS).find((v) => $(v.id).contains(target));
  return view ? { view, target } : null;
}

function showView(hash) {
  const deep = VIEWS[hash] ? null : partOfAView(hash);
  const wanted = VIEWS[hash] || (deep && deep.view) || null;
  Object.values(VIEWS).forEach((view) => {
    const on = view === wanted;
    document.body.classList.toggle(view.cls, on);
    $(view.id).hidden = !on;
  });
  $("method-link").classList.toggle("is-on", wanted === VIEWS["#method"]);
  if (deep) deep.target.scrollIntoView({ block: "start" });
  else if (wanted) window.scrollTo({ top: 0 });
  if (wanted === VIEWS["#saved"]) {
    renderSaved();
    refreshStatuses();
  }
}

function showSavedView(on) {
  showView(on ? "#saved" : "");
}

/* The title in the top bar is the way home: whichever page you are on, it
   puts the main one back and goes to the top. The hash is taken off the URL
   rather than set to something, so the back button still works -- removing it
   with pushState fires no hashchange, which is why the view is set by hand. */
function goHome() {
  if (location.hash) {
    history.pushState("", document.title, location.pathname + location.search);
  }
  showView("");
  window.scrollTo({ top: 0 });
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
  const route = () => showView(location.hash);
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
  restoreScanSetup();                    // what this device last scanned with, if anything
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
    (c.customWords || []).length
      ? "Cards somebody made themselves are rejected before the serial is even read, "
        + "so a custom card called a 1/1 never counts. A listing is taken as custom when "
        + `its title or set says any of: ${c.customWords.join(", ")}.`
      : "",
  ].filter(Boolean).join(" ");
  $("blocklist-note").textContent = blocked;
  const setup = $("colourmatch-setup");
  setup.hidden = !!c.colourMatch;
  setup.textContent = c.colourMatch ? "" : (HOSTED
    ? "Outfit colours are read from photos by Claude once an ANTHROPIC_API_KEY secret is added to the repo; until then no card can be marked a colour match."
    : "Outfit colours are read from photos by Claude once ANTHROPIC_API_KEY is in .env (and pip install anthropic); until then no card can be marked a colour match.");
  if (HOSTED) enterHostedMode(c);
  // The contents panel says where this page runs, and that is not the same
  // sentence in both places: hosted, the scan runs on GitHub, not here.
  $("contents-foot").textContent = HOSTED
    ? "Scans run on GitHub on a schedule. Your eBay keys stay in the repository's secrets and never reach the browser."
    : "Runs on your machine. Your eBay keys stay in .env and never reach the browser.";
}

function syncPlayerChips() {
  const all = $("all-players").checked;
  document.querySelectorAll('input[data-group="player"]').forEach((i) => { i.disabled = all; });
  $("players-chips").style.opacity = all ? ".45" : "";
}

function buildChip(box, value, name, checked, added = false) {
  const id = `${name}-${box.children.length}`;
  const label = el("label", "chip" + (added ? " is-added" : ""));
  label.htmlFor = id;
  const input = el("input");
  input.type = "checkbox";
  input.id = id;
  input.value = value;
  input.checked = checked;
  input.dataset.group = name;
  label.append(input, el("span", null, value));
  if (added) label.append(removeButton(name, value, label));
  box.append(label);
  return input;
}

/* A name you typed in can be taken out again; the built-in ones cannot. The
   cross sits inside the label, so its click has to be stopped from also
   toggling the chip underneath it. */
function removeButton(group, value, label) {
  const x = el("button", "chip-x", "\u00d7");
  x.type = "button";
  x.title = `Remove ${value}`;
  x.setAttribute("aria-label", `Remove ${value}`);
  x.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    forget(group, value);
    label.remove();
    // taking away the last ticked name would leave nothing to scan or show
    if (group === "player" && !selected("player").length) {
      $("all-players").checked = true;
      syncPlayerChips();
    }
    scanSetupChanged();
  });
  return x;
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

function forget(group, name) {
  try {
    localStorage.setItem(SEARCHES[group].key, JSON.stringify(
      remembered(group).filter((n) => n.toLowerCase() !== name.toLowerCase())));
  } catch { /* storage unavailable; the chip is gone for this session anyway */ }
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
      buildChip($(SEARCHES[group].box), name, group, false, true);
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
  const chip = existing || buildChip($(cfg.box), name, group, true, true);
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
  scanSetupChanged();
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

/* A heading inside the results panel, with the count beside it. */
function sectionHead(title, count) {
  const head = el("div", "section-head");
  head.append(el("h3", null, title));
  if (count) head.append(el("span", "pill", count));
  return head;
}

function cardGrid(cards, offset = 0, which = "") {
  const grid = el("div", "matchgrid");
  if (which) grid.dataset.section = which;       // which list this page belongs to
  cards.forEach((card, i) => grid.append(buildCard(card, offset + i)));
  return grid;
}

function noteLine(text, padding = "16px 20px") {
  const note = el("div", "empty");
  note.style.padding = padding;
  note.append(el("p", null, text));
  return note;
}

/* ---- pages, because the record is long ----
   The board is 173 cards and climbing. Drawing every one of them put a mile
   of scrolling between the Matches panel and everything under it -- the
   activity log, the reference, the footer -- and made a phone work hard for
   cards nobody had asked to see. Each section of the panel now shows one page
   at a time, and the picker in the panel head says how big a page is.
   24 is the default: three or four rows on a laptop, enough to browse without
   burying the rest of the page. */
const PAGE_KEY = "tce.pageSize";
const PAGE_SIZES = [12, 24, 48, 96, 0];          // 0 is "All"
const PAGE_DEFAULT = 24;

function initPageSize() {
  const box = $("page-size");
  let saved = PAGE_DEFAULT;
  try {
    // Nothing saved must not read as "All": Number(null) is 0, and 0 is a
    // size in this list.
    const kept = localStorage.getItem(PAGE_KEY);
    if (kept !== null && PAGE_SIZES.includes(Number(kept))) saved = Number(kept);
  } catch { /* private mode: the default stands for this session */ }
  state.pageSize = saved;
  box.value = String(saved);
  box.addEventListener("change", () => {
    const size = Number(box.value);
    state.pageSize = PAGE_SIZES.includes(size) ? size : PAGE_DEFAULT;
    try { localStorage.setItem(PAGE_KEY, String(state.pageSize)); } catch { /* ignore */ }
    state.page.fresh = 1;                        // a new page size has new pages
    state.page.board = 1;
    renderMatches();
  });
}

/* Everything that changes which cards are shown. When it changes, the pages
   are counted afresh from the first one -- landing on page 5 of a list you
   have just narrowed to two cards is not what anyone means by filtering. */
function filterSignature() {
  return JSON.stringify([
    state.price.min, state.price.max, state.listing, state.sort, state.bookend,
    state.window, state.cardtype, state.condition, state.colourmatch,
    $("all-players") && $("all-players").checked, chosen("player"), chosen("brand"),
    $("max-print-run") && $("max-print-run").value,
    $("inclusive") && $("inclusive").checked,
  ]);
}

function pageCount(total) {
  return state.pageSize ? Math.max(1, Math.ceil(total / state.pageSize)) : 1;
}

/* The slice of a list this page shows, with the page clamped into range --
   a list that shrank under you lands on its last page, never on an empty one. */
function pageOf(list, which) {
  if (!state.pageSize) return { cards: list, page: 1, pages: 1, from: 0 };
  const pages = pageCount(list.length);
  const page = Math.min(Math.max(1, state.page[which] || 1), pages);
  state.page[which] = page;
  const from = (page - 1) * state.pageSize;
  return { cards: list.slice(from, from + state.pageSize), page, pages, from };
}

/* Previous / Next under a section, shown only when there is more than one
   page. Turning a page puts you at the top of that section rather than
   leaving you at the bottom of the page you just left. */
function pager(which, slice, anchorId) {
  const bar = el("div", "pager");
  const step = (label, to, enabled) => {
    const b = el("button", "pager-btn", label);
    b.type = "button";
    b.disabled = !enabled;
    b.addEventListener("click", () => {
      state.page[which] = to;
      renderMatches();
      const head = document.getElementById(anchorId);
      if (head) head.scrollIntoView({ block: "start", behavior: "smooth" });
    });
    return b;
  };
  bar.append(step("\u2039 Previous", slice.page - 1, slice.page > 1));
  bar.append(el("span", "pager-at", `Page ${slice.page} of ${slice.pages}`));
  bar.append(step("Next \u203a", slice.page + 1, slice.page < slice.pages));
  return bar;
}

/* A section: its heading, one page of its cards, and a pager when it needs
   one. The count beside the heading is the whole section, not the page. */
function section(area, which, title, count, cards, anchorId) {
  const head = sectionHead(title, count);
  head.id = anchorId;
  area.append(head);
  const slice = pageOf(cards, which);
  area.append(cardGrid(slice.cards, 0, which));
  if (slice.pages > 1) {
    const bar = pager(which, slice, anchorId);
    bar.dataset.section = which;
    area.append(bar);
  }
}

/* Two sections, because they answer two different questions.

   "New this scan" is what the last scan added, and it is empty most of the
   time -- once a listing has been judged it is never a new find again, so a
   scan minutes after the last one correctly adds nothing. That used to be the
   only thing this panel showed, which made every scan look like a failure and
   made the filters look broken: they cannot put a card here that was already
   found.

   "Everything found so far" is the whole record, filtered. That is the section
   the filters act on, and it responds the moment one is changed, with no scan
   needed. */
function renderMatches() {
  const area = $("results-area");
  markNewlyFound();
  const fresh = sortCards(state.matches.filter(passesFilters));
  const everything = sortCards(state.board.filter(passesFilters));
  state.showingExamples = !state.matches.length && !state.board.length;

  const sig = filterSignature();
  if (state.filterSig !== null && sig !== state.filterSig) {
    state.page.fresh = 1;                        // a changed filter is a new list
    state.page.board = 1;
  }
  state.filterSig = sig;

  area.innerHTML = "";

  if (state.showingExamples) {
    const shown = sortCards(EXAMPLES.filter(passesFilters));
    $("match-count").textContent = "example cards";
    area.append(noteLine(
      "No cards found yet. These three are examples of what a match looks like, " +
      "not real listings. Tap one to open the card.", "16px 20px 0"));
    area.append(shown.length ? cardGrid(shown)
      : noteLine("Nothing matches the filters in the scan setup."));
    return;
  }

  $("match-count").textContent = everything.length === state.board.length
    ? `${state.board.length} found`
    : `${everything.length} of ${state.board.length} match the filters`;

  if (fresh.length) {
    section(area, "fresh", "New this scan", `${fresh.length}`, fresh, "sec-fresh");
  } else if (state.matches.length) {
    area.append(sectionHead("New this scan", ""));
    area.append(noteLine("The last scan found "
      + `${state.matches.length} new card${state.matches.length === 1 ? "" : "s"}, `
      + "but none of them match the filters below."));
  } else if (state.config && state.config.lastRunOk === false) {
    area.append(sectionHead("New this scan", ""));
    area.append(noteLine("Nothing new, because the last scan failed before it could look: "
      + "eBay refused its calls, or the run was cut short. The GitHub Actions run says "
      + "which. Everything found so far is below."));
  } else {
    area.append(sectionHead("New this scan", ""));
    area.append(noteLine("Nothing new. Every listing on eBay right now has already "
      + "been judged on an earlier scan, so there was nothing left to add \u2014 which is "
      + "normal, and not the filters. Everything found so far is below."));
  }

  const count = `${everything.length}${everything.length === state.board.length ? "" : ` of ${state.board.length}`}`;
  if (everything.length) {
    section(area, "board", "Everything found so far", count, everything, "sec-board");
  } else {
    area.append(sectionHead("Everything found so far", count));
    area.append(noteLine("Nothing recorded matches the scan setup. Widen the players, sets, "
      + "print-run ceiling, card type, graded or raw, price, listing type, bookend "
      + "or listed-within choice."));
  }
}

function initials(name) {
  return name.split(/\s+/).map((w) => w[0] || "").join("").slice(0, 3).toUpperCase();
}

/* Rows recorded before the title was read for a name carry the literal
   "Unknown player", and results/board.json keeps saying so until the next
   scan rewrites it. Treat that as blank, so the quiet fallback shows now. */
function namedPlayer(card) {
  const p = (card.player || "").trim();
  return p.toLowerCase() === "unknown player" ? "" : p;
}

function buildCard(match, index) {
  match = { ...match, player: namedPlayer(match) };
  const card = el("button", "matchcard" + (match._example ? " is-example" : ""));
  card.type = "button";
  card.dataset.index = index;
  card.style.animationDelay = `${Math.min(index, 8) * 45}ms`;
  card.setAttribute("aria-label", `${match.player || "Card"}, ${match.serial}. Open card.`);

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
      const again = flagFor(match);
      if (again) photo.append(again);
    });
    photo.append(img);
  } else {
    photo.append(el("span", "mc-crest", initials(match.player)));
  }
  const serialTag = el("span", "mc-serial-tag", match.serial);
  photo.append(serialTag);
  photo.append(starFor(match));
  const flag = flagFor(match);
  if (flag) {
    // the serial shares that corner with the star; the class steps it clear
    // so the three do not stack
    photo.classList.add("has-flag");
    photo.append(flag);
  }
  card.append(photo);

  const body = el("div", "mc-body");
  body.append(el("div", "mc-player" + (match.player ? "" : " is-unnamed"), match.player || "Player not named"));
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
  card = { ...card, player: namedPlayer(card) };
  const lot = el("button", "lot" + (card._example ? " is-example" : ""));
  lot.type = "button";
  lot.setAttribute("aria-label", `${rank}. ${card.player || "Card"}, ${card.price}. Open card.`);

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
  const flag = flagFor(card);
  if (flag) photo.append(flag);
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
  body.append(el("div", "lot-player" + (card.player ? "" : " is-unnamed"), card.player || "Player not named"));
  body.append(el("div", "lot-set", brandLine(card)));
  lot.append(body);

  lot.addEventListener("click", () => { if (!rail.suppressClick) openHologram(card); });
  return lot;
}

function renderBoard() {
  const railEl = $("rail");
  markNewlyFound();
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
  if (!state.matches.length) renderMatches();   // fall back to the board's cards
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

/* One-tap scans. A GitHub token pasted on this device is kept only in this
   browser's storage, never in the repo, so the public page stays safe: any
   other browser still gets the plain "open GitHub" button. */
const GH_TOKEN_KEY = "tce.githubToken";

function githubToken() {
  try { return localStorage.getItem(GH_TOKEN_KEY) || ""; } catch { return ""; }
}

function setGithubToken(token) {
  try {
    if (token) localStorage.setItem(GH_TOKEN_KEY, token);
    else localStorage.removeItem(GH_TOKEN_KEY);
  } catch { /* private mode: nothing is kept */ }
}

function enterHostedMode(c) {
  const pill = $("status-pill");
  // #18 publishes the results of a run that failed, so lastRun alone would
  // dress a burnt allowance or a cut-short run as a quiet day
  const failed = c.lastRunOk === false;
  pill.textContent = c.lastRun ? `${failed ? "Last scan failed" : "Last scan"} ${c.lastRun}` : "Hosted";
  pill.className = failed ? "pill is-warn" : "pill is-ok";
  document.body.classList.add("is-hosted");
  $("stop-btn").hidden = true;
  $("download-btn").hidden = !c.spreadsheetExists;
  stopHostedProgress(!c.lastRun ? "Idle."
    : failed ? `The last scan, ${c.lastRun}, failed on GitHub; the run there says why.`
    : `Last scan finished ${c.lastRun}.`);

  const run = $("run-btn");
  run.onclick = (e) => { e.stopImmediatePropagation(); onHostedRun(c); };
  if (!resumeHostedWatch(c)) renderHostedNote(c);
}

function actionsUrl(c) {
  return c.repo ? `https://github.com/${c.repo}/actions/workflows/${c.workflow || "scan.yml"}` : "";
}

function renderHostedNote(c, message) {
  const note = $("hosted-note");
  const run = $("run-btn");
  const hasToken = !!githubToken();
  note.hidden = false;
  note.innerHTML = "";

  if (!c.repo) {
    run.textContent = "Run a scan on GitHub";
    run.disabled = true;
    note.append(el("span", null, "Scans run on GitHub on a schedule, and this page updates after each one."));
    return;
  }

  run.disabled = false;
  run.textContent = hasToken ? "Run a scan" : "Run a scan on GitHub";
  note.append(el("span", null, message || (hasToken
    ? "One-tap scans are on for this device. Run a scan starts one on GitHub straight away. "
    : "Scans run on GitHub on a schedule, and this page updates after each one. "
      + "Run a scan on GitHub opens GitHub, where you press Run workflow. ")));

  const toggle = el("button", "linkbtn", hasToken ? "Remove key from this device" : "Set up one-tap scans on this device");
  toggle.type = "button";
  toggle.addEventListener("click", () => (hasToken ? forgetKey(c) : addKey(c)));
  note.append(toggle);
}

function addKey(c) {
  const entered = window.prompt(
    "Paste your GitHub fine-grained token for this repo (Actions: Read and write). "
    + "It is stored only in this browser.");
  const token = (entered || "").trim();
  if (!token) return;
  if (!/^(github_pat_|ghp_)[A-Za-z0-9_]+$/.test(token)) {
    renderHostedNote(c, "That doesn't look like a GitHub token. It should start with github_pat_. ");
    return;
  }
  setGithubToken(token);
  renderHostedNote(c, "Key saved on this device. Run a scan now starts one on GitHub straight away. ");
  loadAllowance();                       // the allowance is shown once a key is set
}

function forgetKey(c) {
  setGithubToken("");
  renderHostedNote(c, "Key removed from this device. Run a scan on GitHub opens GitHub again. ");
  $("allowance").hidden = true;
}

async function onHostedRun(c) {
  const url = actionsUrl(c);
  const token = githubToken();
  if (!token) {
    if (url) window.open(url, "_blank", "noopener");
    return;
  }

  const settings = scanSettings();
  const problem = settingsProblem(settings);
  if (problem) {
    renderHostedNote(c, problem + " ");
    return;
  }

  const run = $("run-btn");
  run.disabled = true;
  run.textContent = "Starting scan…";
  let r;
  try {
    r = await fetch(
      `https://api.github.com/repos/${c.repo}/actions/workflows/${c.workflow || "scan.yml"}/dispatches`,
      {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json" },
        body: JSON.stringify({ ref: "main", inputs: workflowInputs(settings) }),
      });
  } catch {
    renderHostedNote(c, "Couldn't reach GitHub. Check your connection and try again. ");
    return;
  }

  if (r.status === 204) {
    renderHostedNote(c, "Scan started. You can leave this page or switch tabs -- the scan runs on "
      + "GitHub, not here. This page reloads by itself when the new results are published, "
      + "usually within 2 to 4 minutes. ");
    run.textContent = "Scan started";
    run.disabled = true;
    watchForNewResults({ before: c.lastRun || "", startedAt: Date.now() });
    return;
  }
  if (r.status === 401) {
    setGithubToken("");
    renderHostedNote(c, "GitHub turned down the key, so it was removed from this device. "
      + "It may have expired or been deleted. Add a new one to try again. ");
  } else if (r.status === 403 || r.status === 404) {
    renderHostedNote(c, `This key can't start scans for ${c.repo}. `
      + "Check it has access to this repo with Actions set to Read and write. ");
  } else if (r.status === 422) {
    // GitHub reads the input list from the workflow file on the default
    // branch, so this is what a page newer than main looks like.
    renderHostedNote(c, "GitHub wouldn't accept the scan settings. The copy of "
      + "scan.yml on main may be older than this page. ");
  } else {
    renderHostedNote(c, `GitHub didn't start the scan (HTTP ${r.status}). Try again in a minute. `);
  }
}

/* Reload once the published results change, for up to 45 minutes. Checking
   every 10 seconds rather than every minute: the check is one small file, and
   a minute's wait used to be most of what stood between a finished scan and
   seeing it. */
const RESULT_POLL_MS = 10000;
const RESULT_POLL_LIMIT_MS = 45 * 60000;
const WATCH_KEY = "tce.watching";
let resultTimer = null;

/* Nothing about the scan lives in this page -- it runs on GitHub -- so leaving
   the tab never stops it. What stops is this page noticing: a hidden tab has
   its timers throttled or suspended, and a phone may drop the tab from memory
   altogether, taking the interval with it. So the watch is written down rather
   than left to an interval that may never fire again. */
function rememberWatch(watch) {
  try { localStorage.setItem(WATCH_KEY, JSON.stringify(watch)); }
  catch { /* private mode: this run has only the interval to go on */ }
}

function forgetWatch() {
  try { localStorage.removeItem(WATCH_KEY); } catch { /* ignore */ }
}

/* the watch this device is part-way through, or null */
function savedWatch() {
  try {
    const w = JSON.parse(localStorage.getItem(WATCH_KEY) || "null");
    return w && w.until > Date.now() ? w : null;
  } catch { return null; }
}

async function checkForNewResults(before) {
  try {
    const r = await fetch(`results/config.json?t=${Date.now()}`, { cache: "no-store" });
    const fresh = await r.json();
    if (fresh.lastRun && fresh.lastRun !== before) {
      forgetWatch();                        // before reloading, so it cannot loop
      setHostedPhase("done");
      paintHostedProgress();
      landOnAfterReload("matches");         // the results are why this reloads
      window.location.reload();
    }
  } catch { /* try again on the next tick */ }
}

function watchForNewResults(watch) {
  clearInterval(resultTimer);
  const before = watch.before || "";
  const until = watch.until || Date.now() + RESULT_POLL_LIMIT_MS;
  rememberWatch({ ...watch, before, until });
  startHostedProgress(watch.startedAt, watch.runId);
  let tick = 0;
  resultTimer = setInterval(() => {
    paintHostedProgress();
    if (Date.now() > until) {
      clearInterval(resultTimer);
      resultTimer = null;
      forgetWatch();
      stopHostedProgress("Gave up waiting. The scan may still be running on GitHub.");
      renderHostedNote(state.config);
      return;
    }
    tick += 1;
    if (tick % (RESULT_POLL_MS / HOSTED_PAINT_MS) !== 0) return;   // paint often, ask rarely
    readRunProgress(state.config);
    checkForNewResults(before);
  }, HOSTED_PAINT_MS);
  checkForNewResults(before);               // and look now, not in ten seconds
  readRunProgress(state.config);
}

/* Picks up a scan this device was already waiting on, after a tab switch or
   after the tab was dropped and reloaded. */
function resumeHostedWatch(c) {
  const watching = savedWatch();
  if (!watching) return false;
  if (watching.before !== (c.lastRun || "")) {
    forgetWatch();                          // its results are already on this page
    return false;
  }
  renderHostedNote(c, "A scan is running on GitHub. You can leave this page or switch tabs "
    + "-- it carries on, and this page reloads by itself once the results are published. ");
  // after the note, which re-enables the button as part of drawing itself
  const run = $("run-btn");
  run.disabled = true;
  run.textContent = "Scan running";
  watchForNewResults(watching);
  return true;
}

/* ------------------------------------------ eBay's daily call allowance */
/* The figures come from eBay, but the keys that read them never reach the
   browser: the scheduled run writes results/usage.json after each scan, and
   the local server answers /api/usage from its own .env. On the hosted site
   this is shown only once a one-tap key has been set for the device -- it is
   operating detail, not part of the card board. That gate is what is drawn,
   not a secret kept: results/ is published with the page, so the file is
   readable by anyone who looks for it. */

function allowanceLabel(reset) {
  const at = new Date(reset || "");
  if (isNaN(at)) return "";
  const hours = Math.max(0, Math.round((at.getTime() - Date.now()) / 3600000));
  return hours ? `, resets in about ${hours}h` : ", resets shortly";
}

function renderAllowance(usage) {
  const box = $("allowance");
  if (!usage || !usage.limit) {
    // no figures: eBay reports none until a keyset has been used, and says
    // nothing at all about one not enabled for its Analytics API. Hosted the
    // box goes; locally it stays, since the bypass toggle lives in it and a
    // local scan can run whether or not eBay will say what it has cost.
    box.hidden = HOSTED && !(usage && usage.error);
    if (!box.hidden) {
      $("allowance-used").textContent = "Not available";
      $("allowance-left").textContent = "";
      $("allowance-bar").style.width = "0%";
      $("allowance-note").textContent = (usage && usage.error)
        || "eBay has not reported today's figures for this keyset yet.";
    }
    return;
  }
  const pct = Math.min(100, Math.round((usage.used / usage.limit) * 100));
  box.hidden = false;
  $("allowance-bar").style.width = `${pct}%`;
  $("allowance-bar").classList.toggle("is-low", usage.remaining / usage.limit < 0.2);
  $("allowance-used").textContent = `${usage.used.toLocaleString()} of ${usage.limit.toLocaleString()} used`;
  $("allowance-left").textContent = `${usage.remaining.toLocaleString()} left`;
  $("allowance-note").textContent =
    (HOSTED ? "As of the last scan" : "Checked just now") + allowanceLabel(usage.resets)
    + ". Shared with scans run from your PC and phone.";
}

async function loadAllowance() {
  if (HOSTED && !githubToken()) { $("allowance").hidden = true; return; }
  try {
    const url = HOSTED ? `results/usage.json?t=${Date.now()}` : "api/usage";
    renderAllowance(await (await fetch(url, { cache: "no-store" })).json());
  } catch {
    // hosted: no file yet, say nothing. local: no server or no figures, but
    // the box also carries the bypass toggle, so it stays.
    if (HOSTED) $("allowance").hidden = true;
    else renderAllowance(null);
  }
}

/* ------------------------------------ how far along a scan on GitHub is */
/* A hosted scan runs on GitHub, so there is no event stream to follow the way
   the local server provides one. Where a key is set for this device, GitHub's
   own job tells us which step the run is on, and that is what the bar shows --
   the step in plain words rather than the workflow's wording for it. Where it
   is not, the phases below are used instead: what happens is still known, only
   its timing is a guess, and the bar says as much rather than implying it
   knows more. Either way the wait that is left at the end is pages.yml
   republishing the site, which is not part of the run at all. */

const HOSTED_PAINT_MS = 1000;
const GITHUB_API = "https://api.github.com";

/* the workflow's step names, in the words of someone watching for cards */
const STEP_WORDS = {
  "Set up job": "Starting up on GitHub",
  "Bring back last run's results": "Loading what earlier scans found",
  "Run the engine": "Searching eBay",
  "Export what the hosted site reads": "Working out what this page shows",
  "Keep the results in the repo": "Saving the results",
  "Offer the spreadsheet as a download too": "Attaching the spreadsheet",
  "Complete job": "Finishing up",
};

function stepWords(name) {
  if (STEP_WORDS[name]) return STEP_WORDS[name];
  if (/^Post Run /.test(name || "")) return "Tidying up";
  // the unnamed steps read "Run actions/setup-python@v6", "Run pip install ..."
  return /^Run /.test(name || "") ? "Getting the machine ready" : (name || "Working");
}

const HOSTED_PHASES = {
  queued:     { from: 4,  to: 18,  over: 45000,  text: "Waiting for GitHub to start" },
  scanning:   { from: 18, to: 76,  over: 90000,  text: "Scanning eBay" },
  publishing: { from: 76, to: 96,  over: 90000,  text: "Publishing the page" },
  done:       { from: 100, to: 100, over: 1,     text: "Done" },
};
const HOSTED_RUN_PHASE = {
  queued: "queued", requested: "queued", waiting: "queued", pending: "queued",
  in_progress: "scanning",
  completed: "publishing",
};

function startHostedProgress(startedAt, runId) {
  const at = startedAt || Date.now();
  state.hostedScan = { startedAt: at, phase: "queued", phaseAt: at, runId: runId || null, step: null };
  $("progress-bar").classList.add("is-live");
  paintHostedProgress();
}

function setHostedPhase(phase) {
  const p = state.hostedScan;
  if (!p || p.phase === phase) return;
  // a phase never goes backwards: a slow status read must not rewind the bar
  if (Object.keys(HOSTED_PHASES).indexOf(phase) < Object.keys(HOSTED_PHASES).indexOf(p.phase)) return;
  p.phase = phase;
  p.phaseAt = Date.now();
}

function elapsedSince(at) {
  const s = Math.max(0, Math.round((Date.now() - at) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

function paintHostedProgress() {
  const p = state.hostedScan;
  if (!p) return;
  const bar = $("progress-bar");
  const elapsed = elapsedSince(p.startedAt);

  if (p.step) {                             // GitHub told us which step it is on
    const known = typeof p.step.pct === "number";
    bar.classList.toggle("is-waiting", !known);
    if (known) bar.style.width = `${p.step.pct}%`;
    $("progress-count").textContent = p.step.count || p.step.words;
    $("progress-checked").textContent = elapsed;
    $("progress-now").textContent = p.step.count ? p.step.words : `${p.step.words}.`;
    return;
  }

  const spec = HOSTED_PHASES[p.phase];
  const into = Math.min(1, (Date.now() - p.phaseAt) / spec.over);
  bar.classList.remove("is-waiting");
  bar.style.width = `${Math.round(spec.from + (spec.to - spec.from) * into)}%`;
  $("progress-count").textContent = spec.text;
  $("progress-checked").textContent = elapsed;
  if (p.phase !== "done") {
    $("progress-now").textContent = githubToken()
      ? "This page reloads by itself when the new results are published."
      : "Timings are estimated: without a key this page can't read the run's status.";
  }
}

function stopHostedProgress(message) {
  state.hostedScan = null;
  const bar = $("progress-bar");
  bar.classList.remove("is-live", "is-waiting");
  bar.style.width = "0%";
  $("progress-count").textContent = "Not scanning";
  $("progress-checked").textContent = "";
  $("progress-now").textContent = message || "Idle.";
}

async function githubJson(path) {
  const token = githubToken();
  const headers = { Accept: "application/vnd.github+json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const r = await fetch(`${GITHUB_API}${path}`, { headers, cache: "no-store" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

/* The run this scan started, rather than whatever ran most recently: the
   daily scheduled run, or a scan started from another device, would otherwise
   be reported as this one's progress. */
async function findDispatchedRun(c, startedAt) {
  const data = await githubJson(
    `/repos/${c.repo}/actions/workflows/${c.workflow || "scan.yml"}/runs`
    + "?event=workflow_dispatch&per_page=10");
  return (data.workflow_runs || [])
    .filter((r) => Date.parse(r.created_at) >= startedAt - 120000)
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at))[0] || null;
}

/* How far along that run is, read off the job's own steps. Returns null when
   nothing can be read, so the caller falls back to the phases and the clock. */
async function runProgress(c, runId) {
  const data = await githubJson(`/repos/${c.repo}/actions/runs/${runId}/jobs`);
  const job = (data.jobs || [])[0];
  const steps = (job && job.steps) || [];
  if (!job || !steps.length) return { words: "Starting up on GitHub" };
  if (job.status === "completed") {
    return job.conclusion && job.conclusion !== "success"
      ? { words: "The scan stopped early on GitHub. Open Actions to see why", stop: true }
      : { pct: 100, count: "Publishing the page", words: "The scan is done; the page is being rebuilt" };
  }
  const done = steps.filter((s) => s.status === "completed").length;
  const running = steps.find((s) => s.status === "in_progress");
  return {
    pct: Math.round((done / steps.length) * 100),
    count: stepWords((running || {}).name),
    words: `Step ${Math.min(done + 1, steps.length)} of ${steps.length} on GitHub`,
  };
}

/* Reads the run behind the scan being watched and hands it to the bar. Every
   way this can fail -- no key, rate-limited, a run GitHub has not created yet
   -- leaves the phases and the clock in charge, which are still true. */
async function readRunProgress(c) {
  const p = state.hostedScan;
  if (!p || !c || !c.repo || !githubToken()) return;
  try {
    if (!p.runId) {
      const run = await findDispatchedRun(c, p.startedAt);
      if (!run) return;                     // not created yet: the phases cover it
      p.runId = run.id;
      const saved = savedWatch();
      if (saved) rememberWatch({ ...saved, runId: run.id });
    }
    const step = await runProgress(c, p.runId);
    p.step = step;
    if (step.stop) {
      clearInterval(resultTimer);
      resultTimer = null;
      forgetWatch();
      paintHostedProgress();
      stopHostedProgress(step.words + ".");
      renderHostedNote(c);
    }
  } catch {
    p.step = null;                          // fall back to the phases and the clock
  }
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

  const noPhoto = () => {
    wrap.hidden = true;
    $("holo-scrim").hidden = true;
    img.removeAttribute("src");
    $("holo-monogram").style.display = "";
  };

  if (photoUrl) {
    // eBay drops a photo once its listing is gone; show the monogram rather
    // than leaving the alt text spread across the face of the card
    img.onerror = noPhoto;
    img.src = photoUrl;
    img.alt = altText;
    wrap.hidden = false;
    $("holo-scrim").hidden = false;
    $("holo-monogram").style.display = "none";
  } else {
    img.onerror = null;
    noPhoto();
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
  $("holo-name").textContent = namedPlayer(match) || "Player not named";
  $("holo-brand").textContent = brandLine(match);
  $("holo-serial").textContent = match.serial;
  $("holo-price").textContent = match.price || "\u2014";
  $("holo-monogram").textContent = initials(match.player);

  $("holo-back-crest").textContent = initials(match.player);
  $("holo-back-name").textContent = namedPlayer(match) || "Player not named";
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
  const noPhoto = () => {
    img.removeAttribute("src");
    wrap.hidden = true;
    face.classList.remove("has-photo");
  };
  if (url) {
    img.onerror = noPhoto;
    img.src = url;
    img.alt = "Listing photo of the card back";
    wrap.hidden = false;
    face.classList.add("has-photo");
  } else {
    img.onerror = null;
    noPhoto();
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
    img.addEventListener("error", () => {
      b.hidden = true;
      if (!strip.querySelector(".holo-thumb:not([hidden])")) strip.hidden = true;
    });
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

/* The local scan runs in the server, in its own thread, and keeps its whole
   event log -- so it carries on through a tab switch, a reload, or the page
   being closed entirely. This picks the live view back up: the log replays
   from the start, so nothing that happened while away is missed. */
async function resumeLocalScan() {
  if (HOSTED || state.running) return;
  let snap;
  try {
    snap = await (await fetch(API.events(0))).json();
  } catch {
    return;                                    // no server: nothing to resume
  }
  if (!snap.running) return;
  $("logbox").innerHTML = "";
  state.since = 0;
  setRunning(true);
  (snap.events || []).forEach((e) => {
    state.since = Math.max(state.since, e.seq);
    applyEvent(e);
  });
  if (!state.polling) state.polling = setInterval(poll, 900);
}

/* Coming back to the tab looks straight away rather than waiting for the next
   tick, which in a hidden tab may have been throttled to minutes apart or
   stopped altogether. pageshow covers Safari restoring a page from its
   back/forward cache, where no timer of ours is running at all. */
function initWakeChecks() {
  const wake = () => {
    if (document.visibilityState === "hidden") return;
    sweepNewFlags();            // an hour may have passed with our timers stopped
    if (HOSTED) {
      const watching = savedWatch();
      if (!watching) return;
      checkForNewResults(watching.before);
      if (state.config) readRunProgress(state.config);   // and catch the bar up
      return;
    }
    if (state.running) poll();
    else resumeLocalScan();
  };
  document.addEventListener("visibilitychange", wake);
  window.addEventListener("pageshow", wake);
  window.addEventListener("focus", wake);
}

/* The scan settings, read straight off the controls. Both paths start here:
   the local server takes this as JSON, GitHub as workflow inputs. Before, only
   the local path read them at all, so every hosted scan ran engine defaults
   however the controls were set. */
function scanSettings() {
  const everyone = $("all-players").checked;
  return {
    everyone,
    players: everyone ? [] : selected("player"),
    brands: selected("brand"),
    maxPrintRun: Number($("max-print-run").value) || undefined,
    printRunInclusive: $("inclusive").checked,
    writeOutputs: $("write-outputs").checked,
    minPrice: state.price.min || undefined,
    maxPrice: state.price.max == null ? undefined : state.price.max,
    listingTypes: state.listing === "all" ? undefined : [state.listing],
    cardTypes: state.cardtype === "all" ? undefined : [state.cardtype],
    conditions: state.condition === "all" ? undefined : [state.condition],
    bypassBudget: bypassBudget(),
  };
}

/* Nothing to scan: no sets ticked, or named players wanted but none named. */
function settingsProblem(s) {
  if (!s.brands.length) return "Pick at least one set.";
  if (!s.everyone && !s.players.length) return "Pick some players, or choose every player.";
  return "";
}

/* The same settings as the workflow_dispatch inputs scan.yml declares. Only
   what was actually chosen is sent; anything left out keeps the engine
   default, so a scheduled run and an untouched page scan alike. */
function workflowInputs(s) {
  const inputs = {};
  const put = (key, value) => {
    const text = Array.isArray(value) ? value.join(",") : value;
    if (text !== undefined && text !== null && text !== "") inputs[key] = String(text);
  };
  put("players", s.players);
  put("brands", s.brands);
  put("max_print_run", s.maxPrintRun);
  put("min_price", s.minPrice);
  put("max_price", s.maxPrice);
  put("listing_types", s.listingTypes);
  put("card_types", s.cardTypes);
  put("conditions", s.conditions);
  inputs.print_run_inclusive = !!s.printRunInclusive;   // declared as a boolean
  inputs.bypass_budget = !!s.bypassBudget;              // declared as a boolean
  return inputs;
}

async function runScan() {
  if (HOSTED) return;
  const settings = scanSettings();
  const problem = settingsProblem(settings);
  if (problem) {
    logLine("error", problem);
    return;
  }

  $("logbox").innerHTML = "";
  state.since = 0;
  setRunning(true);
  updateProgress();

  const { everyone, ...body } = settings;

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
  initPageSize();
  loadConfig().then(loadSavedMatches).then(loadBoard).then(settleScroll).then(loadStatuses)
    .then(resumeLocalScan)
    // a startup step that fails must not take the meter, and the bypass
    // toggle that lives in it, down with it
    .catch((e) => console.warn("a startup step failed:", e))
    .then(settleScroll)                 // a startup step that failed still settles it
    .then(loadAllowance);
  initRail();
  initWakeChecks();

  $("home-btn").addEventListener("click", goHome);
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
      scanSetupChanged();
    });
  });
  wireScanSetup();
  wireBypass();

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
