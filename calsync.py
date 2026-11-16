#!/usr/bin/env python3
"""Sync Encore shifts (snapshot.json from process.py) into an iCloud calendar named 'Encore Work'.

  calsync.py --test      connect, create the calendar if missing, print calendar names
  calsync.py             sync the next 4 weeks (add/update/delete only events whose UID starts 'encore-')
  calsync.py --dry-run   show what would change
Credentials: icloud.json (chmod 600) next to this file: {"user": ..., "password": <app-specific password>}.
"""
import hashlib, json, os, sys, datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

D = Path(__file__).parent
CAL_NAME = os.environ.get("ENCORE_CALENDAR", "Encore Work")  # name used on first run / --create
PT = ZoneInfo(os.environ.get("ENCORE_TZ", "America/Los_Angeles"))
ALARM = "-PT1H"  # reminder 1 hour before each shift


def connect():
    import caldav
    c = json.loads((D / "icloud.json").read_text())
    client = caldav.DAVClient(url="https://caldav.icloud.com/", username=c["user"], password=c["password"])
    return client.principal()


STATE = D / "calendar-state.json"      # {"url": ..., "name": ...}: the pinned calendar (survives renames in Apple Calendar)
CAND_NAMES = tuple({CAL_NAME, "Encore Work", "Encore"})  # only used to find the calendar the first time
MAX_REMOVE = 8                          # more removals than this in one run = suspicious data, skip them


class CalendarError(Exception):
    pass


def _path(url):
    return str(url).split("://", 1)[-1].split("/", 1)[-1].rstrip("/")


def _own_count(cal):
    try:
        return sum(1 for e in cal.events() if str(e.icalendar_component.get("UID", "")).startswith("encore-"))
    except Exception:
        return 0


def get_calendar(principal, create=False):
    """Pinned calendar by URL first; else the candidate-named calendar holding the most 'encore-' events.
    Never creates a calendar unless create=True (a silent new calendar once made every sync fail with 412)."""
    cals = principal.calendars()
    if STATE.exists():
        st = json.loads(STATE.read_text())
        for cal in cals:
            if _path(cal.url) == _path(st["url"]):
                if cal.name != st.get("name"):
                    STATE.write_text(json.dumps({"url": str(cal.url), "name": cal.name}))
                return cal
        raise CalendarError(f"pinned calendar {st.get('name')!r} no longer exists (delete calendar-state.json to re-pick)")
    cands = [c for c in cals if c.name in CAND_NAMES]
    if cands:
        cal = max(cands, key=_own_count)
        STATE.write_text(json.dumps({"url": str(cal.url), "name": cal.name}))
        return cal
    if not create:
        raise CalendarError("no 'Encore' / 'Encore Work' calendar found (run calsync.py --test --create)")
    cal = principal.make_calendar(name=CAL_NAME, supported_calendar_component_set=["VEVENT"])
    STATE.write_text(json.dumps({"url": str(cal.url), "name": cal.name}))
    return cal


from process import load_locations  # noqa: E402


def esc(s):
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def uid_for(s, n):
    h = hashlib.sha1(f"{s['date']}|{s['event']}|{s['role']}|{n}".encode()).hexdigest()[:16]
    return f"encore-{s['date']}-{h}@encore-schedule"


def bounds(s):
    d = dt.date.fromisoformat(s["date"])
    st = dt.datetime.combine(d, dt.time.fromisoformat(s["start"]), PT)
    en = dt.datetime.combine(d, dt.time.fromisoformat(s["end"]), PT)
    if en <= st:
        en += dt.timedelta(days=1)
    return st.astimezone(dt.timezone.utc), en.astimezone(dt.timezone.utc)


def title_for(s):
    return f"{s['role']} - {s['event']}" if s["role"] else s["event"]


NOTE_ORDER = [("In-Out", "Time"), ("Client", "Client"), ("Show Name", "Show"), ("Room", "Room"),
              ("Order #", "Order #"), ("Job #", "Job #"), ("Product Code", "Product code"), ("Skill", "Skill"),
              ("PM", "PM"), ("Onsite Contact", "Onsite contact"), ("Shift Notes", "Shift notes"),
              ("Navigator Line Item Comment", "Navigator comment"), ("Working For", "Working for"),
              ("Working At", "Working at")]


def notes_for(s):
    d = s.get("details", {})
    lines = [f"{label}: {d[k]}" for k, label in NOTE_ORDER if d.get(k)]
    lines.append("Synced from Encore Lighthouse")
    return "\n".join(lines)


def location_for(s):
    name = s.get("location", "")
    if not name:
        name = os.environ.get("ENCORE_HOME_VENUE", "")  # list-only data (no details): usual venue, if configured
    locs = load_locations()
    if name in locs:
        return locs[name]
    addr = s.get("details", {}).get("Address", "")
    return f"{name}\n{addr}" if addr else name


def build_ics(s, uid):
    st, en = bounds(s)
    f = lambda x: x.strftime("%Y%m%dT%H%M%SZ")
    title = title_for(s)
    return "\r\n".join([
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//encore-schedule//EN", "BEGIN:VEVENT",
        f"UID:{uid}", f"DTSTAMP:{f(dt.datetime.now(dt.timezone.utc))}",
        f"DTSTART:{f(st)}", f"DTEND:{f(en)}", f"SUMMARY:{esc(title)}",
        f"DESCRIPTION:{esc(notes_for(s))}", f"LOCATION:{esc(location_for(s))}",
        "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Shift", f"TRIGGER:{ALARM}", "END:VALARM",
        "END:VEVENT", "END:VCALENDAR", ""])


def desired(shifts):
    out, seen = {}, {}
    for s in shifts:
        k = (s["date"], s["event"], s["role"])
        n = seen.get(k, 0); seen[k] = n + 1
        out[uid_for(s, n)] = s
    return out


def sync(shifts, dry=False):
    """Make calendar match `shifts` for this week + next 3. Returns a one-line summary; raises CalendarError."""
    today = dt.datetime.now(PT).date()
    start = today - dt.timedelta(days=today.weekday())
    lo, hi = start.isoformat(), (start + dt.timedelta(weeks=4)).isoformat()
    want = desired([s for s in shifts if lo <= s["date"] < hi])
    cal = get_calendar(connect())
    have = {}
    w0 = dt.datetime.combine(start, dt.time.min, PT).astimezone(dt.timezone.utc)
    w1 = w0 + dt.timedelta(weeks=4, days=1)
    for ev in cal.search(start=w0, end=w1, event=True, expand=False):
        comp = ev.icalendar_component
        uid = str(comp.get("UID", ""))
        if uid.startswith("encore-"):
            have[uid] = (ev, comp)
    added = updated = removed = skipped = 0
    for uid, s in want.items():
        st, en = bounds(s)
        if uid in have:
            comp = have[uid][1]
            same = (comp["DTSTART"].dt == st and comp["DTEND"].dt == en and str(comp.get("SUMMARY")) == title_for(s)
                    and str(comp.get("DESCRIPTION", "")) == notes_for(s)
                    and str(comp.get("LOCATION", "")) == location_for(s))
            if same:
                continue
            updated += 1
            if not dry:
                ev = have[uid][0]
                ev.data = build_ics(s, uid)
                ev.save()
        else:
            added += 1
            if not dry:
                cal.save_event(build_ics(s, uid))
    gone = [u for u in have if u not in want]
    if len(gone) > MAX_REMOVE:
        skipped = len(gone)
    else:
        for uid in gone:
            removed += 1
            if not dry:
                have[uid][0].delete()
    msg = f"Calendar sync{' (dry run)' if dry else ''} -> '{cal.name}': {added} added, {updated} updated, {removed} removed."
    if skipped:
        msg += f" NOT removed: {skipped} events missing from the schedule (more than {MAX_REMOVE}, looks wrong; check logs)."
    return msg


def main():
    a = sys.argv[1:]
    if "--test" in a:
        p = connect()
        cal = get_calendar(p, create="--create" in a)
        print("Connected. Calendars:", ", ".join(sorted(c.name or "?" for c in p.calendars())))
        print(f"Pinned calendar: '{cal.name}' ({_own_count(cal)} encore events).")
        return
    snap = json.loads((D / "snapshot.json").read_text())
    print(sync(snap["shifts"], dry="--dry-run" in a))


if __name__ == "__main__":
    main()
