"""The University Drill-down: reads the 13-file BigQuery export (niat_drilldown_export.zip) into the
data the page and the daily email use, and works out the email's summary.

Shape of the data (the page's script reads it as is):
  meta            run_date (latest class day in file 02), from, files (export_status.csv)
  cols            column names of the packed rows below
  universities    one per university in file 01, most classes first; each with its sections
                  (days, attendance, feedback, course plan, tomorrow), issue classes, rubric sums,
                  weekly QA, tickets and the semester schedule by status
  instructors     file 11: active instructors, plus inactive ones who still have a QA score
  managers_weekly file 12
  tickets_no_uni  file 13 rows without a university
  qa_weeks        the weeks (Mondays) in file 07
  caps            the capability managers in file 12
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import date, timedelta

FILES = ["01_universities", "02_section_day", "03_attention_classes", "04_subject_status",
         "05_curriculum_progress", "06_next_day", "07_qa_weekly", "08_rubric", "09_students_daily",
         "10_feedback_daily", "11_instructors", "12_managers_weekly", "13_tickets_month"]

COLS = {
    "days": ["day", "classes", "completed", "partially_completed", "pending", "cancelled", "skipped", "other_status",
             "lecture", "practice", "exam", "break_type", "other_type", "offline", "online", "merged", "first_start",
             "last_end", "span_mins", "class_mins", "with_recording_info", "recorded", "no_recording", "evaluated",
             "recorded_not_evaluated", "qa_sum", "qa_n", "perf_sum", "perf_n", "grooming_sum", "grooming_n", "zero_qa",
             "report_known", "report_opened", "flagged", "malpractice", "instructors"],
    "att": ["day", "records", "submitted", "present", "absent", "not_submitted", "students", "students_present",
            "by_face_recognition", "by_manual"],
    "fb": ["day", "subject", "responses", "students", "understanding_sum", "understanding_n", "teaching_sum",
           "teaching_n", "guidance_sum", "guidance_n", "positive", "neutral", "negative"],
    "cur": ["semester", "subject", "course", "planned", "due_by_today", "completed", "partially_completed",
            "pending_after_date"],
    "attn": ["section_name", "day", "start_time", "end_time", "session_title", "session_type", "session_status",
             "instructor_name", "recording_flow", "qa_rating", "no_recording", "recorded_not_evaluated",
             "pending_after_date", "partially_completed", "malpractice", "flagged", "report_not_opened", "zero_qa"],
    "qaw": ["week", "subject", "lp_sessions", "lp_no_recording", "lp_evaluated", "qa_sum", "qa_n", "zero_qa"],
    "tickets": ["assigned_team", "ticket_status", "category", "sub_category", "tickets", "sla_breach_statuses",
                "oldest_open_created", "instructor_ids"],
}
# columns that stay text even when they look like numbers
TEXT = {"institute_id", "institute_name", "institute_type", "location", "section_id", "section_name", "batch_name",
        "semester", "day", "first_start", "last_end", "start_time", "end_time", "session_title", "session_type",
        "session_status", "instructor_name", "recording_flow", "subject", "semester_course_id", "course",
        "section_id_one", "week", "rubric", "instructor_user_id", "category", "home_institute", "status", "managers",
        "taught_at", "qa_last3_days", "connect_statuses_this_week", "last_completed_connect", "manager_id", "manager",
        "assigned_team", "ticket_status", "sub_category", "sla_breach_statuses", "oldest_open_created",
        "instructor_ids", "file_name", "error", "rows", "first_start"}


class DrillError(ValueError):
    pass


def _val(col: str, v: str):
    if v is None or v == "":
        return None
    if col in TEXT:
        return v
    lv = v.lower()
    if lv in ("true", "false"):
        return lv == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def read_zip(data: bytes) -> dict[str, list[dict]]:
    """{file stem: rows} for the 13 files (and export_status). Folders inside the zip are fine."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise DrillError("This isn’t a zip file. Upload niat_drilldown_export.zip.") from e
    names = {re.sub(r"\.csv$", "", n.rsplit("/", 1)[-1].lower()): n for n in z.namelist() if n.lower().endswith(".csv")}
    missing = [f for f in FILES if f not in names]
    if missing:
        raise DrillError("This zip isn’t the drill-down export: it has no " + ", ".join(f + ".csv" for f in missing[:4])
                         + (f" and {len(missing) - 4} more" if len(missing) > 4 else "") + ".")
    out = {}
    for stem in FILES + ["export_status"]:
        if stem not in names:
            continue
        txt = z.read(names[stem]).decode("utf-8-sig")
        out[stem] = [{k: _val(k, v) for k, v in r.items()} for r in csv.DictReader(io.StringIO(txt))]
    return out


def is_drill_zip(data: bytes) -> bool:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return False
    stems = {n.rsplit("/", 1)[-1].lower() for n in z.namelist()}
    return "02_section_day.csv" in stems and "11_instructors.csv" in stems


def _pack(kind: str, r: dict) -> list:
    return [r.get(c) for c in COLS[kind]]


def build(data: bytes) -> dict:
    F = read_zip(data)
    days = F["02_section_day"]
    if not days:
        raise DrillError("02_section_day.csv is empty, so there is no run date.")
    # the export's run date: the query reads run_date - 13 days to run_date, so it is the first day of the
    # window + 13 (an export made before the day's first class has no class rows for run_date itself)
    first = min(r["day"] for f in ("02_section_day", "09_students_daily", "10_feedback_daily") for r in F[f] if r.get("day"))
    run = max(max(r["day"] for r in days), (date.fromisoformat(first) + timedelta(days=13)).isoformat())
    unis, order = {}, []
    for r in F["01_universities"]:
        u = {"id": r["institute_id"], "name": r["institute_name"], "type": r["institute_type"], "location": r["location"],
             "sections_total": r["sections"], "batches": r["batches"], "students": r["total_students"],
             "attn": [], "rub": [], "qaw": [], "tickets": [], "status": [], "sections": [], "_sec": {}, "_lbl": {}}
        unis[r["institute_id"]] = u
        order.append(u)
    by_name = {u["name"]: u for u in order}

    def uni(iid, name):
        u = unis.get(iid) if iid else None
        return u or by_name.get(name)

    def section(u, sid, label, batch=None, sem=None):
        s = u["_sec"].get(sid) if sid else None
        if s is None:
            s = {"id": sid, "label": label, "batch": batch, "semester": sem, "days": [], "att": [], "fb": [], "cur": [],
                 "next": None}
            if sid:
                u["_sec"][sid] = s
            u["sections"].append(s)
        s["batch"] = s["batch"] or batch
        s["semester"] = s["semester"] or sem
        return s

    for r in days:
        u = uni(r["institute_id"], r["institute_name"])
        if u:
            section(u, r["section_id"], r["section_name"], r["batch_name"], r["semester"])["days"].append(_pack("days", r))
    for r in F["09_students_daily"]:
        u = uni(r["institute_id"], r["institute_name"])
        if u:
            section(u, r["section_id"], r["section_name"])["att"].append(_pack("att", r))
    for r in F["05_curriculum_progress"]:
        u = uni(r["institute_id"], r["institute_name"])
        if u:
            section(u, r["section_id"], r["section_name"], sem=r["semester"])["cur"].append(_pack("cur", r))
    for r in F["04_subject_status"]:
        u = uni(r["institute_id"], r["institute_name"])
        if u:
            u["status"].append([r["section_id"], r["section_name"], r["semester"], r["subject"], r["session_status"],
                                r["due"], r["sessions"] or 0])
    for r in F["06_next_day"]:
        u = uni(r["institute_id"], r["institute_name"])
        s = u and u["_sec"].get(r["section_id_one"])
        if s:
            s["next"] = {"classes": r["classes_tomorrow"], "first": r["first_start"]}
    for r in F["03_attention_classes"]:
        u = by_name.get(r["institute_name"])
        if u:
            u["attn"].append(_pack("attn", r))
    for r in F["08_rubric"]:
        u = by_name.get(r["institute_name"])
        if u:
            u["rub"].append([r["subject"], r["rubric"], r["score_sum"], r["n"]])
    for r in F["07_qa_weekly"]:
        u = uni(r["institute_id"], r["institute_name"])
        if u:
            u["qaw"].append(_pack("qaw", r))
    no_uni = []
    for r in F["13_tickets_month"]:
        u = by_name.get(r["institute_name"])
        if u:
            u["tickets"].append(_pack("tickets", r))
        else:
            no_uni.append(r)

    for u in order:
        for s in u["sections"]:
            s["days"].sort(key=lambda d: d[0])
            s["att"].sort(key=lambda d: d[0])
        u["classes14"] = sum(d[1] or 0 for s in u["sections"] for d in s["days"])
        u["sections"].sort(key=lambda s: -sum(d[1] or 0 for d in s["days"]))
        for s in u["sections"]:
            if s["label"]:
                u["_lbl"].setdefault(s["label"], []).append(s)
    # feedback has only the section label; when two batches share a label, it goes to the one
    # that had classes that day (the most classes, if both did)
    for r in F["10_feedback_daily"]:
        u = by_name.get(r["institute_name"])
        cands = u["_lbl"].get(r["section_name"]) if u else None
        if cands:
            s = max(cands, key=lambda s: sum(d[1] or 0 for d in s["days"] if d[0] == r["day"]))
            s["fb"].append(_pack("fb", r))
    for u in order:
        del u["_sec"], u["_lbl"]
    order.sort(key=lambda u: (-u["classes14"], u["name"] or ""))

    inst = [i for i in F["11_instructors"] if i.get("instructor_user_id")
            and (i.get("status") == "ACTIVE" or i.get("qa_last3_avg") is not None)]
    mw = F["12_managers_weekly"]
    caps = list(dict.fromkeys(r["manager"] for r in mw if r.get("manager")))
    return {
        "cols": COLS,
        "meta": {"run_date": run, "from": (date.fromisoformat(run) - timedelta(days=13)).isoformat(), "source": "BigQuery drill-down export",
                 "files": [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in F.get("export_status", [])]},
        "universities": order, "instructors": inst, "managers_weekly": mw, "tickets_no_uni": no_uni,
        "qa_weeks": sorted({r["week"] for r in F["07_qa_weekly"] if r.get("week")}), "caps": caps,
    }


# ------------------------------------------------------------------ campus reasons
def last_session(u: dict) -> str | None:
    d = [x[0] for s in u["sections"] for x in s["days"] if x[1]]
    return max(d) if d else None


def apply_reasons(D: dict, reasons: dict) -> dict:
    """{university: reason} for the reasons that still apply: one applies while the university's
    last class is still the one it was set against, so it clears itself when classes resume."""
    out = {}
    for u in D["universities"]:
        r = reasons.get(u["name"])
        if r and (r.get("last_session") or None) == last_session(u):
            out[u["name"]] = {"reason": r["reason"], "note": r.get("note") or "", "set_on": r.get("set_on")}
    return out


# ------------------------------------------------------------------ the email's summary
OPEN_T = {"Yet To Pick", "In Progress", "Awaiting Instructors Response", "Awaiting Instructor Response", "Re-open"}
CATS = ["TECH", "APTITUDE", "MATH", "ENGLISH"]
RN = {"curriculum alignment and content accuracy": "Curriculum alignment", "pedagogical quality": "Pedagogical quality",
      "language": "Language", "student engagement": "Student engagement",
      "professional conduct and ethics": "Professional conduct",
      "doubt resolution and practice session facilitation": "Doubt resolution",
      "student engagement and independence": "Engagement & independence"}
LEC = list(RN)[:5]
PRA = list(RN)[5:7]


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _avg(s, n):
    return round(s / n, 2) if n else None


def _pct(a, b):
    return round(1000 * a / b) / 10 if b else None


def _sch(rows):
    t = {"total": 0, "sched": 0, "done": 0, "gap": 0, "later": 0, "nodate": 0}
    for r in rows:
        n, st, due = r[6] or 0, r[4], r[5]
        done = st in ("COMPLETED", "PARTIALLY_COMPLETED")
        t["total"] += n
        if not st:
            t["nodate"] += n
        elif due:
            t["sched"] += n
            t["done" if done else "gap"] += n
        else:
            t["later"] += n
            if done:
                t["done"] += n
    return t


def _col(kind, row, name):
    return row[COLS[kind].index(name)]


def summary(D: dict, reasons: dict | None = None) -> dict:
    """What the daily email says, from the same rules as the page."""
    reasons = reasons or {}
    run = D["meta"]["run_date"]
    nxt = (_d(run) + timedelta(days=1)).isoformat()
    thiswk = _d(run) - timedelta(days=_d(run).weekday())
    lastwk, prevwk = (thiswk - timedelta(days=7)).isoformat(), (thiswk - timedelta(days=14)).isoformat()
    D_ = COLS["days"].index
    tot = {k: 0 for k in ("classes", "completed", "recorded", "with_recording_info", "qa_sum", "qa_n")}
    att = {"present": 0, "submitted": 0}
    unis, live_n, act_n, plan_n = [], 0, 0, 0
    for u in D["universities"]:
        act = [s for s in u["sections"] if s["days"]]
        for s in u["sections"]:
            for d in s["days"]:
                for k in tot:
                    tot[k] += d[D_(k)] or 0
            for a in s["att"]:
                att["present"] += _col("att", a, "present") or 0
                att["submitted"] += _col("att", a, "submitted") or 0
        if not act:
            continue
        live_n += 1
        plan = [s for s in act if s["next"]]
        act_n += len(act)
        plan_n += len(plan)
        today = any(d[0] == run and d[1] for s in u["sections"] for d in s["days"])
        unis.append({"name": u["name"], "act": len(act), "plan": len(plan), "today": today, "last": last_session(u),
                     "sch": _sch(u["status"]), "u": u})

    no_today = []
    for o in sorted([o for o in unis if not o["today"]], key=lambda o: o["last"] or ""):
        rs = reasons.get(o["name"])
        no_today.append({"name": o["name"], "last": o["last"], "plan": o["plan"], "act": o["act"],
                         "reason": rs["reason"] if rs else None, "note": rs["note"] if rs else ""})
    nothing = [o["name"] for o in unis if not o["plan"]]
    some = sorted([o for o in unis if o["plan"] and o["plan"] < o["act"]], key=lambda o: o["plan"] / o["act"])

    # not marked done, by university and semester (current semester of each batch)
    lag = {}  # every university, also those without classes lately (as the page does)
    for u in D["universities"]:
        groups = {}
        for r in u["status"]:
            groups.setdefault(r[0] or r[1], []).append(r)
        for rows in groups.values():
            c = _sch(rows)
            g = lag.setdefault((u["name"], rows[0][2] or "No semester"),
                               {"uni": u["name"], "sem": rows[0][2] or "No semester", "secs": 0, "gs": 0, "gap": 0, "sched": 0})
            g["secs"] += 1
            g["sched"] += c["sched"]
            if c["gap"]:
                g["gs"] += 1
                g["gap"] += c["gap"]
    lag = sorted([g for g in lag.values() if g["gap"]], key=lambda g: -g["gap"])

    inst = D["instructors"]
    caps = {c.lower() for c in D["caps"]}

    def capl(i):
        c = [m for m in (i.get("managers") or "").split(", ") if m.lower() in caps]
        return ", ".join(c) if c else "no capability manager"

    low = sorted([i for i in inst if i.get("status") == "ACTIVE" and i.get("qa_last3_avg") is not None
                  and i.get("qa_last3_n") and i["qa_last3_avg"] < 5],
                 key=lambda i: (i["qa_last3_avg"], -(i["qa_last3_n"] or 0)))
    low = [{"name": i.get("instructor_name") or "Unnamed", "qa": i["qa_last3_avg"], "n": i["qa_last3_n"],
            "subject": i.get("category"), "manager": capl(i), "taught_at": i.get("taught_at")} for i in low]

    # subjects: last full week against the week before
    def qa_week(cat, w):
        s = n = 0
        for u in D["universities"]:
            for r in u["qaw"]:
                if r[0] == w and (cat == "ALL" or r[1] == cat):
                    s += _col("qaw", r, "qa_sum") or 0
                    n += _col("qaw", r, "qa_n") or 0
        return _avg(s, n), n

    def rub(cat):
        m = {}
        for u in D["universities"]:
            for sub, rb, s, n in u["rub"]:
                if cat == "ALL" or sub == cat:
                    a = m.setdefault(rb, [0, 0])
                    a[0] += s or 0
                    a[1] += n or 0
        return {k: _avg(*v) for k, v in m.items() if v[1]}

    def weakest(r, ks):
        ks = [k for k in ks if r.get(k) is not None]
        return min(ks, key=lambda k: r[k]) if ks else None

    subjects = []
    for cat in CATS:
        now, n = qa_week(cat, lastwk)
        prev, pn = qa_week(cat, prevwk)
        r = rub(cat)
        wl, wp = weakest(r, LEC), weakest(r, PRA)
        c = _sch([x for u in D["universities"] for x in u["status"] if x[3] == cat])
        subjects.append({"cat": cat.title(), "now": now, "n": n, "prev": prev,
                         "change": round(now - prev, 2) if now is not None and prev is not None else None,
                         "weak_lec": f"{RN[wl]} {r[wl]}" if wl else None, "weak_pra": f"{RN[wp]} {r[wp]}" if wp else None,
                         "gap": c["gap"], "done_pct": _pct(c["done"], c["total"])})

    # managers: 1:1 coverage last full week
    by = {}
    for r in D["managers_weekly"]:
        if not r.get("manager"):
            continue
        m = by.setdefault(r["manager"], {"name": r["manager"], "cat": r.get("category"), "active": r.get("active_instructors") or 0,
                                         "weeks": {}})
        if r.get("week"):
            m["weeks"][r["week"]] = r
    mgrs = []
    for name in D["caps"]:
        m = by.get(name)
        if not m:
            continue
        w, t = m["weeks"].get(lastwk) or {}, m["weeks"].get(thiswk.isoformat()) or {}
        con = w.get("instructors_connected") or 0
        mgrs.append({"name": name, "cat": m["cat"], "active": m["active"], "connected": con,
                     "coverage": _pct(con, m["active"]) if m["active"] else None,
                     "qa": _avg(w.get("connect_qa_sum") or 0, w.get("connect_qa_n") or 0),
                     "missed": (w.get("missed") or 0) + (w.get("reschedule_required") or 0),
                     "this_week": t.get("instructors_connected") or 0, "booked": t.get("connects") or 0,
                     "idle": not any((x.get("connects") or 0) for x in m["weeks"].values())})
    mgrs.sort(key=lambda m: (m["coverage"] if m["coverage"] is not None else 999))
    act_i = [i for i in inst if i.get("status") == "ACTIVE"]
    no_cap = sum(1 for i in act_i if capl(i) == "no capability manager")
    tot_active = sum(m["active"] for m in mgrs)
    tot_con = sum(m["connected"] for m in mgrs)

    return {
        "run_date": run, "next_date": nxt, "from": D["meta"]["from"], "last_week": lastwk, "this_week": thiswk.isoformat(),
        "figs": {"sections": act_n, "universities": live_n, "all_universities": len(D["universities"]),
                 "classes": tot["classes"], "completed": tot["completed"],
                 "recorded": _pct(tot["recorded"], tot["with_recording_info"]),
                 "qa": _avg(tot["qa_sum"], tot["qa_n"]), "qa_n": tot["qa_n"],
                 "attendance": _pct(att["present"], att["submitted"]), "planned": plan_n},
        # run before the day's first class: every university looks idle, so the email says that instead
        "early": bool(unis) and not any(o["today"] for o in unis),
        "no_today": no_today, "nothing_planned": nothing,
        "some_planned": [{"name": o["name"], "plan": o["plan"], "act": o["act"]} for o in some],
        "not_done": lag, "not_done_total": sum(g["gap"] for g in lag),
        "low_qa": low, "subjects": subjects,
        "managers": mgrs, "managers_idle": [m["name"] for m in mgrs if m["idle"]],
        "mgr_totals": {"active": tot_active, "connected": tot_con, "coverage": _pct(tot_con, tot_active), "no_cap": no_cap},
    }
