# Running everything from an iPad

An iPad can't run the engine or the web server, so GitHub does both. Every six
hours GitHub starts a small machine, runs `tennis_card_engine.py`, commits the
results into this repo's `results/` folder, and republishes the website from
them. You open the site in Safari like any other site: the board, the matches,
the holograms -- everything except scanning from inside the page, which becomes
a button that starts a run on GitHub.

Everything below happens in Safari on the iPad.

## 0. Make the repo public (or use a paid plan)

GitHub Pages is free on **public** repositories; on a private one it needs
GitHub Pro. Nothing secret is in the repo -- the keys live in Secrets, which
are never published -- so making it public is the usual route: **Settings →
General → Danger Zone → Change visibility → Public**.

## 1. Your eBay keys

Go to <https://developer.ebay.com/my/keys>, sign in, and create an application
keyset if you don't have one. You want the **Production** keyset: an
**App ID (Client ID)** and a **Cert ID (Client Secret)**. Keep the tab open.

## 2. Give the keys to GitHub

In this repo on GitHub: **Settings → Secrets and variables → Actions →
New repository secret**. Add these two:

| Name | Value |
| --- | --- |
| `EBAY_CLIENT_ID` | the App ID |
| `EBAY_CLIENT_SECRET` | the Cert ID |

Secrets are write-only: GitHub never shows them again, and they are handed to
the scan only while it runs.

Optional, if you want an email each time something new turns up:

| Name | Value |
| --- | --- |
| `DIGEST_FROM_EMAIL` | the Gmail address to send from |
| `DIGEST_FROM_APP_PASSWORD` | a Gmail *app password* from <https://myaccount.google.com/apppasswords> (not your normal password) |
| `DIGEST_TO_EMAIL` | where to send it; leave out to send to the FROM address |

## 3. Merge the pull request

The workflows only run on a schedule from the `main` branch. Open **Pull
requests → #1 → Merge pull request → Confirm**. That puts everything on
`main`.

## 4. Turn on the website

**Settings → Pages → Build and deployment → Source: GitHub Actions.** That's
the whole setting. The address will be
`https://<your-username>.github.io/<repo-name>/` -- GitHub shows it on that
page once the first publish has run.

## 5. Switch the schedule on

Open the **Actions** tab. If GitHub asks you to enable workflows for the
repo, do. Pick **Scan eBay for bookend cards** in the left column, then
**Run workflow → Run workflow** to do a first run by hand. It takes a minute
or two; a green tick means it worked, a red cross means open it and read the
log -- a missing or wrong key is the usual cause, and the message says so.

From then on it runs on its own at 00:00, 06:00, 12:00 and 18:00 UTC, and
each run republishes the website a minute or two later.

The website's **Run a scan on GitHub** button brings you to this same
place: press **Run workflow** there and the site updates when it finishes.

## Where the results are

- **The website** at the Pages address above -- the board of the newest
  listings, every match, and the hologram with the listing link.
- **`results/tennis_cards_verified.xlsx`** in the repo -- every qualifying
  card ever found, one row each, with a link to the listing. Tap it on GitHub
  to view or download; it opens in Numbers or Excel on the iPad.
- **`results/new_matches.json`** -- just the latest run's finds.
- Each run also attaches the spreadsheet under the run's **Artifacts**, kept
  for 30 days.
- The email digest, if you set it up.

If you also run the web app on a computer, it reads `results/` when there is
no spreadsheet beside it, so a `git pull` brings the board up to date.

## Changing what it looks for

Players, sets, the print-run ceiling and the blocked sets are the config
block at the top of `tennis_card_engine.py`. Edit it on GitHub (the pencil
icon, or press `.` in the repo to open the web editor), commit, and the next
run uses it.

## Things to know

- GitHub's schedule is best-effort: a run can start some minutes late at busy
  times. It does not miss runs, it just slips.
- GitHub pauses scheduled workflows on a repo that has had no commits for 60
  days. The result commits count as activity, so this only happens if the
  engine finds nothing new for two months; if the Actions tab says the
  schedule was disabled, one tap re-enables it.
- Two scans never overlap: if one is still going when the next is due, the
  second waits.
- The keys live only in GitHub's secrets store. Don't paste them into the
  code or into a commit.
