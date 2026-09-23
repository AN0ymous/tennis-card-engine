# Tennis Card Engine: project notes

Scans eBay for "bookend" tennis cards (serial #1 or the last of a print run
under 500) from licensed makers, records them in a spreadsheet, and shows them
on a website.

## About the owner

- Justin, on Windows 11. Uses Command Prompt (not PowerShell) and `py` (not `python3`).
- Project folder: `%USERPROFILE%\Downloads\tennis-main`
- Prefers copy-paste commands, one per step, with plain explanations.

## How it runs

- **GitHub (main setup):** public repo `tennis-card-engine`, branch `main`.
  - `.github/workflows/scan.yml` runs the engine once a day at `30 9 * * *`
    (09:30 UTC, 5:30pm Singapore time), timeout 60 minutes, then
    `export_static.py results` and commits `results/`.
    **The hour is chosen against the call budget, not the clock at home.**
    The local counter runs on Pacific days and starts again at Pacific
    midnight -- 07:00 UTC in summer, 08:00 UTC in winter -- so 09:30 clears
    the reset whichever way the clocks have gone. It used to be 23:17 UTC,
    which is 16:17 Pacific: the tail of the counter's own day, so the one run
    that has to be comprehensive was left whatever the day's hand-started
    scans had not spent. On 19 Sep it was left nothing -- run 66 checked 0
    listings and failed on its first call, with 450 calls still free on
    eBay's side. Any morning-Singapore slot is the tail of the Pacific day by
    construction, so waking up to fresh results costs a run that can fail for
    want of allowance. `TheDailyRunStartsOnAFullAllowance` in
    `test_engine.py` fails if the hour drifts back.
  - `.github/workflows/pages.yml` publishes `web/` plus `results/` to GitHub Pages.
  - Secrets set: `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` (Production keyset),
    and possibly `ANTHROPIC_API_KEY` (optional photo checks).
  - Actions versions already updated to Node 24: checkout@v5, setup-python@v6,
    upload-artifact@v6.
- **PC:** `.env` holds the same eBay keys. `py server.py` serves the app at
  http://127.0.0.1:8765. `py check.py` checks setup. `py ebay_usage.py` shows
  eBay API usage (falls back to all APIs when eBay replies 204).

## Decisions (do not undo)

- **1/0 serials are deliberate.** Cards listed as "-1/0" are real; the minus
  sign can't be shown, so they are kept as 1/0 and treated as bookends. Do not
  add validation that rejects print run 0 or card number > print run.
- **The scheduled scan covers everything; a scan you start yourself obeys the
  settings.** The daily GitHub run sends no settings at all, so it still covers
  all players, all 7 sets, all card types, graded and raw, both listing types,
  any price -- that is what keeps the spreadsheet comprehensive. Pressing "Run
  a scan" sends whatever the scan setup is set to, on the hosted site exactly
  as on the PC, and the same setup narrows "Everything found so far" the
  moment it changes, with no scan needed. Until 17 Sep the hosted button sent nothing but `{"ref":
  "main"}` and `scan.yml` declared no inputs, so every phone-started scan
  quietly ran the defaults however the controls were set.
- eBay's account deletion notifications: opted out ("not persisting eBay data").
- **The wide search says "tennis" for lines printed for every sport and never
  says "card".** The wide query used to be `"{set} tennis card"`. eBay matches
  every word, and most listings do not say "card": on 17 Sep two player scans
  found 17 bookends the wide scan had never seen in any of its 4,800-listing
  walks, and 15 of them lacked that word. `wide_query()` is now `"{set}
  tennis"` for Topps Chrome, Topps Now and Panini Instant, and the bare set
  name for NetPro, Ace Authentic, Topps Graphite and Topps Royalty -- Royalty
  is mostly tennis, its tennis titles rarely say so (4 of the 5 Royalty
  bookends found that day did not), and its UFC line names its sport in the
  title, which `settled_by_title` turns away for no call. A Topps Chrome title
  that says neither "tennis" nor a player's name stays out of the wide scan's
  reach; that line is printed for every sport, and bare "Topps Chrome" would
  be tens of thousands of listings of which eBay hands over the newest 1,200. The query
  text is part of the cursor key, so changing it walks every set in full once.
  **What the wide scan still cannot do:** eBay returns the newest 1,200 per
  query, so the wide scan covers the newest 1,200 listings of each set and
  everything listed after that; older listings surface only through a player
  scan, whose result set is small enough to reach the back of.
- **A player is searched by surname; by full name only past the cap; never
  by first name alone.** eBay matches every word, so "Shapovalov Topps Chrome"
  returns everything "Denis Shapovalov Topps Chrome" would and more -- the
  full-name search is only worth a call when the surname search hit eBay's
  1,200 cap and listings past it may still be reachable by the narrower
  words. "Denis Topps Chrome" brought every Denis in every sport: 1,083 of
  the 2,077 listings the 17 Sep Shapovalov scan paid for were not his. The judge no
  longer accepts a first name alone either -- that is how "2025 Topps Royalty
  UFC Benoit Saint Denis" was recorded as Denis Shapovalov. A card titled with
  a first name only is not attributed in a player scan; the wide scan still
  records it under whatever player the specifics name.
- **Topps Royalty is not only tennis** (2025 Topps Royalty UFC exists), so it
  is out of `TENNIS_ONLY_SETS`, and a title that names another sport
  (`OTHER_SPORT_WORDS`: UFC, MMA, baseball, football and so on, as whole
  words, never when the title also says tennis) is as good as a Sport specific
  that does. `build_board` drops such a row as it drops customs; the UFC
  "Shapovalov" row stays in the spreadsheet, so delete it there by hand.
- **A maker the specifics leave blank, or spell as the line, is read off the
  title.** `resolve_manufacturer`: "Topps Chrome" in the Manufacturer field is
  Topps; a blank field on "2024 Topps Chrome Tennis Denis Shapovalov ... 77/77"
  is Topps. Before this both were "manufacturer not in allow-list", which is
  the likeliest reason that exact card was passed over on 17 Sep. A maker the
  allow-list does not know is still turned away by name.
- **A card of another sport is turned away; a card that says nothing is kept
  and marked.** The scan searches category 212, which is every sport, and with
  `SCAN_ALL_PLAYERS` True nothing else checks what sport a card is. Measured
  against the 56 matches recorded by 17 Sep: 21 said "tennis" nowhere in their
  title, maker or set, and every one was a real tennis card, so a blanket
  `is_tennis_listing` requirement would have been expensive and wrong. Instead:
  `TENNIS_ONLY_SETS` (Topps Graphite, Topps Royalty) joins `TENNIS_ONLY_MAKERS`
  (NetPro, Ace Authentic), since those lines are only ever tennis -- that alone
  took the unconfirmed 21 down to 5. For what is left (Topps Chrome, Topps Now,
  Panini Instant, which every sport is printed in), a listing whose `Sport`
  specific names another sport is rejected outright, and a listing that states
  nothing is kept with a "check by eye" caution. A wrong card is one glance to
  dismiss; a missed one is gone for good. **A Sport field that ticks several
  sports says nothing** (`sport_settled`, 21 Sep): "Auto Racing, Baseball,
  Basketball, Soccer, Tennis, Volleyball, Wrestling" sat on five recorded
  rows, four of them tennis and one a Colorado Rockies card (Yanquiel
  Fernandez, 20 Sep) that the word Tennis in that string let through with no
  caution. Now only a field naming one sport settles anything: one that is
  not tennis rejects, several sports fall back to the title and the
  tennis-only lines, and a card nothing vouches for is kept with "listing
  ticks 7 sports, which settles nothing; check by eye". One sport said twice
  ("Tennis, Tennis (网球)") is one sport. `sport_named` still records the
  field as the seller wrote it. `sport_caution` is shared by the judge and
  `build_board`, so the nine rows recorded on 16-17 Sep before the rule
  existed, and the Rockies row, carry on the page the caution a scan would
  give them today; a caution never drops a card.
- **A card's player is read off the title when eBay's field is blank.**
  12 of 75 recorded cards read "Unknown player" with the name in plain sight
  ("VINCE SPADEA \"SILVER BASE CARD 100 /100\" ACE SIGNATURE SERIES 2005").
  `get_player` now tries eBay's specifics (including `Signed By` and
  `Autographed By`, the keys an autograph listing carries when Player/Athlete
  is empty), then a known name whose every word appears whole in the title,
  then `player_from_title`: a run of two or three name-like words once maker,
  set, colour, grade, insert and card vocabulary (`PLAYER_NOISE`) is stripped.
  A comma or slash between names is a wall, a run of dashes is a separator, a
  single hyphen stays inside a word (Saint-Denis), and a card word hyphenated
  onto a name keeps the name. A run of four or more is not trusted -- two
  players with nothing between them -- and yields "", never a guess. Measured
  on the 63 rows that had a recorded player: 62 agree, 1 deliberately blank,
  0 wrong. `get_player` returns "" rather than "Unknown player"; the page shows
  "Player not named" in muted type (`.is-unnamed`) and treats the literal
  "Unknown player" as blank too, since `results/board.json` carries the old
  string until the next scan rewrites it. `build_board` fills a blank from the
  title using every name already on the sheet as the known list.
  **Two things stood in front of a name and hid it.** An insert or parallel
  name the noise list did not know ("PURPLE GEOMETRIC CAPTURED MARTA
  KOSTYUK", "Carlos Alcaraz Full Extension") makes the run four words long,
  which is not trusted, so the name in plain sight was thrown away -- run 52
  recorded the Kostyuk card with no player at all. `geometric captured
  pineapple youthquake aces bookend full extension superior` are named now;
  a word that rides on cards of several different players is card vocabulary,
  not a name, which is how to spot the next one. Separately, the rule that
  turns away RC, SSP and USA turned away **BEN, ZOE and IGA**: two or three
  letters in capitals. An all-capitals title left a run of one and yielded
  nothing, which is why "AUTOS BLACK REFRACTOR ZOE KRUGER" had no player.
  Such a word is now read as the first name it is **only where a first name
  stands** -- immediately before a plain name of four letters or more -- and
  never when it is one of `SHORT_CODES` (the card, competition and country
  codes that really do sit there: USA, UFC, GBR, ATP, RC, SSP). Measured
  against the 102 recorded rows: 6 titles the reader could not place became
  2, and both of those are cards with two players on them, where naming
  neither is right.
  **Dots put a first name outside the pattern altogether.** "2024 Topps
  Chrome Tennis J.J. Wolf 1st Gold Refractor 50/50" left a run of one and so
  no player at all -- the one blank on a 252-card board, and enough to fail
  the suite against live data on 19 Sep. `INITIALS_RE` reads dotted initials
  as the first name they are, on exactly the BEN/ZOE terms: only immediately
  before a plain name, never as a run of their own, **two initials only**
  (three is nearly always a competition -- A.T.P., I.T.F., U.S.A.), and never
  when the letters are one of `SHORT_CODES`, which is what keeps R.C. out.
  `_as_written` puts back the trailing dot the tokeniser peels, so it reads
  "J.J. Wolf". **A blank player is a decision, not a gap**, so the board test
  no longer demands one; what it does forbid is the old literal "Unknown
  player", which the page would print as a name.
  **A field eBay shouts back is written the way the page reads** (`as_typed`,
  20 Sep). eBay hands back Player and Sport however the seller typed them, so
  "HOLGER RUNE" sat among the Holger Runes and "TENNIS" among the Tennises --
  13 player names and 3 sports on a 266-card board. Only a value that is all
  capitals or all lower case is touched; anything mixed is somebody's own
  spelling ("John McEnroe", "Tennis, Tennis (\u7f51\u7403)", the seven-sport
  strings) and is left exactly as it came. Each run of letters is capitalised
  on its own, so J.J. Wolf, O'Brien and Saint-Denis all survive; a shouted
  MCENROE comes back Mcenroe, which is the price of the rule. It runs in
  `get_player` and on the match record, and again in `build_board`, so rows
  recorded before the rule read right with no scan needed. **Cosmetic only:**
  the page's player filter lower-cases both sides before comparing (`words()`
  in `app.js`), so nothing it decides changes. Deliberately **not** applied in
  `sport_named`, because the reject reason quotes the sport as eBay wrote it
  and those reasons live in `seen_items.json`, where a changed spelling would
  read as a new reason.
- **A card grade over an autograph grade is not a serial.** "PSA 9/9",
  "Psa MINT 9/9" and "BGS 9.5/10" read exactly like N/M, and three PSA 9
  autos were recorded as the last of a run of nine. `is_grade_pair` steps
  over an N/M when both numbers are 10 or under and the word immediately
  before it is grade context (a grader's name, or MINT/GEM/MT/NM/GRADE/
  GRADED); `extract_serial` then reads on for a real serial behind it, so
  "BGS 9.5/10 ... 1/25" still yields 1/25. Only the word immediately before
  counts: in "PSA 10 1/10" that word is "10", so the 1/10 is the serial it is
  (the Seles card). "auto" is deliberately not grade context -- "Rookie Auto
  5/5" is a real bookend. `SERIAL_RE` also refuses a numerator that is the
  tail of a decimal, so 9.5/10 can no longer read as 5/10. `build_board`
  applies the same rule, so the three recorded 9/9s drop off the page while
  their rows stay -- the same treatment as customs. **Two more signs since
  18 Sep**, after "Auto/250 Rare 10/10" at $25,000 was kept as the last of
  ten because "Rare" is nobody's grader: a pair both 10 or under is a grade
  when the title states a larger print run on its own elsewhere
  (`PRINT_RUN_ELSEWHERE_RE`: a "/N" with no digit or slash in front, so a
  Topps Now date like 9/7/2025 states nothing, and the same run twice, as in
  the Andreeva "01/10 ... /10", is not a sign), or when eBay's `Grade`
  specific is exactly the first number. Measured against the 90 recorded
  rows the print-run sign changes two, that card and the Gauff "/199 ... Psa
  MINT 9/9", both genuine grade pairs. The `Grade` sign needs the specifics,
  so `settled_by_title` and `build_board` apply the other two only.
- **"Signature Series" is a set name, not an autograph.** 2005 Ace Authentic
  Signature Series had plain base cards, and 'VINCE SPADEA "SILVER BASE CARD
  100 /100" ACE SIGNATURE SERIES 2005' was recorded as an Auto with the Ace
  "on-card or sticker not stated" caution on the strength of the word.
  `SET_NAME_PHRASES` are blanked from the text (`without_set_names`) before
  `classify_card` and `ace_authentic_check` look for autograph words; an
  "Auto" beside the set name still counts, and so does eBay's `Autographed:
  Yes`. `build_board` relabels a recorded Auto whose title names such a set
  and reads as base without it, and swaps the caution for the unsigned-card
  one, so the Massu row now says Base. (The Spadea row has since left the
  page for the reason below: its "100 /100" is a card number.)
- **A card number beside the set's size is not a serial.** The sign is a
  gap before the slash -- the seller wrote two things -- behind a word that
  says the first thing is the card's number (`is_card_number_pair`). Behind
  a "#" glued to the number: the Alcaraz Aqua Refractor "#1 /199", recorded
  as 1/199, was card #1 of the set, one of 199, stamped 148/199 in its
  photo; of the twelve recorded titles with a "#" in front of the pair only
  that one had the gap, and "#1/199", "# 1/10", "#001/100" and "S#01/10"
  stay serials. Behind a **lettered card code** (`CARD_CODE_BEFORE_RE`):
  "Court Kings Roger Federer #CK-1 /500" is card CK-1, one of 500 -- the
  "#" test reads the character in front of the digit, and on a code that
  character is the code's own hyphen, which is how that row was recorded as
  a 001-of-500 bookend. Behind **"Pop"** (`POP_BEFORE_RE`): "YOUNGEST Pop 1
  /10 Black PSA 10" is the grader's population report beside the print run,
  and neither number says which of the ten is in the slab. Behind "base
  card" (`BASE_CARD_BEFORE_RE`): 'SILVER BASE CARD 100 /100' is card 100 of
  a 100-card set. **The words alone are no
  sign, and neither is the gap alone.** On this site "base" means no
  autograph and no patch, so a numbered insert or parallel is still base:
  'SILVER BASE CARD #001/100' has no gap and is the stamped serial it looks
  like, and stays on the page (the owner's call on both rows, 18 Sep). The
  recorded "BEN SHELTON RC 1 /5 PSA 10" has the gap and no such word and is
  a real 1/5. `extract_serial` steps over a card-number pair to the
  specifics; with nothing there, `judge_listing` reads the stamp from the
  photo when the photo step is on (only for these shapes of title, so the
  call is rare), records what it read with a "read from the photo" caution,
  and without it rejects for no serial. `build_board` drops the recorded
  rows from the page (`card_number_pairs_in`); the rows stay.
- **The title's serial is not the last word.** The same card showed the
  wider gap: `extract_serial` believed the title and
  compared it with nothing. Now `judge_listing`, once a listing has passed
  every other rule, reads the specifics' own N/M (`specifics_serial`) and,
  when it disagrees, adds a "title says 1/199 but eBay's details say
  148/199; check by eye" caution; and with `ANTHROPIC_API_KEY` set it asks
  one photo question (`serial_photo_reading`, `SERIAL_PROMPT`, cached by the
  first photo like the other readings). A legible stamp that is not a
  bookend is a reject naming both readings; a legible stamp that is a
  different bookend is kept with both readings on the page; an unreadable
  stamp changes nothing. It runs after the price and type filters, so a
  filtered listing never pays for it, and it is an Anthropic call, never an
  eBay one. Without the key only the specifics caution is possible, and a
  card already recorded is never re-judged.
- **A title that argues with itself is not believed on the serial.**
  '2025 Topps Chrome Coco Gauff Purple Geometric Refractor # /10 1/1 on
  eBay' at $285 was recorded as a True 1/1, and so was a second Gauff at
  $315. "1/1 on eBay" is the seller's boast -- the only one listed there --
  and the card is one of ten, its photo stamped 09/10. A run of ten has no
  true 1/1 in it. `is_contradicted_pair` turns away a 1/1 when the same
  title states a print run of two or more on its own
  (`PRINT_RUN_ELSEWHERE_RE`, the sign the grade rule already uses), and
  `contradicted_pairs_in` drops the recorded rows from the page while the
  rows stay. **Deliberately narrow, because completeness comes first:** only
  a 1/1 is doubted, since a genuine 1/1 has no print run to state; two
  ordinary numbers that disagree ("/250 ... 1/25") are left alone, because a
  parallel really can be a shorter run than the base; and the same run
  stated twice is no contradiction, which keeps the Andreeva "01/10 ... /10"
  a serial. **A run of five states itself in one digit** (`PRINT_RUN_ANY_RE`,
  19 Sep): "2024 Topps Chrome Tennis Rookie /5 Mirra Andreeva eBay 1/1" at
  $6,000 was recorded as a true 1/1 because the sign this rule shared with
  the grade rule asks for two digits, and a run of five has no true 1/1 in it
  either. The grade rule keeps the two-digit sign, since it wants a run over
  ten and one digit can never be that. Measured against the 252 cards on the
  board that day, widening it changes exactly that one.
  Like a grade pair or a card number it is **stepped over, not
  rejected outright** -- the specifics, and the photo where the photo step is
  on, get their say before the listing is turned away -- so `settled_by_title`
  no longer settles such a title for free and it costs one call. Measured
  against the 182 recorded rows it changes exactly the two Gauff boasts.
- **A date is not a serial** (PR #51, 23 Sep). Topps Now titles carry the
  event's date, and "2024 Topps Now Novak Djokovic 2026 Australian Open
  Oldest Finalist 1/30/26" was recorded as a 1/30 bookend -- it is 30
  January. `SERIAL_RE` now refuses a pair with a slash on either side of it
  (`(?<![./])` and `(?!/\d)`), so the whole of 8/3/26 or 9/7/2025 reads as
  nothing, while "Superfractor. 1/1", "Serial No. 1/25" and "Refractor / 1/1"
  -- a dot or slash with a space before the number -- still read. That fixes
  every future listing; **the recorded row needed a board rule of its own**,
  since `build_board` reads the recorded `Serial #` rather than the title.
  `date_pairs_in` names both pairs a date could have been read as ("1/30" and
  "30/26" of 1/30/26) and drops a recorded row whose serial is one of them,
  like the grade, card-number and contradicted rules; the row stays in the
  spreadsheet. **Less any pair the title states outside the date**, which is
  where completeness is kept: "Topps Now 1/1/2025 Superfractor 1/1" reads 1/1
  from its date *and* carries a real 1/1, and the real one keeps the card on
  the page. Measured against the 335 recorded rows: one title holds a date,
  and that is the Djokovic card. Judge version 3 came with #51 and
  reconsiders maker, set and not-a-bookend rejects once; counted against the
  23 Sep `seen_items.json` that is 4,187 rejects, 3,460 of them free from
  the title and 727 up to a call each, plus at most 475 date-shaped
  not-a-bookend pairs that may now need a fetch -- the first scan after it
  walks all seven sets in full and costs roughly 1,500 calls at worst.
- **Custom cards are rejected before the serial is read.** A card somebody
  made themselves carries a real maker in the Manufacturer field and is nearly
  always called a 1/1, because only one exists, so neither the allow-list nor
  the bookend rule stops it. `CUSTOM_CARD_WORDS` does, matched as whole words
  against the title and the Set field only -- never the other item specifics,
  since eBay puts "Custom Bundle: No" on a great many ordinary listings.
  "sketch" is in the list by the owner's decision, knowing it also turns away
  licensed artist sketch cards: a hand-drawn custom and a real sketch card
  read the same in a listing title. `build_board` applies the same rule, so a
  custom recorded before the rule existed drops off the page while its row
  stays in the spreadsheet.

## Changes made to web/assets/app.js

1. **One-tap scans:** on the hosted site, a GitHub fine-grained token
   (Actions: read and write, this repo only) can be pasted per device and is kept
   in localStorage (`tce.githubToken`), never in the repo. With a token, "Run a
   scan" calls the workflow_dispatch API and the page reloads when
   `results/config.json` shows a new `lastRun`. Without one, the button opens
   the GitHub Actions page. Hosted note text says "on a schedule", not a fixed
   interval.
2. **Scans survive leaving the tab.** No scan runs in the page: the local one
   runs in `server.py`'s thread, the hosted one on GitHub. What used to stop
   was the page *noticing*, since a hidden tab has its timers throttled and a
   phone may drop the tab entirely. The hosted watch is kept in localStorage
   (`tce.watching`, with a deadline and the run id) and picked up on load, and
   `visibilitychange` / `focus` / `pageshow` re-check straight away instead of
   waiting for the next tick. On a local page, `resumeLocalScan()` replays the
   server's whole event log from seq 0, so a scan started before a reload comes
   back with its log and progress intact. A watch whose results already landed
   is dropped rather than acted on, so it can never cause a reload loop.
3. **Progress while a scan runs.** A local scan drives the bar from the
   engine's own events. A hosted scan has no event stream, so the bar follows
   the GitHub run instead: `readRunProgress()` finds the run this dispatch
   created (by time, not "the latest run", which could be the daily one) and
   reads `/actions/runs/{id}/jobs` for the step, shown in plain words
   ("Searching eBay"), with the step count and the clock. When that cannot be
   read -- no key, rate-limited, run not yet created -- it falls back to
   `HOSTED_PHASES`, which is honest about being an estimate. The bar is
   visible in hosted mode whether or not a scan is running; when idle it says
   when the last scan finished. The activity log stays hidden there, since
   GitHub sends no per-listing events.
4. **The Matches panel is two sections, because there are two questions.**
   "New this scan" is what the last scan added, and it is empty most of the
   time: once a listing is judged it is never a new find again, so a scan
   minutes after the last one correctly adds nothing. "Everything found so
   far" is the whole record, filtered, and it responds the moment a filter
   changes with no scan needed. Until 17 Sep the panel showed only new finds,
   which made every scan read as a failure and made the filters look broken --
   no filter setting can put an already-recorded card in a list of new ones.
   The empty state now says why nothing is new and that the filters are not
   the reason. Example cards show only when nothing has ever been found.
   `BOARD_LIMIT` (500) is what the lower section can show; the board was
   capped at 24 while the spreadsheet held 54, so most of the record never
   reached the page. At roughly 780 bytes a card that ceiling is about 390KB
   -- revisit if the spreadsheet approaches it.
5. **The scan setup is the filter, everywhere, and it survives a reload.**
   Players, sets and the print-run ceiling (with "inclusive") go to the engine
   to decide what is recorded -- and until 17 Sep that was all they did:
   "Everything found so far" ignored them, so a scan narrowed to one set and
   one player still showed the whole record, while card type, condition,
   price and listing type (whose state the page already shared with the
   display filters) narrowed it. Reproduced with real clicks: Topps Chrome
   only, one player, ceiling 100 -- 54 shown. Now `inPlayers`, `inBrands` and
   `underCeiling` sit in `passesFilters` beside the others. A card's set comes
   from `brand` on each board card, which `build_board` works out with
   `brand_of` from the same evidence the brand gate accepted it on (the Set
   field, else the qualified title), so the two cannot disagree; a card it
   cannot place is shown, never hidden, like every other unknown on the page.
   Players match when one whole name contains the other ("DANIIL MEDVEDEV",
   "Erika Andreeva, Mirra Andreeva"), which keeps Serena and Venus apart.
   Separately, `loadConfig` used to put every scan-setup control back to the
   engine default on each load, and a finished hosted scan reloads the page
   -- so the setup you had just scanned with was gone by the time its results
   appeared. The whole setup is now kept in localStorage (`tce.scanSetup`) and
   restored after the defaults go in. Nothing saved means exactly the old
   behaviour.
6. **`app.js` and `styles.css` are stamped with the commit on publish.**
   `pages.yml` rewrites the two asset links to `?v=<sha>`, because a browser
   keeps `app.js` for a while and a merge could leave a phone running the old
   page against new results: run 29 on 17 Sep dispatched a scan with no
   settings at all for exactly that reason and looked like a failed fix.
7. **A name you typed in can be taken out again.** An added chip (a player or
   set from the search box, class `is-added`, remembered in `tce.addedPlayers`
   / `tce.addedSets`) carries a small cross that removes it and drops it from
   the remembered list; the built-in 15 players and 7 sets do not. The cross
   sits inside the chip's label, so its click is stopped from also toggling
   the chip. Taking away the last ticked player turns "every player" back on,
   since nothing ticked would mean nothing to scan and nothing to show.
8. **eBay calls today.** A small meter under the progress bar, from eBay's own
   Developer Analytics figures. The eBay keys never reach the browser: the
   scheduled run writes `results/usage.json` after each scan, and `py
   server.py` answers `/api/usage` from its `.env` (cached 2 minutes, since
   reading it costs a call). Hosted, it appears only once a one-tap key is set
   for the device, and disappears when the key is removed. **That gate is what
   is drawn, not a secret kept** -- the repo is public, so `results/usage.json`
   is published with the page and anyone who looks can read it. It holds
   counts only, never a key. Locally it is always shown.

9. **A "New" or "Sold" flag beside the star.** Cards the latest scan turned up carry a
   green NEW pill next to the star, on the board rail and on match cards. It is
   derived from `state.matches` (that is `new_matches.json` hosted, the live
   event stream locally) at render time, so nothing extra is stored. Cards
   found earlier never carry it, and an empty `new_matches.json` means no flags
   at all. **A flag comes off an hour after the scan recorded the card**
   (`NEW_FOR_MS`), measured from the "Date Found" the scan wrote, so every
   device agrees without remembering anything per browser. It goes by itself:
   `scheduleNewFlagSweep()` sets one timer for the next card due, and
   `initWakeChecks` sweeps on the way back to a tab whose timers were throttled
   while hidden. A card whose date will not parse keeps its flag until the next
   scan replaces the list. **A sold card shows a red SOLD flag instead**, since
   sold outranks new -- one flag rides in that corner, never two. Sold comes
   from `statusOf()`, so the owner's own mark wins over eBay's reading, the
   same as on the saved page. `loadStatuses()` reads `results/status.json` once
   at startup so the board knows before the saved page is ever opened; it is
   **hosted only on purpose**, because the local path asks eBay per listing and
   those calls come out of the same daily allowance a scan spends. Locally the
   board uses whatever the saved page last checked, kept in this browser. The
   saved page keeps its SOLD band across the photo and suppresses the pill, so
   a card there is not marked twice. `.mc-serial-tag` moved from `right: 10px`
   to `44px` while doing this: the star sits at `right: 8px` and is 30 wide, so
   at 10px it covered the serial and clipped it. With a flag present the serial
   steps left again (`.mc-photo.has-flag`).
   **That class must follow the flag that is drawn, not one that exists.** The
   saved page hid the SOLD pill in the stylesheet while drawing its band, but
   `buildCard` still added `has-flag`, so on every sold saved card the serial
   stepped 96px clear of a pill that was not there and floated in the middle
   of the photo. The saved page now asks `buildCard` for no pill
   (`{ soldFlag: false }`) rather than hiding one after the fact, the class
   goes on beside the flag it describes, and no stylesheet rule hides a flag
   at all -- `TheSerialTagStepsAsideOnlyForAFlagThatIsDrawn` fails if one
   appears again. Only the SOLD pill is suppressed there: a saved card the
   last scan found still earns its NEW pill, and steps aside for it.

10. **The Matches panel is paged, and a reload lands somewhere predictable.**
   173 cards drawn at once put a mile of scrolling between the panel and
   everything under it -- the scan controls on a phone, the activity log, the
   reference, the footer -- and made a phone lay out hundreds of cards nobody
   had asked to see. Each section of the panel now draws one page:
   `PAGE_SIZES` (12, 24, 48, 96, and 0 meaning All) with `PAGE_DEFAULT` 24,
   picked from a dropdown **in the Matches panel head, not the scan setup**,
   and remembered per device (`tce.pageSize`). Both sections page from the
   same size, each with its own counter in `state.page` and its own Previous
   / Next bar, shown only when there is more than one page; turning a page
   puts you at the top of that section. `pageOf` clamps a page into range, so
   a list that shrank under you lands on its last page and never on an empty
   one, and `filterSignature()` -- every filter that changes what is shown --
   starts again at page one when it changes, because landing on page 5 of a
   list you have just narrowed to two cards is not what filtering means. The
   counts beside the headings and in the pill are still the whole record, not
   the page. **One trap worth remembering:** `Number(null)` is 0 and 0 is a
   size in that list, so reading the remembered value carelessly made a first
   visit show every card -- `initPageSize` checks for `null` before
   converting.
   **Where a reload leaves you.** The browser restores a reloaded page to its
   old scroll offset, measured against a height this page does not have until
   the board, the matches and their photos arrive -- so it landed somewhere
   different every time, and a finished hosted scan reloads the page on every
   run. `history.scrollRestoration` is now `"manual"` and `settleScroll()`
   (once, after `loadBoard`) decides: a `#section` in the URL wins, then a
   marker a finished scan left (`tce.landOn` in sessionStorage, written by
   `checkForNewResults` **before** it reloads) puts you on the Matches panel,
   which is why it reloaded; otherwise the top. The marker is spent by the
   load it was written for, so the next reload goes to the top again.

11. **"How it works" is a page, not the bottom of the main one.** The method
   and process reference -- six parts of prose, about a third of the page's
   height -- sat under every card on the board, where nobody scrolled to it
   and everybody scrolled past it. It is now a page of its own, reached from
   a **"How it works" button in the top bar** (`#method-link`, marked `is-on`
   while you are on it) and left by "Back to the board", on exactly the
   routing the saved cards already used. `VIEWS` maps `#saved` and `#method`
   to their section and body class, and one `showView(hash)` drives both, so
   they can never both be open. Measured in Chromium: the main page went from
   12,653px tall to 8,427px.
   **Two traps, both met.** `.reference` sets `display: flex`, which beats the
   browser's own rule for the `hidden` attribute -- so the page never actually
   went away until `#method[hidden] { display: none; }` said so. And `#limits`,
   `#allowlist` and the rest were anchors on this page; `partOfAView()` finds
   a hash that names an element inside a page, opens that page and goes to it,
   so every link into the reference still works.

12. **The contents panel is the map of the site, so it is kept in step by
   tests, not by memory.** It still called the reference "Method and process,
   a section of this page" a day after it became a page of its own, and it
   offered the activity log on the hosted site, where there is no activity
   log and the link went nowhere at all. A map goes stale silently, so
   `TheContentsPanelKeepsUp` in `test_engine.py` fails when it does: every
   entry must lead to an id on the page or a hash `VIEWS` routes, **every
   page in `VIEWS` must have an entry**, the reference entry must be called
   whatever the top-bar button calls it, and the numbers must be a CSS
   counter rather than typed in, so an entry that does not apply leaves no
   gap in the sequence. The activity entry carries `data-local-only` and the
   same `.is-hosted` rule that hides the panel hides it. The foot line is
   written by `loadConfig`, because "Runs on your machine" is false on the
   hosted page.
   **When you change the UI, change the panel in the same commit.** The list
   is grouped: "On this page" (board, scan setup, matches, and the activity
   log locally) and "Other pages" (saved cards, how it works).
   **No entry is marked as where you are** (the old `is-here`, driven by an
   IntersectionObserver). On a page of its own the section it watches is the
   only thing on screen, so "How it works" stayed lit for good once you opened
   it. The marking is gone rather than patched, at the owner's call.
   **The veil dims the page rather than covering it:** it was an opaque
   `color-mix` of the ground colour with black, which made the panel read as a
   different page instead of a layer over this one. It is now a translucent
   tint with a small `backdrop-filter` blur, so the page shows through in both
   themes.

14. **Nothing asks eBay before it knows whether there is a server to ask.**
   `HOSTED` starts false and only turns true once `loadConfig` has failed to
   reach one -- but the saved page is routed off the hash the moment the
   script runs, well before that. So opening the hosted site straight at
   `#saved` (or reloading while on it, which the reload rule now makes
   likely) had `refreshStatuses` ask `api/status` on GitHub Pages, which
   answered with its own 404 **HTML** page, and the reader got "Couldn't
   check eBay (Unexpected token '<', "<!DOCTYPE "... is not valid JSON)"
   over a page whose statuses were sitting in `results/status.json` all
   along. `modeSettled` says whether `HOSTED` can be believed yet;
   `refreshStatuses` waits rather than guessing, and `settleMode()` -- called
   from **every** way out of `loadConfig`, including the unreachable-server
   one -- calls it back when the answer is in. `readJson()` also turns a
   response that is not JSON into "the server answered 502" rather than a
   parser error. `TheSavedPageAsksTheRightSide` in `test_engine.py` pins all
   of it, including that the guard sits before the branch and that waiting is
   not worded as a failure.

15. **The title in the top bar is the way home.** Two pages and a long main
   one, and nothing said how to get back except the browser. `#home-btn`
   (`goHome()`) puts the main page back and goes to the top, whichever page
   you are on. It takes the hash **off** the URL with `history.pushState`
   rather than setting one, which fires no `hashchange` -- so the view is set
   by hand -- and leaves the back button returning you to the page you left.

## Why a scan usually adds nothing, and why that is right

`seen_items.json` held 5,635 judged listings on 17 Sep -- 5,577 permanent
rejects, 56 matches, 2 filtered -- against roughly 4,840 listings live across
the seven sets. So nearly everything on eBay has already been judged, and a
scan started now correctly adds nothing: a card already recorded is reported
as `known`, not as a new match. The engine is working when it says "Added 0
new qualifying listing(s)". Judge a scan by the "Checked N listings" line and
by the lower section of the Matches panel, not by whether anything was new.

## Limitations

These are things the engine cannot fix from a listing alone. They are not
bugs to be chased; a rule that caught them would cost real cards.

- **A title that states the wrong serial, with nothing to contradict it.**
  "Dominik Koepfer RC | #01/77 Pineapple Refractor -- 2024 Topps Chrome #94"
  at $7.95 is on the page as 001 of 77; the photo shows **21/77**. The title
  has no gap before the slash, no card code, no second print run, and eBay's
  specifics said nothing -- so every reading the engine has agrees with the
  seller, and the only witness is the photograph. Nothing in the text can be
  tightened without turning away the many honest "#01/77" titles that are
  exactly what they say. **The remedy exists but is not on:** with
  `ANTHROPIC_API_KEY` set, `serial_photo_reading` reads the stamp, and
  widening its trigger from the two odd title shapes to every listing that
  has otherwise passed would catch this -- at one Anthropic call per match
  (not an eBay call, so it spends no eBay allowance). Judge that by the
  number of matches a scan makes, not by the number it checks.
- **A card is only as findable as its title.** eBay matches whole words and
  serves the newest 1,200 listings per query, so a card whose title names
  neither its set nor its player in the words a search uses is out of reach
  of the wide scan, and an older listing surfaces only through a player scan.
  See the wide-search decision above.

## What a scan costs, measured

Read `results/ebay_api_usage.json` before and after a run for the exact
figure; the "eBay calls today" panel is eBay's own and lags. Runs 60 and 61
on 18 Sep decompose exactly (counter deltas 432 and 307):

| | run 60 (1 h 44 m gap) | run 61 (1 h 07 m gap) |
|---|---|---|
| listings checked | 275 | 159 |
| search pages | 7 | 7 |
| detail calls | 245 | 119 |
| status calls | 180 | 181 |
| **total** | **432** | **307** |

- **Cost follows the gap since the last scan, not the number of scans.** Both
  walked one page per set (7 calls) and paid only for what was listed since.
  Listings arrive at **2.4 to 2.6 a minute -- about 150 an hour** across the
  seven queries, measured twice.
- **The status refresh is the biggest fixed cost of a repeat scan** -- one
  call per recorded card, so it grows with the spreadsheet. It is skipped
  entirely when the last scan was under `STATUS_FRESH_SECONDS` (an hour) ago,
  which makes a scan within the hour the cheapest way to test anything.
- **Measured after the no-sign rule and the status savings.** Run 62 (43 min
  gap) cost 20 calls; run 63 (two new players, 10 new cursors, 1,196 listings
  checked) cost 375 -- 170 detail, 189 status, 16 search. The no-sign rule
  saved 583 detail calls on run 63 alone. With statuses now refreshed once a
  day rather than once an hour, a repeat scan costs its search pages plus a
  detail call for each never-seen listing that hints at numbering, and
  nothing else:

  | when you scan | search | detail | status | total |
  |---|---|---|---|---|
  | within 20 h of the last | 7-16 | 10-170 | **0** | **~20-190** |
  | the daily scheduled run | 70-115 | 230-370 | ~182 | **~480-670** |

  A first-time player scan is the expensive shape: each new name is a new
  search text per set, so the walk goes to the back of every one of them
  rather than stopping at a mark. Run 63 checked 1,196 listings for it. A
  repeat of the same players costs a page per set.

## Open items

- **Answered 19 Sep: the "check by eye" caution has to stay a caution.**
  Measured on the 252-card board, **198 state a sport and 54 (21%) leave it
  blank**. A blanket rejection of a listing that says nothing would drop a
  fifth of the record. Two things worth knowing about the column: eBay hands
  back `TENNIS` beside `Tennis`, and multi-sport strings like "Auto Racing,
  Baseball, ... Tennis", so sort on it loosely; and one recorded Nadal Topps
  Graphite "Break Point" card carries `Sport: Breaking`, kept only because
  Graphite is in `TENNIS_ONLY_SETS` -- a seller's wrong field, and evidence
  for why those lists exist.
- **Does the surname search really return everything the full-name search
  does?** Run 41 (both searches) checked 605 Shapovalov Topps Chrome
  listings; run 42, 47 minutes later (surname only), checked 579. Listings
  ending in between, and eBay's own drift, are the likely 26; eBay matches
  every word, so a listing the full name finds and the surname does not
  would have to come from eBay relaxing the longer query, which brings other
  people's cards, not his. Settle it for two calls from the PC:
  `py -c "import tennis_card_engine as e; t=e.get_ebay_token(); [print(q, e.search_ebay(t, q, 1, newest_first=True)[1]) for q in ('Shapovalov Topps Chrome', 'Denis Shapovalov Topps Chrome')]"`
  -- if the second total is ever the larger, the narrower search must run
  every time again, not only past the cap.
- **The back catalogue is not scanned, and one sweep would close it for
  good.** A filterless scan is not the same as scanning every player, for two
  reasons that have nothing to do with the cursors.
  **Depth.** eBay serves `MAX_RESULTS_PER_BRAND` (1,200) per query, so the
  wide scan reaches the newest 1,200 listings of each set and everything
  listed after -- never further back. A surname query matches far fewer than
  1,200 listings in total, so it reaches the back of that player's whole
  history on eBay.
  **Words.** For the three lines printed for every sport
  (`MULTI_SPORT_LINES`) the wide query is `"{set} tennis"`, and eBay matches
  every word. Measured against the recorded cards on those three lines on 18
  Sep: **45 say "tennis" in the title and 37 do not.** Those 37 could never
  have been matched by the wide query -- every one came in through a player
  scan ("Iga Swiatek 2024 Topps Chrome Served! Signatures Auto 01/25 PSA
  Card 9", "2025 Topps Chrome Autograph Rookie Card-Elina Avanesyan
  #CA-EAN +1/75", "2021 Topps Chrome Autograph Card Tracy Austin 50/50
  Bookend"). The other 119 recorded cards sit on lines whose query is the
  bare set name, so they carry no word requirement and only the depth limit
  applies. It is the same effect as the 17 Sep note in the wide-search
  decision, where two player scans found 17 bookends no 4,800-listing wide
  walk had ever seen.
  **The fix is a one-off sweep of the 15 built-in players**, which lays down
  a cursor per surname-and-set -- 105 search texts -- after which a repeat
  player scan costs about a page per set. Run it when the allowance resets
  (07:00 UTC, 3pm Singapore) and **in slices**, three or four players a day:
  a first-time player scan costs roughly one detail call per never-seen
  listing that hints at numbering, run 41 checked 605 listings for
  Shapovalov on Topps Chrome alone, and the daily scheduled run still needs
  its own ~500. Read `results/ebay_api_usage.json` between slices, and do
  not bypass the ceiling for it -- eBay's own limit is the one that cannot
  be bypassed. Nothing about it can go wrong quietly: "not a Federer card"
  is a filtered verdict keyed to the rules fingerprint, so a player scan can
  never hide a card from the wide scan that follows. Agreed with the owner
  on 18 Sep, to be run when the calls reset.

Done since these notes were written: `app.js` confirmed on `main` (the Matches
fallback works), `scan.yml`'s six-hourly comment corrected, and `test_engine.py`
added -- run `py test_engine.py` before pushing engine changes. Later on 17 Sep:
`results/status.json` verified (56 statuses written by every run since 06:04),
and the one-time full re-walk after the cursor format changed happened at run
36, after which all seven cursors carry the resolved fingerprint.

## Durability

- **A run keeps what it earned, however it ends.** `run_scan` writes the
  spreadsheet, `seen_items.json`, the cursors and `new_matches.json` from a
  `finally`, each one separately, so one failure cannot take the others with
  it. Before this everything was held in memory until the last line, and a
  stumble -- a bad eBay response, the workflow's 60-minute timeout -- threw
  away both the matches found and the record of every listing judged, which
  the next scan then paid eBay to judge again.
- **`scan.yml` exports and commits with `if: always()`.** Those saved files
  live on the runner; skipping the later steps on a failure would throw them
  away, which is what the saving was for. The run still shows red.
- **A failed run is published as a failed run.** Because of the above, a run
  that fails still writes a fresh `lastRun`, and the page reloads into it. With
  a burnt allowance that read as "Nothing new ... which is normal" under a
  green "Last scan" pill -- exactly the reassurance that sentence was written
  to give, in exactly the case it is false. `scan.yml` now passes the engine
  step's outcome (`SCAN_OUTCOME`, from `steps.engine.outcome`) to the export,
  `config.json` carries `lastRunOk`, and the page says "Last scan failed" and
  why nothing is new. Unset, as when `export_static.py` is run by hand, means
  fine. `.gitignore` also covers the `.writing` and `.unreadable` files the
  atomic write and the unreadable-file handling can leave beside a state file.
- **State files are written beside themselves and swapped in**
  (`write_json_atomically`, using `os.replace`, which is atomic on Windows
  too). A kill mid-write used to leave half a document where the real file
  was. `load_state` now treats a file it cannot parse as no memory at all and
  keeps the bad copy as `.unreadable`; it used to raise, and since `main()`
  catches only `EngineError` that killed every later run until the file was
  deleted by hand.
- **A refused call is not an answer.** `get_item_detail` returns `None` when
  eBay no longer serves a listing (404, 410) and `REFUSED` when the call
  itself failed (allowance gone, eBay down, network); `get_item_details`
  reports the refused ids through its `failures` set. Before this
  both were `None`, and "nothing came back" was read as "ended": on 17 Sep at
  06:21, with the allowance used up, one refused batch of status calls
  recorded 55 live listings as ended -- which counts as settled, so they were
  never asked about again and the site showed them ended all day. Now a
  refused status keeps its last reading, "ended" is settled only on eBay's
  word (an `endDate` it gave, or `gone` because it no longer serves the
  listing), and an ended reading with neither is asked about again -- so
  those 55 repair themselves on the next export. In a scan, a listing gone
  between search and fetch is a reject at once; a refused fetch is the
  retried-then-gone case above. **Run 42 (17 Sep, 12:45) then showed the
  other half:** the export made three batch status calls for the 55, every
  one was turned away, and not one status changed -- with nothing in the log,
  since the warning went to `engine_run.log`. Run 43 printed eBay's answer:
  `HTTP 403 Access denied, insufficient permissions to fulfill the request`.
  The batch call (`getItems`) is a limited release eBay never offered this
  keyset; every batch ever sent was a wasted call, hidden by a silent
  fallback to single calls. It is gone from the engine. The 55 were repaired
  by single calls in run 43 (all 55 live). The export prints "statuses: asked
  eBay about N ... M refused ... K no longer served" every time, so "nothing
  changed" can be told from "nothing was asked".
- **The status refresh was the biggest line in the bill, and it was buying
  nothing.** Once the no-sign rule had taken two thirds off the detail calls
  it was the largest single cost of a scan -- 181 of run 61's 307 calls, 189
  of run 63's 375 -- and **across those two runs 370 status calls changed not
  one reading**. Three things now stop it paying for that:
  **`STATUS_FRESH_SECONDS` is 20 hours, not one.** The daily scheduled run is
  24 hours after the last, so it still refreshes every listing exactly as
  before -- nothing about the scheduled behaviour changes -- while a scan
  started by hand during the day pays **nothing at all** for statuses instead
  of re-checking every unsold row to be told nothing. Measured against the
  published results: a scan 2, 6 or 12 hours after the last one went from 194
  status calls to 0. **The one invariant that must hold: the constant stays
  under 24 hours**, or the daily run stops refreshing and the SOLD flag
  quietly dies -- `test_the_daily_run_still_refreshes_every_listing` fails if
  it ever does. Set it to 0 to re-check on every run.
  **A row the board drops is asked about by nobody.** A custom, a grade pair,
  a card number, a title that argues with itself: shown nowhere on the site,
  so the export asks about the board's own cards rather than every row in the
  spreadsheet -- 12 calls a run at 199 rows, and it grows. Their last
  readings are **kept, not dropped**, so a card starred before a rule dropped
  its row still shows what was last known of it.
  **A card the run has just recorded is not asked about again.** It came from
  a live search result and a detail call that answered seconds ago, so the
  export seeds it as active from `new_matches.json`: the one call in a run
  that can be *known* to say nothing new. It never overwrites a settled
  reading.
  Together the daily run went from 194 status calls to 182, and every repeat
  scan in between from 194 to 0. The saved page's "Check eBay now" still asks
  at once, so a card you care about is never more than a click from fresh.
- **The ceiling keeps the work it interrupts.** `consume_api_call` raised
  from inside the detail fetch, and the exception unwound the whole fetch:
  every listing already fetched alongside the one that met the ceiling went
  with it, paid for and never judged, and the next run bought them again.
  Measured on ten listings with the ceiling set to bite after four: before,
  four calls spent and nothing saved; after, four calls spent, four cards
  judged and four rows in the spreadsheet. `get_item_detail` now answers
  `OUT_OF_BUDGET` instead of raising, `get_item_details` reports those ids
  through `unattempted` (and through `failures` as well, since nothing is
  known of them either, which is what keeps a status check from reading
  "we never asked" as "gone"), and `run_scan` judges what came back, leaves
  the unasked ones **with no entry at all** -- not refused by eBay, so no try
  is spent and the set's mark stays put -- and only then raises, so the run
  still shows red and still says why. The one thing that must not change: a
  spent allowance is never a quiet day.
- **The export never blanks the published board.** `build_board` hands back
  nothing at all when the spreadsheet is missing or unreadable -- a lost
  file, not a day with no cards -- and `export_static.py` wrote that nothing
  straight over `results/board.json`. `scan.yml` commits with `if: always()`,
  so the empty board would have been published and the site would have gone
  blank with no red run to show for it. The export now keeps the board
  already there when it has no cards to write, and says so with a
  `::warning::` line that shows in the Actions summary; a first run, with no
  board yet, still writes one. The file is also a script with nothing to
  guard it, so importing it used to run the whole thing and overwrite
  `results/` from whatever directory was current -- it now refuses to be
  imported. `TheExportNeverBlanksThePublishedBoard` in `test_engine.py` runs
  the real script in a temporary directory for all four cases.
- **A digest email that will not send is a warning, not a failed scan.** It is
  sent after the scan has already saved, so raising there exited non-zero and
  stopped the steps that publish and commit -- losing a good scan over an
  email. Dormant until the `DIGEST_*` secrets are set, which is why it went
  unnoticed.
- `say()` prints warnings to stderr as well as the log, because
  `engine_run.log` lives only on the runner and goes away with it.

## Things to keep in mind

- eBay Browse API default limit is about 5,000 calls a day, shared by GitHub
  scans, phone-triggered scans and PC scans. A full fresh walk covers up to
  8,400 listings (7 sets x 1,200) and a never-seen listing the title cannot
  settle costs one call, so a first walk is a day or two of allowance; after
  it, the marks make a repeat cost a page per set. Never delete
  `seen_items.json`: a listing judged on an earlier run costs no call at all.
- **Three files carry the quota guards between runs**, all restored from
  `results/` by `scan.yml` before the engine starts: `seen_items.json` (what
  each listing was judged, with filtered verdicts keyed to a fingerprint of
  the rules that produced them, so changing a filter re-judges only what that
  filter touches), `scan_cursors.json` (the newest listing each exact search has
  already reached **together with a fingerprint of the rules that reached it**,
  so a scheduled scan does not walk the same window again but a changed setting
  does -- see below), and `ebay_api_usage.json` (a local daily counter enforcing a 4,500
  call ceiling per Pacific day, below eBay's own ~5,000, set with
  `EBAY_DAILY_CALL_BUDGET`). Deleting any of them makes the next scan pay for
  work already done. A card turned away for its sport is a permanent `reject`,
  so it costs one detail call ever, not one per run.
- **A cursor is only trusted while the rules -- and the judge -- behind it
  are unchanged.** It means "everything older than this is already judged",
  which is true only of the settings and the `JUDGE_VERSION` that judged it;
  a judge bump stales every mark, so listings a newer rule could reverse are
  walked past once without anyone editing a file. Player scans keep marks
  too, one per search text, so a repeat scan for the same player costs a
  page per set instead of a walk to the back (run 41 on 17 Sep: 25 calls for
  605 cached listings; with marks, about 7). Before 17 Sep the key covered the search scope
  alone (player, set, price, listing type), so raising the print-run ceiling or
  picking a card type left the mark in place, the search stopped one page in,
  and every scan reported "no new cards" whatever the filter said. Measured
  against a 1,200-listing set: with the mark wrongly kept, 1 call and 0 cards;
  with it correctly dropped, 44 calls and 665 cards. Re-walking is cheap
  because `seen_items.json` still answers for every listing already settled as
  a match or a reject, so only the search pages are paid for again -- about 42
  calls across all 7 sets, against a 4,500 budget. Keeping the cursor for an
  unchanged repeat saves 35 of those; that is all it was ever worth.
- **A fingerprint resolves the settings before hashing.** The website always
  fills the print-run box, so pressing "Run a scan" without touching anything
  sends `max_print_run=500` where a scheduled run sends nothing. They are the
  same scan. Before 17 Sep they hashed differently, so no page-started scan
  ever matched the cursor the scheduled one had left, and every one walked all
  seven sets in full. Seen live in runs 29 and 30: the same default scan wrote
  `7a651a5f2ed5ae03` and `60a3c781e14f279c`.
- **"Not a Federer card" is a filtered verdict, not a reject.** It describes
  the search, not the card, so the player is part of the rules fingerprint. As
  a permanent reject it would have hidden that card from every later scan,
  including the all-players scan that would have matched it.
- **The "Bypass ceiling" toggle beside the eBay calls meter turns the local
  4,500-call ceiling off for scans started from that device.** Hosted it goes
  as the `bypass_budget` workflow input (the tenth and last input GitHub
  allows) to `SCAN_BYPASS_BUDGET` on the engine and export steps; locally as
  `bypassBudget` in the scan request, which `server.py` turns into
  `engine.API_BUDGET_BYPASSED` for that one scan. `consume_api_call` still
  counts, so the meter stays true; it just does not raise. The run log says
  the ceiling is bypassed. eBay's own ~5,000 limit cannot be bypassed: past
  it eBay refuses, refused searches are said out loud and refused fetches
  keep their readings and hold the set's mark back. The toggle is remembered
  per device (`tce.bypassBudget`) like the rest of the setup, because a
  finished hosted scan reloads the page; the scheduled run sends no inputs
  and never bypasses. Asked for by the owner on 17 Sep after run 44 stopped
  at the ceiling mid-walk.
- **A refused eBay search is said out loud, and a run that could check
  nothing fails.** Runs 32 to 35 on 17 Sep each checked 0 listings with the
  day's allowance used up (5,000 of 5,000), and every one went green -- the
  refusal went to an `on_event` that `main()` never passed, and to
  `engine_run.log`, which the runner throws away. `main()` now passes an
  `on_event` that routes every `error` through `say()`, and exits non-zero
  when searches were refused and not one listing was checked. Because #18
  put `if: always()` on the export and commit steps, the run still publishes
  what it has; it just shows red instead of pretending it was a quiet day. A
  partly refused run stays green with a warning that it covered less.
- **Measured 17 Sep: a never-seen listing costs one call, and batching never
  helped.** The Shapovalov scan judged 2,077 listings for 1,873 single
  detail calls plus 96 batch calls. That was first read as "`getItems` hands
  back the specifics for about 4 listings in 100"; run 43 settled it:
  `getItems` returned HTTP 403 to this keyset every time, and the old code
  fell back to singles without a word, which gives the same arithmetic. There
  is no batch call any more. The old 420-calls-a-scan figure was wrong. What does
  save calls: `seen_items.json` (a judged listing is never fetched again), the
  cursor (a repeat wide scan fetches only what was listed since), and
  `settled_by_title` (a custom, another sport, a serial that is not a
  bookend, or -- since 18 Sep -- a title that says nothing about numbering at
  all, each rejected from the title with no call; a title that hints at
  numbering without giving a readable pair is still fetched, because the
  specifics may carry it). A first-time player scan still costs roughly one
  call per listing eBay returns for the name, so a common surname is
  expensive.
- **A reject is only as permanent as its rule.** A reject carries the
  `JUDGE_VERSION` it was made under. When a permanent rule changes in a way
  that could reverse old rejects, bump the version and name the old reason's
  opening words in `RECONSIDER_REASONS`; those rejects, and only those, are
  judged again when a walk next reaches them -- free where the title settles
  it, one call otherwise. Version 2 reconsiders "manufacturer not in
  allow-list" (a blank or line-named maker now reads off the title). A reject
  from before 17 Sep is a bare string with no reason: it is reconsidered only
  when the title in front of the scan carries a bookend serial, the one case
  that could be a match; a bare reject whose title has no serial stays, and
  that slice of the backlog (specifics-only serials, 1 of 71 recorded
  matches) is the price of not re-fetching thousands. Old rejects are only
  reached by the forced full walk after a query change and by player scans.
- **A reject keeps its reason** in `seen_items.json` (`{"verdict": "reject",
  "reason": ...}`; older entries are the bare string). To see why a listing
  was passed over, take the number from its eBay URL (`/itm/336797712136`)
  and look up `v1|336797712136|0` in `results/seen_items.json`.
- **The local counter and eBay's own figure count different stretches, and
  eBay's can be zeroed without warning.** On 17 Sep they tracked each other
  closely all morning (13:10 UTC: local 4,500, eBay 4,340, the gap being
  eBay's lag). At 13:26 eBay's figure went from 4,340 to **0** while it still
  reported `resets` as 18 Sep 07:00, so eBay restarted its count four hours
  into the window and said nothing. The local counter kept its running total
  to the end of its own Pacific day, which is why it read 8,790 against
  eBay's 5,000 that night: the same calls, two different starting points, and
  neither figure wrong. When they drift like that, eBay's is the one to
  believe and the local one can be set to match by hand -- done once at 02:54
  on 18 Sep. Both then reset together at 07:00 UTC (3pm Singapore), since
  Pacific midnight is the same moment. Do not make the engine trust eBay's
  figure automatically: it lags, and a lagging figure that relaxes the local
  ceiling is how a day's allowance gets spent twice.
- **A `log.warning` does not appear in the Actions log.** `logging.basicConfig`
  sends it to `engine_run.log`, a file on whichever machine ran the scan, and
  that file is gitignored. Anything a reader must see goes through `say()`,
  which also prints to stderr. To measure what a scan really cost, read
  `results/ebay_api_usage.json` before and after (the local counter, exact),
  not the "eBay calls today" panel, whose figure is eBay's and lags.
- Every detail is one call per listing, `DETAIL_WORKERS` (8) in flight at
  once, for the judge and for statuses alike; lower it if eBay starts
  refusing calls. `OneCallPerListing` and `StatusRefresh` in `test_engine.py`
  pin it, with no keys.
- **A search stops when eBay says there is no further page.** eBay's `total`
  is an estimate that runs high, so the last page used to be followed by an
  empty one, a call each. `search_ebay` now hands back whether the response
  carried `next`, eBay's own word that more exists, and the walk stops
  without it. A page shorter than asked for is deliberately *not* taken as
  the last: eBay does not promise that, and a walk cut short would write
  its mark and never come back for the rest.
- **A listing eBay will not return is retried on the next run, up to
  `UNAVAILABLE_TRIES` (3) times, then recorded as gone.** While it is being
  retried it holds its set's mark back, so the next run walks past it again
  (search pages only; everything judged is cached) -- a real listing behind a
  hiccup is never lost. After the third miss it is a reject with that reason
  and the mark moves on.
  **Holding the mark back was not enough on its own.** A mark is only held by
  failures in the run that had them, so a later clean run knows nothing about
  work left behind: the 03:41 run on 18 Sep met an exhausted allowance, eBay
  refused every call (`113 refused` on the statuses alone), and 74 listings
  were recorded unavailable with their marks rightly withheld -- then the
  full seven-set walk at 07:45 never reached those 74, had no failures of its
  own, and wrote its marks straight past them. They were left unjudged for
  good: not rejects, not matches, invisible. So after the walk a run asks
  about the leftovers **by id**, which needs no walk to reach them, one call
  each and bounded by the three tries. After the walk, so the scan's own work
  comes first and anything the walk already settled is off the list; and only
  on a scan with no player named, since a player scan would judge them "not
  an X card", which is a verdict about that search and settles nothing.
  **When eBay turns away every one of them, none of their tries is spent** --
  that is the allowance talking, not the listings, and spending tries on it
  would reject real cards for being asked at a bad moment.
  `record_judgement` is the one judging path the walk and this pass share, so
  a listing settled either way is settled the same way.
- **A blocked seller is turned away from the search result**, which names the
  seller, before any detail call.
- **The run says where its calls went, and the figures add up.** After
  "Checked N listings" the Actions log prints detail calls made, listings the
  title settled with no call, listings judged on an earlier run, listings
  already recorded as matches, and the top reasons listings were turned away.
  Every listing checked belongs to exactly one of those four, and the line
  ends with their total against N so a reader can see it without adding up.
  **They used to overlap:** a listing this run settled from its own title was
  counted as settled and again as "already judged", so run 53 on 18 Sep
  printed 39, 4 and 7 against 46 checked -- 50 -- which reads exactly like a
  fault in the counting, and "already judged" covered work the run had just
  done for free. The counters themselves were right all along. If they ever
  disagree with N again, `main()` says so out loud rather than leaving it to
  be spotted. A refused detail call is said out loud with its count.
- **A title that says nothing about numbering at all is turned away for no
  call, and the rule is audited every run.** It is the biggest line in the
  bill: two thirds of every detail call goes to a title with no serial (run
  61: 103 of 152), and 5,930 listings in the record are rejected for having
  none in the title or the specifics.
  **A blanket skip would lose real cards** -- eleven of the 184 recorded give
  no readable serial in the title, one of them (a Jamie Murray Topps Royalty
  relic) carrying it only in eBay's specifics. So the sign is generous
  (`NUMBERING_HINT_RE`): any digit beside a slash ("1/10", "/10", "# /10",
  "1 /10"), the "#'d" sellers write, a spelled-out "1 of 10", or any of the
  words a numbered card is described with. **Measured against all 184
  recorded cards, every one carries a sign, including all eleven** --
  `test_every_recorded_card_carries_a_sign_of_numbering` re-measures that
  against the live spreadsheet on every run of the suite, so the day a card
  is recorded that the rule would have skipped, the tests fail.
  **And it is not taken on trust.** Each run fetches a few of the listings it
  skipped and judges them properly anyway (`NO_SIGN_AUDIT`, at most 25 and
  never more than a twentieth of what was skipped, always at least one), so
  the rule is measured against live listings rather than against the rows it
  was written from. One that turns out to be a match is kept -- it is judged
  by the same `record_judgement` as everything else -- and `say()`s out loud
  that the rule is losing cards. That is the signal to name `NO_SIGN_REASON`
  in `RECONSIDER_REASONS` and bump `JUDGE_VERSION`, which brings **every**
  skipped listing back to be judged again. Nothing is lost quietly and
  nothing is lost for good.
  The run prints how many it skipped and how many it audited, so the real
  saving is on the Actions log rather than estimated here.
  **Why now, when 18 Sep turned it down:** that note said "steady state is a
  couple of hundred detail calls a day", which was a figure for one run an
  hour after another, not for a day. Measured across runs 60 and 61, listings
  arrive at about 150 an hour across the seven queries, so a once-a-day
  filterless scan checks roughly 3,000 and spends roughly 2,900 detail calls
  -- two thirds of the 4,500 ceiling, and growing. The saving was never gone.
- Never commit `.env` or put any key or token in the code.
- The scan commits to `main`, so always `git pull --rebase` before `git push`
  from your PC. Merging a pull request while a scan runs is now safe: run 31
  scanned for 100 seconds, had its bare `git push` refused because PR #16 had
  been merged in the meantime, and lost the lot -- the cards it found and the
  state files that stop the next scan re-paying for the same listings. The
  "Keep the results in the repo" step now keeps its results aside, rebuilds the
  commit on top of whatever `main` has become and tries again, up to five
  times.
- **A queued run starts from the freshest state, and may publish it.** A run
  dispatched while another is still going is checked out at the commit it was
  dispatched from, so its `results/` can be a whole scan out of date by the
  time it starts. Runs 48 and 49 on 17 Sep were the same Alcaraz scan nine
  seconds apart: the second restored the state files the first had already
  superseded, paid eBay for all 949 listings again, found the same 7 cards,
  and then stood down at publish because the first had landed -- about 950
  calls for nothing. "Bring back last run's results" now takes the state
  files from `main` rather than from the checkout, and passes on where they
  came from as `RESULTS_BASE`. The publish step compares that against
  `main`'s results **tree** rather than the checked-out commit: unchanged
  since this run loaded them means what it holds is theirs plus what it
  judged, so it rebuilds on `main` and pushes (a pull request merging
  mid-scan is no longer a reason to stop); changed means another scan
  published after this one started and knows listings it never saw, so it
  stands down and keeps theirs. A `main` it cannot reach is tried five times
  and then said out loud as a failed run, never a silent stand-down. All
  four cases are driven through a real git repo by
  `ThePublishStepDrivenForReal` in `test_engine.py`, which runs the step's
  own script text through bash -- reading the words in the YAML only proves
  they are there. It skips itself where there is no bash.
