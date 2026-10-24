"""Runs the QA export queries in Google Cloud Shell and writes the CSVs for the app.

    python3 run_exports.py            # all five files
    python3 run_exports.py 5          # only the page-1 instructor file
    python3 run_exports.py 1 2 3 4    # only the page-2 (manager analysis) files

Output: ~/qa_exports/YYYYMMDD/*.csv, ~/qa_exports/latest/*.csv (same names every day)
        and ~/qa_exports/qa_exports_YYYYMMDD.zip
Set QA_EXPORT_DIR to write somewhere else (e.g. your project folder on Windows).
Download from Cloud Shell with:  cloudshell download ~/qa_exports/qa_exports_YYYYMMDD.zip
"""
import csv, datetime as dt, io, os, shutil, subprocess, sys, zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATASET = "kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace"
EVENTS = "niat_instructor_platform_cloud_watch_events_data"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
today = dt.datetime.now(IST).date()
# Where the CSVs go: QA_EXPORT_DIR if set, otherwise ~/qa_exports
BASE = Path(os.environ.get("QA_EXPORT_DIR", Path.home() / "qa_exports")).expanduser()
OUT = BASE / f"{today:%Y%m%d}"
LATEST = BASE / "latest"          # same file names every day, overwritten on each run
BQ = shutil.which("bq") or shutil.which("bq.cmd") or "bq"   # bq.cmd on Windows

JOBS = {
    "1": ("1_qa_daily.sql", "qa_daily.csv"),
    "2": ("2_manager_map.sql", "manager_map.csv"),
    "3": ("3_manager_connects.sql", "manager_connects.csv"),
    "4": ("4_manager_activity.sql", "manager_activity.csv"),
    "5": ("5_instructor_qa_last3_export.sql", "instructor_qa_last3_export.csv"),
}


def bq(sql: str) -> list[dict]:
    # the query goes in on stdin: no command-line length limit (Windows) and leading "--" comments are safe
    proj = [f"--project_id={os.environ['QA_BQ_PROJECT']}"] if os.environ.get("QA_BQ_PROJECT") else []
    r = subprocess.run([BQ, "--quiet", *proj, "query", "--nouse_legacy_sql",
                        "--format=csv", "--max_rows=5000000"], input=sql, capture_output=True, text=True,
                       encoding="utf-8")
    lines = [l for l in r.stdout.splitlines(True) if not l.startswith("Waiting on")]
    if r.returncode != 0 or not lines:
        raise RuntimeError((r.stderr or r.stdout)[-1500:])
    return list(csv.DictReader(io.StringIO("".join(lines))))


def fill_activity(sql: str) -> str:
    """Finds the user, event and time columns of the platform events view."""
    cols = {c["column_name"].lower(): (c["column_name"], c["data_type"].upper()) for c in bq(
        f"SELECT column_name, data_type FROM `{DATASET}`.INFORMATION_SCHEMA.COLUMNS "
        f"WHERE table_name = '{EVENTS}'")}
    pick = lambda names: next((cols[n][0] for n in names if n in cols), None)  # noqa: E731
    user = pick(["user_id", "instructor_user_id", "manager_user_id", "actor_user_id", "userid", "user"])
    event = pick(["event_name", "event", "event_type", "name", "action"])
    times = [(n, t) for n, t in cols.values() if t in ("TIMESTAMP", "DATETIME", "DATE")]
    times.sort(key=lambda c: next((i for i, k in enumerate(["event_time", "event_timestamp", "timestamp",
                                   "created", "time", "date"]) if k in c[0].lower()), 9))
    if not (user and event and times):
        raise RuntimeError(f"Could not find user/event/time columns in {EVENTS}. Columns: "
                           + ", ".join(f"{n} {t}" for n, t in cols.values()))
    tcol, ttype = times[0]
    day = {"TIMESTAMP": f"DATE({tcol}, 'Asia/Kolkata')", "DATETIME": f"DATE({tcol})", "DATE": tcol}[ttype]
    print(f"   events view: user={user}, event={event}, day={day}")
    return sql.replace("{USER_COL}", user).replace("{EVENT_COL}", event).replace("{DAY_EXPR}", day)


def main():
    keys = sys.argv[1:] or list(JOBS)
    OUT.mkdir(parents=True, exist_ok=True)
    done = []
    failed = []
    for k in keys:
        sql_file, out_name = JOBS[k]
        sql = (HERE / sql_file).read_text()
        print(f"{sql_file} ...", flush=True)
        if k == "4":
            sql = fill_activity(sql)
            (HERE / "4_manager_activity.filled.sql").write_text(sql)
        try:
            rows = bq(sql)
        except RuntimeError as e:
            print("   FAILED:", e)
            failed.append(sql_file)
            continue
        path = OUT / out_name
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else [])
            w.writeheader()
            w.writerows(rows)
        print(f"   {len(rows):,} rows -> {path}")
        check(k, rows)
        done.append(path)
    if done:
        LATEST.mkdir(parents=True, exist_ok=True)
        for p in done:
            shutil.copyfile(p, LATEST / p.name)
        print(f"\nLatest copies: {LATEST}")
        z = BASE / f"qa_exports_{today:%Y%m%d}.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(LATEST.glob("*.csv")):   # always all five latest files, not only this run's
                zf.write(p, p.name)
        print(f"ZIP: {z}\nDownload (Cloud Shell): cloudshell download {z}")
    if failed:
        print("\nFAILED:", ", ".join(failed))
        sys.exit(1)


def check(k, rows):
    """A few sanity numbers to read before uploading."""
    if not rows:
        print("   WARNING: no rows")
        return
    if k == "1":
        ds = sorted({r["session_date"] for r in rows})
        scored = sum(int(r["scored"] or 0) for r in rows)
        print(f"   dates {ds[0]} .. {ds[-1]} | campuses {len({r['campus_name'] for r in rows})} | scored reports {scored:,}")
    elif k == "2":
        print(f"   instructors {len({r['instructor_user_id'] for r in rows}):,} | managers "
              f"{len({r['manager_id'] for r in rows if r['manager_id']})} | instructors without manager "
              f"{len({r['instructor_user_id'] for r in rows if not r['manager_id']})}")
    elif k == "3":
        ds = sorted(r["scheduled_date"] for r in rows)
        st = {}
        for r in rows:
            st[r["status"]] = st.get(r["status"], 0) + 1
        print(f"   dates {ds[0]} .. {ds[-1]} | status {st} | not in directory "
              f"{sum(r['manager_in_directory'] == 'false' for r in rows)}")
    elif k == "4":
        print(f"   managers {len({r['manager_id'] for r in rows})} | events {len({r['event_name'] for r in rows})}")
    elif k == "5":
        days = {}
        for r in rows:
            days[r["evaluated_days"]] = days.get(r["evaluated_days"], 0) + 1
        print(f"   instructors {len(rows)} | QA days {dict(sorted(days.items()))} | alerts "
              f"{sum(r['low_score_alert_3_consecutive_days'] == 'true' for r in rows)} | no manager email "
              f"{sum(not r['capability_manager_email'] for r in rows)}")


if __name__ == "__main__":
    main()
