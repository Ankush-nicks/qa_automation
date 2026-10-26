"""The daily email for the University Drill-down: fills templates/drill_email.html from
drilldown.summary() and writes the plain-text version that goes alongside it."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES = Path(__file__).resolve().parent / "templates"
_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def day(s: str | None) -> str:
    if not s:
        return "no data"
    d = date.fromisoformat(s)
    return f"{d.day} {MON[d.month - 1]}"


def wday(s: str | None) -> str:
    return f"{WD[date.fromisoformat(s).weekday()]} {day(s)}" if s else "no data"


def subject(S: dict) -> str:
    return f"NIAT University Drill-down · {wday(S['run_date'])}"


def _n(v) -> str:
    return "—" if v is None else f"{v:,}" if isinstance(v, int) else str(v)


def context(S: dict, link: str, preview: bool) -> dict:
    f = S["figs"]
    no_today = [dict(r, last_day=wday(r["last"])) for r in S["no_today"] if not r["reason"]]
    known = ", ".join(f"{r['name']} ({r['reason']}{': ' + r['note'] if r['note'] else ''})"
                      for r in S["no_today"] if r["reason"])
    down = [r["cat"] for r in S["subjects"] if r["change"] is not None and r["change"] < 0]
    subject_note = (("QA went down in " + ", ".join(down) + ". ") if down else "QA held or rose in every subject. ") + \
        "Average QA of recorded, evaluated lecture and practice sessions, weeks starting Monday. Rubrics: last 8 weeks. " \
        "Not marked done = this semester’s sessions dated on or before " + day(S["run_date"]) + \
        " that are not Completed or Partly completed."
    return {
        "preview": preview, "subject": subject(S), "s": S, "f": f, "mt": S["mgr_totals"], "link": link,
        "run_day": wday(S["run_date"]), "run_short": day(S["run_date"]), "next_day": wday(S["next_date"]),
        "range14": f"{day(S['from'])} – {day(S['run_date'])}", "last_wk": day(S["last_week"]),
        "figs": [("Sections teaching", _n(f["sections"]), f"in {f['universities']} universities"),
                 ("Classes, 14 days", _n(f["classes"]), f"{f['completed']:,} completed"),
                 ("Recorded", f"{_n(f['recorded'])}%", "of classes with recording info"),
                 ("Avg QA score", _n(f["qa"]), f"n={f['qa_n']:,}"),
                 ("Attendance present", f"{_n(f['attendance'])}%", "of submitted records"),
                 (f"Planned for {day(S['next_date'])}", f"{f['planned']}/{f['sections']}", "sections")],
        "no_today": no_today, "known": known, "subject_note": subject_note,
    }


def render_html(S: dict, link: str, preview: bool = True) -> str:
    return _env.get_template("drill_email.html").render(**context(S, link, preview))


def render_text(S: dict, link: str) -> str:
    c = context(S, link, False)
    f = S["figs"]
    L = [c["subject"], "Internal — contains instructor names.", "",
         f"NIAT University Drill-down — {c['run_day']}",
         f"Last 14 days ({c['range14']}): {f['sections']} sections teaching in {f['universities']} of "
         f"{f['all_universities']} universities · {f['classes']:,} classes ({f['completed']:,} completed) · recorded "
         f"{_n(f['recorded'])}% · avg QA {_n(f['qa'])} (n={f['qa_n']:,}) · attendance {_n(f['attendance'])}% · planned for "
         f"{day(S['next_date'])}: {f['planned']}/{f['sections']} sections.", "",
         "1. UNIVERSITIES — NEEDS ATTENTION"]
    if S.get("early"):
        L.append(f"No classes are recorded for {c['run_day']} yet: the export was run before the day's classes, so "
                 "today's and tomorrow's checks aren't meaningful. Run the export after classes for them.")
    elif c["no_today"]:
        L.append(f"No session today ({c['run_day']}): {len(c['no_today'])}")
        L += [f"  - {r['name']}: last session {r['last_day']}, {r['plan']} of {r['act']} sections planned for "
              f"{day(S['next_date'])}" for r in c["no_today"]]
    else:
        L.append("Every university with classes in the last 14 days has a session today.")
    if c["known"]:
        L.append(f"  Not listed, reason known: {c['known']}")
    if S["nothing_planned"] and not S.get("early"):
        L.append(f"Nothing planned for {c['next_day']}: {', '.join(S['nothing_planned'])}")
    if S["some_planned"] and not S.get("early"):
        L.append(f"Only some sections planned for {c['next_day']}: " +
                 ", ".join(f"{r['name']} {r['plan']} of {r['act']}" for r in S["some_planned"]))
    if S["not_done"]:
        L.append(f"{S['not_done_total']:,} sessions scheduled till {c['run_short']} are not marked done:")
        L += [f"  - {g['uni']}, {g['sem']}: {g['gap']:,} of {g['sched']:,} ({g['gs']} of {g['secs']} sections)"
              for g in S["not_done"][:10]]
    if S["low_qa"]:
        L.append(f"{len(S['low_qa'])} active instructors below 5 QA (last 3 QA days):")
        L += [f"  - {i['name']} {i['qa']} (n={i['n']}), {(i['subject'] or '').title()}, {i['manager']}"
              for i in S["low_qa"][:15]]
    L += ["", f"2. SUBJECTS — QA in the week of {c['last_wk']} vs the week before"]
    for r in S["subjects"]:
        ch = "—" if r["change"] is None else f"{r['change']:+.2f}"
        L.append(f"  - {r['cat']}: {_n(r['now'])} (was {_n(r['prev'])}, {ch}); weakest lecture rubric "
                 f"{r['weak_lec'] or '—'}; practice {r['weak_pra'] or '—'}; {r['gap']:,} not marked done")
    mt = S["mgr_totals"]
    L += ["", f"3. MANAGERS — 1:1s in the week of {c['last_wk']}",
          f"{mt['connected']} of {mt['active']} active instructors had a completed 1:1 ({_n(mt['coverage'])}%)."]
    if S["managers_idle"]:
        L.append(f"No 1:1s at all in 8 weeks: {', '.join(S['managers_idle'])}.")
    L += [f"  - {m['name']}: {'no 1:1s' if m['idle'] else m['connected']} of {m['active']} "
          f"({_n(m['coverage'])}%), avg 1:1 QA {_n(m['qa'])}, this week {m['this_week']} of {m['booked']} booked"
          for m in S["managers"]]
    L += ["", f"Open the drill-down: {link}"]
    return "\n".join(L)
