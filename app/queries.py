"""Read-only queries over the local DB.

These functions serve double duty: the FastAPI dashboard calls them, AND they
are the exact tools the LLM advisor is allowed to call. Keeping them here (one
place, read-only) means the model can never do anything but look things up.
All return plain JSON-serializable dicts/lists.
"""
from . import db


def _where(start=None, end=None, account_id=None, category=None, search=None,
           min_amount=None, max_amount=None, extra=None):
    """Build a WHERE clause from optional filters. `extra` is an additional
    raw SQL condition (e.g. "t.amount < 0") ANDed in alongside the rest, so
    callers never have to hand-roll their own AND/WHERE joining."""
    clauses, params = [], []
    if start:
        clauses.append("t.date >= ?"); params.append(start)
    if end:
        clauses.append("t.date <= ?"); params.append(end)
    if account_id:
        clauses.append("t.account_id = ?"); params.append(account_id)
    if category:
        clauses.append("t.category = ?"); params.append(category)
    if search:
        clauses.append("(LOWER(t.payee) LIKE ? OR LOWER(t.raw_description) LIKE ?)")
        params += [f"%{search.lower()}%", f"%{search.lower()}%"]
    if min_amount is not None:
        clauses.append("t.amount >= ?"); params.append(min_amount)
    if max_amount is not None:
        clauses.append("t.amount <= ?"); params.append(max_amount)
    if extra:
        clauses.append(extra)
    sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return sql, params


def list_accounts():
    """List all accounts with current computed balance and transaction count."""
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT a.id, a.name, a.type, a.institution, a.currency, a.source,
                   COUNT(t.id) AS tx_count,
                   COALESCE(SUM(t.amount), 0) AS balance,
                   MIN(t.date) AS first_tx, MAX(t.date) AS last_tx
            FROM accounts a LEFT JOIN transactions t ON t.account_id = a.id
            GROUP BY a.id ORDER BY a.name
        """).fetchall()
        return [dict(r) for r in rows]


def get_transactions(start=None, end=None, account_id=None, category=None,
                     search=None, min_amount=None, max_amount=None, limit=200):
    """List individual transactions matching filters, newest first."""
    where, params = _where(start, end, account_id, category, search, min_amount, max_amount)
    with db.get_conn() as conn:
        rows = conn.execute(f"""
            SELECT t.id, t.date, t.amount, t.payee, t.category, t.source,
                   a.name AS account
            FROM transactions t JOIN accounts a ON a.id = t.account_id
            {where}
            ORDER BY t.date DESC, t.id DESC
            LIMIT ?
        """, [*params, int(limit)]).fetchall()
        return [dict(r) for r in rows]


def get_summary(start=None, end=None, account_id=None):
    """Totals for a period: income, spending, net, and transaction count."""
    where, params = _where(start, end, account_id)
    with db.get_conn() as conn:
        row = conn.execute(f"""
            SELECT
              COALESCE(SUM(CASE WHEN amount > 0 AND category != 'Transfers' THEN amount END), 0) AS income,
              COALESCE(SUM(CASE WHEN amount < 0 AND category != 'Transfers' THEN amount END), 0) AS spending,
              COALESCE(SUM(amount), 0) AS net,
              COUNT(*) AS count
            FROM transactions t {where}
        """, params).fetchone()
        return dict(row)


def spending_by_category(start=None, end=None, account_id=None):
    """Total spending (money out) grouped by category, largest first."""
    where, params = _where(start, end, account_id, extra="t.amount < 0 AND t.category != 'Transfers'")
    with db.get_conn() as conn:
        rows = conn.execute(f"""
            SELECT category, ROUND(SUM(-amount), 2) AS spent, COUNT(*) AS count
            FROM transactions t {where}
            GROUP BY category ORDER BY spent DESC
        """, params).fetchall()
        return [dict(r) for r in rows]


def spending_by_month(start=None, end=None, account_id=None):
    """Income vs spending per month (YYYY-MM)."""
    where, params = _where(start, end, account_id)
    with db.get_conn() as conn:
        rows = conn.execute(f"""
            SELECT substr(date, 1, 7) AS month,
                   ROUND(SUM(CASE WHEN amount > 0 AND category != 'Transfers' THEN amount ELSE 0 END), 2) AS income,
                   ROUND(SUM(CASE WHEN amount < 0 AND category != 'Transfers' THEN -amount ELSE 0 END), 2) AS spending
            FROM transactions t {where}
            GROUP BY month ORDER BY month
        """, params).fetchall()
        return [dict(r) for r in rows]


def top_merchants(start=None, end=None, account_id=None, limit=10):
    """Merchants/payees you spent the most at."""
    where, params = _where(start, end, account_id,
                           extra="t.amount < 0 AND t.payee != '' AND t.category != 'Transfers'")
    with db.get_conn() as conn:
        rows = conn.execute(f"""
            SELECT payee, ROUND(SUM(-amount), 2) AS spent, COUNT(*) AS visits
            FROM transactions t {where}
            GROUP BY LOWER(payee) ORDER BY spent DESC LIMIT ?
        """, [*params, int(limit)]).fetchall()
        return [dict(r) for r in rows]
