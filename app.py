"""QA email drafts – Flask app.

Upload the instructor QA export (last 7 days), see who is flagged, and write each
instructor's email with an LLM (falls back to a rule-based draft)."""
from __future__ import annotations

import base64
import hmac
import json
import os
import re
import threading
from html import escape
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

import pandas as pd

import daily_email
import llm
import store as dbstore
from bundle import BundleError, prepare, raw_frames, read_bundle, upload_export_date, instructor_file
from manager_engine import Engine
from managers import load_managers, match_managers
from qa_engine import analyze, read_export
from roster import parse_roster, read_roster, split_cc
from rubrics import RubricBook

BASE = Path(__file__).parent
RUBRIC_DIR = Path(os.getenv("RUBRIC_DIR", BASE / "rubrics"))
ACTIVE = RUBRIC_DIR / "active.xlsx"
# CC'd on every email unless CC_EMAILS overrides it
DEFAULT_CC = "meka.prudhvicharan@nxtwave.co.in"
# Capability manager names and emails; each instructor's manager is found among the
# names in the export's instructor_manager_name column (column F) and CC'd
MANAGERS_FILE = Path(os.getenv("MANAGERS_FILE", BASE / "capability_managers.xlsx"))
_mgr_cache: dict = {"key": None, "map": {}}


def manager_map() -> dict:
    if not MANAGERS_FILE.exists():
        return {}
    key = MANAGERS_FILE.stat().st_mtime
    with _lock:
        if _mgr_cache["key"] != key:
            _mgr_cache.update(key=key, map=load_managers(MANAGERS_FILE))
        return _mgr_cache["map"]

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

# ---------------------------------------------------------------- password --
APP_PASSWORD = os.getenv("APP_PASSWORD", "")


@app.before_request
def require_password():
    if not APP_PASSWORD or request.path == "/healthz":
        return None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Basic "):
        try:
            _, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
            if hmac.compare_digest(pw, APP_PASSWORD):
                return None
        except Exception:  # noqa: BLE001
            pass
    return Response("Password required", 401, {"WWW-Authenticate": 'Basic realm="QA email drafts"'})


# ----------------------------------------------------------------- rubrics --
_lock = threading.Lock()
_cache: dict = {"key": None, "book": None, "name": None}


def rubric_path() -> Path | None:
    if ACTIVE.exists():
        return ACTIVE
    files = sorted(RUBRIC_DIR.glob("*.xlsx")) if RUBRIC_DIR.exists() else []
    return files[0] if files else None


def rubric_book():
    p = rubric_path()
    if not p:
        return None, None
    key = (str(p), p.stat().st_mtime)
    with _lock:
        if _cache["key"] != key:
            _cache.update(key=key, book=RubricBook(p),
                          name=(ACTIVE.with_suffix(".name").read_text().strip()
                                if p == ACTIVE and ACTIVE.with_suffix(".name").exists() else p.name))
        return _cache["book"], _cache["name"]


# ------------------------------------------------------------------ routes --
def parse_settings(raw: dict | None) -> dict:
    raw = raw or {}

    def fnum(k, d):
        try:
            return float(raw.get(k, d))
        except (TypeError, ValueError):
            return d

    return {"threshold": fnum("threshold", 5.0), "goal": fnum("goal", 6.0), "call": raw.get("call") or "", "review": raw.get("review") or "",
            "name": (raw.get("name") or "").strip() or "Central QA Team", "plan": bool(raw.get("plan", True))}


@app.get("/")
def index():
    return send_from_directory(BASE / "templates", "index.html")


@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/api/status")
def status():
    _, name = rubric_book()
    return jsonify({"llm": llm.provider_info(), "rubric": name, "cc": os.getenv("CC_EMAILS", "").strip() or DEFAULT_CC,
                    "managers": {"file": MANAGERS_FILE.name, "count": len(manager_map())},
                    "send": {k: v for k, v in send_config().items() if k not in ("url", "log_url")}})


@app.post("/api/analyze")
def api_analyze():
    f = request.files.get("export")
    if not f or not f.filename:
        return jsonify(error="Choose the QA export file first."), 400
    data = f.read()
    try:
        S = parse_settings(json.loads(request.form.get("settings") or "{}"))
        df = read_export(data, f.filename)
        out = analyze(df, S)
        mgrs = manager_map()
        rows = {str(r.get("instructor_user_id") or i): r for i, r in enumerate(df.to_dict(orient="records"))}
        for x in out["instructors"]:
            # 1) the capability manager list, matched against the names in column F
            found = match_managers(x.get("managers"), mgrs)
            if found:
                cc, external = split_cc(", ".join(m["email"] for m in found))
                x["capmgr"] = {"name": ", ".join(m["name"] for m in found), "cc": cc, "external": external,
                               "source": "list"}
                continue
            # 2) otherwise the manager emails some exports carry themselves
            r = rows.get(x["id"], {})
            cc, external = split_cc(r.get("capability_manager_email") or "")
            if cc or external:
                x["capmgr"] = {"name": (r.get("capability_manager_name") or "").strip(), "cc": cc, "external": external,
                               "source": "export"}
    except ValueError as e:
        roster = read_roster(data)  # the roster export dropped in the wrong box
        if roster is not None:
            return jsonify(roster={**parse_roster(roster), "file": f.filename})
        return jsonify(error=str(e)), 400
    except Exception as e:  # noqa: BLE001
        app.logger.exception("analyze failed")
        return jsonify(error=f"The file was read, but something in it couldn’t be processed ({e})."), 400
    out["file"] = f.filename
    return jsonify(out)


@app.post("/api/generate")
def api_generate():
    payload = request.get_json(silent=True) or {}
    inst = payload.get("instructor")
    if not isinstance(inst, dict) or "days" not in inst:
        return jsonify(error="Missing instructor data. Upload the export again."), 400
    S = parse_settings(payload.get("settings"))
    book, _ = rubric_book()
    try:
        return jsonify(llm.write_email(inst, S, book))
    except RuntimeError as e:
        return jsonify(error=str(e)), 400
    except Exception as e:  # noqa: BLE001
        app.logger.exception("generate failed")
        cause = f" Cause: {e.__cause__}" if e.__cause__ else ""
        return jsonify(error=f"The AI couldn’t write this email ({type(e).__name__}: {e}).{cause} "
                             "The rule-based draft is still available."), 502


# ------------------------------------------------------------------ sending --
# Who sends the email:
#   Resend          - when RESEND_API_KEY is set: the app sends through Resend's API
#                     (from RESEND_FROM). The Google Sheet script at SEND_API_URL (or
#                     SHEET_LOG_URL), if set, is used only for the repeat check and the log.
#   Google Sheets   - SEND_API_URL is the Apps Script web app and it sends through Gmail
#   Power Automate  - SEND_API_URL is a Power Automate flow
# SEND_MODE picks what happens:
#   dryrun (default) - nothing is sent (Resend isn't called; the sheet logs a dry-run row)
#   test             - every email goes to SEND_TEST_TO instead of the instructor, with no
#                      CC, "[TEST]" in the subject and a note of who it would have gone to
#   live             - the email goes to the instructor with the CC list
# A live email to an instructor already emailed (live) in the last SEND_REPEAT_DAYS days
# (default 7) is refused by the sheet until the page confirms it (force).
SEND_MODES = ("dryrun", "test", "live")
RESEND_URL = "https://api.resend.com/emails"
RESEND_DEFAULT_FROM = "Central QA Team <onboarding@resend.dev>"


def send_config() -> dict:
    url = os.getenv("SEND_API_URL", "").strip()
    mode = os.getenv("SEND_MODE", "").strip().lower().replace("-", "").replace("_", "")
    mode = mode if mode in SEND_MODES else "dryrun"
    test_to = ", ".join(e for e in re.split(r"[,;\s]+", os.getenv("SEND_TEST_TO", "")) if e)
    resend = bool(os.getenv("RESEND_API_KEY", "").strip())
    provider = "resend" if resend else "google" if "script.google.com" in url else "powerautomate"
    log_url = os.getenv("SHEET_LOG_URL", "").strip() or (url if "script.google.com" in url else "")
    try:
        repeat_days = max(1, int(os.getenv("SEND_REPEAT_DAYS", "7")))
    except ValueError:
        repeat_days = 7
    return {"configured": (resend or bool(url)) and (mode != "test" or bool(test_to)), "url": url, "mode": mode,
            "test_to": test_to, "provider": provider,
            "provider_name": {"resend": "Resend", "google": "Google Sheets"}.get(provider, "Power Automate"),
            "repeat_days": repeat_days, "log_url": log_url,
            "log": bool(log_url and os.getenv("SEND_SECRET", "").strip()),
            "from": (os.getenv("RESEND_FROM", "").strip() or RESEND_DEFAULT_FROM) if resend else ""}


def _emails(v) -> list[str]:
    items = v if isinstance(v, list) else re.split(r"[,;]", str(v or ""))
    return list(dict.fromkeys(str(e).strip() for e in items if str(e).strip()))


def build_message(p: dict, cfg: dict) -> dict:
    """The JSON the sender (and the sheet log) receives. Addresses are ';'-separated strings."""
    to, cc = _emails(p.get("to")), _emails(p.get("cc"))
    subject = str(p.get("subject") or "").strip()
    html, text = str(p.get("html") or ""), str(p.get("text") or "")
    dry_run = cfg["mode"] == "dryrun"
    test = cfg["mode"] == "test"
    if dry_run:
        subject = f"[DRY RUN] {subject}"
    if test:
        note = f"TEST EMAIL. In live mode this would go to: {'; '.join(to)}" + (f" | CC: {'; '.join(cc)}" if cc else "")
        html = (f'<p style="font-family:Arial,sans-serif;font-size:13px;background:#FFF4CE;border:1px solid #E8C547;'
                f'padding:8px 10px;margin:0 0 14px">{escape(note)}</p>' + html)
        text = note + "\n\n" + text
        to, cc, subject = _emails(cfg["test_to"]), [], f"[TEST] {subject}"
    return {"to": "; ".join(to), "cc": "; ".join(cc), "subject": subject, "html": html, "text": text,
            "instructor_id": str(p.get("instructor_id") or ""), "instructor_name": str(p.get("instructor_name") or ""),
            # the repeat check matches this or instructor_id; the daily email matches on its date only
            "instructor_email": "; ".join(_emails(p["instructor_email"] if "instructor_email" in p else p.get("to"))),
            "subject_area": str(p.get("subject_area") or ""), "institute": str(p.get("institute") or ""),
            "scores": str(p.get("scores") or ""), "sender_name": str(p.get("sender_name") or "Central QA Team"),
            "test": test, "dry_run": dry_run, "force": bool(p.get("force")), "repeat_days": cfg["repeat_days"],
            "secret": os.getenv("SEND_SECRET", "").strip()}


def _call_sender(req: urllib.request.Request):
    """Returns (parsed JSON or text, None) or (None, error message). Apps Script answers a
    POST with a redirect to its result, which urllib follows with a GET."""
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode(errors="replace")
        app.logger.error("sender refused: %s %s", e.code, detail)
        return None, f"refused the request ({e.code}). {detail}".strip()
    except Exception as e:  # noqa: BLE001
        app.logger.exception("sender unreachable")
        return None, f"couldn’t be reached ({e})."
    text = raw.decode(errors="replace").strip()
    if text.startswith("<"):  # a sign-in or error page instead of JSON
        return None, ("answered with a web page instead of JSON. For Google, check the web app is deployed "
                      "with ‘Execute as: Me’ and ‘Who has access: Anyone’.")
    try:
        return (json.loads(text) if text else None), None
    except ValueError:
        return text[:300], None


def _post_json(url: str, data: dict, headers: dict | None = None) -> urllib.request.Request:
    return urllib.request.Request(url, data=json.dumps(data).encode(), method="POST",
                                  headers={"Content-Type": "application/json", "User-Agent": "qa-email-drafts/1.0",
                                           **(headers or {})})


def _sheet(cfg: dict, action: str, msg: dict, **extra):
    """Calls the Google Sheet log ('check' or 'log'). Returns (answer, error)."""
    return _call_sender(_post_json(cfg["log_url"], {**msg, "action": action, **extra}))


def _send_resend(cfg: dict, msg: dict):
    """Sends through Resend. Returns (email id, None) or (None, error message)."""
    body = {"from": cfg["from"], "to": _emails(msg["to"]), "subject": msg["subject"], "html": msg["html"],
            "text": msg["text"]}
    if _emails(msg["cc"]):
        body["cc"] = _emails(msg["cc"])
    reply_to = _emails(os.getenv("RESEND_REPLY_TO", ""))
    if reply_to:
        body["reply_to"] = reply_to
    req = _post_json(RESEND_URL, body, {"Authorization": f"Bearer {os.getenv('RESEND_API_KEY', '').strip()}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            out = json.loads(r.read() or b"{}")
        return out.get("id") or "", None
    except urllib.error.HTTPError as e:
        raw = e.read()[:500].decode(errors="replace")
        try:
            detail = json.loads(raw).get("message") or raw
        except ValueError:
            detail = raw
        app.logger.error("resend refused: %s %s", e.code, raw)
        return None, f"Resend refused the email ({e.code}): {detail}"
    except Exception as e:  # noqa: BLE001
        app.logger.exception("resend unreachable")
        return None, f"Couldn’t reach Resend ({e})."


def _duplicate_reply(out: dict):
    return jsonify(error=out.get("error") or "Already emailed recently.", duplicate=True,
                   last_sent=out.get("last_sent"), repeat_days=out.get("repeat_days")), 409


def _api_send_resend(cfg: dict, msg: dict):
    log_warning = None
    if cfg["log"]:
        out, err = _sheet(cfg, "check", msg)
        if err:
            return jsonify(error=f"The Google Sheet log {err} Nothing was sent."), 502
        if isinstance(out, dict) and out.get("ok") is False:
            if out.get("duplicate"):
                return _duplicate_reply(out)
            return jsonify(error=f"Google Sheet log: {(out.get('error') or 'unexpected answer').rstrip('.')}. Nothing was sent."), 502
    if msg["dry_run"]:
        if cfg["log"]:
            _sheet(cfg, "log", msg, status="Dry run (not sent)", note=f"From: {cfg['from']}")
        return jsonify(ok=True, dry_run=True, test=False, to=msg["to"])
    email_id, err = _send_resend(cfg, msg)
    if cfg["log"]:
        status = "Failed" if err else ("Sent again (confirmed)" if msg["force"] else "Sent")
        out, log_err = _sheet(cfg, "log", msg, status=status, note=err or f"Resend id {email_id}")
        if not err and (log_err or (isinstance(out, dict) and out.get("ok") is False)):
            log_warning = "The email was sent, but it couldn’t be written to the Google Sheet log."
    if err:
        return jsonify(error=err), 502
    return jsonify(ok=True, test=msg["test"], dry_run=False, to=msg["to"], id=email_id, warning=log_warning)


@app.post("/api/send")
def api_send():
    return deliver(request.get_json(silent=True) or {})


def deliver(p: dict):
    """Sends one email through the configured sender (and its Google Sheet log). Used by page 1
    (instructor emails) and page 2 (the daily email)."""
    if not _emails(p.get("to")) or not str(p.get("subject") or "").strip() or not (p.get("html") or p.get("text")):
        return jsonify(error="The email needs a recipient, a subject and a body."), 400
    cfg = send_config()
    name = cfg["provider_name"]
    if cfg["provider"] != "resend" and not cfg["url"]:
        return jsonify(error="Sending isn’t set up yet. Use Copy email for now."), 501
    if not cfg["configured"]:
        return jsonify(error="Test mode needs SEND_TEST_TO (the address that receives test emails) on the server."), 501
    if cfg["provider"] == "google" and not os.getenv("SEND_SECRET", "").strip():
        return jsonify(error="Google Sheets sending needs SEND_SECRET on the server (the same value as the "
                             "script’s SECRET property)."), 501
    msg = build_message(p, cfg)
    if cfg["provider"] == "resend":
        return _api_send_resend(cfg, msg)
    headers = {}
    token = os.getenv("SEND_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    out, err = _call_sender(_post_json(cfg["url"], msg, headers))
    if err:
        return jsonify(error=f"{name} {err}"), 502
    if isinstance(out, dict) and out.get("ok") is False:
        if out.get("duplicate"):
            return _duplicate_reply(out)
        return jsonify(error=f"{name}: {out.get('error') or 'the email wasn’t sent.'}"), 502
    if msg["dry_run"] and not (isinstance(out, dict) and out.get("dry_run")):
        # the sender didn't confirm the dry run, so it may not know about dry runs
        app.logger.warning("dry run: sender answered without dry_run=true: %r", out)
        return jsonify(error=f"Dry run: {name} answered, but didn’t confirm it skipped sending. "
                             f"Check its setup guide."), 502
    return jsonify(ok=True, test=msg["test"], dry_run=msg["dry_run"], to=msg["to"],
                   sent_at=(out or {}).get("sent_at") if isinstance(out, dict) else None)


@app.get("/api/sent-log")
def api_sent_log():
    """Live emails sent in the last SEND_REPEAT_DAYS days, read from the Google Sheet log
    (empty when there's no sheet, e.g. Power Automate on its own)."""
    cfg = send_config()
    if not cfg["log"]:
        return jsonify(sent=[], available=False)
    qs = urllib.parse.urlencode({"action": "recent", "days": cfg["repeat_days"],
                                 "secret": os.getenv("SEND_SECRET", "").strip()})
    out, err = _call_sender(urllib.request.Request(f"{cfg['log_url']}?{qs}", method="GET",
                                                   headers={"User-Agent": "qa-email-drafts/1.0"}))
    if err or not isinstance(out, dict) or out.get("ok") is False:
        return jsonify(sent=[], available=False,
                       error=err or (out.get("error") if isinstance(out, dict) else "unexpected answer"))
    return jsonify(sent=out.get("sent") or [], available=True, days=out.get("days"))

# ---------------------------------------------------------- page 2: managers --
# The daily zip from BigQuery (qa_exports_YYYYMMDD.zip) is read in memory on every request,
# like page 1's export: nothing is stored. DAILY_TO = who gets the daily email (comma-separated);
# the Capability Managers in capability_managers.xlsx are CC'd.
def _clean(o):
    """JSON-safe: numpy types to Python, NaN to None."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if hasattr(o, "item") and not isinstance(o, (str, bytes)):
        try:
            o = o.item()
        except (ValueError, AttributeError):
            return str(o)
    if isinstance(o, float) and o != o:
        return None
    return o


def _daily_recipients() -> dict:
    to = _emails(os.getenv("DAILY_TO", ""))
    cc = [m["email"] for m in manager_map().values() if m.get("email")]
    return {"to": to, "cc": [e for e in dict.fromkeys(cc) if e not in to]}


@app.get("/manager")
def manager_page():
    return send_from_directory(BASE / "templates", "manager.html")


# Turso keeps the uploads (store.py): each upload overrides what it covers, KEEP_DAYS days are kept,
# and the page opens on the saved data. Without TURSO_DATABASE_URL nothing is stored.
try:
    STORE = dbstore.from_env()
    STORE_ERROR = ""
except dbstore.StoreError as e:
    STORE, STORE_ERROR = None, str(e)
_saved = {"version": None, "bundle": None}


def _saved_bundle():
    """The saved data, parsed once per upload. None when nothing is saved yet."""
    got = STORE.load()
    if not got:
        return None
    frames, meta = got
    if _saved["version"] != meta["version"]:
        up = pd.Timestamp(meta["updated_at"]).tz_convert("Asia/Kolkata")
        first = (f"Saved data: QA from {pd.Timestamp(meta['cutoff']):%d %b} to {pd.Timestamp(meta['latest_qa']):%d %b} "
                 f"(the last {meta.get('keep_days', '30')} days), updated {up:%d %b %H:%M} IST"
                 + (f" from {meta['last_file']}" if meta.get("last_file") else "") + ".")
        _saved["bundle"] = prepare(frames, meta.get("export_date") or None, first_check=first)
        _saved["version"] = meta["version"]
    return _saved["bundle"]


@app.get("/api/manager/saved")
def api_manager_saved():
    if not STORE:
        return jsonify(configured=False, error=STORE_ERROR)
    try:
        meta = STORE.meta()
        return jsonify(configured=True, has_data=bool(meta.get("version")), latest_qa=meta.get("latest_qa"),
                       cutoff=meta.get("cutoff"), updated_at=meta.get("updated_at"), keep_days=dbstore.keep_days(),
                       uploads=STORE.uploads(10))
    except dbstore.StoreError as e:
        return jsonify(configured=True, has_data=False, error=str(e))


@app.post("/api/manager/analyze")
def api_manager_analyze():
    """The zip (or the four CSVs) -> everything page 2 shows, plus the daily email. With the database
    set up, an upload is saved first and the analysis runs on the saved data; no upload = saved data."""
    saved = None
    try:
        ups = [(f.filename, f.read()) for f in request.files.getlist("files") if f and f.filename]
        if STORE:
            if ups:
                raw, found = raw_frames(ups)
                saved = STORE.ingest(raw, upload_export_date(ups), ", ".join(n for n, _ in ups))
            b = _saved_bundle()
            if b is None:
                raise BundleError("Nothing is saved yet. Upload today’s qa_exports_YYYYMMDD.zip.")
            if ups:
                b = {**b, "has_instructor_file": instructor_file(found) is not None}
        else:
            if not ups:
                raise BundleError("Choose the daily qa_exports_YYYYMMDD.zip first.")
            b = read_bundle(ups)
        eng = Engine({k: v.copy() for k, v in b["frames"].items()}, manager_map())
        out = eng.analyze(request.form.get("report_date") or None, request.form.get("month") or None)
    except (BundleError, ValueError) as e:
        return jsonify(error=str(e), saved=saved), 400
    except dbstore.StoreError as e:
        return jsonify(error=f"The database step failed: {e}", saved=saved), 502
    except Exception as e:  # noqa: BLE001
        app.logger.exception("manager analysis failed")
        return jsonify(error=f"The analysis failed ({e}). Check the files are today’s BigQuery export."), 500
    Y = pd.Timestamp(out["report_date"])
    ms = pd.Timestamp(out["month"] + "-01")
    out["manager_details"] = {m: eng.manager_detail(Y, m, ms) for m in out["managers"]}
    out.pop("manager_detail", None)
    link = (os.getenv("APP_URL", "").strip().rstrip("/") or request.host_url.rstrip("/")) + "/manager"
    out["email"] = {"subject": daily_email.subject(out), "preview": daily_email.render_html(out, link, preview=True),
                    "html": daily_email.render_html(out, link, preview=False),
                    "text": daily_email.render_text(out, link), **_daily_recipients()}
    out["bundle"] = {"checks": b["checks"], "ranges": b["ranges"], "export_date": b["export_date"],
                     "has_instructor_file": b["has_instructor_file"]}
    out["saved"] = saved
    out["db"] = bool(STORE)
    return Response(json.dumps(_clean(out), ensure_ascii=False), mimetype="application/json")


@app.post("/api/manager/send")
def api_manager_send():
    """Sends the daily email through page 1's sender. One live send per report date: the Sent log
    refuses a second one (within SEND_REPEAT_DAYS) until it's confirmed on the page."""
    p = request.get_json(silent=True) or {}
    day = str(p.get("report_date") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return jsonify(error="The email has no report date. Run the analysis again."), 400
    return deliver({"to": p.get("to"), "cc": p.get("cc"), "subject": p.get("subject"), "html": p.get("html"),
                    "text": p.get("text"), "instructor_id": f"daily-{day}", "instructor_name": f"Daily QA email {day}", "instructor_email": "",
                    "subject_area": "Daily email", "sender_name": "Central QA Team", "force": bool(p.get("force"))})


@app.post("/api/roster")
def api_roster():
    f = request.files.get("roster")
    if not f or not f.filename:
        return jsonify(error="Choose the roster file first."), 400
    df = read_roster(f.read())
    if df is None:
        return jsonify(error="This doesn’t look like the roster export. It needs instructor_user_id and "
                             "capability_manager_email columns."), 400
    return jsonify(roster={**parse_roster(df), "file": f.filename})


@app.post("/api/rubrics")
def api_rubrics():
    f = request.files.get("rubrics")
    if not f or not f.filename.lower().endswith((".xlsx", ".xlsm")):
        return jsonify(error="Upload the rubric workbook as an .xlsx file."), 400
    RUBRIC_DIR.mkdir(parents=True, exist_ok=True)
    tmp = RUBRIC_DIR / "upload.tmp.xlsx"
    f.save(tmp)
    try:
        book = RubricBook(tmp)
        if not book.sheets:
            raise ValueError("No rubric sheets with a 'Category Name' column were found.")
    except Exception as e:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        return jsonify(error=f"This workbook couldn’t be used: {e}"), 400
    tmp.replace(ACTIVE)
    ACTIVE.with_suffix(".name").write_text(f.filename)
    _, name = rubric_book()
    return jsonify(rubric=name, sheets=list(book.sheets))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
