"""QA email drafts – Flask app.

Upload the 3-day instructor QA export, see who is flagged, and write each
instructor's email with an LLM (falls back to a rule-based draft)."""
from __future__ import annotations

import base64
import hmac
import json
import os
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

import llm
from qa_engine import analyze, read_export
from rubrics import RubricBook

BASE = Path(__file__).parent
RUBRIC_DIR = Path(os.getenv("RUBRIC_DIR", BASE / "rubrics"))
ACTIVE = RUBRIC_DIR / "active.xlsx"

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

    return {"threshold": fnum("threshold", 5.0), "rule": raw.get("rule") if raw.get("rule") in ("all3", "available", "avg3") else "all3",
            "goal": fnum("goal", 6.0), "call": raw.get("call") or "", "review": raw.get("review") or "",
            "name": (raw.get("name") or "").strip() or "[Your Name]", "plan": bool(raw.get("plan", True))}


@app.get("/")
def index():
    return send_from_directory(BASE / "templates", "index.html")


@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/api/status")
def status():
    _, name = rubric_book()
    return jsonify({"llm": llm.provider_info(), "rubric": name})


@app.post("/api/analyze")
def api_analyze():
    f = request.files.get("export")
    if not f or not f.filename:
        return jsonify(error="Choose the QA export file first."), 400
    try:
        S = parse_settings(json.loads(request.form.get("settings") or "{}"))
        df = read_export(f.read(), f.filename)
        out = analyze(df, S)
    except ValueError as e:
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
