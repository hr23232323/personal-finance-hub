"""Tier 2 — the LLM coach.

Reads the *deterministic* facts produced by discover.py and returns 3-5 typed
insights that add something the raw findings don't already say: connecting two
findings, explaining what a number implies, or singling out what deserves
attention. The hard rule: the model interprets, ranks, and connects — it never
produces a number. Every figure it cites comes from the facts bundle, which was
computed exactly from the local database.

Optional by design: with no LLM key the app is fully useful without this, and
nothing leaves the machine. When enabled, only the computed facts (aggregates
and finding summaries) are sent — never the raw transaction ledger.
"""
import json

from . import config, db, discover, llm, queries

SYSTEM = (
    "You are a sharp, calm personal finance analyst.\n"
    "You are given FACTS computed exactly from someone's transaction data, including the "
    "deterministic findings already displayed to them.\n\n"
    "Return 4 or 5 insights that ADD something those findings don't already state (3 only if the "
    "data is genuinely thin). Good insights:\n"
    "- connect two or more findings into a single point\n"
    "- explain what a number actually implies for this person\n"
    "- single out what genuinely deserves attention, or reassure that something looks fine\n"
    "Never simply restate a finding in different words.\n\n"
    "Rules:\n"
    "- Every figure you cite must appear verbatim in the facts. Never invent or recompute a number.\n"
    "- headline: under 60 characters, concrete and specific. No fluff, no questions.\n"
    "- detail: one or two plain-language sentences.\n"
    "- metric: the single most relevant figure, copied exactly as written in the facts, for example "
    "$756/mo. Use an empty string if no single figure fits.\n"
    "- Never wrap figures in quotation marks. Write $963, not \"$963\".\n"
    "- kind: 'pattern' for recurring behaviour, 'opportunity' for something worth changing, "
    "'watch' for something to keep an eye on, 'observation' for neutral context.\n"
    "- Be non-judgmental. A one-off trip or a big purchase is not a problem to be fixed."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "insights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "headline": {"type": "string"},
                    "detail": {"type": "string"},
                    "metric": {"type": "string"},
                    "kind": {"type": "string",
                             "enum": ["observation", "pattern", "opportunity", "watch"]},
                },
                "required": ["headline", "detail", "metric", "kind"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["insights"],
    "additionalProperties": False,
}

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
        "findings_already_shown": [{"headline": d["title"], "detail": d["summary"]}
                                   for d in discover.discoveries()],
    }


def _clean(insight):
    """Models like to wrap quoted figures in quotation marks — strip them."""
    out = {k: (v.replace('"', "").strip() if isinstance(v, str) else v)
           for k, v in insight.items()}
    if out.get("kind") not in ("observation", "pattern", "opportunity", "watch"):
        out["kind"] = "observation"
    return out


def read(force: bool = False):
    if not config.llm_configured():
        return {"available": False, "insights": [], "model": None}
    fp = _fingerprint()
    if not force and fp in _cache:
        return {"available": True, "insights": _cache[fp], "model": config.LLM_MODEL}
    try:
        data = llm.complete_json(SYSTEM, json.dumps(facts(), indent=1), SCHEMA)
        insights = [_clean(i) for i in data.get("insights", []) if i.get("headline")][:5]
    except Exception as e:
        return {"available": True, "insights": [], "model": config.LLM_MODEL, "error": str(e)}
    _cache.clear()
    _cache[fp] = insights
    return {"available": True, "insights": insights, "model": config.LLM_MODEL}
