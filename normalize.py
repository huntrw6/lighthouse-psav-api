#!/usr/bin/env python3
"""API week JSON (lighthouse.py) -> shifts in the snapshot format used by process.py / calsync.py.

Mapping (verified against the Lighthouse UI list, 2026-11-16):
  role  = assignmentName
  event = showName or notes         (what the old list showed as the title line)
  shiftType 9 = 'NA', 6 = 'PTO', 8 = 'HOL' paid holiday (all ignored: not worked shifts), 1 = work;
  notes-only entries have no hours.
Any other shiftType is NOT guessed: it is skipped and reported in `warnings`.
"""
import re, datetime as dt

OFF_TYPES = {9: "NA", 6: "PTO", 8: "HOL"}  # days off / paid holiday: never calendared
KNOWN = {1} | set(OFF_TYPES)
OFF_NAMES = {"na", "n/a", "not available", "pto"}
TIME = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([AP]M)$", re.I)


def parse_time(txt):
    """'2PM' / '10:30PM' -> 'HH:MM'; '-' or '' -> None."""
    m = TIME.match((txt or "").strip())
    if not m:
        return None
    h, mi, ap = int(m.group(1)) % 12, int(m.group(2) or 0), m.group(3).upper()
    return f"{h + (12 if ap == 'PM' else 0):02d}:{mi:02d}"


def normalize(weeks, orders=None):
    """weeks: list of API week dicts. orders: {shiftId: order#} (optional). -> (shifts, warnings)."""
    orders = orders or {}
    out, warnings, seen = [], [], set()
    for w in weeks:
        for day in w["shifts"]["days"]:
            for s in day.get("shifts", []):
                sid = s.get("shiftId")
                if sid in seen:
                    continue
                seen.add(sid)
                typ = s.get("shiftType")
                date = str(s["date"])[:10]
                if typ in OFF_TYPES or s.get("isNotesOnly"):
                    continue
                if typ not in KNOWN:
                    warnings.append(f"Unrecognized shift type {typ} on {date} (skipped)")
                    continue
                st, en = parse_time(s.get("inTimeText")), parse_time(s.get("outTimeText"))
                if not st or not en:
                    warnings.append(f"Shift on {date} has unreadable times {s.get('inTimeText')!r}-{s.get('outTimeText')!r} (skipped)")
                    continue
                role = (s.get("assignmentName") or "").strip()
                event = (s.get("showName") or "").strip() or (s.get("notes") or "").strip()
                if event.lower() in OFF_NAMES:
                    continue
                det = {
                    "In-Out": f"{st}-{en}", "Assignment": s.get("assignmentName"), "Product Code": s.get("productCode"),
                    "Working For": s.get("shiftLocationName"), "Working At": s.get("workingAt"), "Client": s.get("client"),
                    "Show Name": s.get("showName"), "Order #": orders.get(sid), "Job #": s.get("jobNo"), "Room": s.get("room"),
                    "PM": s.get("pm"), "Onsite Contact": s.get("onSiteContact"), "Shift Notes": s.get("notes"),
                    "Navigator Line Item Comment": s.get("navigatorLineComment"), "Address": s.get("workingAtAddress"),
                }
                det = {k: str(v).strip() for k, v in det.items() if v and str(v).strip() not in ("", "-")}
                out.append({"date": date, "start": st, "end": en, "event": event, "role": role,
                            "location": (s.get("workingAt") or "").strip(), "details": det, "_sid": sid})
    out.sort(key=lambda x: (x["date"], x["start"]))
    return out, warnings
