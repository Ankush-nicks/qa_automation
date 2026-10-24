"""Keeps the daily BigQuery export in a Turso (libSQL) database, so page 2 opens on saved data.

Each upload overrides what it covers and the database keeps only the last KEEP_DAYS days:
- qa_daily, manager_activity, manager_connects: rows dated inside the upload's date range are
  replaced by the upload's rows. Rows older than KEEP_DAYS days (counted back from the latest QA
  day) are dropped, except a few history rows the analysis needs: per manager-instructor pair the
  first, last and last completed connect, and per manager the last platform activity. Those keep
  lines like "no completed connect in 3 months" true after the detail is gone.
- manager_map is a snapshot: replaced by each upload unless the upload is older than the saved one.
Only rows that changed are written: every row carries a hash of its values.

TURSO_DATABASE_URL = libsql://<db>-<org>.turso.io (or a local file path for testing),
TURSO_AUTH_TOKEN = a database token (turso db tokens create <db>). Unset = nothing is stored.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pandas as pd

from bundle import FILES, NUMERIC

TABLES = list(FILES)                       # qa_daily, manager_map, manager_connects, manager_activity
DATE_COL = {k: v[2] for k, v in FILES.items()}
DONE = "Completed"
NUM_RE = re.compile(r"-?\d+(\.\d+)?([eE][-+]?\d+)?")
IDENT = re.compile(r"[a-z_][a-z0-9_]*")


def keep_days() -> int:
    try:
        return max(28, int(os.getenv("KEEP_DAYS", "30")))
    except ValueError:
        return 30


class StoreError(RuntimeError):
    pass


# ------------------------------------------------------------- connections --
class _Turso:
    """Turso's HTTP API (Hrana over HTTP, /v2/pipeline). Standard library only."""

    def __init__(self, url: str, token: str):
        u = url.strip()
        u = re.sub(r"^(libsql|wss)://", "https://", u)
        u = re.sub(r"^ws://", "http://", u)
        self.base = u.rstrip("/")
        self.token = token.strip()

    def _post(self, body: dict) -> dict:
        req = urllib.request.Request(self.base + "/v2/pipeline", data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {self.token}",
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                out = json.loads(r.read())
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:300]
            hint = " Check TURSO_AUTH_TOKEN." if e.code in (401, 403) else ""
            raise StoreError(f"The database refused the request ({e.code}): {msg}.{hint}") from None
        except urllib.error.URLError as e:
            raise StoreError(f"Can’t reach the database ({e.reason}). Check TURSO_DATABASE_URL.") from None
        if out.get("base_url"):
            self.base = out["base_url"].rstrip("/")
        return out

    @staticmethod
    def _results(out: dict) -> list:
        res = []
        for r in out.get("results", []):
            if r.get("type") == "error":
                raise StoreError("Database error: " + (r.get("error") or {}).get("message", "unknown"))
            resp = r.get("response") or {}
            if resp.get("type") == "execute":
                res.append(resp["result"])
        return res

    @staticmethod
    def _val(v: dict):
        t = v.get("type")
        if t == "null":
            return None
        if t == "integer":
            return int(v["value"])
        if t == "float":
            return float(v["value"])
        if t == "blob":
            b = v.get("base64", "")  # Turso sends it without padding
            return base64.b64decode(b + "=" * (-len(b) % 4))
        return v.get("value")

    def query(self, sql: str) -> list[tuple]:
        out = self._post({"requests": [{"type": "execute", "stmt": {"sql": sql}}, {"type": "close"}]})
        res = self._results(out)[0]
        return [tuple(self._val(v) for v in row) for row in res["rows"]]

    def transaction(self, stmts: list[str], max_bytes: int = 800_000):
        """Writes the statements in chunks of about max_bytes, each chunk its own transaction (Turso
        ends a transaction after 5 seconds). The meta rows come last, so the saved data's version only
        changes once every chunk is in; rows are matched by hash, so the next upload repairs a cut-off write."""
        chunks, cur, size = [], [], 0
        for s in stmts:
            if cur and size + len(s) > max_bytes:
                chunks.append(cur)
                cur, size = [], 0
            cur.append(s)
            size += len(s)
        if cur:
            chunks.append(cur)
        ex = lambda s: {"type": "execute", "stmt": {"sql": s}}  # noqa: E731
        for ch in chunks:
            baton = None
            try:
                out = self._post({"requests": [ex("BEGIN")] + [ex(s) for s in ch]})
                baton = out.get("baton")
                self._results(out)
                out = self._post({"baton": baton, "requests": [ex("COMMIT"), {"type": "close"}]})
                baton = None
                self._results(out)
            except Exception:
                if baton:
                    try:
                        self._post({"baton": baton, "requests": [ex("ROLLBACK"), {"type": "close"}]})
                    except StoreError:
                        pass
                raise


class _Local:
    """A local SQLite file with the same interface, for testing without Turso."""

    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()

    def _con(self):
        con = sqlite3.connect(self.path, isolation_level=None)
        return con

    def query(self, sql: str) -> list[tuple]:
        with self.lock:
            con = self._con()
            try:
                return [tuple(r) for r in con.execute(sql).fetchall()]
            finally:
                con.close()

    def transaction(self, stmts: list[str]):
        with self.lock:
            con = self._con()
            try:
                con.execute("BEGIN")
                for s in stmts:
                    con.execute(s)
                con.execute("COMMIT")
            except Exception as e:
                con.execute("ROLLBACK")
                raise StoreError(f"Database error: {e}") from None
            finally:
                con.close()


# ------------------------------------------------------------------ helpers --
def _q(name: str) -> str:
    if not IDENT.fullmatch(name):
        raise StoreError(f"Unexpected column name {name!r} in the export.")
    return f'"{name}"'


def _lit(v, numeric: bool) -> str:
    s = "" if v is None else str(v).replace("\x00", "")
    if numeric:
        t = s.strip()
        if not t:
            return "NULL"
        if NUM_RE.fullmatch(t):
            return t
    return "'" + s.replace("'", "''") + "'"


def _hashes(df: pd.DataFrame) -> list[str]:
    """One hash per row from its values (column order doesn't matter); repeats get a counter."""
    cols = sorted(df.columns)
    seen: dict[str, int] = {}
    out = []
    for row in df[cols].itertuples(index=False, name=None):
        h = hashlib.sha1("\x1f".join(f"{c}={v}" for c, v in zip(cols, row)).encode()).hexdigest()[:24]
        k = seen.get(h, 0)
        seen[h] = k + 1
        out.append(h if k == 0 else f"{h}-{k}")
    return out


def _day(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce").dt.normalize()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -------------------------------------------------------------------- store --
class Store:
    def __init__(self, db):
        self.db = db
        self._ready = False
        self._lock = threading.Lock()
        self._cache = None  # (version, frames)
        self._files = {}    # key -> (saved_at, filename, bytes)

    # ---- schema
    def _setup(self):
        if self._ready:
            return
        self.db.transaction([
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
            "CREATE TABLE IF NOT EXISTS uploads (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, file TEXT, "
            "export_date TEXT, qa_from TEXT, qa_to TEXT, added INTEGER, removed INTEGER, note TEXT)",
            "CREATE TABLE IF NOT EXISTS files (key TEXT PRIMARY KEY, filename TEXT, export_date TEXT, "
            "saved_at TEXT, size INTEGER, data BLOB)",
            "CREATE TABLE IF NOT EXISTS campus_reasons (campus TEXT PRIMARY KEY, reason TEXT, note TEXT, "
            "last_session TEXT, set_on TEXT, set_at TEXT)",
        ])
        self._ready = True

    def _columns(self, table: str) -> list[str]:
        rows = self.db.query(f"SELECT name FROM pragma_table_info('{table}')")
        return [r[0] for r in rows if r[0] != "row_hash"]

    def _schema_stmts(self, table: str, cols: list[str], have: list[str]) -> list[str]:
        def decl(c):
            return f"{_q(c)} {'NUMERIC' if c in NUMERIC else 'TEXT'}"
        if not have:
            out = [f"CREATE TABLE IF NOT EXISTS {table} (row_hash TEXT PRIMARY KEY, "
                   + ", ".join(decl(c) for c in cols) + ")"]
            if DATE_COL[table]:
                out.append(f"CREATE INDEX IF NOT EXISTS {table}_day ON {table} ({_q(DATE_COL[table])})")
            return out
        return [f"ALTER TABLE {table} ADD COLUMN {decl(c)}" for c in cols if c not in have]

    def meta(self) -> dict:
        self._setup()
        return {k: v for k, v in self.db.query("SELECT key, value FROM meta")}

    # ---- ingest
    def ingest(self, frames: dict, export_date: str | None, filename: str = "") -> dict:
        """Saves one upload (the all-text frames from bundle.raw_frames). Returns what changed."""
        with self._lock:
            return self._ingest(frames, export_date, filename)

    def _ingest(self, frames: dict, export_date: str | None, filename: str) -> dict:
        self._setup()
        meta = self.meta()
        stmts: list[str] = []
        have = {t: self._columns(t) for t in TABLES}
        up = {}
        for t in TABLES:
            df = frames[t].copy()
            stmts += self._schema_stmts(t, list(df.columns), have[t])
            df["row_hash"] = _hashes(frames[t])
            if DATE_COL[t]:
                df["_day"] = _day(df[DATE_COL[t]])
                df = df[df._day.notna()]
            up[t] = df

        # current rows: just what's needed to decide what stays
        need = {"qa_daily": ["session_date"], "manager_activity": ["d", "manager_id"],
                "manager_connects": ["scheduled_date", "manager_id", "instructor_user_id", "status"],
                "manager_map": []}
        cur = {}
        for t in TABLES:
            if have[t]:
                cols = ["row_hash"] + need[t]
                rows = self.db.query(f"SELECT {', '.join(_q(c) for c in cols)} FROM {t}")
                d = pd.DataFrame(rows, columns=cols).astype(object)
            else:
                d = pd.DataFrame(columns=["row_hash"] + need[t])
            if DATE_COL[t]:
                d["_day"] = _day(d[DATE_COL[t]])
            cur[t] = d

        # the upload overrides its own date range; older and newer saved rows stay
        keep = {}
        for t in ("qa_daily", "manager_activity", "manager_connects"):
            u, c = up[t], cur[t]
            lo, hi = u._day.min(), u._day.max()
            outside = c[(c._day < lo) | (c._day > hi)] if len(u) else c
            keep[t] = pd.concat([outside, u[list(c.columns)]], ignore_index=True)
        latest = keep["qa_daily"]._day.max()
        cutoff = latest - pd.Timedelta(days=keep_days() - 1)

        keep["qa_daily"] = keep["qa_daily"][keep["qa_daily"]._day >= cutoff]
        a = keep["manager_activity"]
        old = a[a._day < cutoff]
        last_seen = old.sort_values("_day").drop_duplicates("manager_id", keep="last")
        keep["manager_activity"] = pd.concat([a[a._day >= cutoff], last_seen])
        c = keep["manager_connects"]
        old = c[c._day < cutoff].sort_values("_day")
        pair = ["manager_id", "instructor_user_id"]
        hist = pd.concat([old.drop_duplicates(pair, keep="first"), old.drop_duplicates(pair, keep="last"),
                          old[old.status == DONE].drop_duplicates(pair, keep="last")])
        keep["manager_connects"] = pd.concat([c[c._day >= cutoff], hist])

        # the manager map is a snapshot: a newer saved one isn't replaced by an older upload
        map_note = ""
        saved_map = meta.get("map_export_date")
        if saved_map and export_date and export_date < saved_map and len(cur["manager_map"]):
            keep["manager_map"] = cur["manager_map"]
            map_note = f"manager map kept from {saved_map} (this upload is older)"
        else:
            keep["manager_map"] = up["manager_map"]

        added = removed = 0
        changes = {}
        for t in TABLES:
            want = set(keep[t].row_hash)
            now = set(cur[t].row_hash)
            gone = sorted(now - want)
            new = up[t][up[t].row_hash.isin(want - now)]
            for i in range(0, len(gone), 500):
                stmts.append(f"DELETE FROM {t} WHERE row_hash IN ("
                             + ",".join(f"'{h}'" for h in gone[i:i + 500]) + ")")
            cols = [c for c in frames[t].columns]
            num = [c in NUMERIC for c in cols]
            head = f"INSERT INTO {t} (row_hash, {', '.join(_q(c) for c in cols)}) VALUES "
            vals = []
            for row in new[["row_hash"] + cols].itertuples(index=False, name=None):
                vals.append("('" + row[0] + "'," + ",".join(_lit(v, n) for v, n in zip(row[1:], num)) + ")")
                if len(vals) == 200:
                    stmts.append(head + ",".join(vals))
                    vals = []
            if vals:
                stmts.append(head + ",".join(vals))
            changes[t] = {"added": int(len(new)), "removed": len(gone), "rows": len(want)}
            added += len(new)
            removed += len(gone)

        version = str(time.time_ns())
        q = keep["qa_daily"]._day
        new_meta = {"version": version, "updated_at": _now(), "latest_qa": f"{latest:%Y-%m-%d}",
                    "cutoff": f"{cutoff:%Y-%m-%d}", "keep_days": str(keep_days()), "last_file": filename,
                    "export_date": max(filter(None, [export_date, meta.get("export_date")]), default="") or ""}
        if keep["manager_map"] is up["manager_map"]:
            new_meta["map_export_date"] = export_date or ""
        for k, v in new_meta.items():
            stmts.append(f"INSERT INTO meta (key, value) VALUES ({_lit(k, False)}, {_lit(v, False)}) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value")
        stmts.append("INSERT INTO uploads (at, file, export_date, qa_from, qa_to, added, removed, note) VALUES ("
                     + ", ".join([_lit(new_meta["updated_at"], False), _lit(filename, False),
                                  _lit(export_date or "", False), _lit(f"{q.min():%Y-%m-%d}", False),
                                  _lit(f"{q.max():%Y-%m-%d}", False), str(added), str(removed),
                                  _lit(map_note, False)]) + ")")
        stmts.append("DELETE FROM uploads WHERE id NOT IN (SELECT id FROM uploads ORDER BY id DESC LIMIT 60)")
        self.db.transaction(stmts)
        self._cache = None
        return {"added": added, "removed": removed, "tables": changes, "cutoff": new_meta["cutoff"],
                "latest_qa": new_meta["latest_qa"], "note": map_note}

    # ---- load
    def load(self) -> tuple[dict, dict] | None:
        """(all-text frames, meta) of the saved data, or None when nothing is saved yet.
        Cached until the next upload (one small query checks)."""
        meta = self.meta()
        if not meta.get("version"):
            return None
        c = self._cache
        if c and c[0] == meta["version"]:
            return c[1], meta
        frames = {}
        for t in TABLES:
            cols = self._columns(t)
            rows = self.db.query(f"SELECT json_array({', '.join(_q(c) for c in cols)}) FROM {t}")
            data = [json.loads(r[0]) for r in rows]
            df = pd.DataFrame(data, columns=cols)
            frames[t] = df.apply(lambda s: s.map(_text))
        self._cache = (meta["version"], frames)
        return frames, meta

    # ---- whole files (page 1's instructor export): the newest upload replaces the saved one
    def file_info(self, key: str) -> dict | None:
        self._setup()
        rows = self.db.query(f"SELECT filename, export_date, saved_at, size FROM files WHERE key = {_lit(key, False)}")
        return dict(zip(["filename", "export_date", "saved_at", "size"], rows[0])) if rows else None

    def save_file(self, key: str, filename: str, data: bytes, export_date: str | None) -> dict:
        """Saves the file gzipped, unless the saved one comes from a newer export."""
        info = self.file_info(key)
        if info and info["export_date"] and export_date and export_date < info["export_date"]:
            return {**info, "kept": True}
        gz = gzip.compress(data, 6)
        now = _now()
        self.db.transaction([
            "INSERT INTO files (key, filename, export_date, saved_at, size, data) VALUES ("
            + ", ".join([_lit(key, False), _lit(filename, False), _lit(export_date or "", False), _lit(now, False),
                         str(len(data)), f"X'{gz.hex()}'"]) + ") ON CONFLICT(key) DO UPDATE SET "
            "filename = excluded.filename, export_date = excluded.export_date, saved_at = excluded.saved_at, "
            "size = excluded.size, data = excluded.data"])
        self._files[key] = (now, filename, data)
        return {"filename": filename, "export_date": export_date or "", "saved_at": now, "size": len(data),
                "kept": False}

    def get_file(self, key: str) -> tuple[str, bytes, dict] | None:
        """(filename, bytes, info) of the saved file; cached until a newer one is saved."""
        info = self.file_info(key)
        if not info:
            return None
        c = self._files.get(key)
        if not c or c[0] != info["saved_at"]:
            blob = self.db.query(f"SELECT data FROM files WHERE key = {_lit(key, False)}")[0][0]
            c = (info["saved_at"], info["filename"], gzip.decompress(blob))
            self._files[key] = c
        return c[1], c[2], info

    # ---- why a campus has no sessions (page 2): one row per campus, for its current quiet stretch
    def reasons(self) -> dict:
        self._setup()
        rows = self.db.query("SELECT campus, reason, note, last_session, set_on, set_at FROM campus_reasons")
        keys = ["reason", "note", "last_session", "set_on", "set_at"]
        return {r[0]: dict(zip(keys, r[1:])) for r in rows}

    def set_reason(self, campus: str, reason: str, note: str, last_session: str, set_on: str):
        self._setup()
        self.db.transaction([
            "INSERT INTO campus_reasons (campus, reason, note, last_session, set_on, set_at) VALUES ("
            + ", ".join(_lit(v, False) for v in (campus, reason, note, last_session, set_on, _now()))
            + ") ON CONFLICT(campus) DO UPDATE SET reason = excluded.reason, note = excluded.note, "
            "last_session = excluded.last_session, set_on = excluded.set_on, set_at = excluded.set_at"])

    def clear_reason(self, campus: str):
        self._setup()
        self.db.transaction([f"DELETE FROM campus_reasons WHERE campus = {_lit(campus, False)}"])

    def uploads(self, n: int = 10) -> list[dict]:
        self._setup()
        rows = self.db.query("SELECT at, file, export_date, qa_from, qa_to, added, removed, note FROM uploads "
                             f"ORDER BY id DESC LIMIT {int(n)}")
        keys = ["at", "file", "export_date", "qa_from", "qa_to", "added", "removed", "note"]
        return [dict(zip(keys, r)) for r in rows]


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def from_env() -> Store | None:
    url = os.getenv("TURSO_DATABASE_URL", "").strip()
    if not url:
        return None
    if re.match(r"^(libsql|https?|wss?)://", url):
        token = os.getenv("TURSO_AUTH_TOKEN", "").strip()
        if not token:
            near = [k for k in os.environ if k != "TURSO_AUTH_TOKEN" and "TURSO_AUTH" in k.upper()]
            raise StoreError("TURSO_AUTH_TOKEN is missing" + (f" (found {', '.join(repr(k) for k in near)}: "
                             "check the name)" if near else "") + ".")
        return Store(_Turso(url, token))
    return Store(_Local(re.sub(r"^file:", "", url)))
