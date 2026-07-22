"""The Discoveries engine — deterministic pattern detectors (Tier 1).

Each detector emits zero or more Insight dicts sharing one contract:

    {
      id, title, summary,          # what it found, in plain language + real numbers
      significance, confidence,    # 0..1, used to rank the feed
      tone,                        # "neutral" | "positive" | "watch"
      estimated_impact,            # optional $/period, or None
      chart,                       # a small visual proof, or None
      evidence, evidence_count,    # the actual transactions behind the finding
      group,                       # optional key, so we show one finding per group
    }

Chart kinds (rendered as small inline visuals by the frontend):
    {"kind": "trend",   "months":[...], "values":[...], "tone": ...}      # sparkline
    {"kind": "months",  "months":[...], "values":[...], "highlight": i}   # bars, one hot
    {"kind": "hbars",   "items":[{"label","value"}, ...]}                 # horizontal bars
    {"kind": "compare", "items":[{"label","value"}, ...]}                 # 2-3 vertical bars

Everything here is exact SQL + arithmetic. The LLM tier (coach.py) reads these
facts and narrates them — it never produces a number.

"Now" is the most recent transaction date (so imported history works); baselines
are this user's own history — a finding only surfaces if it's unusual for them.
"""
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean

from . import analysis, classify, db

RECENT_DAYS = 90
COVERED_BY_LENS = {"Dining", "Shopping", "Travel"}  # have dedicated detectors


def _m(n):
    return f"${round(abs(n)):,}"


def _rows(sql, params=()):
    with db.get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _anchor():
    row = _rows("SELECT MAX(date) mx, MIN(date) mn FROM transactions")
    if not row or not row[0]["mx"]:
        return None, None
    return date.fromisoformat(row[0]["mx"]), date.fromisoformat(row[0]["mn"])


def _months(a, b):
    return max(0.5, (b - a).days / 30.44)


def _all_months(mn, anchor):
    months, y, m = [], mn.year, mn.month
    while (y, m) <= (anchor.year, anchor.month):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return months


def _fetch(where, params, limit=30):
    return _rows(
        f"SELECT t.id, t.date, t.amount, t.payee, t.category, a.name AS account "
        f"FROM transactions t JOIN accounts a ON a.id = t.account_id "
        f"WHERE {where} ORDER BY ABS(t.amount) DESC, t.date DESC LIMIT ?", [*params, limit])


def _spend_by_cat(start, end):
    return {r["category"]: r["s"] for r in _rows(
        "SELECT category, ROUND(SUM(-amount),2) s FROM transactions "
        "WHERE amount < 0 AND category != 'Transfers' AND date >= ? AND date <= ? GROUP BY category",
        (start.isoformat(), end.isoformat()))}


def _cat_monthly(category, mn, anchor):
    """Continuous monthly series (zero-filled) so sparklines are honest."""
    got = {r["m"]: r["s"] for r in _rows(
        "SELECT substr(date,1,7) m, ROUND(SUM(-amount),2) s FROM transactions "
        "WHERE amount < 0 AND category = ? GROUP BY m", (category,))}
    months = _all_months(mn, anchor)
    return months, [got.get(mo, 0.0) for mo in months]


def _insight(id, title, summary, significance, evidence, *, confidence=0.85,
             tone="neutral", impact=None, evidence_count=None, group=None, chart=None):
    return {
        "id": id, "title": title, "summary": summary, "group": group,
        "significance": round(float(significance), 3), "confidence": confidence,
        "tone": tone, "estimated_impact": impact, "chart": chart,
        "evidence": evidence, "evidence_count": evidence_count if evidence_count is not None else len(evidence),
    }


def _one_per_payee(items):
    ev = []
    for r in items:
        row = _fetch("LOWER(t.payee) = ? AND t.amount < 0", [r["payee"].lower()], limit=1)
        if row:
            ev.append(row[0])
    return ev


# ── keystone: needs vs. discretionary ────────────────────────────────────────
def d_needs_vs_discretionary(anchor, mn):
    r_start = anchor - timedelta(days=RECENT_DAYS)
    recent = _spend_by_cat(r_start, anchor)
    r_months = _months(r_start, anchor)

    def split(d):
        needs = sum(v for c, v in d.items() if classify.kind_of(c) == "needs")
        disc = sum(v for c, v in d.items() if classify.kind_of(c) == "discretionary")
        return needs, disc

    needs, disc = split(recent)
    total = needs + disc
    if total < 200:
        return []
    disc_share = disc / total

    change, tone, sig = "", "neutral", 0.72
    p_start = r_start - timedelta(days=RECENT_DAYS)
    if mn <= p_start:
        pn, pd = split(_spend_by_cat(p_start, r_start - timedelta(days=1)))
        if pn + pd > 0:
            prior_share = pd / (pn + pd)
            if abs(disc_share - prior_share) >= 0.03:
                up = disc_share > prior_share
                change = (f" Your discretionary share {'rose' if up else 'eased'} from "
                          f"{round(prior_share * 100)}% to {round(disc_share * 100)}%.")
                tone = "watch" if up else "positive"
                sig = min(0.9, 0.72 + abs(disc_share - prior_share) * 1.5)

    return [_insight(
        "needs-vs-discretionary",
        f"{round(needs / total * 100)}% needs, {round(disc_share * 100)}% discretionary",
        f"About {_m(needs / r_months)}/mo of your spending goes to needs (housing, loans, bills, "
        f"groceries) and {_m(disc / r_months)}/mo to discretionary (dining, shopping, travel, "
        f"subscriptions).{change}",
        significance=sig, evidence=[], tone=tone,
        chart={"kind": "compare", "items": [
            {"label": "Needs", "value": round(needs / r_months, 2)},
            {"label": "Discretionary", "value": round(disc / r_months, 2)}]},
    )]


# ── big moments: trips + one-offs, aggregated (replaces per-category spikes) ──
def d_big_moments(anchor, mn):
    recurring = {r["payee"].lower() for r in analysis.recurring_charges()}
    rows = _rows(
        "SELECT t.id, t.date, t.amount, t.payee, t.category, a.name AS account "
        "FROM transactions t JOIN accounts a ON a.id = t.account_id "
        "WHERE t.amount < 0 AND t.category != 'Transfers' ORDER BY t.date")
    if not rows:
        return []
    total_spend = sum(-r["amount"] for r in rows)

    moments = []
    # cluster Travel into trips by date proximity
    travel = [r for r in rows if r["category"] == "Travel"]
    cluster = []
    for r in travel:
        if cluster and (date.fromisoformat(r["date"]) - date.fromisoformat(cluster[-1]["date"])).days > 4:
            moments.append(_trip(cluster)); cluster = []
        cluster.append(r)
    if cluster:
        moments.append(_trip(cluster))
    moments = [m for m in moments if m and m["value"] > 300]
    # big one-off purchases (non-travel, non-recurring)
    for r in rows:
        if r["category"] != "Travel" and -r["amount"] > 250 and r["payee"].lower() not in recurring:
            moments.append({"label": r["payee"], "value": round(-r["amount"], 2),
                            "tx": [r], "kind": "one-off"})
    if len(moments) < 2:
        return []
    moments.sort(key=lambda x: x["value"], reverse=True)
    top = moments[:7]
    big_sum = sum(m["value"] for m in top)
    if total_spend <= 0 or big_sum / total_spend < 0.08:
        return []
    pct = big_sum / total_spend
    baseline_mo = (total_spend - big_sum) / _months(mn, anchor)
    n_trips = sum(1 for m in top if m["kind"] == "trip")
    n_off = len(top) - n_trips
    parts = []
    if n_trips:
        parts.append(f"{n_trips} trip" + ("s" if n_trips > 1 else ""))
    if n_off:
        parts.append(f"{n_off} one-time purchase" + ("s" if n_off > 1 else ""))
    ev = [x for m in top for x in m["tx"][:4]][:30]
    return [_insight(
        "big-moments",
        f"{len(top)} big moments accounted for {_m(big_sum)}",
        f"{' and '.join(parts)} added up to {_m(big_sum)} — about {round(pct * 100)}% of everything "
        f"you spent. Excluding them, your spending held around {_m(baseline_mo)}/mo.",
        significance=min(1.0, pct * 2 + 0.35), evidence=ev, tone="neutral", evidence_count=len(ev),
        chart={"kind": "hbars", "items": [{"label": m["label"][:16], "value": m["value"]} for m in top]},
    )]


def _trip(cluster):
    if not cluster:
        return None
    return {"label": f"Trip · {cluster[0]['date'][:7]}", "value": round(sum(-r["amount"] for r in cluster), 2),
            "tx": cluster, "kind": "trip"}


# ── eating & going out ───────────────────────────────────────────────────────
def d_eating_out(anchor, mn):
    r_start = anchor - timedelta(days=RECENT_DAYS)
    rows = _rows("SELECT payee, -amount amt FROM transactions "
                 "WHERE amount < 0 AND category = 'Dining' AND date >= ?", (r_start.isoformat(),))
    if len(rows) < 5:
        return []
    r_months = _months(r_start, anchor)
    subs = defaultdict(float)
    for r in rows:
        subs[classify.dining_subtype(r["payee"])] += r["amt"]
    total = sum(subs.values())
    disc = sum(v for c, v in _spend_by_cat(r_start, anchor).items() if classify.kind_of(c) == "discretionary")
    share = total / disc if disc else 0

    trend = ""
    p_start = r_start - timedelta(days=RECENT_DAYS)
    if mn <= p_start:
        prior = _rows("SELECT -amount amt FROM transactions WHERE amount < 0 AND category = 'Dining' "
                      "AND date >= ? AND date < ?", (p_start.isoformat(), r_start.isoformat()))
        p_mo = sum(r["amt"] for r in prior) / _months(p_start, r_start)
        if p_mo > 0 and total / r_months > p_mo * 1.25:
            trend = f" That's up from about {_m(p_mo)}/mo."
        elif p_mo > 0 and total / r_months < p_mo * 0.8:
            trend = f" That's down from about {_m(p_mo)}/mo."

    ranked = sorted(subs.items(), key=lambda x: x[1], reverse=True)
    breakdown = ", ".join(f"{_m(v)} {k.lower()}" for k, v in ranked if v > 0)
    return [_insight(
        "eating-out",
        f"Eating out runs {_m(total / r_months)}/mo",
        f"Over the last 3 months you spent {_m(total)} eating out — {breakdown}. That's about "
        f"{round(share * 100)}% of your discretionary spending.{trend}",
        significance=min(0.72, 0.35 + share), evidence=_fetch(
            "t.category = 'Dining' AND t.date >= ?", [r_start.isoformat()]), tone="neutral",
        group="Dining",
        chart={"kind": "hbars", "items": [{"label": k, "value": round(v, 2)} for k, v in ranked]},
    )]


# ── shopping ─────────────────────────────────────────────────────────────────
def d_shopping(anchor, mn):
    r_start = anchor - timedelta(days=RECENT_DAYS)
    rows = _rows("SELECT payee, -amount amt FROM transactions "
                 "WHERE amount < 0 AND category = 'Shopping' AND date >= ?", (r_start.isoformat(),))
    if len(rows) < 5:
        return []
    r_months = _months(r_start, anchor)
    total, n = sum(r["amt"] for r in rows), len(rows)
    by_merchant = defaultdict(list)
    for r in rows:
        by_merchant[r["payee"]].append(r["amt"])
    top_payee = max(by_merchant, key=lambda k: len(by_merchant[k]))
    tp = by_merchant[top_payee]
    tp_avg = sum(tp) / len(tp)

    order_note = ""
    p_start = r_start - timedelta(days=RECENT_DAYS)
    if mn <= p_start:
        prior = _rows("SELECT -amount amt FROM transactions WHERE amount < 0 AND category = 'Shopping' "
                      "AND LOWER(payee) = ? AND date >= ? AND date < ?",
                      (top_payee.lower(), p_start.isoformat(), r_start.isoformat()))
        if prior:
            p_avg = sum(r["amt"] for r in prior) / len(prior)
            if abs(tp_avg - p_avg) / p_avg > 0.2:
                order_note = f" (was {_m(p_avg)})"

    merch_totals = sorted(((k, sum(v)) for k, v in by_merchant.items()), key=lambda x: x[1], reverse=True)
    return [_insight(
        "shopping",
        f"Shopping is {_m(total / r_months)}/mo across {n} purchases",
        f"{top_payee} was your most frequent — {len(tp)} orders averaging {_m(tp_avg)}{order_note}.",
        significance=min(0.6, 0.3 + total / (r_months * 2000)), evidence=_fetch(
            "t.category = 'Shopping' AND t.date >= ?", [r_start.isoformat()]), tone="neutral",
        group="Shopping",
        chart={"kind": "hbars", "items": [{"label": k[:16], "value": round(v, 2)} for k, v in merch_totals[:6]]},
    )]


# ── weekly rhythm (day of week) ──────────────────────────────────────────────
def d_weekly_rhythm(anchor, mn):
    rows = _rows("SELECT date, category, -amount amt FROM transactions "
                 "WHERE amount < 0 AND category != 'Transfers'")
    sums = [0.0] * 7
    for r in rows:
        if classify.kind_of(r["category"]) != "discretionary":
            continue
        sums[date.fromisoformat(r["date"]).weekday()] += r["amt"]
    if sum(sums) < 100:
        return []
    weeks = max(1.0, (anchor - mn).days / 7)
    avg = [round(s / weeks, 2) for s in sums]
    labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    full = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    top = avg.index(max(avg))
    wk, wknd = mean(avg[0:5]), mean(avg[5:7])
    ratio = wknd / wk if wk else 1.0

    if ratio >= 1.25:
        summary = (f"You spend more on weekends — about {_m(wknd)} on an average weekend day versus "
                   f"{_m(wk)} on a weekday ({ratio:.1f}×).")
        sig = min(0.6, 0.35 + (ratio - 1) * 0.4)
    elif ratio <= 0.8:
        summary = (f"You actually spend more midweek — about {_m(wk)} on a weekday versus {_m(wknd)} "
                   f"on a weekend day.")
        sig = 0.45
    else:
        summary = f"Your discretionary spending is fairly even across the week, peaking a little on {full[top]}s."
        sig = 0.35
    return [_insight(
        "weekly-rhythm",
        f"You spend most on {full[top]}s",
        summary, significance=sig, evidence=[], tone="neutral",
        chart={"kind": "months", "months": labels, "values": avg, "highlight": top},
    )]


# ── recurring: subscriptions + fixed commitments ─────────────────────────────
def d_recurring(anchor, mn):
    active = [r for r in analysis.recurring_charges() if r["active"]]
    out = []
    fixed = sorted((r for r in active if r["category"] in classify.NEEDS),
                   key=lambda x: x["monthly_cost"], reverse=True)
    subs = sorted((r for r in active if r["category"] not in classify.NEEDS),
                  key=lambda x: x["monthly_cost"], reverse=True)

    if subs:
        sub_total = sum(r["monthly_cost"] for r in subs)
        forgotten = [r for r in subs if r["category"] == "Uncategorized"]
        note = (f" One of them, {forgotten[0]['payee']}, isn't even categorized — worth checking you "
                f"still use it." if forgotten else "")
        out.append(_insight(
            "subscriptions",
            f"{len(subs)} subscriptions costing {_m(sub_total)}/mo",
            f"Your discretionary recurring charges add up to about {_m(sub_total)}/mo "
            f"({_m(sub_total * 12)}/yr) — these are the ones you can actually change.{note}",
            significance=min(0.8, 0.4 + 0.03 * len(subs) + (0.15 if forgotten else 0)),
            evidence=_one_per_payee(subs), tone="neutral", evidence_count=len(subs),
            chart={"kind": "hbars", "items": [{"label": r["payee"][:16], "value": r["monthly_cost"]} for r in subs[:6]]},
        ))
    if fixed:
        fx = sum(r["monthly_cost"] for r in fixed)
        out.append(_insight(
            "fixed-commitments",
            f"{_m(fx)}/mo goes to fixed commitments",
            f"Rent, loan, insurance and utilities run about {_m(fx)}/mo ({_m(fx * 12)}/yr) — the stable "
            f"base of your spending, not really discretionary.",
            significance=0.28, evidence=_one_per_payee(fixed), tone="neutral", evidence_count=len(fixed),
            chart={"kind": "hbars", "items": [{"label": r["payee"][:16], "value": r["monthly_cost"]} for r in fixed[:6]]},
        ))
    return out


# ── category changes (for categories without a dedicated lens) ───────────────
def d_category_changes(anchor, mn):
    r_start = anchor - timedelta(days=RECENT_DAYS - 30)
    if mn > r_start - timedelta(days=60):
        return []
    recent, prior = _spend_by_cat(r_start, anchor), _spend_by_cat(mn, r_start - timedelta(days=1))
    r_months, p_months = _months(r_start, anchor), _months(mn, r_start)
    recurring = {r["payee"].lower() for r in analysis.recurring_charges()}
    out = []
    for cat, rspend in recent.items():
        if cat in COVERED_BY_LENS or classify.kind_of(cat) == "other":
            continue
        r_rate, p_rate = rspend / r_months, prior.get(cat, 0) / p_months
        if p_rate < 25:
            continue
        delta = r_rate - p_rate
        pct = delta / p_rate
        if abs(pct) < 0.35 or abs(delta) < 80:
            continue
        months, values = _cat_monthly(cat, mn, anchor)
        if sum(1 for v in values if v > 0) < 0.6 * len(values):
            continue  # lumpy/seasonal — not a real trend
        up = delta > 0
        if up:
            # If one non-recurring charge explains most of the jump, it's a one-off
            # (already surfaced in "big moments") — not a spending trend.
            biggest = _fetch("t.amount < 0 AND t.category = ? AND t.date >= ?",
                             [cat, r_start.isoformat()], limit=1)
            if (biggest and biggest[0]["payee"].lower() not in recurring
                    and -biggest[0]["amount"] > delta * r_months * 0.5):
                continue
        tone = "watch" if up else "positive"
        out.append(_insight(
            f"cat-change:{cat}",
            f"{cat} is {'up' if up else 'down'} {abs(round(pct * 100))}% versus your norm",
            f"Lately you're spending about {_m(r_rate)}/mo on {cat}, versus a typical {_m(p_rate)}/mo "
            f"over your history.",
            significance=min(1.0, abs(delta) / 800), evidence=_fetch(
                "t.amount < 0 AND t.category = ? AND t.date >= ?", [cat, r_start.isoformat()]),
            group=cat, tone=tone, impact=round(delta, 2) if up else None,
            chart={"kind": "trend", "months": months, "values": values, "tone": tone},
        ))
    return out


# ── small discretionary purchases (recurring excluded) ───────────────────────
def d_small_purchases(anchor, mn):
    r_start = anchor - timedelta(days=60)
    recurring = {r["payee"].lower() for r in analysis.recurring_charges()}
    rows = [r for r in _fetch(
        "t.amount < 0 AND t.category != 'Transfers' AND -t.amount < 15 AND t.date >= ?",
        [r_start.isoformat()], limit=500) if r["payee"].lower() not in recurring]
    if len(rows) < 20:
        return []
    total = sum(-r["amount"] for r in rows)
    by_cat = defaultdict(float)
    for r in rows:
        by_cat[r["category"]] += -r["amount"]
    ranked = sorted(by_cat.items(), key=lambda x: x[1], reverse=True)[:5]
    return [_insight(
        "small-purchases",
        f"{len(rows)} small purchases added up to {_m(total)}",
        f"One-off charges under $15 (not subscriptions) are easy to miss, but in the last 60 days they "
        f"totaled {_m(total)} — roughly {_m(total / 2)}/mo.",
        significance=min(0.7, total / 600), evidence=rows[:30], tone="neutral", evidence_count=len(rows),
        chart={"kind": "hbars", "items": [{"label": c[:12], "value": round(v, 2)} for c, v in ranked]},
    )]


def d_lifestyle_inflation(anchor, mn):
    if (anchor - mn).days < 240:
        return []
    mid = mn + (anchor - mn) / 2

    def half(a, b):
        r = _rows("SELECT COALESCE(SUM(CASE WHEN amount>0 THEN amount END),0) inc, "
                  "COALESCE(SUM(CASE WHEN amount<0 THEN -amount END),0) sp FROM transactions "
                  "WHERE category != 'Transfers' AND date >= ? AND date <= ?",
                  (a.isoformat(), b.isoformat()))[0]
        months = _months(a, b)
        return r["inc"] / months, r["sp"] / months

    inc1, sp1 = half(mn, mid)
    inc2, sp2 = half(mid + timedelta(days=1), anchor)
    if inc1 < 100 or inc2 <= inc1 * 1.04:
        return []
    sr1, sr2 = (inc1 - sp1) / inc1, (inc2 - sp2) / inc2
    if sr2 >= sr1 - 0.02:
        return []
    return [_insight(
        "lifestyle-inflation",
        f"Income rose {round((inc2 - inc1) / inc1 * 100)}%, but you're saving less of it",
        f"Your monthly income grew from {_m(inc1)} to {_m(inc2)}, yet your savings rate slipped from "
        f"{round(sr1 * 100)}% to {round(sr2 * 100)}% — the extra income is mostly being spent.",
        significance=min(1.0, (sr1 - sr2) * 3 + 0.3), confidence=0.75, evidence=[], tone="watch",
        chart={"kind": "compare", "items": [
            {"label": "Was", "value": round(sr1 * 100, 1)}, {"label": "Now", "value": round(sr2 * 100, 1)}]},
    )]


def d_duplicates(anchor, mn):
    rows = _rows("SELECT id, date, payee, amount, category FROM transactions "
                 "WHERE amount < 0 AND payee != '' ORDER BY LOWER(payee), amount, date")
    flagged = []
    for i in range(1, len(rows)):
        a, b = rows[i - 1], rows[i]
        if (a["payee"].lower() == b["payee"].lower() and abs(a["amount"] - b["amount"]) < 0.01
                and 0 <= (date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days <= 3):
            flagged += [a, b]
    if not flagged:
        return []
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
    d_needs_vs_discretionary, d_big_moments, d_eating_out, d_shopping, d_weekly_rhythm,
    d_recurring, d_category_changes, d_small_purchases, d_lifestyle_inflation, d_duplicates,
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
