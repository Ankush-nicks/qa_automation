"""Writes the instructor email with an LLM, grounded in the computed facts and
the rubric text. OpenAI (gpt-4o-mini) is the default; Claude is optional."""
from __future__ import annotations

import json
import os
import re

from qa_engine import (CONDUCT, TYPE_ORDER, allowed_numbers, baseline_email, build_plan, f1, f2, fr, t_short,
                       d_short)

SYSTEM_PROMPT = """You are the Central QA Team at an ed-tech company that runs college classes through in-house instructors.
You write one email to one instructor whose session-quality (QA) scores have been low. The email is supportive,
specific and professional: what the scores show, what is working, the priority areas, a concrete action plan and
what happens next.

Hard rules:
- Use ONLY the numbers, dates, session titles and names given in DATA. Never invent a score, percentage, date or session.
- Copy every score exactly as written in DATA (same decimals).
- The export only has category-level averages, not sub-rubric scores or transcripts. Do not claim that something
  specific happened in a session (for example "you spoke Hindi"). Describe what the rubric looks for instead.
- Mention any Professional Conduct concern in DATA neutrally: the score was low and the recording will be reviewed.
  Do not accuse.
- Only mention a structured improvement plan if DATA.mention_improvement_plan is true.
- Write in a polished, impersonal voice ("The QA reports ... have been reviewed", "The reports highlight ...").
  Warm, direct, no threats, no flattery, no filler. Indian/British English. Write dates as "7 October".
- The daily averages are the instructor's last days with QA reviews, up to three (days without reviews are skipped),
  so don't call them consecutive, and never say "three days" when DATA.daily_averages has fewer.
- Formatting: light Markdown exactly as in the REFERENCE DRAFT: "### " section headings, "**bold**" labels,
  "- " bullets, "1. " numbered lists, and bold numbered action headings like "**1. Structure practice sessions effectively**".
  No other Markdown (no tables, no italics, no links).

Structure (keep this order and these exact headings):
Hi <first name>,
<one paragraph: the QA reports for <period> have been reviewed against the updated rubrics; strengths to build on and
  areas where focused improvement can strengthen classroom delivery and the student learning experience>
### Performance Overview – one bullet per day: "- **7 October:** 5.15 (1 session)", then "- **Overall average:** X
  across N sessions", then one or two sentences on the trend.
### What’s Working Well – 2–3 bullets, each "- **Short label:** sentence" (strong categories, best sessions, progress, conduct).
### Key Areas for Improvement – "The reports highlight <n> priority areas:" then a numbered list, at most 3, in order of
  impact, each "1. **Category:** one sentence on what to improve, based on the RUBRIC CONTEXT next/target band". Then
  any Professional Conduct point as a short paragraph starting "Additionally, ...".
### Action Plan for Next Week – one bold numbered heading per priority area with 3–4 concrete "- " bullets each (use the
  rubric's measurable markers where it gives them), then "**n. Learn from effective practices**" (observe a high-performing
  trainer this week; apply two or three techniques), then "**n. Review feedback and track progress**" (recordings to review
  if any conduct concern; use daily QA feedback to track progress).
### Expected Outcomes and Follow-Up – one sentence on next week's focus, then bullets "- **Daily QA score:** ...",
  "- **Priority parameters:** ...", "- **Progress review:** ..." using the targets and review date in DATA. Then the
  improvement-plan sentence (only if allowed). Then one closing sentence.
Regards,
**<sender>**

Do NOT add a support section, a coaching call or daily check-ins.

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
        "average_all_sessions": P["overall"], "session_count": P["session_count"],
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
            "better, in the same format and at a similar length: make each priority area specific to the rubric context, "
            "and make the action items concrete and measurable):\n"
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
    body = str(obj["body"])  # keeps the light Markdown (### headings, **bold**) the page renders
    return {"subject": str(obj.get("subject") or "").strip(), "body": body.strip()}


def _env(name: str) -> str:
    # Keys pasted into a dashboard often carry a stray space or newline, which
    # makes the HTTP header invalid and surfaces as a vague "Connection error".
    return (os.getenv(name) or "").strip().strip('"').strip("'").strip()


def provider_info() -> dict:
    p = _env("LLM_PROVIDER").lower()
    if not p:  # default to OpenAI unless only an Anthropic key is set
        p = "anthropic" if _env("ANTHROPIC_API_KEY") and not _env("OPENAI_API_KEY") else "openai"
    if p == "openai":
        return {"provider": "openai", "model": _env("OPENAI_MODEL") or "gpt-4o-mini",
                "configured": bool(_env("OPENAI_API_KEY"))}
    return {"provider": "anthropic", "model": _env("ANTHROPIC_MODEL") or "claude-haiku-5-5",
            "configured": bool(_env("ANTHROPIC_API_KEY"))}


def _call(user: str) -> str:
    info = provider_info()
    if not info["configured"]:
        raise RuntimeError("AI isn’t set up. Add OPENAI_API_KEY (or ANTHROPIC_API_KEY with LLM_PROVIDER=anthropic) "
                           "in the server’s environment variables.")
    if info["provider"] == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=_env("OPENAI_API_KEY"), timeout=120)
        r = client.chat.completions.create(model=info["model"], max_tokens=2500,
                                           response_format={"type": "json_object"}, messages=[
            {"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}])
        return r.choices[0].message.content or ""
    from anthropic import Anthropic
    client = Anthropic(api_key=_env("ANTHROPIC_API_KEY"), timeout=120)
    msg = client.messages.create(model=info["model"], max_tokens=2500, system=SYSTEM_PROMPT,
                                 messages=[{"role": "user", "content": user}])
    return "".join(getattr(b, "text", "") for b in msg.content)


def check_numbers(body: str, inst: dict, S: dict) -> list[str]:
    allowed = allowed_numbers(inst, S)
    # Session titles can contain decimals ("Practical 1.2"); those aren't scores
    for t in sorted({s["title"] for s in inst["sessions"]} | {e["title"] for e in inst["evid"]}, key=len, reverse=True):
        if t:
            body = body.replace(t, " ")
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
