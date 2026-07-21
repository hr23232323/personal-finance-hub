"""Deterministic analysis: the math layer that finds patterns worth surfacing.

This is the substrate the LLM coach reads from. Everything here is plain SQL +
arithmetic — no LLM, no guessing — so figures are always exact:

  • recurring_charges  — subscriptions & regular bills (incl. "forgotten" ones)
  • anomalies          — category-months that spiked vs. their own norm
  • category_over_time  — monthly spend per category (for trend charts)
  • daily_spending      — spend per day (for the calendar heatmap)
  • money_flow          — income → categories (for the Sankey)
"""
from collections import defaultdict
from datetime import date
from statistics import mean, median, pstdev

from . import db, queries


def _range_where(start=None, end=None):
    clauses, params = ["amount < 0", "category != 'Transfers'"], []
    if start:
        clauses.append("date >= ?"); params.append(start)
    if end:
        clauses.append("date <= ?"); params.append(end)
    return "WHERE " + " AND ".join(clauses), params


# ── recurring charges ────────────────────────────────────────────────────────
_CADENCES = [  # (min_gap, max_gap, label, period_days)
    (5, 9, "weekly", 7), (11, 17, "biweekly", 14), (25, 35, "monthly", 30),
    (55, 65, "monthly", 30), (84, 95, "quarterly", 91), (350, 380, "yearly", 365),
]


def _cadence(gap_days):
    for lo, hi, label, period in _CADENCES:
        if lo <= gap_days <= hi:
            return label, period
    return None


def recurring_charges(min_occurrences=3, amount_tolerance=0.15):
    """Merchants charged on a regular cadence for a consistent amount —
    subscriptions and recurring bills. Flags whether each still looks active."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT payee, category, date, amount FROM transactions "
            "WHERE amount < 0 AND category != 'Transfers' AND payee != '' ORDER BY date"
        ).fetchall()

    groups = defaultdict(list)
    for r in rows:
        groups[r["payee"].lower()].append(r)

    today = date.today()
    out = []
    for items in groups.values():
        if len(items) < min_occurrences:
            continue
        amounts = [abs(i["amount"]) for i in items]
        avg = mean(amounts)
        if avg <= 0:
            continue
        # inconsistent amount => not a subscription (e.g. Amazon, groceries)
        if len(amounts) > 1 and pstdev(amounts) / avg > amount_tolerance:
            continue
        dates = [date.fromisoformat(i["date"]) for i in items]
        gaps = [(dates[k] - dates[k - 1]).days for k in range(1, len(dates))]
        cadence = _cadence(median(gaps))
        if not cadence:
            continue
        label, period = cadence
        last = dates[-1]
        out.append({
            "payee": items[0]["payee"],
            "category": items[0]["category"],
            "amount": round(avg, 2),
            "cadence": label,
            "occurrences": len(items),
            "last_charge": last.isoformat(),
            "monthly_cost": round(avg * 30.0 / period, 2),
            "active": (today - last).days <= period * 1.8,
        })
    out.sort(key=lambda x: x["monthly_cost"], reverse=True)
    return out


# ── anomalies ────────────────────────────────────────────────────────────────
def anomalies(z=2.0, floor=120.0):
    """Category-months whose spend sits well above that category's own norm."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT category, substr(date,1,7) AS month, ROUND(SUM(-amount),2) AS spend "
            "FROM transactions WHERE amount < 0 AND category != 'Transfers' "
            "GROUP BY category, month"
        ).fetchall()

    by_cat = defaultdict(dict)
    for r in rows:
        by_cat[r["category"]][r["month"]] = r["spend"]

    out = []
    for category, months in by_cat.items():
        vals = list(months.values())
        if len(vals) < 4:
            continue
        avg, sd = mean(vals), pstdev(vals)
        if sd == 0:
            continue
        for month, spend in months.items():
            if spend > avg + z * sd and spend > floor and spend > avg * 1.4:
                out.append({
                    "category": category, "month": month, "amount": spend,
                    "typical": round(avg, 2), "factor": round(spend / avg, 2),
                })
    out.sort(key=lambda x: x["amount"] - x["typical"], reverse=True)
    return out


# ── chart feeds ──────────────────────────────────────────────────────────────
def category_over_time(start=None, end=None, top=6):
    """Monthly spend per category, limited to the top N categories (+ Other)."""
    where, params = _range_where(start, end)
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT category, substr(date,1,7) AS month, ROUND(SUM(-amount),2) AS spend "
            f"FROM transactions {where} GROUP BY category, month", params
        ).fetchall()

    months = sorted({r["month"] for r in rows})
    totals = defaultdict(float)
    for r in rows:
        totals[r["category"]] += r["spend"]
    keep = {c for c, _ in sorted(totals.items(), key=lambda x: x[1], reverse=True)[:top]}

    grid = defaultdict(lambda: defaultdict(float))  # category -> month -> spend
    for r in rows:
        cat = r["category"] if r["category"] in keep else "Other"
        grid[cat][r["month"]] += r["spend"]

    order = [c for c, _ in sorted(totals.items(), key=lambda x: x[1], reverse=True) if c in keep]
    if "Other" in grid:
        order.append("Other")
    series = [{"name": cat, "data": [round(grid[cat].get(m, 0.0), 2) for m in months]} for cat in order]
    return {"months": months, "series": series}


def daily_spending(start=None, end=None):
    """[[date, spend], ...] for the calendar heatmap."""
    where, params = _range_where(start, end)
    with db.get_conn() as conn:
        rows = conn.execute(
            f"SELECT date, ROUND(SUM(-amount),2) AS spend FROM transactions {where} "
            f"GROUP BY date ORDER BY date", params
        ).fetchall()
    return [[r["date"], r["spend"]] for r in rows]


def money_flow(start=None, end=None):
    """Sankey feed: income sources → Income → spending categories (+ unspent)."""
    inc_clauses, inc_params = ["amount > 0", "category != 'Transfers'"], []
    if start:
        inc_clauses.append("date >= ?"); inc_params.append(start)
    if end:
        inc_clauses.append("date <= ?"); inc_params.append(end)
    with db.get_conn() as conn:
        income = conn.execute(
            "SELECT payee, ROUND(SUM(amount),2) AS amt FROM transactions "
            "WHERE " + " AND ".join(inc_clauses) + " GROUP BY payee HAVING amt > 0 ORDER BY amt DESC",
            inc_params,
        ).fetchall()

    cats = queries.spending_by_category(start, end)
    total_income = sum(r["amt"] for r in income)
    total_spent = sum(c["spent"] for c in cats)

    nodes, links = {"Income"}, []
    for r in income:
        src = r["payee"][:28]
        nodes.add(src)
        links.append({"source": src, "target": "Income", "value": r["amt"]})
    for c in cats:
        nodes.add(c["category"])
        links.append({"source": "Income", "target": c["category"], "value": c["spent"]})
    saved = round(total_income - total_spent, 2)
    if saved > 0:
        nodes.add("Unspent / saved")
        links.append({"source": "Income", "target": "Unspent / saved", "value": saved})

    return {"nodes": [{"name": n} for n in nodes], "links": links}
