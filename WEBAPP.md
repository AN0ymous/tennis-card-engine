# Tennis Card Engine -- web app

A browser front end for `tennis_card_engine.py`. Pick players, brands and a
print-run ceiling, card type, graded or raw, colour match, price range and listing type, choose the order, bookend and listed-within filters, run a scan, star cards into a saved list that tracks whether each listing is still active or has sold, and watch matches arrive live. Click any match to
open it as a hologram card with the full listing description and a direct link
to the eBay page.

## Run it

```
python3 check.py          # tells you exactly what, if anything, to fix
pip install -r requirements.txt
python server.py
```

That opens <http://127.0.0.1:8765>.

## Credentials

The app needs your own free eBay API keys. Copy `.env.example` to `.env` next
to `server.py` and fill it in:

```
EBAY_CLIENT_ID=...
EBAY_CLIENT_SECRET=...
```

Get them at <https://developer.ebay.com/my/keys>. They're read by the server
process only and are never sent to the browser. Until they're set, the page
comes up and shows the setup steps, with example cards so you can see how
results are presented.

## How it fits together

| File | Does |
| --- | --- |
| `tennis_card_engine.py` | All the searching and filtering. Still runs standalone on a schedule. |
| `server.py` | Localhost HTTP server: serves `web/` and drives `run_scan()`. Standard library only. |
| `web/` | The browser UI. |

`server.py` binds to `127.0.0.1` and has no authentication, so it's reachable
only from your own machine. `--host` will change that; don't use it on a network
you don't control.

## Scanning on demand vs. on a schedule

The web app scans when you click. For unattended runs keep scheduling
`tennis_card_engine.py` itself (cron, Task Scheduler, or GitHub Actions) -- it
writes the same spreadsheet and sends the same digest email, and the web app
will show whatever the last run found.

Scheduled all-player scans remember the newest listing reached by each exact
search and stop there on the next run. Filtered decisions are reused while the
filter settings stay the same, and a local daily safety counter stops at 4,500
calls per eBay quota bucket by default. Set `EBAY_DAILY_CALL_BUDGET` only if
eBay has assigned the application a different allowance.

## Running it from an iPad, or unattended

See `IPAD_SETUP.md`: a GitHub Actions workflow runs the engine once a day
on GitHub's machines and commits the results to `results/`.

## If a scan misses a card you found by hand

Ask the engine to explain itself for that player and set:

```
python3 check.py --probe "Carlos Alcaraz" "topps chrome"
```

It lists every listing eBay returns for that search (newest first, up to 200)
with the verdict the engine reaches and why: matched, already recorded on an
earlier run, or the exact rule that rejected it. Nothing is written. Leave the
set out to probe every set.

## Colour match

A "colour match" is a card whose colour parallel (Blue Refractor, Gold Wave,
Purple /299...) matches the colour of the player's outfit in the photo. The
parallel is read from the title or eBay's Parallel/Variety specific. The
outfit colour needs a look at the photo, which Claude does when
`ANTHROPIC_API_KEY` is set in `.env` (locally, with `pip install anthropic`)
or as a repository secret (hosted). One short question per recorded card that
names a colour parallel, answers cached in `vision_cache.json`, so no photo is
asked about twice. The model is `claude-opus-5` unless `VISION_MODEL` says
otherwise.

The "Colour match" switch in the scan setup narrows the board and matches to
those cards, and each shows a Colour match tag. Cards recorded before the
photo step existed get their reading from:

```
python3 tennis_card_engine.py --colour-match
```

which the hosted run also does a little of on each pass.



## Ace Authentic

Ace Authentic could not produce on-card autographs and never numbered a card
on a round hologram sticker, so either one marks a fake. Sticker autographs
and stamped numbering are fine. The engine reads the listing text first
("on-card" rejects, "sticker" passes); when neither is said it asks Claude
about the photos if `ANTHROPIC_API_KEY` is set, and without a key it lets the
card through with a "Check by eye" note on the card and in the spreadsheet's
Caution column.
