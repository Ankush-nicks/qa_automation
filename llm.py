"""Writes the instructor email with an LLM, grounded in the computed facts and
the rubric text. OpenAI (gpt-4o-mini) is the default; Claude is optional."""
from __future__ import annotations

import json
import os
import re

from qa_engine import (CONDUCT, TYPE_ORDER, allowed_numbers, baseline_email, build_plan, f1, f2, fr, t_short,
                       d_short)

SYSTEM_PROMPT = """You are a senior QA lead at an ed-tech company that runs college classes through in-house instructors.
You write one email to one instructor whose session-quality (QA) scores have been low. The goal is a clear,
supportive and specific email that tells the instructor exactly what the scores show, what the rubric expects,
what to do differently, and what happens next.

Hard rules:
- Use ONLY the numbers, dates, session titles and names given in DATA. Never invent a score, percentage, date or session.
- Copy every score exactly as written in DATA (same decimals).
- The export only has category-level averages, not sub-rubric scores or transcripts. Do not claim that something
  specific happened in a session (for example "you spoke Hindi" or "you didn't ask questions"). Instead say what the
  score band means under the rubric ("a score in this band usually means…", "the rubric looks for…").
- Mention any Professional Conduct concern in DATA neutrally: say the score was low and that you'd like to review the
  recording together. Do not accuse.
- Only mention a structured improvement plan if DATA.mention_improvement_plan is true.
- Plain text only. No markdown, no asterisks, no # headings. Use the section headings below in capitals, "- " for
  bullet lists and "1. " for numbered lists.
- Warm, direct, professional. No threats, no flattery, no filler. Indian/British English.
- LENGTH: 300–450 words in the body. Be concise: one short sentence per point, no repetition across sections.

Structure (keep this order and these headings):
Hi <first name>,
<one short paragraph: period reviewed, against the updated rubrics, why they are receiving this email>
YOUR SCORES – each day's average with session count, then the 3-day average across all sessions.
WHAT'S WORKING – 1–2 sentences, specific (best sessions, strong categories, conduct).
KEY INSIGHTS – group by session type ("Lecture-format sessions", "Practice sessions", ...). Number the weak categories
  in order of impact (at most 3). For each: average, range, weight, then 1 sentence explaining in plain words what the
  current band means and what the next/target band requires, using the RUBRIC CONTEXT. Then the trend line and any
  conduct point.
ACTION ITEMS – 4–5 numbered, concrete actions tied to the rubric's next/target band (use the rubric's own measurable
  markers where it gives them, e.g. questions per 15 minutes, talk-time %, coverage %). Last item: observe a
  high-scoring trainer's session this week.
EXPECTED OUTCOME AND NEXT STEPS – daily-average target, the category targets, review date (and improvement plan only if allowed).
<closing line asking whether anything about the setup is getting in the way>
Do NOT add a support section, a coaching call or daily check-ins.
Regards,
<sender>

Return ONLY a JSON object, no code fences: {"subject": "...", "body": "..."}"""


def build_prompt(inst: dict, S: dict, book) -> tuple[str, dict]:
    P = build_plan(inst, S)
    weak = []
    for x in sorted(P["weak"], key=lambda x: -x["impact"]):
        ctx = None
        if book is not None:
            try:
                ctx = book.context(inst["category"], x["type"], x["key"], x["mean"], S["goal"])
            except Exception:  # noqa: BLE001
                ctx = None
        weak.append({
            "session_type": x["type"], "category": x["label"], "average": f1(x["mean"]),
            "range": f"{fr(x['min'])}–{fr(x['max'])}" if x["n"] > 1 else None, "sessions": x["n"],
            "weight_in_overall_score": f"{round(x['weight'] * 100)}%" if x["weight"] else None,
            "sessions_scoring_1_or_below": x["very_low"],
            "biggest_drag": x is P["top"],
            "rubric_context": ctx,
        })
    data = {
        "instructor": {"first_name": P["first_name"], "full_name": inst["name"], "subject_area": inst["category"],
                       "institute": inst["institute"]},
        "subject_line": P["subject"],
        "period_reviewed": P["period"],
        "why_receiving_this_email": P["reason"],
        "daily_averages": P["days"],
        "three_day_average_all_sessions": P["overall"], "session_count": P["session_count"],
        "sessions": [{"date": d_short(s["date"]), "time": t_short(s["start"]), "title": s["title"],
                      "session_type": s["type"], "score": f2(s["score"])} for s in inst["sessions"]],
        "category_averages_by_session_type": {
            t: [{"category": s["label"], "average": f1(s["mean"]), "range": f"{fr(s['min'])}–{fr(s['max'])}",
                 "sessions": s["n"]} for k, s in inst["cat"][t].items()]
            for t in TYPE_ORDER if t in inst["cat"]},
        "whats_working_facts": P["working"],
        "weak_categories_in_order_of_impact": weak,
        "no_category_below_threshold": P["fallback"],
        "trend": P["trend"],
        "conduct_concerns": P["conduct"],
        "suggested_actions_from_playbook": P["actions"],
        "review_date": P["review"],
        "threshold": P["threshold"], "target_daily_average_by_review": P["goal"],
        "categories_to_lift": P["targets"], "category_minimum_target": P["target_category_min"],
        "mention_improvement_plan": P["plan"],
        "setup_question_topic": P["setup"],
        "sender_name": P["sender"],
    }
    draft = baseline_email(inst, S)
    user = ("DATA (the only source of facts):\n" + json.dumps(data, ensure_ascii=False, separators=(",", ":")) +
            "\n\nREFERENCE DRAFT (rule-based; keep its key facts and structure, but write it "
            "better and SHORTER (300–450 words): make each insight specific to the rubric context, and make the action items concrete and measurable):\n"
            f"Subject: {draft['subject']}\n\n{draft['body']}")
    return user, data


def _parse(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        obj = json.loads(t)
    except ValueError:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            raise ValueError("The AI response wasn’t valid JSON.")
        obj = json.loads(m.group(0))
    if not isinstance(obj, dict) or not obj.get("body"):
        raise ValueError("The AI response didn’t include an email body.")
    body = re.sub(r"\*\*(.+?)\*\*", r"\1", str(obj["body"]))  # strip stray markdown bold
    return {"subject": str(obj.get("subject") or "").strip(), "body": body.strip()}


def provider_info() -> dict:
    p = (os.getenv("LLM_PROVIDER") or "").lower()
    if not p:  # default to OpenAI unless only an Anthropic key is set
        p = "anthropic" if os.getenv("ANTHROPIC_API_KEY") and not os.getenv("OPENAI_API_KEY") else "openai"
    if p == "openai":
        return {"provider": "openai", "model": os.getenv("OPENAI_MODEL") or "gpt-4o-mini",
                "configured": bool(os.getenv("OPENAI_API_KEY"))}
    return {"provider": "anthropic", "model": os.getenv("ANTHROPIC_MODEL") or "claude-haiku-5-5",
            "configured": bool(os.getenv("ANTHROPIC_API_KEY"))}


def _call(user: str) -> str:
    info = provider_info()
    if not info["configured"]:
        raise RuntimeError("AI isn’t set up. Add OPENAI_API_KEY (or ANTHROPIC_API_KEY with LLM_PROVIDER=anthropic) "
                           "in the server’s environment variables.")
    if info["provider"] == "openai":
        from openai import OpenAI
        client = OpenAI(timeout=120)
        r = client.chat.completions.create(model=info["model"], max_tokens=1500,
                                           response_format={"type": "json_object"}, messages=[
            {"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}])
        return r.choices[0].message.content or ""
    from anthropic import Anthropic
    client = Anthropic(timeout=120)
    msg = client.messages.create(model=info["model"], max_tokens=1500, system=SYSTEM_PROMPT,
                                 messages=[{"role": "user", "content": user}])
    return "".join(getattr(b, "text", "") for b in msg.content)


def check_numbers(body: str, inst: dict, S: dict) -> list[str]:
    allowed = allowed_numbers(inst, S)
    found = set(re.findall(r"(?<![\d.])\d{1,2}\.\d{1,2}(?!\d)(?!\.\d)", body))
    bad = sorted(x for x in found if x not in allowed)
    return [f"The AI draft contains {'numbers' if len(bad) > 1 else 'a number'} not found in the data: "
            f"{', '.join(bad)}. Check before sending."] if bad else []


def write_email(inst: dict, S: dict, book) -> dict:
    user, _ = build_prompt(inst, S, book)
    text = _call(user)
    out = _parse(text)
    if not out["subject"]:
        out["subject"] = build_plan(inst, S)["subject"]
    out["warnings"] = check_numbers(out["body"], inst, S)
    out["model"] = provider_info()["model"]
    return out
