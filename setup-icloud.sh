#!/bin/bash
# Stores iCloud CalDAV credentials (chmod 600) and tests the connection.
D="$(cd "$(dirname "$0")" && pwd)"
read -rp "Apple ID email: " AID
read -rsp "App-specific password (hidden): " APW; echo
umask 077
printf '{"user": "%s", "password": "%s"}\n' "$AID" "$APW" > "$D/icloud.json"
chmod 600 "$D/icloud.json"
"$D/venv/bin/python" "$D/calsync.py" --test
