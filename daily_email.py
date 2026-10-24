"""Fills the approved daily email (templates/daily_email.html) from manager_engine's output,
and writes the plain-text version that goes alongside it."""
from __future__ import annotations

import re
from html import escape, unescape
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from manager_engine import day_range, fmt_day, fmt_wday, nth

TEMPLATES = Path(__file__).resolve().parent / "templates"
_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
COLORS = {"red": "#b42318", "orange": "#b54708", "amber": "#8a6d00", "green": "#027a48"}
WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def _word(n: int) -> str:
    return WORDS[n] if 0 <= n < len(WORDS) else str(n)


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _strip(html: str) -> str:
    return unescape(re.sub(r"<br\s*/?>", " ", re.sub(r"<(?!br)[^>]+>", "", html or ""))).strip()


def subject(a: dict) -> str:
    return f"NIAT QA Daily · {fmt_wday(a['send_date'])} · Focus: {a['focus']}"


def _idle_note(idle: list[dict]) -> str:
    """idle = the campuses without a known reason (the email leaves the others out)."""
    if not idle:
        return ""
    n = len(idle)
    return (f"{_word(n).capitalize()} campus{'es have' if n > 1 else ' has'} no sessions and no known reason — "
            "confirm with the campus whether it’s exams, a holiday, or the schedule feed stopped.")


def _coverage_note(h: dict, cov: list[dict]) -> str:
    below = [r for r in cov if r["below"]]
    better = [r for r in cov if r["usual"] is not None and r["pct"] > r["usual"] + 10]
    level = "high" if (h.get("coverage") or 0) >= 90 else "lower than usual" if (h.get("coverage") or 0) < 80 else "steady"
    s = f"Coverage was {level} across the network yesterday ({h.get('coverage')}%)."
    if below:
        names = ", ".join(f"{escape(r['short'])} ({escape(r['note'].split(' — ')[0])})" if " — " in r["note"]
                          else escape(r["short"]) for r in below)
        s += f" {'Only ' if len(below) == 1 else ''}{names} {'was' if len(below) == 1 else 'were'} clearly below " \
             f"{'its' if len(below) == 1 else 'their'} usual level."
    else:
        s += " None of the lowest five was clearly below its usual level."
    if better:
        s += " " + " and ".join(escape(r["short"]) for r in better) + \
             f", usually around {round(sum(r['usual'] for r in better) / len(better))}%, {'was' if len(better) == 1 else 'were'} much better."
    return s


def _watch_note(wl: dict) -> str:
    out = []
    rows = [r for r in wl["practice"]["rows"] if r["status"] != "Removed today" and r["subject"]]
    cnt = {}
    for r in rows:
        if r["subject"]["rubrics"]:
            k = r["subject"]["rubrics"][0]["name"]
            cnt[k] = cnt.get(k, 0) + 1
    if cnt:
        top = max(cnt, key=cnt.get)
        k, n = cnt[top], len(rows)
        where = f"all {_word(n)} practice campuses" if k == n and n > 1 else f"{k} of {n} practice campuses"
        if k >= 2:
            out.append(f"<b>{escape(top)}</b> is the criterion that fell most at {where}.")
    close = []
    for t, label in (("lecture", "lectures"), ("practice", "practice")):
        c = wl[t]["close"]
        if c:
            close.append(f"{label} — " + ", ".join(
                f"{escape(x['short'])} {('%+.2f' % x['chg']).replace('-', '−')}" + (f" ({x['n']} evals)" if x["n"] < 30 else "")
                for x in c))
    if close:
        out.append("<b>Close to the line:</b> " + "; ".join(close) + ".")
    return " ".join(out)


def _cu_summary(a: dict) -> str:
    g, lg = a["connect_groups"], a["long_gap"]
    parts = []
    if g["none"]["managers"]:
        parts.append(f"<b>{_plural(g['none']['managers'], 'manager')} ({g['none']['instructors']} instructors) "
                     "recorded no connects at all</b>")
    if g["zero"]["managers"]:
        parts.append(f"{g['zero']['managers']} {'more ' if parts else ''}({g['zero']['instructors']} instructors) "
                     "scheduled connects but completed none")
    if g["half"]["managers"]:
        parts.append(f"{g['half']['managers']} completed under half or only just started")
    s = "; ".join(parts) + "." if parts else ""
    if g["normal"]["managers"]:
        s += f" {g['normal']['managers']} {'is' if g['normal']['managers'] == 1 else 'are'} using it normally."
    if lg["instructors"]:
        s += (f" {lg['instructors']} instructors ({escape(', '.join(lg['short']))}) have had no recorded connect "
              "for 2 months or ever.")
    return s.strip()


def _use_html(u: dict) -> str:
    if not u["days"]:
        seen = u.get("last_seen") or []
        if len(seen) > 1:
            return "; ".join(f"{escape(x['name'])} " + (f"last seen {fmt_day(x['last'])}" if x["last"] else "never")
                             for x in seen)
        if seen and seen[0]["last"]:
            return f"Last seen {fmt_day(seen[0]['last'])}"
        return "No platform activity"
    return f"{u['days']} of {u['of']} days<br>{u['tab']} connect-tab · {u['qa']} QA reports"


def context(a: dict, link: str, preview: bool = True, sent_time: str | None = None) -> dict:
    h = a["headline"]
    cu = a["connects"]
    idle = [dict(r, last_day=fmt_day(r["last"]) if r["last"] else None,
                 nth=f"{nth(r['days'])} day" if r["days"] is not None else None) for r in a["idle"]
            if not r.get("reason")]  # campuses with a known reason (exams, holiday...) are left out
    cov = [dict(r, note_html=escape(r["note"]).replace("below usual", "<b>below usual</b>")) for r in a["coverage"]]
    mgrs = []
    for r in cu["rows"]:
        mgrs.append(dict(r, bg=COLORS.get(r["color"], "#52606d"),
                         last_c=fmt_day(r["last_completed"]) if r["last_completed"] else "Never",
                         last_s=fmt_day(r["last_scheduled"]) if r["total"] == 0 and r["last_scheduled"]
                         and r["last_scheduled"] != r["last_completed"] else None,
                         use_html=_use_html(r["use"])))
    days = cu["days"]
    return {
        "preview": preview, "sent_time": sent_time, "subject": subject(a), "h": h,
        "net_range": day_range(h["net14"]["from"], h["net14"]["to"]),
        "act": a["act"], "report_day": fmt_wday(a["report_date"]),
        "idle": idle, "idle_note": _idle_note(idle), "idle_known": len(a["idle"]) - len(idle),
        "coverage": cov, "coverage_note": _coverage_note(h, a["coverage"]),
        "watch": [dict(a["watchlist"][t], title=title) for t, title in (("lecture", "Lectures"), ("practice", "Practice"))],
        "watch_note": _watch_note(a["watchlist"]),
        "cu_range": f"{fmt_wday(days[0]).rsplit(' ', 1)[0]} – {fmt_wday(days[-1])}" if pd.Timestamp(days[0]).month ==
                    pd.Timestamp(days[-1]).month else f"{fmt_wday(days[0])} – {fmt_wday(days[-1])}",
        "cu_range_short": day_range(days[0], days[-1]),
        "cu_days": " · ".join(str(pd.Timestamp(d).day) for d in days),
        "provisional": day_range(days[-2], days[-1]) if len(days) > 1 else fmt_day(days[-1]),
        "usual_range": day_range(cu["usual_from"], cu["usual_to"]),
        "cu_summary": _cu_summary(a), "managers": mgrs,
        "small": ", ".join(f"{s['manager']} ({s['instructors']})" for s in cu.get("small", [])),
        "link": link,
    }


def render_html(a: dict, link: str, preview: bool = True) -> str:
    return _env.get_template("daily_email.html").render(**context(a, link, preview))


def render_text(a: dict, link: str) -> str:
    c = context(a, link, preview=False)
    h = c["h"]
    L = [c["subject"], "Internal — contains instructor names.", "",
         "NIAT QA Daily — where to focus",
         f"Campus and subject figures: last 14 days ({c['net_range']}), network average {h['net14']['avg']} from "
         f"{h['net14']['n']:,} evaluations ({h['net14']['prev_avg']} the 14 days before). Instructor figures: each "
         "instructor's last 3 days with QA.", "", "ACT ON THESE TODAY"]
    for i, it in enumerate(c["act"], 1):
        L.append(f"{i}. {_strip(it['bold'])}")
        if it["line"]:
            L.append(f"   {_strip(it['line'])}")
    L += ["", f"CAMPUS ANALYSIS — {c['report_day']}",
          f"{h['taught']} of {h['campuses']} campuses taught · {h['sessions']:,} lecture/practice sessions · "
          f"{h['with_qa']:,} got QA ({h['coverage']}%) · average {h['avg']} (lecture {h['avg_lecture']}, practice "
          f"{h['avg_practice']}) vs {h['avg_p7']} over the previous 7 days.", "",
          f"1. Campuses with no sessions yesterday — {len(c['idle'])}"]
    for r in c["idle"]:
        L.append(f"   {r['short']}: last session {r['last_day'] or 'no data'}, {r['nth'] or 'no data'}")
    if c["idle_note"]:
        L.append(f"   Action: {_strip(c['idle_note'])}")
    L += ["", "2. QA coverage yesterday — lowest five"]
    for r in c["coverage"]:
        L.append(f"   {r['short']}: {r['with_qa']} of {r['sessions']} ({r['pct']}%), usual "
                 f"{str(r['usual']) + '%' if r['usual'] is not None else 'no data'}" + (f" — {r['note']}" if r["note"] else ""))
    L.append(f"   {_strip(c['coverage_note'])}")
    L += ["", "3. Watchlist — campuses losing ground against the same subjects elsewhere"]
    for t in c["watch"]:
        L.append(f"   {t['title']} — {len(t['rows'])} listed ({t['down']} of {t['compared']} campuses declined)")
        for r in t["rows"]:
            s = r["subject"]
            line = f"   - {r['short']} ({r['status']}): gap {r['gap_p']} → {r['gap']}"
            if s:
                line += f"; {s['subject']} {s['avg']} vs {s['others']} ({s['n']} evals, was {s['was']})"
                if s["rubrics"]:
                    line += "; " + ", ".join(f"{x['name']} {x['before']} → {x['now']} (others {x['others']})"
                                             for x in s["rubrics"])
            L.append(line)
    if c["watch_note"]:
        L.append(f"   {_strip(c['watch_note'])}")
    L += ["", f"MANAGER CONNECT USAGE — last 3 days ({c['cu_range']})", _strip(c["cu_summary"]),
          f"Connects per day {c['cu_days']} · done / missed / resched / not updated · usual completion · last completed"]
    for r in c["managers"]:
        L.append(f"- {r['manager']} [{r['tag']}] {r['instructors']} instructors · {' · '.join(map(str, r['per_day']))} · "
                 + (f"{r['done']} / {r['missed']} / {r['resched']} / {r['pending']}" if r["total"] else "—")
                 + f" · usual {str(r['usual']) + '%' if r['usual'] is not None else '—'} · last {r['last_c']}"
                 + f" · {_strip(r['use_html'])}")
        if r.get("note"):
            L.append(f"  {_strip(r['note'])}")
    L += ["", f"Open the full analysis: {link}", "",
          "Completed SCHEDULE evaluations of lecture and practice sessions, latest report per session, scores 0–10, "
          "weighted by evaluation count. Instructors need 3 QA days, the latest within 14 days, to be ranked. Rankings "
          "are relative; there is no fixed score cutoff. A score change after a connect is an association, not proof "
          "of effect. Follow-up actions are not yet in BigQuery."]
    return "\n".join(L)
