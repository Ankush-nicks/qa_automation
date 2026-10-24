"""Manager analysis (page 2): campus analysis, watchlists, instructor last-3-QA-day averages,
Capability Managers and their connects, and the month view. Works on the DataFrames from
bundle.read_bundle(). Averages are always SUM(score_sum) / SUM(scored) over the grouping,
shown with their count; missing data is None ("no data"), never 0.

Ported from the analysis behind the approved daily email (reference/*.py)."""
from __future__ import annotations

import math
import re
from html import escape

import numpy as np
import pandas as pd

TP = ("LECTURE", "PRACTICE")
SD = {"LECTURE": 1.2, "PRACTICE": 1.9}       # usual noise of one evaluation, by session type
NOT_CAMPUS = {"Training Institute", "IIT kharagpur", "Nxtwave & IIT KGP OCN"}
CRIT = {"content": "Content accuracy", "language": "Language", "pedagogy": "Pedagogical quality",
        "engagement": "Student engagement", "doubt": "Doubt resolution", "conduct": "Professional conduct",
        "process": "Process adherence", "clarity": "Clarity and communication",
        "support": "Pedagogical support", "independence": "Student independence"}

RUBRIC_SHORT = {"content": "content", "language": "language", "pedagogy": "pedagogy", "engagement": "engagement",
                "doubt": "doubt resolution", "conduct": "conduct", "process": "process", "clarity": "clarity",
                "support": "support", "independence": "independence"}

# Primary-manager rule
INT_TEAM = {"Akhil", "Akhilendar Reddy Karri", "Nishut Mahunta", "Sandeep Sonna", "Sireesha", "Bhavana Panchothi"}
INT_LABEL = "Intensive Offline team"
SECONDARY = {"Chandrasekhar", "Pradosh Patnaik"}
CP_LABEL = "Chandrasekhar / Pradosh Patnaik"
DROP_FROM_SHARED = {"Naga Venkata Dasaradhi Nunna", "Akhil", "Akhilendar Reddy Karri", "Sk Md Basharat Mones"}

DONE, MISSED = "Completed", "Missed"
RESCHED = {"Rescheduled", "Reschedule Required"}
SCHEDULED = "Scheduled"

DAY = pd.Timedelta(days=1)
MIN_PORTFOLIO = 3   # managers with fewer instructors are listed under the table, not in it


# ------------------------------------------------------------------ helpers --
def ratio(s, n):
    return float(s) / float(n) if n else None


def r2(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), 2)


def d_str(t) -> str | None:
    return None if t is None or pd.isna(t) else pd.Timestamp(t).strftime("%Y-%m-%d")


STOP = {"for", "of", "the", "in", "to", "a", "an", "with", "and", "&"}


def short_campus(name: str) -> str:
    """'SPIHER University Chennai' -> 'SPIHER Chennai', 'Chaitanya Deemed-to-be University' -> 'Chaitanya'."""
    s = re.sub(r"\bDeemed[- ]to[- ]be\b", "", str(name), flags=re.I)
    s = re.sub(r"\bUniversity\b", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" -,")
    if not s or (" " not in s and len(s) <= 3 and s.upper() != str(name).strip().upper()):
        return str(name)
    return " ".join(w[:1].upper() + w[1:] if w.islower() and w not in STOP else w for w in s.split(" "))


def short_subject(name: str) -> str:
    """'Communicative English Foundation' -> 'CEF', 'Probability and Statistics' -> 'P&S'."""
    words = re.findall(r"[A-Za-z][A-Za-z']*", str(name))
    if len(str(name)) <= 18 or len(words) < 2:
        return str(name)
    has_and = any(w.lower() == "and" for w in words)
    core = [w for w in words if w.lower() not in STOP]
    if len(core) < 2:
        return str(name)
    return ("&" if has_and and len(core) == 2 else "").join(w[0].upper() for w in core)


def primary_manager(names) -> str:
    s = {n for n in names if isinstance(n, str) and n}
    if not s:
        return "Unassigned"
    if s <= INT_TEAM:
        return INT_LABEL
    rest = s - SECONDARY
    if len(rest) == 1:
        return rest.pop()
    if not rest:
        return CP_LABEL
    if "Tejaswini Venkata" in rest and "Riya" in rest:
        return "Riya"
    rest -= {"Sk Md Basharat Mones"}
    if len(rest) == 1:
        return rest.pop()
    parts = [p for p in sorted(rest) if p not in DROP_FROM_SHARED]
    if len(parts) == 1:
        return parts[0]
    if rest <= INT_TEAM | DROP_FROM_SHARED and rest & INT_TEAM:
        return INT_LABEL
    return " / ".join(parts or sorted(rest))


def fmt_day(t) -> str:
    return pd.Timestamp(t).strftime("%d %b").lstrip("0")


def fmt_wday(t) -> str:
    t = pd.Timestamp(t)
    return f"{t:%a} {t.day} {t:%b}"


def nth(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def day_range(a, b) -> str:
    a, b = pd.Timestamp(a), pd.Timestamp(b)
    if a.month == b.month:
        return f"{a.day}–{b.day} {b:%b}"
    return f"{fmt_day(a)} – {fmt_day(b)}"


# ------------------------------------------------------------------- engine --
class Engine:
    def __init__(self, frames: dict, capability_emails: dict | None = None):
        q = frames["qa_daily"]
        q = q[~q.campus_name.isin(NOT_CAMPUS)].copy()
        self.q = q
        self.lp = q[q.session_type.isin(TP)]
        self.m = frames["manager_map"]
        self.c = frames["manager_connects"]
        self.a = frames["manager_activity"]
        self.cap_emails = capability_emails or {}
        self.last_date = q.session_date.max()
        self.first_date = q.session_date.min()
        self.campuses = sorted(q.campus_name.unique())
        self._cache = {}
        self._build_managers()

    # ---------------------------------------------------------------- windows
    def window(self, end, days, types=TP):
        d = self.q if types is None else self.lp
        return d[(d.session_date > end - pd.Timedelta(days=days)) & (d.session_date <= end)]

    @staticmethod
    def avg(df):
        n = df.scored.sum()
        return ratio(df.score_sum.sum(), n)

    # --------------------------------------------------------------- headline
    def headline(self, Y) -> dict:
        dy = self.q[self.q.session_date == Y]
        lp = dy[dy.session_type.isin(TP)]
        p7 = self.lp[(self.lp.session_date >= Y - 7 * DAY) & (self.lp.session_date < Y)]
        out = {"campuses": len(self.campuses), "taught": int(dy.campus_name.nunique()),
               "sessions": int(lp.sessions_scheduled.sum()), "with_qa": int(lp.sessions_with_completed_qa.sum()),
               "scored": int(lp.scored.sum()), "avg": r2(self.avg(lp)), "avg_p7": r2(self.avg(p7))}
        out["coverage"] = round(100 * out["with_qa"] / out["sessions"]) if out["sessions"] else None
        for t in TP:
            out[f"avg_{t.lower()}"] = r2(self.avg(lp[lp.session_type == t]))
            out[f"avg_p7_{t.lower()}"] = r2(self.avg(p7[p7.session_type == t]))
        cur, prev = self.window(Y, 14), self.window(Y - 14 * DAY, 14)
        out["net14"] = {"from": d_str(Y - 13 * DAY), "to": d_str(Y), "avg": r2(self.avg(cur)),
                        "n": int(cur.scored.sum()), "prev_avg": r2(self.avg(prev)), "prev_n": int(prev.scored.sum())}
        return out

    # ------------------------------------------------------------------- idle
    def idle(self, Y) -> list[dict]:
        upto = self.q[self.q.session_date <= Y]
        taught = set(upto[upto.session_date == Y].campus_name)
        last = upto.groupby("campus_name").session_date.max()
        rows = []
        for c in self.campuses:
            if c in taught:
                continue
            ld = last.get(c)
            rows.append({"campus": c, "short": short_campus(c), "last": d_str(ld),
                         "days": None if ld is None or pd.isna(ld) else int((Y - ld).days)})
        return sorted(rows, key=lambda r: -(r["days"] or 0))

    # --------------------------------------------------------------- coverage
    def coverage(self, Y, top=5) -> dict:
        lp = self.lp[self.lp.session_date == Y]
        hist = self.lp[(self.lp.session_date < Y) & (self.lp.session_date >= Y - 27 * DAY)]
        cols = ["sessions_scheduled", "sessions_with_completed_qa"]
        h = hist.groupby("campus_name")[cols].sum()
        cy = lp.groupby("campus_name")[cols].sum()
        rows = []
        for c, r in cy[cy.sessions_scheduled >= 8].iterrows():
            cov = 100 * r.sessions_with_completed_qa / r.sessions_scheduled
            usual = (100 * h.loc[c].sessions_with_completed_qa / h.loc[c].sessions_scheduled
                     if c in h.index and h.loc[c].sessions_scheduled else None)
            det, worst = [], None
            for t in TP:
                x = lp[(lp.campus_name == c) & (lp.session_type == t)]
                s, w = int(x.sessions_scheduled.sum()), int(x.sessions_with_completed_qa.sum())
                if s and (worst is None or s - w > worst[0]):
                    worst = (s - w, f"{t.lower()} {w} of {s}")
            note = worst[1] if worst and worst[0] > 0 else ""
            if usual is not None and cov < usual - 5:
                note = (note + " — " if note else "") + "below usual"
            elif usual is not None and cov > usual + 10:
                note = "better than usual"
            rows.append({"campus": c, "short": short_campus(c), "sessions": int(r.sessions_scheduled),
                         "with_qa": int(r.sessions_with_completed_qa), "pct": round(cov),
                         "usual": None if usual is None else round(usual), "note": note,
                         "below": usual is not None and cov < usual - 5})
        rows.sort(key=lambda r: (r["pct"], -r["sessions"]))
        return {"rows": rows[:top], "all": rows}

    # -------------------------------------------------------------- watchlist
    @staticmethod
    def _gapframe(df):
        g = df.groupby(["campus_name", "subject", "session_type"]).agg(n=("scored", "sum"), s=("score_sum", "sum"))
        g = g.reset_index()
        t = g.groupby(["subject", "session_type"]).agg(N=("n", "sum"), S=("s", "sum")).reset_index()
        g = g.merge(t, on=["subject", "session_type"])
        g["on"] = g.N - g.n
        g = g[g.on >= 20].copy()
        g["exp"] = (g.S - g.s) / g.on * g.n
        return g.set_index(["campus_name", "subject", "session_type"])

    def watch(self, end) -> dict:
        key = ("watch", end)
        if key in self._cache:
            return self._cache[key]
        cur, prev = self._gapframe(self.window(end, 14)), self._gapframe(self.window(end - 14 * DAY, 14))
        res = {}
        for t in TP:
            try:
                a, b = cur.xs(t, level="session_type"), prev.xs(t, level="session_type")
            except KeyError:
                res[t] = pd.DataFrame(columns=["n", "n_p", "gap", "gap_p", "chg", "se2", "on"])
                continue
            k = a[["n", "s", "exp"]].join(b[["n", "s", "exp"]], rsuffix="_p", how="inner").groupby("campus_name").sum()
            k["gap"] = (k.s - k.exp) / k.n
            k["gap_p"] = (k.s_p - k.exp_p) / k.n_p
            k["chg"] = k.gap - k.gap_p
            k = k[(k.n >= 20) & (k.n_p >= 20)].copy()
            k["se2"] = 2 * np.sqrt(SD[t] ** 2 / k.n + SD[t] ** 2 / k.n_p)
            k["on"] = k.chg < -k.se2
            res[t] = k
        self._cache[key] = res
        return res

    def subject_most_affected(self, Y, campus, t) -> dict | None:
        cur = self.window(Y, 14)
        pri = self.window(Y - 14 * DAY, 14)
        cols = ["scored", "score_sum"] + [f"{k}_{s}" for k in CRIT for s in ("n", "sum")]
        a = cur[cur.session_type == t].groupby(["campus_name", "subject"])[cols].sum()
        b = pri[pri.session_type == t].groupby(["campus_name", "subject"])[cols].sum()
        if campus not in a.index.get_level_values(0):
            return None
        ta = a.groupby("subject").sum()
        me = a.loc[campus]
        mb = b.loc[campus] if campus in b.index.get_level_values(0) else pd.DataFrame(columns=cols)
        df = pd.DataFrame({"n": me.scored, "avg": me.score_sum / me.scored.replace(0, np.nan)})
        oa = ta.loc[me.index] - me
        df["n_o"] = oa.scored
        df["avg_o"] = oa.score_sum / oa.scored.replace(0, np.nan)
        df["n_b"] = mb.scored.reindex(df.index) if len(mb) else np.nan
        df["avg_b"] = (mb.score_sum / mb.scored.replace(0, np.nan)).reindex(df.index) if len(mb) else np.nan
        df["gap"] = df.avg - df.avg_o
        df["dropw"] = (df.avg - df.avg_b) * df.n
        df = df[(df.n >= 5) & (df.n_o >= 10)]
        if not len(df):
            return None
        cand = df[(df.n_b >= 5) & (df.gap < 0)].sort_values("dropw")
        s = cand.index[0] if len(cand) and cand["dropw"].iloc[0] < 0 else df.sort_values("gap").index[0]
        r = df.loc[s]
        rub = []
        for k, label in CRIT.items():
            n1 = a.loc[(campus, s), f"{k}_n"]
            if n1 < 3:
                continue
            n0 = b.loc[(campus, s), f"{k}_n"] if (campus, s) in b.index else 0
            now = a.loc[(campus, s), f"{k}_sum"] / n1
            on = ta.loc[s, f"{k}_n"] - n1
            oth = (ta.loc[s, f"{k}_sum"] - a.loc[(campus, s), f"{k}_sum"]) / on if on > 0 else None
            bef = b.loc[(campus, s), f"{k}_sum"] / n0 if n0 >= 3 else None
            if bef is None:
                continue
            rub.append({"key": k, "name": label, "before": r2(bef), "now": r2(now), "others": r2(oth),
                        "chg": now - bef})
        rub.sort(key=lambda x: x["chg"])
        fell = [x for x in rub if x["chg"] <= -0.5][:2] or [x for x in rub if x["chg"] < 0][:1]
        for x in fell:
            x["chg"] = r2(x["chg"])
        also = None
        low = df.sort_values("gap")
        if len(low) and low.index[0] != s and low.gap.iloc[0] <= -1:
            o = low.iloc[0]
            also = {"subject": low.index[0], "avg": r2(o.avg), "others": r2(o.avg_o), "n": int(o.n)}
        return {"subject": s, "short": short_subject(s), "n": int(r.n), "avg": r2(r.avg), "others": r2(r.avg_o),
                "n_others": int(r.n_o), "was": r2(r.avg_b), "n_was": None if pd.isna(r.n_b) else int(r.n_b),
                "rubrics": fell, "no_rubric": not rub, "also": also}

    def watchlist(self, Y) -> dict:
        key = ("watchlist", Y)
        if key in self._cache:
            return self._cache[key]
        W, W0 = self.watch(Y), self.watch(Y - DAY)
        last = self.q[self.q.session_date <= Y].groupby("campus_name").session_date.max()
        out = {}
        for t in TP:
            k, k0 = W[t], W0[t]
            on_now, on_prev = set(k[k.on].index), set(k0[k0.on].index)
            rows = []
            for c in sorted(on_now | on_prev, key=lambda c: k.chg.get(c, 0)):
                status = ("Still on list" if c in on_now and c in on_prev
                          else "New today" if c in on_now else "Removed today")
                r = k.loc[c] if c in k.index else None
                rows.append({"campus": c, "short": short_campus(c), "status": status,
                             "gap_p": None if r is None else r2(r.gap_p), "gap": None if r is None else r2(r.gap),
                             "chg": None if r is None else r2(r.chg), "n": None if r is None else int(r.n),
                             "n_p": None if r is None else int(r.n_p), "last_session": d_str(last.get(c)),
                             "subject": self.subject_most_affected(Y, c, t) if status != "Removed today" else None})
            cut_close = k[(~k.on) & (k.chg <= -0.3) & (k.chg < -0.6 * k.se2)].sort_values("chg")
            close = [{"campus": c, "short": short_campus(c), "chg": r2(r.chg), "n": int(r.n)}
                     for c, r in cut_close.iterrows() if c not in on_prev]
            out[t.lower()] = {"rows": rows, "close": close, "compared": int(len(k)), "down": int((k.chg < 0).sum())}
        self._cache[key] = out
        return out

    # -------------------------------------------------------------- managers
    def _build_managers(self):
        m = self.m
        self.names = (m.drop_duplicates("instructor_user_id").set_index("instructor_user_id"))
        self.mgr = (m[m.manager_id != ""].drop_duplicates("manager_id").set_index("manager_id"))
        act = m[m.instructor_status.str.upper() == "ACTIVE"]
        pick = act[act.is_capability_manager_pick & ~act.is_org_wide & (act.manager_id != "")]
        self.active_ids = set(act.instructor_user_id)
        rows = []
        for iid, g in pick.groupby("instructor_user_id"):
            label = primary_manager(list(g.manager_name))
            rows.append((iid, label, sorted(set(g.manager_id)), sorted(set(g.manager_name))))
        pm = pd.DataFrame(rows, columns=["iid", "manager", "mids", "mnames"]).set_index("iid")
        size = pm.manager.value_counts()
        def fold(label):
            parts = label.split(" / ")
            own = [p for p in parts if p in size.index] if len(parts) > 1 and label != CP_LABEL else []
            return max(own, key=lambda p: size[p]) if own else label
        pm["manager"] = pm.manager.map(fold)
        self.unassigned = sorted(self.active_ids - set(pm.index))
        self.no_manager = sorted(self.active_ids - set(act[act.manager_id != ""].instructor_user_id))
        pm["name"] = self.names.instructor_name.reindex(pm.index)
        pm["home_campus"] = self.names.home_campus.reindex(pm.index)
        self.pm = pm
        # each manager id -> the label its connects count under
        labels = set(pm.manager)
        size = pm.manager.value_counts()
        id2label = {}
        for mid, mname in self.mgr.manager_name.items():
            if mname in labels:
                id2label[mid] = mname
            elif mname in INT_TEAM:
                id2label[mid] = INT_LABEL
            elif mname in SECONDARY:
                id2label[mid] = CP_LABEL
            else:
                cands = [lb for lb in labels if mname in lb.split(" / ")]
                if cands:
                    id2label[mid] = max(cands, key=lambda lb: size.get(lb, 0))
        self.id2label = id2label
        self.label_ids = {}
        for mid, lb in id2label.items():
            self.label_ids.setdefault(lb, []).append(mid)
        c = self.c
        c = c[c.manager_in_directory].copy()
        c["g"] = c.manager_id.map(id2label)
        self.cg = c[c.g.notna()]
        a = self.a.copy()
        a["g"] = a.manager_id.map(id2label)
        self.ag = a[a.g.notna()]

    def instructor_metric(self, Y) -> pd.DataFrame:
        key = ("l3", Y)
        if key in self._cache:
            return self._cache[key]
        qs = self.lp[self.lp.session_date <= Y]
        day = qs.groupby(["instructor_user_id", "session_date"]).agg(n=("scored", "sum"), s=("score_sum", "sum"))
        day = day.reset_index()
        day = day[day.n > 0].sort_values(["instructor_user_id", "session_date"], ascending=[True, False])
        day["rk"] = day.groupby("instructor_user_id").cumcount() + 1
        l3 = day[day.rk <= 3].groupby("instructor_user_id").agg(
            n3=("n", "sum"), s3=("s", "sum"), d_from=("session_date", "min"), d_to=("session_date", "max"),
            days=("rk", "size"))
        l3["avg3"] = l3.s3 / l3.n3
        p3 = day[(day.rk > 3) & (day.rk <= 6)].groupby("instructor_user_id").agg(np3=("n", "sum"), sp3=("s", "sum"))
        l3 = l3.join(p3, how="left")
        l3["avg3_prev"] = l3.sp3 / l3.np3
        l3["dates"] = day[day.rk <= 3].groupby("instructor_user_id").session_date.apply(
            lambda s: [d_str(x) for x in sorted(s)])
        elig = (l3.days == 3) & (l3.d_to >= Y - 13 * DAY) & l3.index.isin(self.active_ids)
        cut = l3.loc[elig, "avg3"].quantile(0.25) if elig.any() else None
        l3["eligible"] = elig
        l3["bottomq"] = elig & (l3.avg3 <= cut) if cut is not None else False
        l3.attrs["cut"] = cut
        self._cache[key] = l3
        return l3

    def last_completed(self, Y) -> pd.Series:
        comp = self.c[(self.c.status == DONE) & (self.c.scheduled_date <= Y)]
        return comp.groupby("instructor_user_id").scheduled_date.max()

    def portfolio(self, Y) -> pd.DataFrame:
        key = ("pf", Y)
        if key in self._cache:
            return self._cache[key]
        pm = self.pm.join(self.instructor_metric(Y), how="left")
        pm["eligible"] = pm.eligible.fillna(False).astype(bool)
        pm["bottomq"] = pm.bottomq.fillna(False).astype(bool)
        pm["last_connect"] = self.last_completed(Y).reindex(pm.index)
        pm["connected14"] = pm.last_connect >= Y - 13 * DAY
        pm["not_connected"] = pm.eligible & ~pm.connected14
        self._cache[key] = pm
        return pm

    # ------------------------------------------------------- connect usage
    def connect_usage(self, Y, days=3) -> dict:
        key = ("cu", Y, days)
        if key in self._cache:
            return self._cache[key]
        W0 = Y - (days - 1) * DAY
        win = [W0 + i * DAY for i in range(days)]
        c = self.cg[self.cg.scheduled_date <= Y]
        w = c[c.scheduled_date >= W0]
        P0 = W0 - 14 * DAY
        pw = c[(c.scheduled_date >= P0) & (c.scheduled_date < W0)]
        pf = self.portfolio(Y)
        a = self.ag[(self.ag.d >= W0) & (self.ag.d <= Y)]
        a_all = self.ag[self.ag.d <= Y]
        nm = self.names.instructor_name
        rows = []
        for label, grp in pf.groupby("manager"):
            if label in ("Unassigned", CP_LABEL):
                continue
            ww, cc_, pp = w[w.g == label], c[c.g == label], pw[pw.g == label]
            per_day = [int((ww.scheduled_date == d).sum()) for d in win]
            done = int((ww.status == DONE).sum())
            missed = int((ww.status == MISSED).sum())
            res = int(ww.status.isin(RESCHED).sum())
            pend = int((ww.status == SCHEDULED).sum())
            first_any = cc_.scheduled_date.min() if len(cc_) else None
            # usual completion needs a week of history before the window
            usual = (round(100 * (pp.status == DONE).sum() / len(pp))
                     if len(pp) and first_any is not None and first_any <= P0 + 7 * DAY else None)
            comp = cc_[cc_.status == DONE]
            last_c = comp.scheduled_date.max() if len(comp) else None
            last_s = cc_.scheduled_date.max() if len(cc_) else None
            first_s = cc_.scheduled_date.min() if len(cc_) else None
            aa = a[a.g == label]
            use = {"days": int(aa.d.nunique()), "of": days,
                   "tab": int(aa[aa.event_name == "manager_connect_tab_click"].events.sum()),
                   "qa": int(aa[aa.event_name == "qa_report_opened"].events.sum())}
            if not use["days"]:
                seen = []
                for mid in self.label_ids.get(label, []):
                    mname = self.mgr.manager_name.get(mid, mid)
                    ls = a_all[a_all.manager_id == mid].d.max()
                    seen.append({"name": mname, "last": d_str(ls)})
                use["last_seen"] = seen
            day_detail = []
            for d in win:
                x = ww[ww.scheduled_date == d]
                day_detail.append({"date": d_str(d), "n": int(len(x)), "done": int((x.status == DONE).sum()),
                                   "missed": int((x.status == MISSED).sum()),
                                   "resched": int(x.status.isin(RESCHED).sum()),
                                   "pending": int((x.status == SCHEDULED).sum())})
            people = {k: list(dict.fromkeys(nm.get(i) or "unknown instructor" for i in ww[sel].instructor_user_id))
                      for k, sel in (("missed", ww.status == MISSED), ("resched", ww.status.isin(RESCHED)),
                                     ("pending", ww.status == SCHEDULED))}
            rcount = ww[ww.status.isin(RESCHED)].instructor_user_id.value_counts()
            row = {"manager": label, "instructors": int(len(grp)), "per_day": per_day, "total": int(len(ww)),
                   "done": done, "missed": missed, "resched": res, "pending": pend, "usual": usual,
                   "last_completed": d_str(last_c), "last_scheduled": d_str(last_s), "first_scheduled": d_str(first_s),
                   "use": use, "days": day_detail, "people": people,
                   "resched_twice": [nm.get(i) for i, n in rcount.items() if n >= 2],
                   "bottomq": int(grp.bottomq.sum()), "not_connected": int(grp.not_connected.sum()),
                   "emails": self._label_emails(label)}
            if last_s is not None and len(ww) == 0:
                lw = cc_[cc_.scheduled_date >= last_s - 2 * DAY]
                row["last_slots"] = {"from": d_str(lw.scheduled_date.min()), "to": d_str(last_s), "n": int(len(lw)),
                                     "done": int((lw.status == DONE).sum())}
            row.update(self._tag(row, Y, win))
            rows.append(row)
        rows.sort(key=lambda r: (r["rank"], -r["instructors"]))
        small = [r for r in rows if r["instructors"] < MIN_PORTFOLIO]
        rows = [r for r in rows if r["instructors"] >= MIN_PORTFOLIO]
        self._cache[key] = {"from": d_str(W0), "to": d_str(Y), "days": [d_str(d) for d in win],
                            "usual_from": d_str(P0), "usual_to": d_str(W0 - DAY), "rows": rows,
                            "provisional": d_str(Y),
                            "small": [{"manager": r["manager"], "instructors": r["instructors"]} for r in small]}
        return self._cache[key]

    def _label_emails(self, label) -> list[str]:
        out = []
        for mid in self.label_ids.get(label, []):
            mname = self.mgr.manager_name.get(mid, "")
            e = self.cap_emails.get(re.sub(r"\s+", " ", str(mname)).strip().lower(), {}).get("email") \
                or str(self.mgr.manager_email.get(mid, "") or "").strip().lower()
            if e and e not in out:
                out.append(e)
        return out

    @staticmethod
    def _tag(r, Y, win) -> dict:
        """Status tag, colour and sort rank (0 = needs follow-up first). Statuses and new slots keep
        arriving for about a day, so the tag is judged on the days before the report date; the report
        date itself (provisional) only counts when nothing earlier was scheduled."""
        judged = r["days"][:-1] or r["days"]
        n = sum(d["n"] for d in judged)
        done = sum(d["done"] for d in judged)
        settled = sum(d["done"] + d["missed"] + d["resched"] for d in judged)
        if r["total"] == 0:
            if r["last_completed"] is None and r["last_scheduled"] is None:
                return {"tag": "Never used", "color": "red", "rank": 0, "group": "none"}
            return {"tag": "No connects", "color": "red", "rank": 0, "group": "none"}
        if n == 0:  # only the provisional report date has connects
            last = r["days"][-1]
            if last["done"]:
                return {"tag": "Only on " + fmt_day(last["date"]), "color": "amber", "rank": 2, "group": "half"}
            if last["pending"] == last["n"]:
                return {"tag": "Status not updated", "color": "amber", "rank": 1, "group": "zero"}
            return {"tag": "0 completed", "color": "orange", "rank": 1, "group": "zero"}
        if done == 0:
            if settled == 0:
                return {"tag": "Status not updated", "color": "amber", "rank": 1, "group": "zero"}
            return {"tag": "0 completed", "color": "orange", "rank": 1, "group": "zero"}
        rate = done / settled if settled else 1
        first = pd.Timestamp(r["first_scheduled"]) if r["first_scheduled"] else None
        if first is not None and first >= pd.Timestamp(win[0]) - 7 * DAY:
            return {"tag": "Just started", "color": "amber", "rank": 2, "group": "half"}
        bad = [d for d in judged if d["n"] >= 3 and d["done"] == 0 and d["missed"] + d["resched"] == d["n"]]
        good = [d for d in judged if d["n"] and d["done"] == d["n"]]
        if bad and good and (r["usual"] or 0) >= 80:
            return {"tag": f"Bad day {fmt_day(bad[0]['date'])}", "color": "amber", "rank": 2, "group": "half",
                    "bad_day": bad[0]["date"]}
        if rate < 0.5:
            return {"tag": "Low completion", "color": "amber", "rank": 2, "group": "half"}
        if rate < 0.75:
            return {"tag": "Using, misses high", "color": "amber", "rank": 3, "group": "normal"}
        return {"tag": "Using well", "color": "green", "rank": 3, "group": "normal"}

    # ------------------------------------------------------ manager detail
    def manager_detail(self, Y, label, month_start=None) -> dict | None:
        pf = self.portfolio(Y)
        g = pf[pf.manager == label]
        if not len(g):
            return None
        recent = self.lp[(self.lp.session_date > Y - 14 * DAY) & (self.lp.session_date <= Y)]
        main = (recent.groupby(["instructor_user_id", "campus_name", "subject"]).scored.sum().reset_index()
                .sort_values("scored", ascending=False).drop_duplicates("instructor_user_id")
                .set_index("instructor_user_id"))
        cs = self.c[self.c.scheduled_date <= Y].sort_values("scheduled_date")
        last_slot = cs.drop_duplicates("instructor_user_id", keep="last").set_index("instructor_user_id")
        rows = []
        for iid, r in g.iterrows():
            mm = main.loc[iid] if iid in main.index else None
            ls = last_slot.loc[iid] if iid in last_slot.index else None
            campus = (short_campus(mm.campus_name) if mm is not None
                      else (short_campus(r.home_campus) if isinstance(r.home_campus, str) and r.home_campus else None))
            rows.append({"name": r["name"] or "unknown instructor", "campus": campus,
                         "subject": None if mm is None else mm.subject,
                         "avg": r2(r.avg3), "prev": r2(r.avg3_prev), "n": None if pd.isna(r.n3) else int(r.n3),
                         "dates": r.dates if isinstance(r.dates, list) else [], "eligible": bool(r.eligible),
                         "bottomq": bool(r.bottomq), "last_connect": d_str(r.last_connect),
                         "connect_status": None if ls is None else ls.status,
                         "connect_date": None if ls is None else d_str(ls.scheduled_date),
                         "not_connected": bool(r.not_connected)})
        rows.sort(key=lambda x: (x["avg"] is None, x["avg"] if x["avg"] is not None else 0))
        n3, s3 = g.n3.sum(), g.s3.sum()
        ms = month_start or Y.replace(day=1)
        cc_ = self.cg[(self.cg.g == label) & (self.cg.scheduled_date >= ms) & (self.cg.scheduled_date <= Y)]
        hist = []
        d = ms
        while d <= Y:
            x = cc_[cc_.scheduled_date == d]
            hist.append({"date": d_str(d), "done": int((x.status == DONE).sum()), "missed": int((x.status == MISSED).sum()),
                         "resched": int(x.status.isin(RESCHED).sum()), "pending": int((x.status == SCHEDULED).sum())})
            d += DAY
        return {"manager": label, "instructors": int(len(g)), "avg": r2(ratio(s3, n3)),
                "prev": r2(ratio(g.sp3.sum(), g.np3.sum())), "n": int(n3 or 0),
                "bottomq": int(g.bottomq.sum()), "not_connected": int(g.not_connected.sum()),
                "eligible": int(g.eligible.sum()), "rows": rows, "history": hist, "emails": self._label_emails(label)}

    # ------------------------------------------------------------ month view
    def month(self, Y, month_start) -> dict:
        ms = pd.Timestamp(month_start).replace(day=1)
        end = min(Y, ms + pd.offsets.MonthEnd(0))
        days = list(pd.date_range(ms, end, freq="D"))
        net = []
        for d in days:
            x = self.lp[self.lp.session_date == d]
            row = {"date": d_str(d), "sessions": int(x.sessions_scheduled.sum()),
                   "with_qa": int(x.sessions_with_completed_qa.sum())}
            row["coverage"] = round(100 * row["with_qa"] / row["sessions"]) if row["sessions"] else None
            for t in TP:
                xt = x[x.session_type == t]
                row[t.lower()] = r2(self.avg(xt))
                row[f"n_{t.lower()}"] = int(xt.scored.sum())
            net.append(row)
        # watchlist history: when each campus entered or left the list
        hist = {t.lower(): {} for t in TP}
        for d in days:
            if d < self.first_date + 27 * DAY:
                continue
            W = self.watch(d)
            for t in TP:
                for c in W[t][W[t].on].index:
                    hist[t.lower()].setdefault(c, []).append(d)
        wl = {}
        for t, by in hist.items():
            rows = []
            for c, ds in by.items():
                spans, start, prev = [], None, None
                for s in ds:
                    if start is None:
                        start = prev = s
                    elif s - prev == DAY:
                        prev = s
                    else:
                        spans.append((start, prev))
                        start = prev = s
                spans.append((start, prev))
                rows.append({"campus": c, "short": short_campus(c), "days": len(ds), "on": [d_str(x) for x in ds],
                             "spans": [{"from": d_str(a), "to": d_str(b), "open": b == end} for a, b in spans]})
            rows.sort(key=lambda r: (-r["days"], r["short"]))
            wl[t] = rows
        # managers x day
        cg = self.cg[(self.cg.scheduled_date >= ms) & (self.cg.scheduled_date <= end)]
        labels = [r["manager"] for r in self.connect_usage(Y)["rows"]]
        waiting = {}
        for d in days:
            pf = self.portfolio(d)
            w = pf[pf.bottomq & ~pf.connected14.fillna(False).astype(bool)]
            waiting[d_str(d)] = w.manager.value_counts().to_dict()
        mgr_days = []
        for lb in labels:
            x = cg[cg.g == lb]
            row = {"manager": lb, "days": []}
            for d in days:
                xd = x[x.scheduled_date == d]
                settled = int(xd.status.isin([DONE, MISSED, *RESCHED]).sum())
                row["days"].append({"date": d_str(d), "done": int((xd.status == DONE).sum()), "n": int(len(xd)),
                                    "rate": round(100 * (xd.status == DONE).sum() / settled) if settled else None,
                                    "waiting": int(waiting[d_str(d)].get(lb, 0))})
            row["total"] = sum(r["n"] for r in row["days"])
            row["done"] = sum(r["done"] for r in row["days"])
            mgr_days.append(row)
        # subjects: month so far vs the same number of days before the month
        cur = self.lp[(self.lp.session_date >= ms) & (self.lp.session_date <= end)]
        span = (end - ms).days + 1
        prev = self.lp[(self.lp.session_date < ms) & (self.lp.session_date >= ms - span * DAY)]
        subj = []
        for (s, t), x in cur.groupby(["subject", "session_type"]):
            p = prev[(prev.subject == s) & (prev.session_type == t)]
            subj.append({"subject": s, "type": t.lower(), "avg": r2(self.avg(x)), "n": int(x.scored.sum()),
                         "campuses": int(x[x.scored > 0].campus_name.nunique()),
                         "prev": r2(self.avg(p)), "prev_n": int(p.scored.sum())})
        subj = [r for r in subj if r["n"]]
        subj.sort(key=lambda r: -r["n"])
        # the watchlist needs 28 days of QA before a day; earlier days can't be checked
        watch_from = max(ms, self.first_date + 27 * DAY)
        return {"from": d_str(ms), "to": d_str(end), "network": net, "watchlist": wl, "managers": mgr_days,
                "subjects": subj, "prev_from": d_str(ms - span * DAY), "prev_to": d_str(ms - DAY),
                "watch_from": d_str(watch_from), "data_from": d_str(self.first_date)}

    # ----------------------------------------------------- notes and items
    @staticmethod
    def _names(lst, k=6) -> str:
        lst = [x for x in lst if x]
        s = ", ".join(escape(x) for x in lst[:k])
        return s + (f" and {len(lst) - k} more" if len(lst) > k else "")

    def manager_note(self, r, Y) -> str:
        """The detail line under a manager in the connect table (HTML)."""
        first = escape(r["manager"].split(" / ")[0].split(" ")[0])
        tag, n, p = r["tag"], r["instructors"], r["people"]
        if tag == "Never used":
            return (f"No connect ever recorded for these {n} instructors. "
                    "<b>Ask:</b> confirm ownership and get the first round scheduled.")
        if tag == "No connects":
            lc = pd.Timestamp(r["last_completed"]) if r["last_completed"] else None
            if lc is None or (Y - lc).days > 45:
                days = None if lc is None else (Y - lc).days
                gap = ("No completed connect ever" if lc is None
                       else f"{days // 30} months without a recorded connect" if days >= 60
                       else f"{days} days without a recorded connect")
                return f"{gap}. <b>Ask:</b> is {first} still managing these {n}, or should they be reassigned?"
            ls = r.get("last_slots")
            lead = f"{day_range(ls['from'], ls['to'])}: {ls['n']} slots, {ls['done']} completed. " if ls else ""
            return lead + "<b>Ask:</b> are connects happening outside the tool?"
        if tag.startswith("Only on"):
            d = r["days"][-1]
            return (f"Nothing on {day_range(r['days'][0]['date'], r['days'][-2]['date'])}; {d['n']} on "
                    f"{fmt_day(d['date'])} (provisional): {d['done']} done, {d['missed']} missed, "
                    f"{d['resched']} rescheduled.")
        if tag == "Just started":
            s = f"Started connects {fmt_day(r['first_scheduled'])}."
            if p["resched"]:
                s += f" Rescheduled: {self._names(p['resched'])}."
            if r["total"] / max(len(r["days"]), 1) * 14 < n:
                s += f" Pace needs to rise for {n} instructors."
            return s
        if tag.startswith("Bad day"):
            good = [d for d in r["days"][:-1] if d["n"] and d["done"] == d["n"]]
            bad = next(d for d in r["days"] if d["date"] == r["bad_day"])
            s = (f"All {good[0]['n']} on {fmt_day(good[0]['date'])} done; " if good else "") + \
                f"all {bad['n']} on {fmt_day(bad['date'])} missed or need reschedule."
            if r["usual"] is not None:
                s += f" Usually {r['usual']}% — check what happened on {fmt_day(bad['date'])}."
            return s
        if tag in ("0 completed", "Status not updated"):
            busy = [d for d in r["days"] if d["n"]]
            when = ", ".join(f"{d['n']} on {fmt_day(d['date'])}" + (", provisional" if d["date"] == d_str(Y) else "")
                             for d in busy)
            if tag == "Status not updated":
                return (f"All {sum(d['n'] for d in busy)} ({when}) still “Scheduled”: {self._names(p['pending'])}. "
                        "Likely to update — recheck tomorrow.")
            parts = []
            if p["missed"]:
                parts.append(self._names(p["missed"]) + " (missed)")
            if p["resched"]:
                parts.append(self._names(p["resched"]) + " (reschedule)")
            if p["pending"]:
                parts.append(self._names(p["pending"]) + " (not updated)")
            return f"None of {r['total']} completed ({when}): " + "; ".join(parts) + "."
        out = []
        if p["missed"]:
            out.append("Missed: " + self._names(p["missed"]) + ".")
        if r["resched_twice"]:
            out.append(self._names(r["resched_twice"]) + " rescheduled twice.")
        if p["resched"] and tag == "Low completion":
            rest = [x for x in p["resched"] if x not in r["resched_twice"]]
            if rest:
                out.append(self._names(rest) + (" needs" if len(rest) == 1 else " need") + " reschedule.")
        busy = [d for d in r["days"] if d["n"]]
        if len(busy) == 1 and len(r["days"]) > 1 and tag.startswith("Using"):
            others = [d["date"] for d in r["days"] if not d["n"]]
            out.append(f"All on one day; nothing on "
                       f"{day_range(others[0], others[-1]) if len(others) > 1 else fmt_day(others[0])} for {n} instructors.")
        if p["pending"]:
            out.append(f"{len(p['pending'])} still “Scheduled” (not updated).")
        if tag == "Low completion" and r["usual"] is not None and r["usual"] >= 85:
            out.append(f"Usually completes {round(r['usual'] / 10)} in 10, so likely a one-day slip.")
        return " ".join(out)

    @staticmethod
    def _who(r) -> str:
        parts = r["manager"].split(" / ")
        if r["manager"] == INT_LABEL:
            return "Intensive Offline team"
        return escape(parts[0].split(" ")[0] + (" group" if len(parts) > 1 else ""))

    def act_today(self, Y, wl, idle, cu) -> list[dict]:
        items, who = [], self._who
        # 1. lecture watchlist campuses
        rows = [r for r in wl["lecture"]["rows"] if r["status"] != "Removed today" and r["subject"]]
        if rows:
            by = {}
            for r in rows:
                by.setdefault(r["subject"]["short"], []).append(r["short"])
            head = ", ".join(f"{escape(s)} at {' and '.join(escape(c) for c in cs)}" for s, cs in by.items())
            sup = []
            for r in rows:
                s = r["subject"]
                rub = f", {RUBRIC_SHORT[s['rubrics'][0]['key']]} ↓" if s["rubrics"] else ""
                sup.append(f"{escape(s['short'])} {s['avg']:.2f} vs {s['others']:.2f}"
                           f"{' elsewhere' if not sup else ''} ({escape(r['short'])}{rub})")
            items.append({"bold": f"Lecture: {head}.", "line": " · ".join(sup) + "."})
        else:
            items.append({"calm": True, "bold": "Lecture: no campus is on the watchlist.",
                          "line": "No campus fell beyond normal variation."})
        # 2. practice: the rubric that fell across campuses
        rows = [r for r in wl["practice"]["rows"] if r["status"] != "Removed today" and r["subject"]]
        if rows:
            cnt = {}
            for r in rows:
                if r["subject"]["rubrics"]:
                    k = r["subject"]["rubrics"][0]["name"]
                    cnt[k] = cnt.get(k, 0) + 1
            top = max(cnt, key=cnt.get) if cnt else None
            head = (f"Practice: {top.lower()} is falling." if top and cnt[top] >= 2
                    else "Practice: " + ", ".join(escape(r["short"]) for r in rows) + " on the watchlist.")
            sup, rest = [], []
            for r in rows:
                s = r["subject"]
                rb = next((x for x in s["rubrics"] if x["name"] == top), None) if top else None
                new = " (new today)" if r["status"] == "New today" else ""
                if rb:
                    sup.append(f"{escape(r['short'])} {escape(s['short'])} {rb['before']:.2f} → {rb['now']:.2f}{new}")
                else:
                    rest.append(f"{escape(r['short'])} {escape(s['short'])} practice {s['avg']:.2f} vs {s['others']:.2f}{new}")
            line = " · ".join(sup) + ("." if sup else "")
            if rest:
                line = (line + " " if line else "") + " · ".join(rest) + "."
            items.append({"bold": head, "line": line})
        else:
            items.append({"calm": True, "bold": "Practice: no campus is on the watchlist.",
                          "line": "No campus fell beyond normal variation."})
        # 3. idle campuses: only those without a known reason need a follow-up
        unknown = [r for r in idle if not r.get("reason")]
        if unknown:
            places = ", ".join(escape(r["short"]) + (f" ({nth(r['days'])} day)" if r["days"] else "") for r in unknown)
            items.append({"bold": f"No sessions and no known reason at {places}.",
                          "line": "Ask the campus why: exams, a holiday, or the schedule feed stopped."})
        elif idle:
            items.append({"calm": True, "bold": "No campus needs a follow-up on sessions.",
                          "line": "Every campus without sessions has a known reason."})
        else:
            items.append({"calm": True, "bold": f"Every campus held sessions on {fmt_day(Y)}.", "line": "No idle campuses."})
        # 4. managers with no connects
        none_ = [r for r in cu["rows"] if r["group"] == "none"]
        rng = day_range(cu["from"], cu["to"])
        if none_:
            head = f"No connects {rng}: " + ", ".join(
                f"{who(r)} ({r['instructors']}{' instructors' if i == 0 else ''})" for i, r in enumerate(none_)) + "."
            why = []
            for r in none_:
                if r["tag"] == "Never used":
                    why.append(f"{who(r)}: never")
                elif r["last_completed"]:
                    lc = pd.Timestamp(r["last_completed"])
                    why.append(f"{who(r)}: none since {fmt_day(lc)}" if (Y - lc).days > 30
                               else f"{who(r)}: check if connects are held outside the tool")
            items.append({"bold": head, "line": ". ".join(why) + "."})
        else:
            items.append({"calm": True, "bold": f"Every Capability Manager recorded connects {rng}.", "line": ""})
        # 5. a full day of connects missed (settled days only)
        judged = cu["days"][:-1]
        worst = [(d["date"], r, d) for r in cu["rows"] for d in r["days"]
                 if d["date"] in judged and d["n"] >= 3 and d["missed"] + d["resched"] == d["n"]]
        pend = [r for r in cu["rows"] if r["tag"] == "Status not updated"]
        line = (" and ".join(f"{who(r)} ({r['pending']})" for r in pend) + " still not updated — recheck tomorrow."
                if pend else "")
        if worst:
            day = max({x[0] for x in worst}, key=lambda dd: sum(x[2]["n"] for x in worst if x[0] == dd))
            ws = sorted([x for x in worst if x[0] == day], key=lambda x: -x[2]["n"])
            items.append({"bold": f"Connects missed on {fmt_day(day)}: "
                          + ", ".join(f"{who(r)} {d['n']} of {d['n']}" for _, r, d in ws) + ".", "line": line})
        else:
            items.append({"calm": True, "bold": "No manager missed a full day of connects.", "line": line})
        return items

    def focus(self, wl, cu) -> str:
        bits = []
        rows = [r for r in wl["lecture"]["rows"] if r["status"] != "Removed today" and r["subject"]]
        if rows:
            s = rows[0]["subject"]["subject"]
            camp = [r["short"] for r in rows if r["subject"]["subject"] == s][:2]
            word = "English" if "english" in s.lower() else short_subject(s)
            bits.append(f"{word} lectures at {' & '.join(camp)}")
        cnt = {}
        for r in wl["practice"]["rows"]:
            if r["status"] != "Removed today" and r["subject"] and r["subject"]["rubrics"]:
                k = r["subject"]["rubrics"][0]["name"]
                cnt[k] = cnt.get(k, 0) + 1
        if cnt:
            bits.append("practice " + max(cnt, key=cnt.get).lower())
        if any(r["group"] == "none" for r in cu["rows"]):
            bits.append("managers with no connects")
        return ", ".join(bits) or "no new issues"

    # ------------------------------------------------------------- checks
    def checks(self, Y, month_start) -> list[dict]:
        out = []
        if self.first_date > month_start - 27 * DAY:
            out.append({"level": "warn", "text": f"The QA file starts {fmt_day(self.first_date)}, less than 28 days "
                        f"before {month_start:%B}: the month view starts its watchlist later."})
        if (self.c.scheduled_date >= Y).any():
            out.append({"level": "info", "text": f"Connects on {fmt_day(Y)} are provisional: statuses and new slots keep "
                        "arriving for about a day, so managers are judged on the days before."})
        if self.no_manager:
            extra = len(self.unassigned) - len(self.no_manager)
            out.append({"level": "warn", "text": f"{len(self.no_manager)} active instructors have no manager"
                        + (f" and {extra} more only have org-wide managers" if extra > 0 else "")
                        + ": they’re in no Capability Manager’s portfolio."})
        return out

    # ------------------------------------------------------------- analyze
    @staticmethod
    def apply_reasons(idle: list[dict], reasons: dict | None) -> list[dict]:
        """A reason covers one quiet stretch: it applies while the campus's last session is still the
        one it had when the reason was set, so it clears itself once sessions resume."""
        for r in idle:
            x = (reasons or {}).get(r["campus"])
            if x and x.get("last_session") == r["last"]:
                r["reason"], r["reason_note"] = x.get("reason"), x.get("note") or ""
        idle.sort(key=lambda r: (bool(r.get("reason")), -(r["days"] or 0)))
        return idle

    def analyze(self, Y=None, month=None, manager=None, reasons=None) -> dict:
        Y = pd.Timestamp(Y) if Y else self.last_date
        if Y > self.last_date:
            raise ValueError(f"The report date can’t be after {fmt_day(self.last_date)}, the latest day in the QA file.")
        if Y < self.first_date + 27 * DAY:
            raise ValueError(f"The report date needs 28 days of QA data before it; the file starts "
                             f"{fmt_day(self.first_date)}.")
        ms = pd.Timestamp(month + "-01") if month else Y.replace(day=1)
        if ms > Y:
            ms = Y.replace(day=1)
        idle = self.apply_reasons(self.idle(Y), reasons)
        wl = self.watchlist(Y)
        cu = self.connect_usage(Y)
        for r in cu["rows"]:
            r["note"] = self.manager_note(r, Y)
        l3 = self.instructor_metric(Y)
        groups = {g: [r for r in cu["rows"] if r["group"] == g] for g in ("none", "zero", "half", "normal")}
        long_gap = [r for r in cu["rows"] if r["group"] == "none" and (
            r["last_completed"] is None or (Y - pd.Timestamp(r["last_completed"])).days > 45)]
        out = {"report_date": d_str(Y), "send_date": d_str(Y + DAY), "latest_date": d_str(self.last_date),
               "first_date": d_str(self.first_date), "month": f"{ms:%Y-%m}",
               "headline": self.headline(Y), "idle": idle, "coverage": self.coverage(Y)["rows"], "watchlist": wl,
               "connects": cu,
               "connect_groups": {g: {"managers": len(v), "instructors": sum(r["instructors"] for r in v)}
                                  for g, v in groups.items()},
               "long_gap": {"managers": [r["manager"] for r in long_gap],
                            "short": [self._who(r) for r in long_gap],
                            "instructors": sum(r["instructors"] for r in long_gap)},
               "bottom_cut": r2(l3.attrs.get("cut")), "eligible": int(l3.eligible.sum()),
               "managers": [r["manager"] for r in cu["rows"]],
               "checks": self.checks(Y, ms)}
        out["act"] = self.act_today(Y, wl, idle, cu)
        out["focus"] = self.focus(wl, cu)
        out["month_view"] = self.month(Y, ms)
        pick = manager if manager in out["managers"] else (out["managers"][0] if out["managers"] else None)
        out["manager_detail"] = self.manager_detail(Y, pick, ms) if pick else None
        return out
