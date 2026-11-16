#!/usr/bin/env python3
"""Encore Lighthouse client: headless Chrome (own profile) -> MSAL token -> JSON API. No AI involved.

Auth: the Lighthouse SPA (Azure AD B2C via MSAL) keeps its tokens in localStorage of a dedicated Chrome
profile (chrome-profile/). A normal run only loads the page and reads the fresh access token (SSO).
If SSO has lapsed, `login()` signs in once with the credentials in lighthouse.json (mode 600:
{"email","password","totp_secret"}); the TOTP code is computed locally (RFC 6238). A rejected
credential writes login.lock and stops all further automatic logins until you delete it.

CLI:  lighthouse.py --check        (get a token, call the API once, print ok/steps; no calendar/outbox)
"""
import base64, hashlib, hmac, json, os, shutil, struct, subprocess, sys, tempfile, time
import urllib.request, urllib.error, urllib.parse
from pathlib import Path

D = Path(__file__).parent
PROFILE = D / "chrome-profile"
CREDS = D / "lighthouse.json"
LOCK = D / "login.lock"
def _find_chrome():
    """LIGHTHOUSE_CHROME, else the first Chrome/Chromium on PATH."""
    if os.environ.get("LIGHTHOUSE_CHROME"):
        return Path(os.environ["LIGHTHOUSE_CHROME"])
    for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        p = shutil.which(n)
        if p:
            return Path(p)
    return Path("chrome")


CHROME = _find_chrome()
APP = "https://lighthouse2.psav.com"
API = "https://api-cus.psav.com/lighthouse-api/production/api/schedules/schedule/"


class LighthouseError(Exception):
    """code = short machine-readable reason (used in outbox + log)."""
    def __init__(self, code, msg=""):
        super().__init__(f"{code}: {msg}" if msg else code)
        self.code, self.msg = code, msg


def totp(secret, t=None, step=30, digits=6):
    key = base64.b32decode(secret.replace(" ", "").replace("-", "").upper() + "=" * (-len(secret.replace(" ", "").replace("-", "")) % 8))
    n = int((t if t is not None else time.time()) // step)
    h = hmac.new(key, struct.pack(">Q", n), hashlib.sha1).digest()
    o = h[-1] & 15
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** digits).zfill(digits)


class Browser:
    """Minimal CDP client over the browser websocket (flat sessions)."""
    def __init__(self, log):
        from websockets.sync.client import connect
        self.log = log
        PROFILE.mkdir(mode=0o700, exist_ok=True)
        for f in ("DevToolsActivePort", "SingletonLock", "SingletonCookie", "SingletonSocket"):
            try: (PROFILE / f).unlink()
            except FileNotFoundError: pass
        self.proc = subprocess.Popen(
            [str(CHROME), "--headless=new", "--no-sandbox", "--disable-gpu", "--no-first-run",
             "--disable-dev-shm-usage", "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
             f"--user-data-dir={PROFILE}", "--window-size=1366,850", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        port_file = PROFILE / "DevToolsActivePort"
        for _ in range(100):
            if port_file.exists() and port_file.read_text().strip():
                break
            if self.proc.poll() is not None:
                raise LighthouseError("BROWSER_START", f"chrome exited {self.proc.returncode}")
            time.sleep(0.2)
        else:
            raise LighthouseError("BROWSER_START", "no DevTools port after 20 s")
        port, path = port_file.read_text().split()[:2]
        self._cm = connect(f"ws://127.0.0.1:{port}{path}", max_size=None, open_timeout=15)
        self.ws = self._cm.__enter__()
        self.n = 0
        t = self.cmd("Target.createTarget", url="about:blank")["targetId"]
        self.sid = self.cmd("Target.attachToTarget", targetId=t, flatten=True)["sessionId"]
        self.cmd("Page.enable", sid=self.sid)

    def cmd(self, method, sid=None, **params):
        self.n += 1
        msg = {"id": self.n, "method": method, "params": params}
        if sid: msg["sessionId"] = sid
        self.ws.send(json.dumps(msg))
        while True:
            r = json.loads(self.ws.recv(timeout=60))
            if r.get("id") == self.n:
                if "error" in r:
                    raise LighthouseError("CDP", f"{method}: {r['error'].get('message')}")
                return r.get("result", {})

    def js(self, expr):
        r = self.cmd("Runtime.evaluate", sid=self.sid, expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r:
            raise LighthouseError("JS", str(r["exceptionDetails"].get("text"))[:100])
        return r["result"].get("value")

    def goto(self, url):
        self.cmd("Page.navigate", sid=self.sid, url=url)

    def screenshot(self, path):
        try:
            data = self.cmd("Page.captureScreenshot", sid=self.sid, format="png")["data"]
            Path(path).write_bytes(base64.b64decode(data))
            return str(path)
        except Exception:
            return None

    def close(self):
        try: self.cmd("Browser.close")
        except Exception: pass
        try: self._cm.__exit__(None, None, None)
        except Exception: pass
        try: self.proc.wait(timeout=8)
        except Exception:
            try: os.killpg(self.proc.pid, 9)
            except Exception: pass


TOKEN_JS = """(()=>{let best=null;for(const k of Object.keys(localStorage)){if(!k.includes('-accesstoken-'))continue;
try{const v=JSON.parse(localStorage.getItem(k));const e=parseInt(v.expiresOn);if(!best||e>best.e)best={e,s:v.secret}}catch(_){}}
return best?JSON.stringify(best):''})()"""


def read_token(b):
    try:
        v = b.js(TOKEN_JS)
    except LighthouseError:  # error page / mid-navigation: no storage access
        return None
    if not v:
        return None
    o = json.loads(v)
    return o["s"] if o["e"] - time.time() > 120 else None


def where(b):
    """host+path of the current page (never the query: it can carry state tokens)."""
    u = urllib.parse.urlparse(b.js("location.href") or "")
    return f"{u.netloc}{u.path}"[:90]


def _click_text(b, text):
    return b.js("""((t)=>{const e=[...document.querySelectorAll('div,span,a,button,li')].filter(x=>x.offsetParent&&x.children.length<4&&x.textContent.trim().toLowerCase()===t)[0];if(e){e.click();return true}return false})(%s)""" % json.dumps(text.lower()))


def login(b, log):
    if LOCK.exists():
        raise LighthouseError("LOGIN_LOCKED", "login.lock exists (an earlier sign-in was rejected); fix credentials, delete login.lock")
    if not CREDS.exists():
        raise LighthouseError("NO_CREDENTIALS", "SSO expired and lighthouse.json is missing; run setup-lighthouse.sh")
    c = json.loads(CREDS.read_text())
    for k in ("email", "password", "totp_secret"):
        if not c.get(k):
            raise LighthouseError("NO_CREDENTIALS", f"lighthouse.json lacks {k}")
    log("login: starting full sign-in (SSO had lapsed)")
    b.goto(APP + "/login")
    sent = set()
    otp_used = None
    deadline = time.time() + 150
    progress = time.time()
    while time.time() < deadline:
        time.sleep(1.5)
        tok = read_token(b)
        if tok:
            log(f"login: token present at {where(b)}")
            return tok
        try:
            state = b.js("""(()=>{const v=s=>{const e=document.querySelector(s);return !!(e&&e.offsetParent)};
          return JSON.stringify({host:location.host,
           email:v('input[type=email]#email')||(location.host.includes('lighthouse2')&&v('input[type=email]')),
           msUser:v('#i0116'),msPw:v('#i0118'),otc:v('#idTxtBx_SAOTCC_OTC'),kmsi:v('#KmsiCheckboxField'),
           chooser:v('#signInAnotherWay'),tile:v('#tilesHolder'),
           b2c:v('#EncoreEmployees'),
           err:[...document.querySelectorAll('#usernameError,#passwordError,#idTD_SAOTCC_Error,#idSpan_SAOTCC_Error_OTC,.error.itemLevel.show,#loginMessage,.alert-error')].filter(e=>e.offsetParent&&e.textContent.trim()).map(e=>e.textContent.trim().slice(0,100))})})()""")
            s = json.loads(state)
            if s["err"]:
                LOCK.write_text(time.strftime("%Y-%m-%d %H:%M:%S ") + " | ".join(s["err"]) + "\n")
                raise LighthouseError("LOGIN_REJECTED", "; ".join(s["err"]) + " (login.lock written; no more automatic sign-ins)")
            step = None
            if s["otc"]:
                code = totp(c["totp_secret"])
                if code == otp_used:  # same 30 s window already tried
                    time.sleep(2); continue
                otp_used = code
                b.js("""(()=>{const i=document.querySelector('#idTxtBx_SAOTCC_OTC');i.focus();i.value=%s;i.dispatchEvent(new Event('input',{bubbles:true}));
                  const t=document.querySelector('#idChkBx_SAOTCC_TD');if(t&&!t.checked)t.click();return 1})()""" % json.dumps(code))
                time.sleep(0.4)
                b.js("document.querySelector('#idSubmit_SAOTCC_Continue').click()")
                step = "otc"
            elif s["kmsi"]:
                b.js("(()=>{const k=document.querySelector('#KmsiCheckboxField');if(!k.checked)k.click();document.querySelector('#idSIButton9').click()})()")
                step = "kmsi"
            elif s["msPw"] and "pw" not in sent:
                b.js("""(()=>{const i=document.querySelector('#i0118');i.focus();i.value=%s;i.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('#idSIButton9').click()})()""" % json.dumps(c["password"]))
                step = "pw"
            elif s["msUser"] and "ms-user" not in sent:
                b.js("""(()=>{const i=document.querySelector('#i0116');i.focus();i.value=%s;i.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('#idSIButton9').click()})()""" % json.dumps(c["email"]))
                step = "ms-user"
            elif s["chooser"] and "chooser" not in sent:
                b.js("document.querySelector('#signInAnotherWay').click()")
                step = "chooser"
            elif "chooser" in sent and "otp-row" not in sent and not s["otc"] and _click_text(b, "Use a verification code"):
                step = "otp-row"
            elif s["tile"] and "tile" not in sent:
                b.js("""(()=>{const e=[...document.querySelectorAll('#tilesHolder .table')].find(x=>x.textContent.toLowerCase().includes(%s))||document.querySelector('#tilesHolder .table');if(e)e.click()})()""" % json.dumps(c["email"].lower()))
                step = "tile"
            elif s["b2c"] and "b2c" not in sent:
                b.js("document.querySelector('#EncoreEmployees').click()")
                step = "b2c"
            elif s["email"] and "app-email" not in sent:
                b.js("""(()=>{const i=document.querySelector('input[type=email]');i.focus();i.value=%s;i.dispatchEvent(new Event('input',{bubbles:true}));
                  const bt=[...document.querySelectorAll('button')].find(x=>x.offsetParent&&!x.disabled);if(bt)bt.click()})()""" % json.dumps(c["email"]))
                step = "app-email"
            if step:
                progress = time.time()
                if step not in ("otc", "kmsi"):
                    sent.add(step)
                log(f"login: {step} at {where(b)}")
            if time.time() - progress > 45:
                raise LighthouseError("LOGIN_TIMEOUT", f"no progress for 45 s at {where(b)}")
        except LighthouseError as ex:
            if ex.code in ("JS", "CDP"):  # page was navigating; look again next tick
                continue
            raise
    raise LighthouseError("LOGIN_TIMEOUT", f"stuck at {where(b)}")


def get_token(log, shot_dir=None):
    """-> (token, how, browser-closed). how = 'sso' or 'login'."""
    b = Browser(log)
    try:
        b.goto(APP + "/")
        how = "sso"
        end = time.time() + 40
        tok = None
        while time.time() < end:
            time.sleep(1.5)
            tok = read_token(b)
            if tok:
                break
        if not tok:
            log(f"sso: no token after 40 s at {where(b)}")
            try:
                tok = login(b, log)
                how = "login"
            except LighthouseError:
                if shot_dir:
                    p = b.screenshot(Path(shot_dir) / f"fail-{time.strftime('%Y%m%d-%H%M%S')}.png")
                    log(f"screenshot: {p}")
                    try: log("page text: " + (b.js("document.body.innerText") or "")[:400].replace("\n", " | "))
                    except Exception: pass
                raise
        log(f"auth: {how} ok")
        return tok, how
    finally:
        b.close()


def api_get(tok, path, retries=2):
    req = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + tok, "Accept": "application/json"})
    last = None
    for i in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise LighthouseError("API_AUTH", f"HTTP {e.code} on {path.split('/')[0]}")
            if e.code in (404, 400):
                raise LighthouseError("API_CHANGED", f"HTTP {e.code} on {path[:60]}")
            last = f"HTTP {e.code}"
        except Exception as e:
            last = type(e).__name__
        time.sleep(2 + 3 * i)
    raise LighthouseError("API_DOWN", f"{last} on {path[:60]}")


def fetch_weeks(tok, mondays, log):
    """mondays: list of datetime.date. -> list of raw week dicts (from the API)."""
    weeks = []
    for m in mondays:
        w = api_get(tok, f"employee/week/{m.isoformat()}T00%3A00%3A00.000Z")
        if not isinstance(w, dict) or "shifts" not in w or "days" not in (w["shifts"] or {}):
            raise LighthouseError("API_CHANGED", f"week {m} lacks shifts.days (keys: {sorted(w)[:8] if isinstance(w, dict) else type(w).__name__})")
        weeks.append(w)
        log(f"api: week {m} ok, {sum(len(d.get('shifts', [])) for d in w['shifts']['days'])} entries")
    return weeks


def order_number(tok, shift):
    try:
        r = api_get(tok, f"shiftflowsheetdetail/{shift['shiftId']}/{shift['shiftLocId']}", retries=0)
        return str((r.get("flowsheetInfo") or {}).get("orderNumber") or "")
    except Exception:
        return ""


if __name__ == "__main__" and "--check" in sys.argv:
    import datetime as dt
    if "--fresh" in sys.argv:  # real sign-in test: throwaway profile (the saved SSO profile is untouched)
        PROFILE = Path(tempfile.mkdtemp(prefix="lh-fresh-"))
    from zoneinfo import ZoneInfo
    t0 = time.time()
    lg = lambda m: print(f"[{time.time() - t0:5.1f}s] {m}")
    try:
        tok, how = get_token(lg, shot_dir=D / "logs")
        today = dt.datetime.now(ZoneInfo("America/Los_Angeles")).date()
        mon = today - dt.timedelta(days=today.weekday())
        fetch_weeks(tok, [mon], lg)
        print("CHECK OK via", how)
    except LighthouseError as e:
        print("CHECK FAILED:", e); sys.exit(1)
