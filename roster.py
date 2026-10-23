"""Reads the instructor roster export and maps each instructor to the capability
manager emails that should be CC'd on their QA email."""
from __future__ import annotations

import io
import os
import re

import pandas as pd

from qa_engine import norm_id

ROSTER_COLS = ["instructor_user_id", "capability_manager_email"]
EMAIL = re.compile(r"[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+")


def allowed_domains() -> list[str]:
    raw = os.getenv("CC_ALLOWED_DOMAINS", "nxtwave.co.in,nxtwave.tech")
    return [d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip()]


def is_roster(df: pd.DataFrame) -> bool:
    cols = {str(c).replace("﻿", "").strip().lower() for c in df.columns}
    return all(c in cols for c in ROSTER_COLS)


def read_roster(data: bytes) -> pd.DataFrame | None:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding=enc)
        except Exception:  # noqa: BLE001
            continue
        if is_roster(df):
            df.columns = [str(c).replace("﻿", "").strip().lower() for c in df.columns]
            return df
    return None


def parse_roster(df: pd.DataFrame) -> dict:
    """Returns {"entries": [...], "count": n}. Each entry has the instructor's id and email,
    the capability manager name(s), the addresses to CC, and any external addresses left out."""
    domains = allowed_domains()
    entries = []
    for r in df.to_dict(orient="records"):
        emails = list(dict.fromkeys(e.lower() for e in EMAIL.findall(r.get("capability_manager_email") or "")))
        if not emails:
            continue
        ok = [e for e in emails if e.split("@")[1] in domains]
        entries.append({
            "id": norm_id(r.get("instructor_user_id")),
            "email": (r.get("instructor_email") or "").strip().lower(),
            "name": (r.get("capability_manager_name") or "").strip(),
            "cc": ok,
            "external": [e for e in emails if e not in ok],
        })
    return {"entries": entries, "count": len(entries), "rows": len(df)}
