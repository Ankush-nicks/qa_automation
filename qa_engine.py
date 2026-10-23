"""Reads the instructor QA export (e.g. the last 7 days), keeps each instructor's
last three days that have QA reviews, works out who is flagged, and builds the
facts (and a rule-based fallback email) for each instructor."""
from __future__ import annotations

import io
import json
import re
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
    """Returns a DataFrame with lower-case column names. Raises ValueError with a readable message."""
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

    # Daily averages: dayN_* columns (any number of days), then daily_summary_json,
    # then the mean of that day's session scores. Days without QA are skipped and
    # only the last QA_DAYS days with reviews are kept.
    by_date: dict = {}
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
    for d in {s["date"] for s in sessions if s["date"]}:
        sc = [s["score"] for s in sessions if s["date"] == d]
        by_date.setdefault(d, {"date": d, "avg": round(_mean(sc), 2), "count": len(sc)})
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
    T = S["threshold"]
    vals = [d["avg"] for d in inst["days"] if d["avg"] is not None]
    all3 = len(vals) >= QA_DAYS and all(v <= T for v in vals)
    avail = len(vals) >= 2 and all(v <= T for v in vals)
    avg3 = bool(vals) and mean(vals) <= T
    flagged = all3 if S["rule"] == "all3" else avail if S["rule"] == "available" else avg3
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
    rule = S["rule"]
    if ev["flagged"] and rule == "all3":
        reason = (f"Your daily average was {'below' if all(v < T for v in vals) else 'at or below'} {fT(T)} on each of "
                  f"your last three days with QA reviews.")
    elif ev["flagged"] and rule == "available":
        reason = f"Your daily average was at or below {fT(T)} on every recent day you had evaluated sessions."
    elif ev["flagged"]:
        reason = f"Your average across your last three days with QA reviews was {f2(ev['mean3'])}, which is at or below {fT(T)}."
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
        "plan": S["plan"], "sender": S.get("name") or "[Your Name]",
        "setup": "practical setup (lab access, batch size or materials)" if inst["cat"].get("Practice")
        else "session setup (slides, batch or schedule)",
    }


def baseline_email(inst: dict, S: dict) -> dict:
    P = build_plan(inst, S)
    L = [f"Hi {P['first_name']},", "",
         f"I’ve gone through your QA reports for {P['period']} against the updated rubrics. {P['reason']} "
         "Below is what the reports show and the plan I’d like us to agree on for next week.", "", "YOUR SCORES"]
    for d in P["days"]:
        L.append(f"- {d['date']}: no evaluated sessions" if d["avg"] is None
                 else f"- {d['date']}: {d['avg']} ({d['sessions']} session{'' if d['sessions'] == 1 else 's'})")
    if P["overall"] and P["session_count"] > 1:
        L.append(f"- Average across all {P['session_count']} sessions: {P['overall']}")
    L.append("")
    if P["working"]:
        L += ["WHAT’S WORKING", " ".join(P["working"]), ""]
    L.append("KEY INSIGHTS")
    n = 1
    for t in TYPE_ORDER:
        items = sorted([x for x in P["weak"] if x["type"] == t], key=lambda x: -x["impact"])
        if not items:
            continue
        L.append(TYPE_HEAD[t])
        for x in items:
            line = f"{n}. {x['label']}: average {f1(x['mean'])}"
            n += 1
            if x["n"] > 1:
                line += f", range {fr(x['min'])}–{fr(x['max'])}"
            if x["weight"]:
                line += f" ({round(x['weight'] * 100)}% weight)"
            line += f". {x['insight']}"
            if x["n"] >= 3 and x["very_low"] >= 2:
                line += f" {x['very_low']} of {x['n']} sessions scored 1.0 or below."
            if x is P["top"]:
                line += " This is the biggest drag on your overall score."
            L.append(line)
        L.append("")
    if P["trend"]:
        L += [P["trend"], ""]
    if P["conduct"]:
        items = [f"{c['title']} ({c['date']}{', ' + c['time'] if c['time'] else ''}) received a Professional Conduct score of {c['score']}"
                 for c in P["conduct"]]
        L += [f"One more point: {join_and(items)}, which is well below the expected level. I’d like us to review "
              f"{'those recordings' if len(items) > 1 else 'that recording'} together so we understand what happened.", ""]
    L.append("ACTION ITEMS")
    L += [f"{i}. {a}" for i, a in enumerate(P["actions"], 1)]
    L += ["", "EXPECTED OUTCOME AND NEXT STEPS",
          f"- Target: a daily average above {P['threshold']} every day next week, reaching {P['goal']}+ by {P['review']}."]
    if len(P["targets"]) == 2:
        L.append(f"- {P['targets'][0]} and {P['targets'][1]} should each reach at least {P['target_category_min']}.")
    elif P["targets"]:
        L.append(f"- {P['targets'][0]} should reach at least {P['target_category_min']}.")
    tail = f" If scores are still at or below {P['threshold']}, we’ll move to a structured improvement plan with your manager." if P["plan"] else ""
    L += [f"- We’ll review progress together on {P['review']}.{tail}", "",
          f"Please let me know if anything about the {P['setup']} is getting in the way, so we can fix that from "
          f"our side too.", "",
          "Regards,", P["sender"]]
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
