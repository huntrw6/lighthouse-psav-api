#!/usr/bin/env python3
"""Nightly Encore schedule job. NO AI: token -> Lighthouse API -> shifts -> message -> iCloud calendar -> outbox.txt.

  run.py            full run (writes snapshot.json, calendar, outbox.txt)
  run.py --dry      fetch + diff only: no snapshot, calendar or outbox writes; prints the message

Every run writes logs/run-<time>.log (step timings, API counts, calendar result, failure details and a
screenshot when the login fails) and logs/status.json (last result). The outbox message on failure is a
single 'Encore schedule check failed: <CODE> ...' line; an agent can review the newest log to fix it.
Logs hold no passwords, tokens or URL query strings. Logs/raw older than 30 days are pruned.
"""
import json, os, sys, time, traceback, datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

D = Path(__file__).parent
sys.path.insert(0, str(D))
LOGS = D / "logs"
PT = ZoneInfo(os.environ.get("ENCORE_TZ", "America/Los_Angeles"))
OUTBOX = D / "outbox.txt"
KEEP_DAYS = 30


def main():
    dry = "--dry" in sys.argv
    LOGS.mkdir(exist_ok=True)
    now = dt.datetime.now(PT)
    logfile = LOGS / f"run-{now:%Y%m%d-%H%M%S}.log"
    t0 = time.time()
    fh = open(logfile, "w", buffering=1)

    def log(m):
        fh.write(f"[{time.time() - t0:6.1f}s] {m}\n")
        if dry: print(m)

    status = {"time": now.isoformat(timespec="seconds"), "ok": False, "log": str(logfile.name), "dry": dry}
    msg = None
    try:
        import lighthouse as lh, normalize, process, calsync
        log(f"start {now:%Y-%m-%d %H:%M} PT, dry={dry}")
        today = now.date()
        monday = today - dt.timedelta(days=today.weekday())
        tok, how = lh.get_token(log, shot_dir=LOGS)
        status["auth"] = how
        weeks = lh.fetch_weeks(tok, [monday + dt.timedelta(weeks=k) for k in range(4)], log)
        # order numbers come from a separate per-shift call (optional; failures are ignored)
        orders = {}
        for w in weeks:
            for d_ in w["shifts"]["days"]:
                for s in d_.get("shifts", []):
                    if s.get("shiftType") == 1 and not s.get("isNotesOnly") and s.get("assignmentId"):
                        o = lh.order_number(tok, s)
                        if o: orders[s["shiftId"]] = o
        log(f"order numbers: {len(orders)}")
        (LOGS / f"raw-{now:%Y%m%d}.json").write_text(json.dumps(weeks))  # kept for agent review
        shifts, warnings = normalize.normalize(weeks, orders)
        log(f"shifts: {len(shifts)}; warnings: {warnings}")

        snap = D / "snapshot.json"
        if snap.exists():  # guard: an API hiccup must not wipe the calendar
            prev = json.loads(snap.read_text())["shifts"]
            lo, hi = monday.isoformat(), (monday + dt.timedelta(weeks=4)).isoformat()
            pw = [s for s in prev if lo <= s["date"] < hi]
            if len(pw) >= 5 and not [s for s in shifts if lo <= s["date"] < hi]:
                raise lh.LighthouseError("SUSPICIOUS_EMPTY", f"API returned no shifts but the last snapshot had {len(pw)}; nothing changed")
        msg = process.message_for(shifts, monday, today, save=not dry, notes=warnings)
        log("message built")
        try:
            cal = calsync.sync(shifts, dry=dry)
        except Exception as e:  # calendar problems must not hide the schedule message
            cal = f"Calendar sync failed: {type(e).__name__}: {str(e)[:120]}"
            log(cal + "\n" + traceback.format_exc())
            status["calendar_error"] = cal
        log(cal)
        msg = f"{msg}\n{cal}"
        status["ok"] = "failed" not in cal
        status["shifts"] = len(shifts)
    except Exception as e:
        code = getattr(e, "code", type(e).__name__)
        detail = getattr(e, "msg", "") or str(e)
        log("FAILED: " + traceback.format_exc())
        msg = f"Encore schedule check failed: {code}" + (f" - {detail[:160]}" if detail and detail != code else "") + f" (log: {logfile.name})"
        status["error"] = code
    log("end")
    fh.close()
    if not dry:
        OUTBOX.write_text(msg + "\n")
        (LOGS / "status.json").write_text(json.dumps(status, indent=1))
        prune()
    print(msg)
    return 0 if status["ok"] else 1


def prune():
    cut = time.time() - KEEP_DAYS * 86400
    for f in LOGS.iterdir():
        if f.name != "status.json" and f.stat().st_mtime < cut:
            f.unlink()


if __name__ == "__main__":
    sys.exit(main())
