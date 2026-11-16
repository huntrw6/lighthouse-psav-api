#!/usr/bin/env python3
"""Parse raw Lighthouse week lists, ignore days off, diff vs last snapshot, print the message.

Usage: process.py RAW_JSON [--today YYYY-MM-DD] [--no-save]
RAW_JSON: list of 4 strings (week 0 = week containing today, then following weeks), each the
innerText after 'Team Schedule' on the schedule page.
"""
import json, os, re, sys, datetime as dt
from zoneinfo import ZoneInfo
from pathlib import Path

D = Path(__file__).parent
def load_locations():
    """Optional venue-name -> 'Name\nAddress' map (locations.json, see locations.example.json)."""
    f = D / "locations.json"
    return json.loads(f.read_text()) if f.exists() else {}


HOME_VENUE = os.environ.get("ENCORE_HOME_VENUE", "")  # usual venue: left out of the "changes" lines
OFF = {"na", "n/a", "not available", "pto"}
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
HDR = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\d{1,2})$")
TIME = re.compile(r"^(\d{2}:\d{2}) - (\d{2}:\d{2})$")


LABELS = ["Employee Name", "In-Out", "Assignment", "Product Code", "Skill", "Working For", "Working At",
          "Client", "Show Name", "Order #", "Job #", "Room", "PM", "Onsite Contact", "Shift Notes",
          "Navigator Line Item Comment"]


def parse_details(text):
    """'Shift Details' panel text -> {label: value}; empty and '-' values dropped."""
    out, cur = {}, None
    for l in [x.strip() for x in text.splitlines()][1:]:
        if l in LABELS and l not in out:
            cur = l
            out[cur] = []
        elif cur and l:
            out[cur].append(l)
    return {k: " ".join(v) for k, v in out.items() if " ".join(v).strip() not in ("", "-")}


def parse_week(week, monday):
    text = week["list"] if isinstance(week, dict) else week
    details = week.get("details", []) if isinstance(week, dict) else []
    shifts, cur_day, cur = [], None, None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = HDR.match(line)
        if m:
            idx = DAYS.index(m.group(1))
            cur_day = monday + dt.timedelta(days=idx)
            if cur_day.day != int(m.group(2)):
                raise ValueError(f"day mismatch {line} vs {cur_day}")
            cur = None
            continue
        if cur_day is None or line.lower() == "no shifts":
            continue
        if TIME.match(line):
            cur = {"date": cur_day.isoformat(), "start": line[:5], "end": line[8:], "lines": []}
            shifts.append(cur)
        elif cur is not None:
            cur["lines"].append(line)
    if details and len(details) != len(shifts):
        details = []  # cannot align cards to shifts; fall back to list-only data
    out = []
    for i, s in enumerate(shifts):
        ev = s["lines"][0] if s["lines"] else ""
        if ev.strip().lower() in OFF:
            continue
        d = parse_details(details[i]) if details else {}
        out.append({"date": s["date"], "start": s["start"], "end": s["end"], "event": ev,
                    "role": s["lines"][1] if len(s["lines"]) > 1 else d.get("Assignment", ""),
                    "location": d.get("Working At", ""), "details": d})
    return out


def fmt_t(t):
    h, m = int(t[:2]), t[3:]
    return f"{(h % 12) or 12}:{m} {'AM' if h < 12 else 'PM'}"


def main():
    args = sys.argv[1:]
    raw = json.loads(Path(args[0]).read_text())
    today = dt.date.fromisoformat(args[args.index("--today") + 1]) if "--today" in args else dt.datetime.now(ZoneInfo(os.environ.get("ENCORE_TZ", "America/Los_Angeles"))).date()
    monday0 = today - dt.timedelta(days=today.weekday())
    # The browser clock is UTC: on Sunday evening Pacific the site may already show next week first.
    start = None
    for off in (0, 7):
        try:
            parse_week(raw[0], monday0 + dt.timedelta(days=off))
            start = monday0 + dt.timedelta(days=off)
            break
        except ValueError:
            continue
    if start is None:
        print("ERROR: first week does not match expected dates", file=sys.stderr); sys.exit(2)
    shifts = []
    for k, text in enumerate(raw):
        shifts += parse_week(text, start + dt.timedelta(weeks=k))
    shifts.sort(key=lambda s: (s["date"], s["start"]))
    if not shifts and all(len(t) < 20 for t in raw):
        print("ERROR: empty schedule text", file=sys.stderr); sys.exit(2)
    print(message_for(shifts, start, today, save="--no-save" not in args))


def message_for(shifts, start, today, save=True, notes=()):
    """Diff `shifts` (sorted list in snapshot format) against snapshot.json, optionally save, return the message."""

    snap = D / "snapshot.json"
    prev = json.loads(snap.read_text()) if snap.exists() else None
    key = lambda s: (s["date"], s["start"], s["end"], s["event"], s["role"], s.get("location", ""))
    changes = []
    if prev is not None:
        window = {s["date"] for s in shifts} | {s["date"] for s in prev["shifts"]}
        # only compare dates covered by both snapshots' ranges
        lo, hi = start.isoformat(), (start + dt.timedelta(weeks=4, days=-1)).isoformat()
        a = {key(s) for s in prev["shifts"] if lo <= s["date"] <= hi}
        b = {key(s) for s in shifts if lo <= s["date"] <= hi}
        if prev["shifts"] and not any("location" in x for x in prev["shifts"]):
            # old snapshot format without location: compare without it once
            a = {k[:5] + ("",) for k in a}
            b = {k[:5] + ("",) for k in b}
        for k_ in sorted(b - a):
            changes.append("+ " + describe(k_))
        for k_ in sorted(a - b):
            changes.append("- " + describe(k_))
    if save:
        snap.write_text(json.dumps({"saved": dt.datetime.now().isoformat(timespec="seconds"), "shifts": shifts}, indent=1))

    tom = (today + dt.timedelta(days=1)).isoformat()
    tdate = today + dt.timedelta(days=1)
    label = tdate.strftime("%a %b %-d")
    ts = [s for s in shifts if s["date"] == tom]
    locs = load_locations()
    if ts:
        lines = []
        for s_ in ts:
            where = s_.get("location", "")
            room = s_.get("details", {}).get("Room", "")
            loc_txt = (where + (f", {room}" if room else "")) if where else ""
            lines.append(f"Tomorrow ({label}): {fmt_t(s_['start'])} - {fmt_t(s_['end'])}, "
                         f"{s_['role'] + ' - ' if s_['role'] else ''}{s_['event']}" + (f" @ {loc_txt}" if loc_txt else ""))
    else:
        lines = [f"Tomorrow ({label}): no shift scheduled."]
    unknown = sorted({s_["location"] for s_ in shifts if s_.get("location") and s_["location"] not in locs and not s_.get("details", {}).get("Address")})
    if unknown:
        lines.append("Location not in my address list (calendar uses the name only): " + "; ".join(unknown))
    if changes:
        lines.append("Schedule changes (next 4 weeks):")
        lines += changes[:12]
    lines += list(notes)
    return "\n".join(lines)




def describe(k_):
    d, st, en, ev, role, loc = k_
    day = dt.date.fromisoformat(d).strftime("%a %b %-d")
    return f"{day} {fmt_t(st)}-{fmt_t(en)} {role + ' - ' if role else ''}{ev}" + (f" @ {loc}" if loc and loc != HOME_VENUE else "")


if __name__ == "__main__":
    main()
