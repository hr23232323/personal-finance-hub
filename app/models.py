"""Small, plain data holders shared across connectors and the DB layer.

Amount convention everywhere: POSITIVE = money in (income/refund/credit),
NEGATIVE = money out (spending). Connectors normalize to this.
"""
from dataclasses import dataclass, field
from typing import Optional

ACCOUNT_TYPES = ["checking", "savings", "credit", "loan", "investment", "other"]


@dataclass
class NormalizedTransaction:
    date: str  # ISO "YYYY-MM-DD"
    amount: float  # + in / - out
    payee: str = ""
    raw_description: str = ""
    external_id: Optional[str] = None  # stable id from source (e.g. OFX FITID / SimpleFIN id)
    source: str = "manual"


@dataclass
class NormalizedAccount:
    name: str
    type: str = "other"
    institution: str = ""
    currency: str = "USD"
    external_id: Optional[str] = None
    source: str = "manual"
    transactions: list = field(default_factory=list)  # list[NormalizedTransaction]
