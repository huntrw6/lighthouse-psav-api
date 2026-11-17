# lighthouse-psav-api

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

## Gitignore`)
`lighthouse.json`, `icloud.json`, `login.lock`, `chrome-profile/` (session cookies/tokens), `logs/`, `snapshot.json`,
`calendar-state.json`, `outbox.txt`.

## Tests
`venv/bin/python -m pytest -q tests`
