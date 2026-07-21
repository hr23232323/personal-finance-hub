"""SQLite storage. One local file, zero external services.

Dedup strategy: every transaction has a `dedup_hash`, unique per account.
Re-importing the same file or re-syncing an overlapping date range never
double-counts. For sourced rows the hash is the source's stable id; for
manual rows with no id it's a hash of (date, amount, raw description).
"""
import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import config
from .categorize import categorize
from .models import NormalizedAccount, NormalizedTransaction

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL DEFAULT 'other',
    institution TEXT DEFAULT '',
    currency    TEXT DEFAULT 'USD',
    source      TEXT DEFAULT 'manual',
    external_id TEXT,
    UNIQUE(source, external_id)
);

CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,
    amount          REAL NOT NULL,
    payee           TEXT DEFAULT '',
    description     TEXT DEFAULT '',
    raw_description TEXT DEFAULT '',
    category        TEXT DEFAULT 'Uncategorized',
    source          TEXT DEFAULT 'manual',
    external_id     TEXT,
    dedup_hash      TEXT NOT NULL,
    UNIQUE(account_id, dedup_hash)
);

CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_account ON transactions(account_id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


@contextmanager
def get_conn():
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ── settings (used to persist the SimpleFIN access URL locally) ──────────────
def get_setting(key: str, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


# ── accounts ─────────────────────────────────────────────────────────────────
def create_account(name, type="other", institution="", currency="USD",
                   source="manual", external_id=None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO accounts(name, type, institution, currency, source, external_id) "
            "VALUES(?,?,?,?,?,?)",
            (name, type, institution, currency, source, external_id),
        )
        return cur.lastrowid


def get_or_create_source_account(acct: NormalizedAccount) -> int:
    """For synced accounts: look up by (source, external_id), else create."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM accounts WHERE source = ? AND external_id = ?",
            (acct.source, acct.external_id),
        ).fetchone()
        if row:
            return row["id"]
        cur = conn.execute(
            "INSERT INTO accounts(name, type, institution, currency, source, external_id) "
            "VALUES(?,?,?,?,?,?)",
            (acct.name, acct.type, acct.institution, acct.currency, acct.source, acct.external_id),
        )
        return cur.lastrowid


def list_accounts_raw():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM accounts ORDER BY name")]


# ── transactions ─────────────────────────────────────────────────────────────
def _dedup_hash(tx: NormalizedTransaction) -> str:
    if tx.external_id:
        return f"id:{tx.external_id}"
    basis = f"{tx.date}|{tx.amount:.2f}|{tx.raw_description.strip().lower()}"
    return "h:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()


def insert_transactions(account_id: int, txs) -> int:
    """Insert with dedup. Returns number of NEW rows actually added."""
    inserted = 0
    with get_conn() as conn:
        for tx in txs:
            category = categorize(tx.payee, tx.raw_description, tx.amount)
            cur = conn.execute(
                "INSERT OR IGNORE INTO transactions "
                "(account_id, date, amount, payee, description, raw_description, "
                " category, source, external_id, dedup_hash) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (account_id, tx.date, tx.amount, tx.payee, tx.payee or tx.raw_description,
                 tx.raw_description, category, tx.source, tx.external_id, _dedup_hash(tx)),
            )
            inserted += cur.rowcount
    return inserted
