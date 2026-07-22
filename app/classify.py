"""Spending taxonomy — needs vs. discretionary, and dining sub-types.

Coarse, category-level, and deliberately simple: it's the deterministic first
pass. The LLM tier refines the genuinely ambiguous cases (e.g. a Target charge
that could be groceries or shopping) per-merchant later.
"""

NEEDS = {"Housing", "Loan", "Bills & Utils", "Groceries", "Transport", "Health"}
DISCRETIONARY = {"Dining", "Shopping", "Entertainment", "Travel", "Subscriptions"}
# Everything else (Transfers, Income, Fees, Uncategorized) is "other" and is
# excluded from the needs/discretionary split.


def kind_of(category: str) -> str:
    if category in NEEDS:
        return "needs"
    if category in DISCRETIONARY:
        return "discretionary"
    return "other"


_DINING_SUBTYPES = [
    ("Takeout", ["doordash", "uber eats", "grubhub", "postmates", "seamless"]),
    ("Coffee", ["coffee", "starbucks", "blue bottle", "peet", "philz", "cafe", "café"]),
    ("Bars", ["bar ", "brewery", "pub", "tavern", "wine", "cocktail", "lounge"]),
]


def dining_subtype(payee: str) -> str:
    p = (payee or "").lower()
    for name, keywords in _DINING_SUBTYPES:
        if any(k in p for k in keywords):
            return name
    return "Restaurants"
