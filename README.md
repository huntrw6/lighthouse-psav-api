# lighthouse-psav-api

Reads your work schedule from the Encore/PSAV **Lighthouse** web app through its JSON API (not by scraping the page),
syncs it into an iCloud calendar over CalDAV, and writes a short "tomorrow + changes" message. Plain Python, **no AI or
agent** involved in a run. Unofficial: it uses the same API the Lighthouse web app calls for your own account.

## How it works
1. `lighthouse.py` starts headless Chrome with a dedicated profile (`chrome-profile/`) and reads the MSAL access token the
   Lighthouse SPA keeps in localStorage (single sign-on, ~3 s). The token stays in memory.
   If the saved session has lapsed it signs in once with `lighthouse.json` (email, password, TOTP secret; the one-time code
   is computed locally, RFC 6238). A rejected credential writes `login.lock` and stops all automatic sign-ins.
2. `GET .../schedules/schedule/employee/week/<monday>` for this week + the next 3, plus the order number per shift.
3. `normalize.py` maps API shifts to the snapshot format (NA / PTO / paid holiday / notes-only entries are ignored,
   unknown shift types are skipped with a warning).
4. `process.py` diffs against `snapshot.json` and builds the message.
5. `calsync.py` adds/updates/removes events whose UID starts with `encore-` in one iCloud calendar (pinned by URL in
   `calendar-state.json`, so renaming it in Apple Calendar is safe; never auto-creates a calendar; skips mass deletions).
6. `run.py` ties it together, writes `outbox.txt` (the message, or a one-line `check failed: <CODE>`) and
   `logs/run-<time>.log` + `logs/status.json` for review. Codes: BROWSER_START, NO_CREDENTIALS, LOGIN_LOCKED,
   LOGIN_REJECTED, LOGIN_TIMEOUT, API_AUTH, API_CHANGED, API_DOWN, SUSPICIOUS_EMPTY.

## Setup
```
python3 -m venv venv && venv/bin/pip install -r requirements.txt
./setup-icloud.sh        # Apple ID + app-specific password -> icloud.json (mode 600)
./setup-lighthouse.sh    # work email, Microsoft password, authenticator secret key -> lighthouse.json (mode 600)
cp locations.example.json locations.json   # optional: venue name -> "Name\nAddress" for calendar locations
venv/bin/python run.py --dry               # fetch + diff, writes nothing
venv/bin/python run.py                     # real run
```
Chrome/Chromium must be installed (or set `LIGHTHOUSE_CHROME`). The first run needs a sign-in: use
`venv/bin/python lighthouse.py --check --fresh` to test the full login on a throwaway profile.

Environment: `ENCORE_TZ` (default America/Los_Angeles), `ENCORE_CALENDAR` (calendar name on first run, default
"Encore Work"), `ENCORE_HOME_VENUE` (usual venue name: used as the default location and left out of change lines),
`LIGHTHOUSE_CHROME`.

Schedule nightly with cron/systemd: `cron/schedule_run.py` is a wrapper that prints the message and exits 1 on failure.

## Secrets (never commit; all in `.gitignore`)
`lighthouse.json`, `icloud.json`, `login.lock`, `chrome-profile/` (session cookies/tokens), `logs/`, `snapshot.json`,
`calendar-state.json`, `outbox.txt`.

## Tests
`venv/bin/python -m pytest -q tests`
