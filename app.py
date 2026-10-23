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
import urllib.request
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

import llm
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
                    "send": {k: v for k, v in send_config().items() if k != "url"}})


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
# Send goes to a Power Automate flow ("When an HTTP request is received" ->
# "Send an email (V2)"), see POWER_AUTOMATE.md. SEND_MODE is "test" unless set
# to "live": in test mode every email goes to SEND_TEST_TO instead of the
# instructor, with no CC, "[TEST]" in the subject and a note of who it would
# have gone to.
def send_config() -> dict:
    url = os.getenv("SEND_API_URL", "").strip()
    mode = "live" if os.getenv("SEND_MODE", "").strip().lower() == "live" else "test"
    test_to = ", ".join(e for e in re.split(r"[,;\s]+", os.getenv("SEND_TEST_TO", "")) if e)
    return {"configured": bool(url) and (mode == "live" or bool(test_to)), "url": url, "mode": mode, "test_to": test_to}


def _emails(v) -> list[str]:
    items = v if isinstance(v, list) else re.split(r"[,;]", str(v or ""))
    return list(dict.fromkeys(str(e).strip() for e in items if str(e).strip()))


def build_message(p: dict, cfg: dict) -> dict:
    """The JSON the flow receives. Addresses are ';'-separated strings, which is what
    Outlook's 'Send an email (V2)' expects in To and CC."""
    to, cc = _emails(p.get("to")), _emails(p.get("cc"))
    subject = str(p.get("subject") or "").strip()
    html, text = str(p.get("html") or ""), str(p.get("text") or "")
    test = cfg["mode"] != "live"
    if test:
        note = f"TEST EMAIL. In live mode this would go to: {'; '.join(to)}" + (f" | CC: {'; '.join(cc)}" if cc else "")
        html = (f'<p style="font-family:Arial,sans-serif;font-size:13px;background:#FFF4CE;border:1px solid #E8C547;'
                f'padding:8px 10px;margin:0 0 14px">{escape(note)}</p>' + html)
        text = note + "\n\n" + text
        to, cc, subject = _emails(cfg["test_to"]), [], f"[TEST] {subject}"
    return {"to": "; ".join(to), "cc": "; ".join(cc), "subject": subject, "html": html, "text": text,
            "instructor_id": str(p.get("instructor_id") or ""), "instructor_name": str(p.get("instructor_name") or ""),
            "test": test}


@app.post("/api/send")
def api_send():
    p = request.get_json(silent=True) or {}
    if not _emails(p.get("to")) or not str(p.get("subject") or "").strip() or not (p.get("html") or p.get("text")):
        return jsonify(error="The email needs a recipient, a subject and a body."), 400
    cfg = send_config()
    if not cfg["url"]:
        return jsonify(error="Sending isn’t set up yet. Use Copy email for now."), 501
    if not cfg["configured"]:
        return jsonify(error="Test mode needs SEND_TEST_TO (the address that receives test emails) on the server."), 501
    msg = build_message(p, cfg)
    headers = {"Content-Type": "application/json"}
    token = os.getenv("SEND_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(cfg["url"], data=json.dumps(msg).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode(errors="replace")
        app.logger.error("send failed: %s %s", e.code, detail)
        return jsonify(error=f"Power Automate refused the email ({e.code}). {detail}".strip()), 502
    except Exception as e:  # noqa: BLE001
        app.logger.exception("send failed")
        return jsonify(error=f"Couldn’t reach Power Automate ({e})."), 502
    return jsonify(ok=True, test=msg["test"], to=msg["to"])


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
