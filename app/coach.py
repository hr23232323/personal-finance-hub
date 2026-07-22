"""Tier 2 — the LLM coach.

Reads the *deterministic* facts produced by discover.py and writes a short,
grounded read of them. The hard rule: the model narrates, ranks, and connects —
it never produces a number. Every figure it states comes from the facts bundle,
which was computed exactly from the local database.

Optional by design: with no LLM key the app is fully useful without this, and
nothing leaves the machine. When it *is* enabled, only the computed facts
(aggregates and finding summaries) are sent — not the raw transaction ledger.
"""
import json

from . import config, db, discover, llm, queries

SYSTEM = (
    "You are a calm, sharp personal finance coach reviewing someone's spending.\n"
    "You are given FACTS computed exactly from their transaction data. Every number you state "
    "must come from these facts verbatim — never invent, estimate, or recompute a figure.\n\n"
    "Write a short read of their finances:\n"
    "- 2 to 4 short paragraphs of plain prose. No headings, no bullet lists, no markdown.\n"
    "- Lead with the single most important thing.\n"
    "- Connect facts to each other where it's genuinely insightful, rather than restating them.\n"
    "- Be non-judgmental. A one-off trip or a big purchase is not a problem to be fixed.\n"
    "- At most ONE gentle suggestion, and only if it's clearly worth making. None is fine.\n"
    "- No greeting, no sign-off, no flattery. Just the read."
)

_cache = {}


def _fingerprint():
    with db.get_conn() as conn:
        r = conn.execute("SELECT COUNT(*) c, COALESCE(MAX(date),'') m FROM transactions").fetchone()
    return f"{r['c']}:{r['m']}:{config.LLM_MODEL}"


def facts():
    """The exact, computed bundle the model is allowed to talk about.

    Figures are pre-formatted so the model can only quote them as written.
    """
    s = queries.get_summary()
    return {
        "totals_all_time": {
            "money_in": f"${s['income']:,.0f}",
            "money_out": f"${abs(s['spending']):,.0f}",
            "net": f"${s['net']:,.0f}",
            "transactions": s["count"],
        },
        "findings": [{"headline": d["title"], "detail": d["summary"]}
                     for d in discover.discoveries()],
    }


def read(force: bool = False):
    if not config.llm_configured():
        return {"available": False, "read": None, "model": None}
    fp = _fingerprint()
    if not force and fp in _cache:
        return {"available": True, "read": _cache[fp], "model": config.LLM_MODEL}
    try:
        text = llm.complete(SYSTEM, json.dumps(facts(), indent=1))
    except Exception as e:
        return {"available": True, "read": None, "model": config.LLM_MODEL, "error": str(e)}
    _cache.clear()
    _cache[fp] = text
    return {"available": True, "read": text, "model": config.LLM_MODEL}
