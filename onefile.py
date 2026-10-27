"""The single-query export (bigquery/niat_all_in_one.sql): one CSV with the columns `part` and `row_json`.

expand() turns it back into the files the app already reads — 01_universities.csv … 13_tickets_month.csv,
instructor_qa_last3_export.csv, meta.csv and an export_status.csv — so both pages work from the one file."""
from __future__ import annotations

import csv
import io
import json

HEADER = ["part", "row_json"]


def is_onefile(data: bytes) -> bool:
    head = data[:200].decode("utf-8-sig", "ignore").lstrip()
    first = head.splitlines()[0] if head else ""
    return [c.strip().strip('"').lower() for c in first.split(",")] == HEADER


def _text(v) -> str:
    """A JSON value written the way a BigQuery CSV export writes it."""
    if v is None:
        return ""
    if v is True or v is False:
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v, separators=(",", ":"), ensure_ascii=False)
    return str(v)


def expand(data: bytes) -> dict[str, bytes]:
    """{"02_section_day.csv": csv bytes, ...} from the one-file export."""
    csv.field_size_limit(1 << 30)
    parts: dict[str, list[dict]] = {}
    for r in csv.DictReader(io.StringIO(data.decode("utf-8-sig"))):
        part = (r.get("part") or "").strip()
        if part and r.get("row_json"):
            parts.setdefault(part, []).append(json.loads(r["row_json"]))
    out = {}
    for part, rows in parts.items():
        cols = list(dict.fromkeys(k for row in rows for k in row))
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(cols)
        for row in rows:
            w.writerow([_text(row.get(c)) for c in cols])
        out[f"{part}.csv"] = buf.getvalue().encode("utf-8")
    if "export_status.csv" not in out:
        st = io.StringIO()
        w = csv.writer(st, lineterminator="\n")
        w.writerow(["file_name", "status", "rows", "error"])
        for part in sorted(p for p in parts if p[:2].isdigit()):
            w.writerow([f"{part}.csv", "EXPORTED", len(parts[part]), ""])
        out["export_status.csv"] = st.getvalue().encode("utf-8")
    return out
