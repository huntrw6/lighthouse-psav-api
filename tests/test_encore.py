import json, sys, datetime as dt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import lighthouse as lh, normalize, calsync


def test_totp_rfc6238_vector():
    # RFC 6238 appendix B, SHA1, secret "12345678901234567890", t=59 -> 94287082 (8 digits) => 6 digits 287082
    assert lh.totp("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ", t=59) == "287082"
    assert lh.totp("GEZD GNBV GY3T QOJQ GEZD GNBV GY3T QOJQ", t=1111111109) == "081804"


def shift(**kw):
    base = dict(shiftId="1", date="2026-11-16T00:00:00", shiftType=1, assignmentId=294, assignmentName="Floor", client="C", showName="",
                jobNo="", room="R", pm="", shiftLocId="1000", inTimeText="2PM", outTimeText="10:30PM", shiftLocationName="1000-X",
                isNotesOnly=False, notes="Coverage", workingAt="Example Hotel", workingAtAddress="1 Main St", navigatorLineComment="",
                productCode="", onSiteContact="")
    base.update(kw)
    return base


def week(*shifts):
    days = {}
    for s in shifts:
        days.setdefault(s["date"], []).append(s)
    return {"shifts": {"days": [{"date": d, "shifts": v} for d, v in days.items()]}}


def test_normalize_mapping():
    shifts, warn = normalize.normalize([week(
        shift(),                                                                   # work: event=notes
        shift(shiftId="2", date="2026-11-20T00:00:00", showName="Big Show", notes="x", inTimeText="7AM", outTimeText="3:30PM"),
        shift(shiftId="3", date="2026-11-19T00:00:00", shiftType=9, assignmentName="", notes=""),   # NA
        shift(shiftId="4", date="2026-12-04T00:00:00", shiftType=6, assignmentName="", notes=""),   # PTO
        shift(shiftId="5", date="2026-11-26T00:00:00", shiftType=8, assignmentName="", notes="Thanksgiving", inTimeText="", outTimeText="-"),
        shift(shiftId="6", date="2026-11-29T00:00:00", isNotesOnly=True, notes="off if possible", inTimeText="-", outTimeText="-"),
        shift(shiftId="7", date="2026-11-30T00:00:00", shiftType=42),
    )], {"1": "6170"})
    got = [(s["date"], s["start"], s["end"], s["role"], s["event"]) for s in shifts]
    assert got == [("2026-11-16", "14:00", "22:30", "Floor", "Coverage"),
                   ("2026-11-20", "07:00", "15:30", "Floor", "Big Show")]  # NA, PTO, holiday, notes-only: skipped
    assert shifts[0]["details"]["Order #"] == "6170" and shifts[0]["details"]["Address"] == "1 Main St"
    assert len(warn) == 1 and "type 42" in warn[0]


def test_parse_time():
    assert [normalize.parse_time(x) for x in ("12AM", "12PM", "7AM", "10:30PM", "-", "")] == ["00:00", "12:00", "07:00", "22:30", None, None]


def test_uid_stable_with_old_snapshot_shape():
    s, _ = normalize.normalize([week(shift())])
    assert calsync.uid_for(s[0], 0).startswith("encore-2026-11-16-")


def test_location_falls_back_to_api_address():
    s = {"location": "New Venue", "details": {"Address": "9 Road"}}
    assert calsync.location_for(s) == "New Venue\n9 Road"
