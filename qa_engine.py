"""Reads the instructor QA export (e.g. the last 7 days), keeps each instructor's
last three days that have QA reviews, works out who is flagged, and builds the
facts (and a rule-based fallback email) for each instructor."""
from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import date, timedelta
from statistics import mean as _mean

import pandas as pd

# --------------------------------------------------------------------------
# Rubric categories as they appear in the export
# --------------------------------------------------------------------------
CONDUCT = "professional conduct and ethics"
LECT_KEYS = ["curriculum alignment and content accuracy", "pedagogical quality", "language", "student engagement"]
PRAC_KEYS = ["process adherence", "clarity and communication", "pedagogical support and guidance",
             "student engagement and independence", "doubt resolution and practice session facilitation"]
TYPE_ORDER = ["Lecture", "Practice", "Invigilation", "Revision", "Other"]
TYPE_HEAD = {
    "Lecture": "Lecture-format sessions (scored on the lecture rubric):",
    "Practice": "Practice sessions (scored on the practice rubric):",
    "Invigilation": "Invigilation sessions:",
    "Revision": "Revision sessions:",
    "Other": "Other sessions:",
}
# Category weights from the updated rubric workbook
WEIGHTS = {
    "Lecture": {"curriculum alignment and content accuracy": .3, "pedagogical quality": .3, "language": .15,
                "student engagement": .15, CONDUCT: .1},
    "Practice": {"process adherence": .3, "clarity and communication": .3, "pedagogical support and guidance": .2,
                 "student engagement and independence": .1, "doubt resolution and practice session facilitation": .5},
    "Invigilation": {"invigilation and environment control": .5, CONDUCT: .5},
}
REQUIRED = ["instructor_name", "qa_reports_json", "session_rubric_evidence_json"]
QA_DAYS = 3  # number of most recent days with QA reviews used per instructor


def is_tech_like(cat: str) -> bool:
    return (cat or "").upper() in ("TECH", "APTITUDE")


def _kb():
    def a(*xs):
        return lambda c=None: list(xs)

    return {
        "curriculum alignment and content accuracy": dict(
            label="Curriculum Alignment & Content Accuracy",
            insight=lambda s, c: "The planned topics were largely not covered, or not covered in sequence."
            if s["mean"] < 3 else "Parts of the planned content were skipped or covered out of order.",
            actions=a("Before every session, go through the session plan and list the core topics or tasks, with a time allocation for each. Covering every core topic in order is what moves Curriculum Alignment into the 6+ band.",
                      "Use at least one relevant example for every concept you teach, and keep any off-topic discussion short.")),
        "pedagogical quality": dict(
            label="Pedagogical Quality",
            insight=lambda s, c: "Explanation clarity, pacing and delivery need attention, including fewer filler words."
            if is_tech_like(c) else "Explanation clarity, checks for understanding and pacing all need attention.",
            actions=lambda c: (["Open with the agenda and close with a 2-minute recap of what was covered.",
                                "Explain step by step, slow down on the harder parts, and cut filler words like “so” and “basically”."]
                               if is_tech_like(c) else
                               ["Open with the agenda and close with a 2-minute recap of what was covered.",
                                "After each concept, add one quick check, such as a poll, a quiz question or a short problem to solve."])),
        "language": dict(
            label="Language",
            insight=lambda s, c: "Teaching should stay in English, with explanations clearly anchored to what’s on screen and a patient tone when handling doubts."
            if is_tech_like(c) else "There’s room to improve on sentence length, grammar and filler words.",
            actions=lambda c: (["Deliver all explanations and doubt-resolution in English; a quick translation of a single term is fine.",
                                "Anchor explanations to the screen with specific references, like “look at line 14” or “the left column of this slide”."]
                               if is_tech_like(c) else
                               ["On language, aim for shorter sentences (around 15 words), fewer fillers, and a short definition for every new term."])),
        "student engagement": dict(
            label="Student Engagement",
            insight=lambda s, c: "Comprehension checks were limited, and students weren’t regularly asked to respond, attempt problems or explain back."
            if is_tech_like(c) else "There were few open questions, little student talk time and limited acknowledgment of student answers.",
            actions=lambda c: (["Check understanding at every key point with a real question, such as asking a student to predict an output or explain a step back, and use the answer to decide whether to re-explain.",
                                "Check in with every part of the room, not just the front rows."]
                               if is_tech_like(c) else
                               ["Ask at least 6–7 open questions per 15 minutes rather than “okay?” or “got it?”. Wait about 3 seconds for answers, and acknowledge or build on each response."])),
        "doubt resolution and practice session facilitation": dict(
            label="Doubt Resolution & Practice Facilitation",
            insight=lambda s, c: "Almost no facilitation was picked up: no agenda, doubt checks or responses to students."
            if s["mean"] <= 1 else "Explanations of student doubts and the facilitation of the practice need to be clearer and more active.",
            actions=a("Open each practice with a short agenda: the task, the time available and how to ask for help.",
                      "Run regular doubt checks during the practice and explain common blockers to the whole class.",
                      "Keep the session on the practice task and avoid long non-academic conversations.")),
        "invigilation and environment control": dict(
            label="Invigilation & Environment Control",
            insight=lambda s, c: "Baseline exam duties (start announcement, time reminders, submission instructions) and support during the exam need to be consistent.",
            actions=a("In every exam, cover the start announcement, time reminders and submission instructions, and help with technical issues without giving content help.")),
        "process adherence": dict(
            label="Process Adherence",
            insight=lambda s, c: "The expected practical flow of opening brief, clear instructions, time checks and wrap-up isn’t being followed consistently.",
            actions=a("Use a fixed structure for every practical: a 3–5 minute brief (objective, task, time available, how to submit), working time with time checks at the halfway point and 5 minutes before the end, and a 5-minute wrap-up covering common errors and the answers.")),
        "clarity and communication": dict(
            label="Clarity & Communication",
            insight=lambda s, c: "Instructions and explanations aren’t consistently clear from session to session.",
            actions=a("Give instructions in short, simple steps and confirm understanding, for example “Can someone tell me what we’re doing in step 2?”")),
        "pedagogical support and guidance": dict(
            label="Pedagogical Support & Guidance",
            insight=lambda s, c: "Students working on tasks were not being guided or given feedback when stuck."
            if s["mean"] < 1.5 else "Students aren’t getting enough help or feedback when they’re stuck.",
            actions=a("Circulate during working time and check in with every student or group at least once, starting with those who haven’t begun.",
                      "When a student is stuck, give a hint or a guiding question before giving the answer, then check that they can do the next one on their own.")),
        "student engagement and independence": dict(
            label="Student Engagement & Independence",
            insight=lambda s, c: "Students aren’t being pushed to attempt tasks on their own and then check back.",
            actions=a("Ask students to attempt each task on their own first, then share and compare answers before you explain.")),
        "revision session quality": dict(
            label="Revision Session Quality",
            insight=lambda s, c: "The revision didn’t consistently reinforce the key points.",
            actions=a("Plan revision around the topics students found hardest, and use quick questions to check recall.")),
        CONDUCT: dict(label="Professional Conduct", insight=lambda s, c: "", actions=a()),
    }


KB = _kb()


def kb(key: str) -> dict:
    if key in KB:
        return KB[key]
    return dict(label=key.title(), insight=lambda s, c: "This category scored below the expected band.", actions=lambda c=None: [])


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTHS_L = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
            "November", "December"]
WD = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def num(v):
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() in ("nan", "null", "none"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def safe_json(s, fb):
    if s is None or (isinstance(s, float) and s != s) or s == "":
        return fb
    try:
        v = json.loads(s)
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                pass
        return fb if v is None else v
    except (ValueError, TypeError):
        return fb


def norm_date(s):
    """Accepts 2026-10-23, 23-10-2026, 23/10/2026 or 10/23/2026; returns YYYY-MM-DD or None."""
    if s is None:
        return None
    t = str(s).strip()
    if not t or t.lower() in ("nan", "null", "none"):
        return None
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", t)
    if m:
        return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}"
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", t)
    if m:
        a, b = int(m[1]), int(m[2])
        y = m[3] if len(m[3]) == 4 else "20" + m[3]
        d, mo = (b, a) if (a <= 12 < b) else (a, b)
        return f"{y}-{mo:02d}-{d:02d}"
    try:
        d = pd.to_datetime(t, dayfirst=True)
        return d.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def pd_date(s):
    n = norm_date(s)
    if not n:
        return None
    try:
        y, m, d = map(int, n.split("-"))
        return date(y, m, d)
    except ValueError:
        return None


def mean(xs):
    xs = [x for x in xs if x is not None]
    return _mean(xs) if xs else None


def f2(v):
    return "–" if v is None else f"{v:.2f}"


def f1(v):
    return "–" if v is None else f"{v:.1f}"


def fr(v):
    return f"{v:.1f}" if float(v).is_integer() else f"{round(v, 2):g}"


def fT(v):
    return f"{round(float(v), 2):g}"


def d_short(s):
    d = pd_date(s)
    return f"{d.day} {MONTHS[d.month - 1]}" if d else (s or "")


def d_long(s):
    d = pd_date(s)
    return f"{WD[d.weekday()]}, {d.day} {MONTHS[d.month - 1]}" if d else (s or "")


def wday(s):
    d = pd_date(s)
    return WD[d.weekday()] if d else ""


def t_short(dt):
    m = re.search(r"T(\d{2}):(\d{2})", str(dt or ""))
    if not m:
        return ""
    h = int(m[1])
    ap = "PM" if h >= 12 else "AM"
    h = h % 12 or 12
    return f"{h}:{m[2]} {ap}"


def period_label(dates, long=False):
    """'21–23 October' for consecutive days, '17, 20 and 23 October' when days were skipped."""
    ds = sorted(d for d in (pd_date(x) for x in dates) if d)
    if len(ds) > 1 and any((b - a).days > 1 for a, b in zip(ds, ds[1:])):
        M = MONTHS_L if long else MONTHS
        if all(d.month == ds[-1].month for d in ds):
            return f"{join_and([str(d.day) for d in ds])} {M[ds[-1].month - 1]}"
        return join_and([f"{d.day} {M[d.month - 1]}" for d in ds])
    return range_label(dates, long)


def range_label(dates, long=False):
    ds = [pd_date(x) for x in dates]
    ds = [d for d in ds if d]
    if not ds:
        return "the last three days"
    a, b = ds[0], ds[-1]
    M = MONTHS_L if long else MONTHS
    if a == b:
        return f"{b.day} {M[b.month - 1]}"
    if a.month == b.month:
        return f"{a.day}–{b.day} {M[b.month - 1]}"
    return f"{a.day} {M[a.month - 1]}–{b.day} {M[b.month - 1]}"


def first_name(name):
    parts = str(name or "").strip().split()
    f = parts[0] if parts else "there"
    if "." in f:
        f = [p for p in f.split(".") if p][-1] if [p for p in f.split(".") if p] else f
    if f == f.upper():
        f = f[:1] + f[1:].lower()
    return f


def classify(keys):
    if any(k in LECT_KEYS for k in keys):
        return "Lecture"
    if any(k in PRAC_KEYS for k in keys):
        return "Practice"
    if "invigilation and environment control" in keys:
        return "Invigilation"
    if "revision session quality" in keys:
        return "Revision"
    return "Other"


def join_and(xs):
    xs = list(xs)
    if len(xs) <= 1:
        return "".join(xs)
    return ", ".join(xs[:-1]) + " and " + xs[-1]


def norm_id(s):
    return str(s or "").replace("-", "").lower()


# --------------------------------------------------------------------------
# Reading the export
# --------------------------------------------------------------------------
def read_export(data: bytes, filename: str) -> pd.DataFrame:
    """Returns a DataFrame with lower-case column names. Raises ValueError with a readable message.
    A zip (the daily qa_exports_YYYYMMDD.zip) is opened and its instructor file is used."""
    import bundle  # here, not at the top: bundle is a sibling module that also reads exports
    import onefile
    if onefile.is_onefile(data) or zipfile.is_zipfile(io.BytesIO(data)):
        # a zip or the one-file export: its instructor file (the newest copy when there are two)
        files = bundle.collect([(filename, data)])
        pick = bundle.instructor_file(files)
        if pick is None:
            pick_name = next((n for n in files if "instructor_qa" in n), None)
            if not pick_name:
                raise ValueError("This file has no instructor QA data (instructor_qa_last3_export.csv). "
                                 f"Files in it: {', '.join(sorted(files)) or 'none'}.")
            pick = files[pick_name]
        data, filename = pick, "instructor_qa_last3_export.csv"
    frames = []
    if re.search(r"\.(xlsx|xlsm|xls)$", filename or "", re.I):
        try:
            frames.append(pd.read_excel(io.BytesIO(data), dtype=str))
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"This Excel file couldn’t be read ({e}).")
    else:
        for enc in ("utf-8-sig", "cp1252"):
            for sep in (",", ";", "\t"):
                try:
                    df = pd.read_csv(io.BytesIO(data), sep=sep, dtype=str, keep_default_na=False, encoding=enc)
                except Exception:  # noqa: BLE001
                    continue
                frames.append(df)
                cols = {str(c).replace("\ufeff", "").strip().lower() for c in df.columns}
                if all(c in cols for c in REQUIRED):
                    frames = [df]
                    break
            else:
                continue
            break
    if not frames:
        raise ValueError("This file couldn’t be read as a CSV or Excel file.")
    df = frames[0]
    df.columns = [str(c).replace("\ufeff", "").strip().lower() for c in df.columns]
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        found = ", ".join(list(df.columns)[:8])
        raise ValueError(f"This file is missing {'columns' if len(missing) > 1 else 'a column'}: {', '.join(missing)}. "
                         f"Columns found: {found}. Upload the instructor QA export (e.g. last 7 days).")
    return df.fillna("")


# --------------------------------------------------------------------------
# Instructor model (plain dicts so it can travel to the browser and back)
# --------------------------------------------------------------------------
def build_instructor(r: dict, idx: int) -> dict:
    ok = lambda o: isinstance(o, dict)  # noqa: E731
    qa_raw = safe_json(r.get("qa_reports_json"), [])
    qa = [q for q in qa_raw if ok(q)] if isinstance(qa_raw, list) else []
    ev_raw = safe_json(r.get("session_rubric_evidence_json"), [])
    ev_raw = [s for s in ev_raw if ok(s)] if isinstance(ev_raw, list) else []

    evid = []
    for s in ev_raw:
        scores = {}
        for x in (s.get("rubric_scores") or []):
            if not ok(x):
                continue
            k = str(x.get("rubric_name") or "").lower().strip()
            v = num(x.get("normalized_score", x.get("original_rubric_score")))
            if k and v is not None:
                scores[k] = v
        if scores:
            evid.append({"date": norm_date(s.get("session_date")), "start": s.get("session_start_datetime") or "",
                         "title": s.get("session_title") or "Untitled session",
                         "sec": norm_id(s.get("session_section_id")), "scores": scores,
                         "type": classify(list(scores))})
    sec_map = {e["sec"]: e for e in evid if e["sec"]}
    sessions = []
    for q in qa:
        sc = num(q.get("final_score"))
        if sc is None:
            continue
        e = sec_map.get(norm_id(q.get("session_section_id")))
        sessions.append({"date": norm_date(q.get("session_date")), "title": q.get("session_title") or "Untitled session",
                         "score": sc, "start": e["start"] if e else "", "type": e["type"] if e else "Unmapped"})
    sessions.sort(key=lambda s: f"{s['date']}{s['start']}")

    # Daily averages: worked out here as the mean of that day's session scores. The
    # export's own dayN_* columns and daily_summary_json only fill in days with no
    # session scores. Days without QA are skipped and only the last QA_DAYS days
    # with reviews are kept.
    by_date: dict = {}
    for d in {s["date"] for s in sessions if s["date"]}:
        sc = [s["score"] for s in sessions if s["date"] == d]
        by_date[d] = {"date": d, "avg": round(_mean(sc), 2), "count": len(sc)}
    for i in range(1, 32):
        d, a = norm_date(r.get(f"day{i}_date")), num(r.get(f"day{i}_average"))
        if d and a is not None:
            by_date.setdefault(d, {"date": d, "avg": a, "count": int(num(r.get(f"day{i}_evaluation_count")) or 0)})
    ds_raw = safe_json(r.get("daily_summary_json"), [])
    for x in (ds_raw if isinstance(ds_raw, list) else []):
        d = norm_date(x.get("session_date")) if ok(x) else None
        a = num(x.get("daily_average")) if ok(x) else None
        if d and a is not None:
            by_date.setdefault(d, {"date": d, "avg": a, "count": int(num(x.get("evaluation_count")) or 0)})
    days = [by_date[d] for d in sorted(by_date)][-QA_DAYS:]
    keep = {d["date"] for d in days}
    for d in days:  # session count from the export can be 0 when the average came from elsewhere
        d["count"] = d["count"] or sum(1 for s in sessions if s["date"] == d["date"])
    sessions = [s for s in sessions if s["date"] in keep]
    evid = [e for e in evid if e["date"] in keep]

    cat = {}
    for e in evid:
        for k, v in e["scores"].items():
            cat.setdefault(e["type"], {}).setdefault(k, []).append(v)
    cat = {t: {k: {"mean": _mean(a), "min": min(a), "max": max(a), "n": len(a), "label": kb(k)["label"]}
               for k, a in o.items()} for t, o in cat.items()}

    conduct_low = [e for e in evid if e["scores"].get(CONDUCT) is not None and e["scores"][CONDUCT] <= 3.5]
    escalations = [e for e in evid if e["scores"].get(CONDUCT) is not None and e["scores"][CONDUCT] <= 1.0]
    map_issues = [e for e in evid if e["type"] == "Lecture" and re.search(r"practi(ce|cal)", e["title"], re.I)]
    return {
        "id": str(r.get("instructor_user_id") or idx),
        "name": (r.get("instructor_name") or "Unnamed instructor").strip(),
        "email": (r.get("instructor_mail") or "").strip(),
        "managers": (r.get("instructor_manager_name") or "").strip(),
        "institute": (r.get("institute_name") or "").strip(),
        "category": (r.get("instructor_category") or "").strip().upper(),
        "days": days, "sessions": sessions, "evid": evid, "cat": cat,
        "conduct_low": conduct_low, "escalations": escalations, "map_issues": map_issues,
    }


def evaluate(inst: dict, S: dict) -> dict:
    """Flagged when each of the last three QA days has a daily average below the threshold.
    The days are judged one by one; their overall average isn't used."""
    T = S["threshold"]
    vals = [d["avg"] for d in inst["days"] if d["avg"] is not None]
    flagged = len(vals) >= QA_DAYS and all(v < T for v in vals)
    watch = (not flagged) and len(vals) >= 2 and all(v < T + 1 for v in vals)
    return {"flagged": flagged, "watch": watch, "mean3": mean(vals)}


# --------------------------------------------------------------------------
# Plan: everything the email needs, computed once
# --------------------------------------------------------------------------
def build_plan(inst: dict, S: dict) -> dict:
    T = S["threshold"]
    ev = evaluate(inst, S)
    dates = [d["date"] for d in inst["days"] if d["date"]]
    vals = [d["avg"] for d in inst["days"] if d["avg"] is not None]
    if ev["flagged"]:
        reason = f"Your daily average was below {fT(T)} on each of your last three days with QA reviews."
    else:
        reason = "A few of your scores are below where we’d like them to be."

    sessions = inst["sessions"]
    srt = sorted(sessions, key=lambda s: -s["score"])
    best = [s for s in srt if s["score"] >= 5.5][:2]

    # What's working
    W = []
    strong = []
    for t in TYPE_ORDER:
        for k, s in (inst["cat"].get(t) or {}).items():
            if k != CONDUCT and s["mean"] >= 6:
                strong.append((t, k, s))
    strong.sort(key=lambda x: -x[2]["mean"])
    if len(best) == 2:
        W.append(f"Your strongest sessions were {best[0]['title']} ({d_short(best[0]['date'])}, {f2(best[0]['score'])}) "
                 f"and {best[1]['title']} ({d_short(best[1]['date'])}, {f2(best[1]['score'])}).")
    elif len(best) == 1:
        W.append(f"Your strongest session was {best[0]['title']} on {d_short(best[0]['date'])}, which scored {f2(best[0]['score'])}.")
    elif srt:
        W.append(f"Your highest-scoring session was {srt[0]['title']} on {d_short(srt[0]['date'])} ({f2(srt[0]['score'])}).")
    for t, k, s in strong[:2]:
        rng = f" ({fr(s['min'])}–{fr(s['max'])})" if s["n"] > 1 else ""
        W.append(f"{kb(k)['label']} is a strength in your {t.lower()} sessions, averaging {f1(s['mean'])}{rng}.")
    c_all = [e["scores"][CONDUCT] for e in inst["evid"] if e["scores"].get(CONDUCT) is not None]
    if c_all:
        if min(c_all) >= 9:
            W.append(f"Professional Conduct is {fr(min(c_all))} in your session." if len(c_all) == 1 else
                     f"Professional Conduct is {fr(min(c_all))}–{fr(max(c_all))} in every session.")
        elif sum(v >= 9 for v in c_all) / len(c_all) >= 0.7:
            W.append("Your Professional Conduct scores are strong in almost every session.")
    if best:
        W.append("That’s the baseline to build from.")

    # Weak areas
    def item(t, k, s):
        w = (WEIGHTS.get(t) or {}).get(k)
        vlow = sum(1 for e in inst["evid"] if e["type"] == t and e["scores"].get(k) is not None and e["scores"][k] <= 1)
        return {"type": t, "key": k, "label": kb(k)["label"], "mean": s["mean"], "min": s["min"], "max": s["max"],
                "n": s["n"], "weight": w, "impact": (w or 0.15) * (10 - s["mean"]), "very_low": vlow,
                "insight": kb(k)["insight"](s, inst["category"])}

    weak = [item(t, k, s) for t in TYPE_ORDER for k, s in (inst["cat"].get(t) or {}).items()
            if k != CONDUCT and s["mean"] < T]
    fallback = False
    if not weak:
        allc = [item(t, k, s) for t in TYPE_ORDER for k, s in (inst["cat"].get(t) or {}).items() if k != CONDUCT]
        allc.sort(key=lambda x: x["mean"])
        weak = allc[:2]
        fallback = True
    by_impact = sorted(weak, key=lambda x: -x["impact"])
    top = by_impact[0] if (by_impact and not fallback and len(weak) > 1) else None

    # Trend
    trend = None
    dd = [d for d in inst["days"] if d["avg"] is not None]
    if len(dd) >= 2:
        hi = max(dd, key=lambda d: d["avg"])
        lo = min(dd, key=lambda d: d["avg"])
        trend = f"Trend: {d_short(hi['date'])} was your best day ({f2(hi['avg'])}) and {d_short(lo['date'])} your lowest ({f2(lo['avg'])})."
        if srt:
            ls = srt[-1]
            trend += f" Your lowest-scoring session was {ls['title']} on {d_short(ls['date'])} ({f2(ls['score'])})."

    conduct = [{"title": e["title"], "date": d_short(e["date"]), "time": t_short(e["start"]),
                "score": fr(e["scores"][CONDUCT])} for e in inst["conduct_low"]]

    # Actions
    acts, seen = [], set()
    for x in by_impact:
        for a in kb(x["key"])["actions"](inst["category"]):
            if a not in seen:
                seen.add(a)
                acts.append(a)
    subj = {"TECH": "Tech", "ENGLISH": "English", "MATH": "Math", "APTITUDE": "Aptitude"}.get(inst["category"], "")
    acts = acts[:7] + [f"Observe one session by a high-scoring {subj + ' ' if subj else ''}trainer this week."]

    # Recordings for the coaching call
    recs, rk = [], set()
    for e in inst["conduct_low"]:
        key = e["title"] + str(e["date"])
        if key not in rk:
            rk.add(key)
            recs.append(f"{e['title']} from {d_short(e['date'])}")
    for s in sorted(sessions, key=lambda s: s["score"]):
        key = s["title"] + str(s["date"])
        if len(recs) < 2 and s["score"] < T + 1 and key not in rk:
            rk.add(key)
            recs.append(f"{s['title']} from {d_short(s['date'])}")
    recs = recs[:2]

    tops = []
    for x in by_impact:
        if x["label"] not in tops:
            tops.append(x["label"])
    tops = tops[:2]

    return {
        "first_name": first_name(inst["name"]),
        "subject": f"QA Review ({range_label(dates)}): Action plan for next week",
        "period": period_label(dates, long=True),
        "reason": reason, "flagged": ev["flagged"],
        "days": [{"date": d_short(d["date"]), "avg": None if d["avg"] is None else f2(d["avg"]), "sessions": d["count"]}
                 for d in inst["days"] if d["date"]],
        "overall": f2(mean([s["score"] for s in sessions])) if sessions else None,
        "session_count": len(sessions),
        "working": W, "weak": weak, "top": top, "fallback": fallback, "trend": trend, "conduct": conduct,
        "actions": acts, "recordings": recs, "targets": tops,
        "threshold": fT(T), "goal": fT(S["goal"]), "target_category_min": fT(max(T, 5)),
        "call": d_long(S["call"]), "call_day": wday(S["call"]), "review": d_long(S["review"]),
        "plan": S["plan"], "sender": S.get("name") or "Central QA Team",
        "setup": "practical setup (lab access, batch size or materials)" if inst["cat"].get("Practice")
        else "session setup (slides, batch or schedule)",
    }


# --------------------------------------------------------------------------
# Email playbook: per category, the wording used in the email
#   area  – one line for "Key Areas for Improvement"
#   title – the action-plan heading;  plan – its bullet points
#   focus – a short phrase used in sentences ("strengthen practice facilitation")
#   strength / strength_text – used in "What's Working Well"
# --------------------------------------------------------------------------
def _pb():
    same = lambda v: (lambda c: v)  # noqa: E731
    tech = lambda a, b: (lambda c: a if is_tech_like(c) else b)  # noqa: E731
    return {
        "curriculum alignment and content accuracy": dict(
            focus="curriculum coverage", strength="Content accuracy",
            strength_text="demonstrating a strong foundation in subject knowledge and curriculum delivery",
            area=same("Cover every core topic in the session plan, in sequence, with relevant examples and minimal off-topic discussion."),
            title="Cover the session plan fully",
            plan=same(["Before each session, list the core topics or tasks from the session plan, with a time allocation for each.",
                       "Cover every core topic in the planned order, and check the plan again before closing the session.",
                       "Use at least one relevant example for every concept taught.",
                       "Keep off-topic discussion short and bring the class back to the plan."])),
        "pedagogical quality": dict(
            focus="explanation clarity", strength="Clear explanations",
            strength_text="showing clear and well-paced explanations",
            area=tech("Focus on clearer explanations, appropriate pacing, step-by-step demonstrations and reducing filler words.",
                      "Focus on clearer explanations, appropriate pacing and regular checks for understanding."),
            title="Strengthen explanation and delivery",
            plan=tech(["Break complex concepts into smaller, easy-to-follow steps.",
                       "Adjust pacing when explaining difficult topics and allow students sufficient time to follow along.",
                       "Reduce filler words such as “so” and “basically” to improve clarity.",
                       "Close each session with a two-minute recap of key concepts and takeaways."],
                      ["Open with the agenda and break each concept into smaller, easy-to-follow steps.",
                       "Adjust pacing on harder topics and allow students sufficient time to follow along.",
                       "After each concept, add a quick check such as a poll, a quiz question or a short problem.",
                       "Close each session with a two-minute recap of key concepts and takeaways."])),
        "language": dict(
            focus="clear language", strength="Language",
            strength_text="reflecting clear and accessible communication",
            area=tech("Deliver explanations in English, anchor them clearly to what is on screen and keep a patient tone while handling doubts.",
                      "Use shorter sentences, accurate grammar and fewer filler words, with a short definition for every new term."),
            title="Sharpen language and communication",
            plan=tech(["Deliver all explanations and doubt resolution in English; a quick translation of a single term is fine.",
                       "Anchor explanations to the screen with specific references, such as “look at line 14”.",
                       "Keep a patient, encouraging tone when responding to doubts."],
                      ["Keep sentences short (around 15 words) and grammar accurate.",
                       "Reduce filler words and pause instead.",
                       "Give a short definition for every new term introduced."])),
        "student engagement": dict(
            focus="student engagement", strength="Student engagement",
            strength_text="showing active involvement of students in the session",
            area=tech("Encourage active participation through regular comprehension checks, problem-solving questions and opportunities for students to explain their understanding.",
                      "Encourage active participation through open questions, more student talk time and acknowledgement of student responses."),
            title="Improve student engagement",
            plan=tech(["Ask students to predict outputs, attempt problems or explain steps in their own words.",
                       "Use student responses to identify gaps in understanding and revisit concepts where needed.",
                       "Engage students across the classroom rather than focusing only on the front rows."],
                      ["Ask at least 6–7 open questions every 15 minutes instead of “okay?” or “got it?”.",
                       "Wait about three seconds for answers, then acknowledge or build on each response.",
                       "Engage students across the classroom rather than focusing only on the front rows."])),
        "doubt resolution and practice session facilitation": dict(
            focus="practice facilitation", strength="Practice facilitation",
            strength_text="showing effective support for students during practice",
            area=same("Introduce structured guidance, regular doubt checks and proactive support to help students overcome blockers during practice sessions."),
            title="Structure practice sessions effectively",
            plan=same(["Begin each session with a clear agenda covering the task, available time and how students can seek help.",
                       "Conduct regular doubt checks and proactively identify students who may be struggling.",
                       "Address common blockers with the entire class where appropriate.",
                       "Keep discussions focused on the learning objectives and assigned practice tasks."])),
        "invigilation and environment control": dict(
            focus="exam invigilation", strength="Invigilation",
            strength_text="showing a well-run exam environment",
            area=same("Cover the start announcement, time reminders and submission instructions in every exam, and support students with technical issues."),
            title="Follow the invigilation routine",
            plan=same(["Make a start announcement covering the duration, the rules and how to submit.",
                       "Give time reminders at the halfway point and 10 minutes before the end.",
                       "Share clear submission instructions before the exam closes.",
                       "Help with technical issues promptly, without giving content help."])),
        "process adherence": dict(
            focus="process adherence", strength="Process adherence",
            strength_text="showing a well-structured practical flow",
            area=same("Follow the expected practical flow: an opening brief, clear instructions, time checks and a wrap-up."),
            title="Follow a fixed practical structure",
            plan=same(["Open with a 3–5 minute brief covering the objective, the task, the time available and how to submit.",
                       "Give time checks at the halfway point and 5 minutes before the end.",
                       "Close with a 5-minute wrap-up of common errors and the answers."])),
        "clarity and communication": dict(
            focus="clear instructions", strength="Clarity and communication",
            strength_text="showing clear instructions and explanations",
            area=same("Give instructions and explanations in short, simple steps and confirm that students have understood them."),
            title="Make instructions clearer",
            plan=same(["Give instructions in short, simple steps.",
                       "Confirm understanding by asking a student to repeat a step, for example “What are we doing in step 2?”.",
                       "Keep the key steps on screen so students can refer back to them."])),
        "pedagogical support and guidance": dict(
            focus="guidance during tasks", strength="Guidance during tasks",
            strength_text="showing helpful support for students while they work",
            area=same("Guide students while they work, offering hints and feedback when they are stuck."),
            title="Guide students during tasks",
            plan=same(["Check in with every student or group at least once during working time, starting with those who haven’t begun.",
                       "When a student is stuck, give a hint or a guiding question before giving the answer.",
                       "Check that the student can then complete the next step on their own."])),
        "student engagement and independence": dict(
            focus="student independence", strength="Student independence",
            strength_text="showing students working confidently on their own",
            area=same("Encourage students to attempt tasks on their own first, then share and compare answers."),
            title="Build student independence",
            plan=same(["Ask students to attempt each task on their own before explaining it.",
                       "Have students share and compare answers, then address the gaps.",
                       "Reduce prompts gradually as students become more confident."])),
        "revision session quality": dict(
            focus="revision quality", strength="Revision sessions",
            strength_text="showing well-targeted revision",
            area=same("Plan revision around the topics students found hardest and check recall with quick questions."),
            title="Make revision sessions targeted",
            plan=same(["Plan revision around the topics students found hardest.",
                       "Use quick questions to check recall of each key point.",
                       "Close with a short summary of what to practise next."])),
    }


PB = _pb()


def pb(key: str) -> dict:
    if key in PB:
        return PB[key]
    label = kb(key)["label"]
    return dict(focus=label.lower(), strength=label, strength_text="a solid base to build on",
                area=lambda c: f"Bring {label} up to the expected band.", title=f"Improve {label}",
                plan=lambda c: [])


NUM_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def nword(n: int) -> str:
    return NUM_WORDS[n] if 0 <= n < len(NUM_WORDS) else str(n)


def d_full(s):
    """'7 October'"""
    d = pd_date(s)
    return f"{d.day} {MONTHS_L[d.month - 1]}" if d else (s or "")


def d_full_wd(s):
    """'Friday, 30 October'"""
    d = pd_date(s)
    return f"{WD[d.weekday()]}, {d.day} {MONTHS_L[d.month - 1]}" if d else (s or "")


def _plural(n: int, word: str) -> str:
    return word if n == 1 else word + "s"


def baseline_email(inst: dict, S: dict) -> dict:
    """The rule-based email, in the Central QA Team format. Light Markdown:
    '### ' headings, '**bold**', '- ' bullets and '1. ' numbered lists."""
    P = build_plan(inst, S)
    cat = inst["category"]
    days = [d for d in inst["days"] if d["date"]]
    sessions = inst["sessions"]
    weak = sorted(P["weak"], key=lambda x: -x["impact"])[:3]
    vals = [d["avg"] for d in days if d["avg"] is not None]
    improved = len(vals) >= 2 and vals[-1] > vals[-2]

    # What's working well
    good = []
    strong = sorted(((t, k, s) for t in TYPE_ORDER for k, s in (inst["cat"].get(t) or {}).items()
                     if k != CONDUCT and s["mean"] >= 6), key=lambda x: -x[2]["mean"])
    for t, k, s in strong[:2]:
        p = pb(k)
        good.append(f"**{p['strength']}:** {t} sessions averaged {f1(s['mean'])} in {kb(k)['label']}, {p['strength_text']}.")
    best = sorted([s for s in sessions if s["score"] >= 5.5], key=lambda s: -s["score"])[:2]
    if len(best) == 2:
        same_type = best[0]["type"] == best[1]["type"] and best[0]["type"] in TYPE_ORDER
        label = f"Strong {best[0]['type'].lower()} sessions" if same_type else "Strongest sessions"
        if best[0]["title"] == best[1]["title"] and best[0]["date"] == best[1]["date"]:
            txt = (f"The {best[0]['title']} sessions on {d_full(best[0]['date'])} scored "
                   f"{f2(best[0]['score'])} and {f2(best[1]['score'])}")
        else:
            txt = (f"{best[0]['title']} ({d_full(best[0]['date'])}, {f2(best[0]['score'])}) and "
                   f"{best[1]['title']} ({d_full(best[1]['date'])}, {f2(best[1]['score'])}) were the strongest sessions")
        good.append(f"**{label}:** {txt}, providing positive examples to build upon.")
    elif best:
        good.append(f"**Strongest session:** {best[0]['title']} on {d_full(best[0]['date'])} scored "
                    f"{f2(best[0]['score'])}, providing a positive example to build upon.")
    if improved:
        good.append(f"**Progress and potential:** The improvement observed on {d_full(days[-1]['date'])} shows an "
                    "opportunity to strengthen overall performance through more structured delivery and active "
                    "student engagement.")
    c_all = [e["scores"][CONDUCT] for e in inst["evid"] if e["scores"].get(CONDUCT) is not None]
    if c_all and min(c_all) >= 9:
        good.append("**Professional conduct:** Professional Conduct scored "
                    + (f"{fr(c_all[0])} in the session." if len(c_all) == 1
                       else f"{fr(min(c_all))}–{fr(max(c_all))} in every session."))

    L = [f"Hi {P['first_name']},", "",
         f"The QA reports for {P['period']} have been reviewed against the updated rubrics. "
         + ("The review highlights several strengths to build on, along with a few areas where focused improvements "
            if good else "The review highlights a few areas where focused improvements ")
         + "can help strengthen classroom delivery and the student learning experience.", ""]

    # Performance overview
    L += ["### Performance Overview", ""]
    for d in days:
        L.append(f"- **{d_full(d['date'])}:** no evaluated sessions" if d["avg"] is None else
                 f"- **{d_full(d['date'])}:** {f2(d['avg'])} ({d['count']} {_plural(d['count'], 'session')})")
    if len(sessions) > 1:
        L.append(f"- **Overall average:** {P['overall']} across {len(sessions)} sessions")
    if len(vals) >= 2:
        scored = [d for d in days if d["avg"] is not None]
        last, hi = scored[-1], max(scored, key=lambda d: d["avg"])
        if improved:
            L += ["", f"The improvement on {d_full(last['date'])}, with a daily average of {f2(last['avg'])}, is "
                      "encouraging. The focus now is to build on this progress and achieve greater consistency "
                      "across sessions."]
        elif hi is not last:
            L += ["", f"{d_full(hi['date'])} was the strongest day, with a daily average of {f2(hi['avg'])}. The focus "
                      "now is to bring every day up to that level and achieve greater consistency across sessions."]
    L.append("")

    if good:
        L += ["### What’s Working Well", ""] + [f"- {g}" for g in good] + [""]

    # Key areas
    conduct = inst["conduct_low"]
    L += ["### Key Areas for Improvement", ""]
    if weak:
        L += [f"The reports highlight {nword(len(weak))} priority {_plural(len(weak), 'area')}:", ""]
        L += [f"{i}. **{x['label']}:** {pb(x['key'])['area'](cat)}" for i, x in enumerate(weak, 1)]
        L.append("")
    conduct_scores = sorted({fr(e["scores"][CONDUCT]) for e in conduct}, key=float)
    if conduct:
        n = len(conduct)
        kind = (f"{conduct[0]['type'].lower()} " if len({e["type"] for e in conduct}) == 1
                and conduct[0]["type"] in TYPE_ORDER else "")
        dates = join_and([d_full(d) for d in sorted({e["date"] for e in conduct if e["date"]})])
        what = (f"{'a ' if n == 1 else ''}Professional Conduct {_plural(n, 'score')} of {conduct_scores[0]}"
                if len(conduct_scores) == 1 else f"low Professional Conduct scores ({join_and(conduct_scores)})")
        L += [f"{'Additionally, ' + nword(n) if n > 1 else 'Additionally, one'} {kind}{_plural(n, 'session')} on "
              f"{dates} received {what}. The {_plural(n, 'recording')} will be reviewed to understand the context "
              "and determine the appropriate next steps.", ""]

    # Action plan
    L += ["### Action Plan for Next Week", ""]
    n = 0
    for x in weak:
        p = pb(x["key"])
        bullets = p["plan"](cat)
        if bullets:
            n += 1
            L += [f"**{n}. {p['title']}**"] + [f"- {b}" for b in bullets] + [""]
    subj = {"TECH": "Tech", "ENGLISH": "English", "MATH": "Math", "APTITUDE": "Aptitude"}.get(cat, "")
    foci = [pb(x["key"])["focus"] for x in weak]
    n += 1
    L += [f"**{n}. Learn from effective practices**",
          f"- Observe one session conducted by a high-performing {subj + ' ' if subj else ''}trainer this week.",
          "- Identify and apply two or three effective techniques"
          + (f", particularly around {join_and(foci)}." if foci else "."), ""]
    n += 1
    L.append(f"**{n}. Review feedback and track progress**")
    if conduct:
        k = len(conduct)
        L.append(f"- Review the {_plural(k, 'recording')} of the {nword(k)} {_plural(k, 'session')} with "
                 f"Professional Conduct {_plural(k, 'score')} of {join_and(conduct_scores)} to understand the "
                 "circumstances and identify the necessary next steps.")
    L += ["- Refer to daily QA feedback to track progress and make timely adjustments.", ""]

    # Outcomes
    review = d_full_wd(S["review"])
    L += ["### Expected Outcomes and Follow-Up", "",
          "The focus for next week is to improve consistency"
          + (f", strengthen {join_and(foci[:2])}" if foci else "")
          + " and create a more engaging learning experience.", "",
          f"- **Daily QA score:** Above {P['threshold']} each day next week, progressing towards {P['goal']}+ by {review}."]
    tops = P["targets"]
    if tops:
        L.append(f"- **Priority parameters:** Achieve a score of at least {P['target_category_min']} in "
                 + (f"both {tops[0]} and {tops[1]}." if len(tops) == 2 else f"{tops[0]}."))
    L += [f"- **Progress review:** Progress against these targets will be reviewed on {review}, to assess "
          "improvement and determine the next steps.", ""]
    if P["plan"]:
        L += [f"If scores remain below {P['threshold']}, a structured improvement plan will be initiated in "
              "coordination with the reporting manager, with clear milestones and follow-up.", ""]
    base = pb(strong[0][1])["strength"].lower() if strong else None
    L += [(f"With a strong foundation in {base} and focused attention" if base else "With focused attention")
          + " on the action items above, there is a clear opportunity to improve session effectiveness and achieve "
            "more consistent QA outcomes.", "",
          "Regards,", f"**{P['sender']}**"]
    return {"subject": P["subject"], "body": "\n".join(L)}


def internal_notes(inst: dict) -> list[str]:
    N = []
    for e in inst["escalations"]:
        others = [v for k, v in e["scores"].items() if k != CONDUCT]
        all_zero = bool(others) and all(v <= 1 for v in others)
        N.append(f"Escalation check: {e['title']} ({d_short(e['date'])}) scored {fr(e['scores'][CONDUCT])} on Professional "
                 f"Conduct. The rubric requires escalation at 1.0 or below."
                 + (" Every other category is also near 0, which usually points to an empty recording or the instructor "
                    "not being present, so check the recording first." if all_zero else ""))
    if inst["map_issues"]:
        cnt = {}
        for e in inst["map_issues"]:
            k = f"{e['title']} ({d_short(e['date'])})"
            cnt[k] = cnt.get(k, 0) + 1
        names = [f"{k} ×{c}" if c > 1 else k for k, c in cnt.items()]
        many = len(inst["map_issues"]) > 1
        N.append(f"Rubric mapping: {join_and(names)} {'look like practice sessions but were' if many else 'looks like a practice session but was'} "
                 "scored on the lecture rubric. Curriculum Alignment can drop close to 0 when there are no slides to cover, "
                 "so confirm the mapping before sending.")
    return N


def default_dates(insts: list[dict]) -> tuple[str, str]:
    ds = sorted(d["date"] for x in insts for d in x["days"] if d["date"])
    base = pd_date(ds[-1]) if ds else date.today()
    base = base or date.today()
    mon = base + timedelta(days=1)
    while mon.weekday() != 0:
        mon += timedelta(days=1)
    return mon.isoformat(), (mon + timedelta(days=4)).isoformat()


def allowed_numbers(inst: dict, S: dict) -> set[str]:
    """Every decimal the email is allowed to contain (used to check the AI draft)."""
    vals = [S["threshold"], S["goal"], max(S["threshold"], 5)]
    vals += [d["avg"] for d in inst["days"] if d["avg"] is not None]
    vals += [s["score"] for s in inst["sessions"]]
    ss = [s["score"] for s in inst["sessions"]]
    if ss:
        vals.append(mean(ss))
    for o in inst["cat"].values():
        for s in o.values():
            vals += [s["mean"], s["min"], s["max"]]
    for e in inst["evid"]:
        vals += list(e["scores"].values())
    vals += [mean(d for d in [x["avg"] for x in inst["days"]] if d is not None) or 0]
    out = set()
    for v in vals:
        if v is None:
            continue
        for s in (f"{v:.2f}", f"{v:.1f}", fr(v), fT(v), f"{v:.0f}.0"):
            out.add(s)
    out.update({"1.0", "0.0", "10.0", "2.0"})
    return out


def analyze(df: pd.DataFrame, S: dict) -> dict:
    insts, skipped = [], 0
    for i, r in enumerate(df.to_dict(orient="records")):
        try:
            x = build_instructor(r, i)
            if x["name"]:
                insts.append(x)
        except Exception:  # noqa: BLE001
            skipped += 1
    if not insts:
        raise ValueError("No instructor rows could be read from this file.")
    if not S.get("call") or not S.get("review"):
        call, review = default_dates(insts)
        S["call"], S["review"] = S.get("call") or call, S.get("review") or review
    out = []
    for x in insts:
        e = evaluate(x, S)
        out.append({**x, **e, "notes": internal_notes(x), "baseline": baseline_email(x, S)})
    return {"instructors": out, "skipped": skipped, "settings": S}
