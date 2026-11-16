#!/bin/bash
# One-time (and whenever the Microsoft password changes): stores the Lighthouse sign-in used ONLY when the
# saved browser session lapses. File lighthouse.json is mode 600, never committed or shared.
# TOTP key = the "secret key" shown when you add an authenticator app at https://aka.ms/mfasetup
# (choose "I want to use a different authenticator app" -> "Can't scan image?"). Spaces are fine.
D="$(cd "$(dirname "$0")" && pwd)"
read -rp "Work email (Lighthouse sign-in): " EM
read -rsp "Microsoft password (hidden): " PW; echo
read -rsp "Authenticator secret key (hidden): " TK; echo
umask 077
EM="$EM" PW="$PW" TK="$TK" "$D/venv/bin/python" - "$D" <<'PY'
import json, os, sys, base64, pathlib
d = pathlib.Path(sys.argv[1])
tk = os.environ["TK"].replace(" ", "").replace("-", "").upper()
try:
    base64.b32decode(tk + "=" * (-len(tk) % 8))
except Exception:
    sys.exit("That secret key is not valid base32; nothing saved.")
if not os.environ["PW"]:
    sys.exit("Empty password; nothing saved.")
f = d / "lighthouse.json"
f.write_text(json.dumps({"email": os.environ["EM"], "password": os.environ["PW"], "totp_secret": tk}))
f.chmod(0o600)
(d / "login.lock").unlink(missing_ok=True)
print("Saved lighthouse.json (mode 600) and cleared any login.lock.")
PY
echo "Test it (uses a fresh browser profile, so it performs one real sign-in):"
echo "  $D/venv/bin/python $D/lighthouse.py --check --fresh"
