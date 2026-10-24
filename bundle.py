"""Reads the daily BigQuery export (qa_exports_YYYYMMDD.zip, or the loose CSVs) for the
manager analysis page: finds each file by name, checks its columns and dates, and returns
DataFrames plus a list of checks to show on the page."""
from __future__ import annotations

import io
import re
import zipfile

import pandas as pd

RUBRICS = ("content", "pedagogy", "language", "engagement", "conduct", "doubt", "process", "clarity", "support",
           "independence")

# file key -> (name inside the zip, required columns, date column)
FILES = {
    "qa_daily": ("qa_daily.csv",
                 ["session_date", "campus_name", "subject", "session_type", "instructor_user_id", "sessions_scheduled",
                  "sessions_with_completed_qa", "scored", "score_sum", "zero_scores"]
                 + [f"{r}_{s}" for r in RUBRICS for s in ("n", "sum")], "session_date"),
    "manager_map": ("manager_map.csv",
                    ["instructor_user_id", "instructor_name", "instructor_status", "home_campus", "manager_id",
                     "manager_name", "manager_email", "is_org_wide", "is_capability_manager_pick"], None),
    "manager_connects": ("manager_connects.csv",
                         ["connect_id", "scheduled_date", "status", "manager_id", "manager_name",
                          "manager_in_directory", "instructor_user_id"], "scheduled_date"),
    "manager_activity": ("manager_activity.csv", ["d", "manager_id", "event_name", "events"], "d"),
}
INSTRUCTOR_FILE = "instructor_qa_last3_export.csv"  # page 1's file, also in the zip

NUMERIC = ["sessions_scheduled", "sessions_with_qa_report", "sessions_with_completed_qa", "scored", "score_sum",
           "zero_scores"] + [f"{r}_{s}" for r in RUBRICS for s in ("n", "sum")]


class BundleError(ValueError):
    pass


def _members(data: bytes, filename: str) -> dict[str, bytes]:
    """{lower-case base name: bytes} from a zip, or the single CSV itself."""
    if zipfile.is_zipfile(io.BytesIO(data)):
        out = {}
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for info in z.infolist():
                base = info.filename.replace("\\", "/").rsplit("/", 1)[-1].lower()
                if base.endswith(".csv") and not base.startswith("._"):
                    out[base] = z.read(info)
        return out
    return {(filename or "").replace("\\", "/").rsplit("/", 1)[-1].lower(): data}


def collect(uploads: list[tuple[str, bytes]]) -> dict[str, bytes]:
    """All CSVs from the uploaded files (zips are opened), by lower-case base name."""
    files: dict[str, bytes] = {}
    for name, data in uploads:
        files.update(_members(data, name))
    return files


def _find(files: dict[str, bytes], wanted: str) -> bytes | None:
    if wanted in files:
        return files[wanted]
    stem = wanted[:-4]
    for name, data in files.items():  # e.g. "qa_daily (1).csv" or "20261024_qa_daily.csv"
        if re.search(rf"(^|[^a-z]){re.escape(stem)}([^a-z]|$)", name[:-4]):
            return data
    return None


def instructor_file(files: dict[str, bytes]) -> bytes | None:
    """Page 1's instructor export inside the bundle, if there."""
    return _find(files, INSTRUCTOR_FILE)


def export_date(filename: str) -> str | None:
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", filename or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _bool(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.lower().isin(["true", "1", "yes", "t"])


def raw_frames(uploads: list[tuple[str, bytes]]) -> tuple[dict, dict[str, bytes]]:
    """The four CSVs as all-text DataFrames (lower-case columns), plus every CSV found.
    Raises BundleError (with every problem listed) when a required file or column is missing."""
    files = collect(uploads)
    frames, problems = {}, []
    for key, (fname, cols, _) in FILES.items():
        raw = _find(files, fname)
        if raw is None:
            problems.append(f"{fname} is missing")
            continue
        try:
            df = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False, encoding="utf-8-sig")
        except Exception as e:  # noqa: BLE001
            problems.append(f"{fname} couldn’t be read ({e})")
            continue
        df.columns = [str(c).strip().lower() for c in df.columns]
        missing = [c for c in cols if c not in df.columns]
        if missing:
            problems.append(f"{fname} is missing {'columns' if len(missing) > 1 else 'a column'}: {', '.join(missing)}")
            continue
        frames[key] = df
    if problems:
        found = ", ".join(sorted(files)) or "no CSV files"
        raise BundleError("The upload can’t be used: " + "; ".join(problems) + f". Found: {found}. "
                          "Upload the daily qa_exports_YYYYMMDD.zip.")
    return frames, files


def upload_export_date(uploads: list[tuple[str, bytes]]) -> str | None:
    return next((export_date(n) for n, _ in uploads if export_date(n)), None)


def read_bundle(uploads: list[tuple[str, bytes]]) -> dict:
    """Returns {"frames": {key: DataFrame}, "checks": [...], "ranges": {...}, "export_date": str|None}."""
    frames, files = raw_frames(uploads)
    out = prepare(frames, upload_export_date(uploads))
    out["has_instructor_file"] = instructor_file(files) is not None
    return out


def prepare(frames: dict, exp: str | None = None, first_check: str | None = None) -> dict:
    """Parses the all-text frames (from an upload or the database) and lists the checks."""
    frames = {k: v.copy() for k, v in frames.items()}
    q = frames["qa_daily"]
    q["session_date"] = pd.to_datetime(q["session_date"], errors="coerce")
    for c in NUMERIC:
        if c in q.columns:
            q[c] = pd.to_numeric(q[c], errors="coerce").fillna(0)
    q["instructor_user_id"] = q["instructor_user_id"].str.strip().str.lower()
    frames["qa_daily"] = q[q.session_date.notna()].copy()

    m = frames["manager_map"]
    for c in ("is_org_wide", "is_capability_manager_pick"):
        m[c] = _bool(m[c])
    for c in ("instructor_user_id", "manager_id"):
        m[c] = m[c].str.strip().str.lower()

    c = frames["manager_connects"]
    c["scheduled_date"] = pd.to_datetime(c["scheduled_date"], errors="coerce")
    c["manager_in_directory"] = _bool(c["manager_in_directory"])
    for col in ("instructor_user_id", "manager_id"):
        c[col] = c[col].str.strip().str.lower()
    frames["manager_connects"] = c[c.scheduled_date.notna()].copy()

    a = frames["manager_activity"]
    a["d"] = pd.to_datetime(a["d"], errors="coerce")
    a["events"] = pd.to_numeric(a["events"], errors="coerce").fillna(0)
    a["manager_id"] = a["manager_id"].str.strip().str.lower()
    frames["manager_activity"] = a[a.d.notna()].copy()

    ranges = {}
    for key, (fname, _, dcol) in FILES.items():
        if dcol:
            s = frames[key][dcol]
            ranges[key] = {"from": s.min().strftime("%Y-%m-%d") if len(s) else None,
                           "to": s.max().strftime("%Y-%m-%d") if len(s) else None, "rows": int(len(s))}
        else:
            ranges[key] = {"rows": int(len(frames[key]))}

    checks = [{"level": "ok", "text": first_check or "All four files are present with the expected columns."}]
    checks.append({"level": "info", "text": "Dates: QA " + _rng(ranges["qa_daily"]) + " · connects "
                   + _rng(ranges["manager_connects"]) + " · platform use " + _rng(ranges["manager_activity"])
                   + (f" · exported {pd.Timestamp(exp):%d %b %Y}" if exp else "")})
    orphan = c[~c.manager_in_directory]
    if len(orphan):
        checks.append({"level": "warn", "text": f"{len(orphan):,} connects belong to {orphan.manager_id.nunique()} "
                       "manager ids that aren’t in the directory; they’re left out of the manager table."})
    return {"frames": frames, "checks": checks, "ranges": ranges, "export_date": exp, "has_instructor_file": False}


def _rng(r: dict) -> str:
    if not r.get("from"):
        return "no rows"
    f, t = pd.Timestamp(r["from"]), pd.Timestamp(r["to"])
    return f"{f:%d %b} – {t:%d %b}"
