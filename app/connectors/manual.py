"""Manual file import — the fully-local, zero-third-party path.

Parses a bank export (CSV or OFX/QFX) into NormalizedTransactions. Nothing
here touches the network. Works with any bank's "download transactions" button.
"""
import csv
import io
from datetime import datetime

from ..models import NormalizedTransaction

# Common date formats banks emit, tried in order.
_DATE_FORMATS = [
    "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d-%b-%Y",
    "%Y/%m/%d", "%m-%d-%Y", "%b %d, %Y", "%d %b %Y",
]

# Header name hints -> logical field. Lowercased substring match.
_HEADER_HINTS = {
    "date":        ["date", "posted", "transaction date"],
    "amount":      ["amount", "value"],
    "debit":       ["debit", "withdrawal", "money out", "paid out"],
    "credit":      ["credit", "deposit", "money in", "paid in"],
    "description": ["description", "payee", "name", "memo", "details", "merchant", "narrative"],
}


def _parse_date(value: str) -> str:
    value = (value or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Last resort: take the first 10 chars if they look ISO-ish.
    return value[:10]


def _num(value: str) -> float:
    """Parse a money string like '$1,234.56' or '(45.00)' (parens = negative)."""
    s = (value or "").strip()
    if not s:
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = s.replace("(", "").replace(")", "").replace("$", "").replace(",", "").strip()
    try:
        n = float(s)
    except ValueError:
        return 0.0
    return -n if neg else n


def _match_column(headers, hints):
    for h in headers:
        hl = h.lower().strip()
        if any(hint in hl for hint in hints):
            return h
    return None


def parse_csv(text: str, mapping: dict | None = None):
    """Parse CSV text. `mapping` optionally overrides auto-detected columns:
    {"date": <col>, "amount": <col>, "description": <col>,
     "debit": <col>, "credit": <col>}.
    Amount convention: negative = spending. If the file only has a positive
    'amount' with separate debit/credit columns, we combine them.
    """
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    headers = reader.fieldnames
    mapping = mapping or {}

    col_date = mapping.get("date") or _match_column(headers, _HEADER_HINTS["date"])
    col_amount = mapping.get("amount") or _match_column(headers, _HEADER_HINTS["amount"])
    col_debit = mapping.get("debit") or _match_column(headers, _HEADER_HINTS["debit"])
    col_credit = mapping.get("credit") or _match_column(headers, _HEADER_HINTS["credit"])
    col_desc = mapping.get("description") or _match_column(headers, _HEADER_HINTS["description"])

    if not col_date or not (col_amount or col_debit or col_credit):
        raise ValueError(
            f"Couldn't identify date/amount columns. Found headers: {headers}. "
            "Pass an explicit column mapping."
        )

    out = []
    for row in reader:
        date = _parse_date(row.get(col_date, ""))
        if col_amount:
            amount = _num(row.get(col_amount, ""))
        else:
            debit = _num(row.get(col_debit, "")) if col_debit else 0.0
            credit = _num(row.get(col_credit, "")) if col_credit else 0.0
            # debit columns are usually listed as positive magnitudes -> make negative
            amount = credit - abs(debit)
        desc = (row.get(col_desc, "") if col_desc else "").strip()
        if not date or amount == 0 and not desc:
            continue
        out.append(NormalizedTransaction(
            date=date, amount=amount, payee=desc, raw_description=desc, source="manual"
        ))
    return out


def parse_ofx(data: bytes):
    """Parse OFX/QFX bytes via ofxtools. Uses FITID as the stable dedup id."""
    from ofxtools.Parser import OFXTree

    tree = OFXTree()
    tree.parse(io.BytesIO(data))
    ofx = tree.convert()

    out = []
    for stmt in ofx.statements:
        for t in stmt.transactions:
            name = (getattr(t, "name", "") or "").strip()
            memo = (getattr(t, "memo", "") or "").strip()
            desc = name or memo
            raw = " ".join(x for x in (name, memo) if x)
            dt = getattr(t, "dtposted", None)
            date = dt.strftime("%Y-%m-%d") if dt else ""
            out.append(NormalizedTransaction(
                date=date,
                amount=float(t.trnamt),
                payee=desc,
                raw_description=raw,
                external_id=str(getattr(t, "fitid", "") or "") or None,
                source="manual",
            ))
    return out


def parse_file(filename: str, data: bytes, mapping: dict | None = None):
    """Dispatch on extension. Returns list[NormalizedTransaction]."""
    lower = filename.lower()
    if lower.endswith((".ofx", ".qfx")):
        return parse_ofx(data)
    if lower.endswith((".csv", ".txt")):
        return parse_csv(data.decode("utf-8-sig", errors="replace"), mapping)
    raise ValueError(f"Unsupported file type: {filename}. Use .csv, .ofx, or .qfx")
