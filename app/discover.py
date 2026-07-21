"""The Discoveries engine — deterministic pattern detectors.

Each detector emits zero or more Insight dicts sharing one contract:

    {
      id, title, summary,          # what it found, in plain language + real numbers
      significance, confidence,    # 0..1, used to rank the feed
      tone,                        # "neutral" | "positive" | "watch" (watch = worth a look)
      estimated_impact,            # optional $/period, or None
      evidence, evidence_count,    # the actual transactions behind the finding
    }

No LLM, no guessing — every number is computed from the data, and every finding
links to the exact transactions it came from. Adding a detector is ~20 lines:
write a function that returns Insight dicts and register it in DETECTORS.

"Now" is anchored to the most recent transaction date (so imported historical
data works), and baselines are this user's own history — a finding only surfaces
if it's unusual *for them*.
"""
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean, median, pstdev

from . import analysis, db

# Categories that are commitments/fixed rather than discretionary day-to-day spend.
FIXED_CATS = {"Housing", "Loan", "Bills & Utils"}
RECENT_DAYS = 60


def _m(n):
    return f"${round(abs(n)):,}"


def _anchor():
    with db.get_conn() as conn:
        row = conn.execute("SELECT MAX(date) mx, MIN(date) mn FROM transactions").fetchone()
    if not row or not row["mx"]:
        return None, None
    return date.fromisoformat(row["mx"]), date.fromisoformat(row["mn"])


def _months(a, b):
    return max(0.5, (b - a).days / 30.44)


def _fetch(where, params, limit=30):
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT t.id, t.date, t.amount, t.payee, t.category, a.name AS account "
            f"FROM transactions t JOIN accounts a ON a.id = t.account_id "
            f"WHERE {where} ORDER BY ABS(t.amount) DESC, t.date DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
    return [dict(r) for r in rows]


def _spend_by_cat(start, end):
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT category, ROUND(SUM(-amount),2) s FROM transactions "
            "WHERE amount < 0 AND category != 'Transfers' AND date >= ? AND date <= ? "
            "GROUP BY category", (start.isoformat(), end.isoformat()),
        ).fetchall()
    return {r["category"]: r["s"] for r in rows}


def _insight(id, title, summary, significance, evidence, *,
             confidence=0.85, tone="neutral", impact=None, evidence_count=None, group=None):
    return {
        "id": id, "title": title, "summary": summary, "group": group,
        "significance": round(float(significance), 3), "confidence": confidence,
        "tone": tone, "estimated_impact": impact,
        "evidence": evidence, "evidence_count": evidence_count if evidence_count is not None else len(evidence),
    }


# ── detectors ────────────────────────────────────────────────────────────────
def d_category_changes(anchor, mn):
    recent_start = anchor - timedelta(days=RECENT_DAYS)
    if mn > recent_start - timedelta(days=60):
        return []  # not enough history to have a baseline
    prior_start, prior_end = mn, recent_start - timedelta(days=1)
    recent, prior = _spend_by_cat(recent_start, anchor), _spend_by_cat(prior_start, prior_end)
    r_months, p_months = _months(recent_start, anchor), _months(prior_start, prior_end)

    out = []
    for cat, rspend in recent.items():
        r_rate, p_rate = rspend / r_months, prior.get(cat, 0) / p_months
        if p_rate < 25:
            continue
        delta = r_rate - p_rate
        pct = delta / p_rate
        if abs(pct) < 0.35 or abs(delta) < 80:
            continue
        up = delta > 0
        rows = _fetch("t.amount < 0 AND t.category = ? AND t.date >= ?", [cat, recent_start.isoformat()])
        out.append(_insight(
            f"cat-change:{cat}",
            f"{cat} is {'up' if up else 'down'} {abs(round(pct * 100))}% versus your norm",
            f"Lately you're spending about {_m(r_rate)}/mo on {cat}, versus a typical "
            f"{_m(p_rate)}/mo over your history.",
            significance=min(1.0, abs(delta) / 800), evidence=rows, group=cat,
            tone="watch" if up else "positive", impact=round(delta, 2) if up else None,
        ))
    return out


def d_recurring(anchor, mn):
    rec = analysis.recurring_charges()
    active = [r for r in rec if r["active"]]
    out = []
    if active:
        total = sum(r["monthly_cost"] for r in active)
        subs = [r for r in active if r["category"] in ("Subscriptions", "Uncategorized") and r["monthly_cost"] < 40]
        rows = _fetch("t.payee IN (%s) AND t.amount < 0" % ",".join("?" * len(active)),
                      [r["payee"] for r in active]) if active else []
        out.append(_insight(
            "recurring-total",
            f"{len(active)} recurring charges totaling {_m(total)}/mo",
            f"You're committed to about {_m(total)}/mo ({_m(total * 12)}/yr) in recurring charges. "
            + (f"{len(subs)} of them are small subscriptions worth a periodic review." if subs else ""),
            significance=min(1.0, total / 1500), evidence=rows, tone="neutral",
            evidence_count=len(active),
        ))

    # new-ish recurring charge (first seen recently)
    with db.get_conn() as conn:
        firsts = {r["p"]: r["f"] for r in conn.execute(
            "SELECT LOWER(payee) p, MIN(date) f FROM transactions "
            "WHERE amount < 0 GROUP BY LOWER(payee)")}
    for r in active:
        first = firsts.get(r["payee"].lower())
        if first and date.fromisoformat(first) >= anchor - timedelta(days=95):
            rows = _fetch("LOWER(t.payee) = ? AND t.amount < 0", [r["payee"].lower()])
            out.append(_insight(
                f"new-recurring:{r['payee']}",
                f"New recurring charge: {r['payee']}",
                f"{r['payee']} started charging {_m(r['amount'])} {r['cadence']} in the last few months "
                f"— that's about {_m(r['monthly_cost'])}/mo you didn't have before.",
                significance=min(1.0, r["monthly_cost"] / 200 + 0.2), evidence=rows,
                tone="watch", impact=r["monthly_cost"],
            ))
    return out


def d_anomalies(anchor, mn):
    out = []
    for a in analysis.anomalies()[:4]:
        rows = _fetch("t.amount < 0 AND t.category = ? AND substr(t.date,1,7) = ?",
                      [a["category"], a["month"]])
        out.append(_insight(
            f"spike:{a['category']}:{a['month']}",
            f"{a['category']} spiked in {a['month']}",
            f"You spent {_m(a['amount'])} on {a['category']} that month — {a['factor']}× your "
            f"typical {_m(a['typical'])}.",
            significance=min(1.0, (a["amount"] - a["typical"]) / 1200), evidence=rows,
            tone="neutral", impact=round(a["amount"] - a["typical"], 2), group=a["category"],
        ))
    return out


def d_one_offs(anchor, mn):
    """Big one-time charges that distort the recent picture — powers the reframe."""
    recent_start = anchor - timedelta(days=RECENT_DAYS)
    recurring = {r["payee"].lower() for r in analysis.recurring_charges()}
    rows = _fetch("t.amount < 0 AND t.category != 'Transfers' AND t.date >= ?",
                  [recent_start.isoformat()], limit=200)
    total = sum(-r["amount"] for r in rows)
    one_offs = [r for r in rows if r["payee"].lower() not in recurring and -r["amount"] > 250]
    one_offs.sort(key=lambda r: r["amount"])  # most negative first
    top = one_offs[:5]
    big_sum = sum(-r["amount"] for r in top)
    if not top or total <= 0 or big_sum / total < 0.15:
        return []
    without = total - big_sum
    return [_insight(
        "one-offs",
        f"A few one-offs shaped your recent spending",
        f"{len(top)} one-time charges added up to {_m(big_sum)} of your {_m(total)} recent spend. "
        f"Excluding them, you spent {_m(without)} — closer to your normal.",
        significance=min(1.0, big_sum / 1500), evidence=top, tone="neutral",
    )]


def d_concentration(anchor, mn):
    """A large share of discretionary spend from just a few days."""
    recent_start = anchor - timedelta(days=RECENT_DAYS)
    placeholders = ",".join("?" * len(FIXED_CATS))
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT date, ROUND(SUM(-amount),2) s FROM transactions "
            f"WHERE amount < 0 AND category != 'Transfers' AND category NOT IN ({placeholders}) "
            f"AND date >= ? GROUP BY date", [*FIXED_CATS, recent_start.isoformat()],
        ).fetchall()
    if len(rows) < 10:
        return []
    days = sorted(([r["date"], r["s"]] for r in rows), key=lambda x: x[1], reverse=True)
    total = sum(d[1] for d in days)
    topn = days[:6]
    share = sum(d[1] for d in topn) / total if total else 0
    if share < 0.4:
        return []
    top_dates = [d[0] for d in topn]
    ev = _fetch("t.amount < 0 AND t.category NOT IN (%s) AND t.date IN (%s)"
                % (placeholders, ",".join("?" * len(top_dates))), [*FIXED_CATS, *top_dates])
    return [_insight(
        "concentration",
        f"{round(share * 100)}% of your flexible spending came from 6 days",
        f"Of {_m(total)} in recent discretionary spending, {_m(sum(d[1] for d in topn))} landed on "
        f"just six days — the rest of the time you spent lightly.",
        significance=min(1.0, share), evidence=ev, tone="neutral",
    )]


def d_weekend(anchor, mn):
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT date, -amount amt FROM transactions "
            "WHERE amount < 0 AND category != 'Transfers'"
        ).fetchall()
    wk, wknd = defaultdict(float), defaultdict(float)
    for r in rows:
        d = date.fromisoformat(r["date"])
        (wknd if d.weekday() >= 5 else wk)[r["date"]] += r["amt"]
    if len(wk) < 10 or len(wknd) < 5:
        return []
    wk_avg, wknd_avg = mean(wk.values()), mean(wknd.values())
    if wk_avg <= 0 or wknd_avg / wk_avg < 1.4:
        return []
    ratio = wknd_avg / wk_avg
    return [_insight(
        "weekend",
        f"Weekend days cost {ratio:.1f}× your weekdays",
        f"You spend about {_m(wknd_avg)} on an average weekend day versus {_m(wk_avg)} on a weekday.",
        significance=min(1.0, (ratio - 1) / 2), confidence=0.7, evidence=[], tone="neutral",
    )]


def d_small_purchases(anchor, mn):
    recent_start = anchor - timedelta(days=RECENT_DAYS)
    rows = _fetch("t.amount < 0 AND t.category != 'Transfers' AND -t.amount < 15 AND t.date >= ?",
                  [recent_start.isoformat()], limit=300)
    if len(rows) < 20:
        return []
    total = sum(-r["amount"] for r in rows)
    return [_insight(
        "small-purchases",
        f"{len(rows)} small purchases added up to {_m(total)}",
        f"Charges under $15 are easy to miss, but in the last {RECENT_DAYS} days they totaled "
        f"{_m(total)} — roughly {_m(total / 2)}/mo.",
        significance=min(1.0, total / 600), evidence=rows[:30], tone="neutral", evidence_count=len(rows),
    )]


def d_lifestyle_inflation(anchor, mn):
    """Income rose but savings didn't keep pace (first half vs second half of history)."""
    if (anchor - mn).days < 240:
        return []
    mid = mn + (anchor - mn) / 2
    def half(a, b):
        with db.get_conn() as conn:
            r = conn.execute(
                "SELECT COALESCE(SUM(CASE WHEN amount>0 THEN amount END),0) inc, "
                "COALESCE(SUM(CASE WHEN amount<0 THEN -amount END),0) sp FROM transactions "
                "WHERE category != 'Transfers' AND date >= ? AND date <= ?",
                (a.isoformat(), b.isoformat())).fetchone()
        months = _months(a, b)
        return r["inc"] / months, r["sp"] / months
    inc1, sp1 = half(mn, mid)
    inc2, sp2 = half(mid + timedelta(days=1), anchor)
    if inc1 < 100 or inc2 <= inc1 * 1.04:
        return []
    inc_growth = (inc2 - inc1) / inc1
    sr1 = (inc1 - sp1) / inc1
    sr2 = (inc2 - sp2) / inc2
    if sr2 >= sr1 - 0.02:
        return []  # savings rate held or improved — not inflation
    return [_insight(
        "lifestyle-inflation",
        f"Income rose {round(inc_growth * 100)}%, but you're saving less of it",
        f"Your monthly income grew from {_m(inc1)} to {_m(inc2)}, yet your savings rate slipped from "
        f"{round(sr1 * 100)}% to {round(sr2 * 100)}% — the extra income is mostly being spent.",
        significance=min(1.0, (sr1 - sr2) * 3 + 0.3), confidence=0.75, evidence=[], tone="watch",
    )]


def d_duplicates(anchor, mn):
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, date, payee, amount, category FROM transactions "
            "WHERE amount < 0 AND payee != '' ORDER BY LOWER(payee), amount, date"
        ).fetchall()
    flagged = []
    for i in range(1, len(rows)):
        a, b = rows[i - 1], rows[i]
        if (a["payee"].lower() == b["payee"].lower() and abs(a["amount"] - b["amount"]) < 0.01
                and 0 <= (date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days <= 3):
            flagged += [dict(a), dict(b)]
    if not flagged:
        return []
    # de-dup the evidence list itself
    seen, ev = set(), []
    for r in flagged:
        if r["id"] not in seen:
            seen.add(r["id"]); ev.append(r)
    return [_insight(
        "duplicates",
        f"{len(ev)} charges look like possible duplicates",
        "Some charges share the same merchant and amount within a few days — worth a quick check that "
        "you weren't billed twice.",
        significance=0.6, confidence=0.6, evidence=ev, tone="watch",
    )]


DETECTORS = [
    d_category_changes, d_recurring, d_anomalies, d_one_offs, d_concentration,
    d_weekend, d_small_purchases, d_lifestyle_inflation, d_duplicates,
]


def discoveries(limit=14):
    anchor, mn = _anchor()
    if not anchor:
        return []
    found = []
    for detector in DETECTORS:
        try:
            found.extend(detector(anchor, mn))
        except Exception:
            continue  # a broken detector must never take down the feed
    found.sort(key=lambda x: x["significance"] * x["confidence"], reverse=True)
    # Keep at most one finding per category group so the feed stays a tight handful.
    seen, result = set(), []
    for d in found:
        g = d.get("group")
        if g and g in seen:
            continue
        if g:
            seen.add(g)
        result.append(d)
        if len(result) >= limit:
            break
    return result
