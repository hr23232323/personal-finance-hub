"""Dead-simple keyword categorization. No ML, no LLM — just readable rules.

Deliberately basic (KISS). LLM-assisted categorization is a documented future
step; this gets every transaction a reasonable label with zero external calls.
"""

# (category, [keywords]) — first match wins. Order matters: specific before broad.
RULES = [
    ("Income",        ["payroll", "direct dep", "salary", "interest paid", "dividend"]),
    ("Transfers",     ["transfer", "xfer", "venmo", "zelle", "cash app", "paypal", "withdrawal", "atm"]),
    ("Groceries",     ["whole foods", "trader joe", "safeway", "kroger", "aldi", "costco",
                       "wegmans", "publix", "grocery", "supermarket", "market"]),
    ("Dining",        ["restaurant", "cafe", "coffee", "starbucks", "mcdonald", "chipotle",
                       "doordash", "uber eats", "grubhub", "pizza", "taco", "sushi", "bar ", "brew"]),
    ("Transport",     ["uber", "lyft", "shell", "chevron", "exxon", "gas ", "fuel", "parking",
                       "transit", "metro", "toll", "bp ", "auto repair", "car repair", "mechanic"]),
    ("Shopping",      ["amazon", "target", "walmart", "best buy", "ebay", "etsy", "ikea", "store"]),
    ("Bills & Utils", ["comcast", "xfinity", "verizon", "at&t", "t-mobile", "pg&e", "electric",
                       "water", "utility", "insurance", "phone"]),
    ("Housing",       ["rent", "mortgage", "hoa", "landlord", "apartment"]),
    ("Loan",          ["auto loan", "loan payment", "student loan", "car payment"]),
    ("Subscriptions", ["netflix", "spotify", "hulu", "disney", "youtube", "apple.com/bill",
                       "prime", "adobe", "notion", "openai", "subscription"]),
    ("Entertainment", ["cinema", "movie", "theater", "steam", "playstation", "xbox", "concert", "ticket"]),
    ("Health",        ["pharmacy", "cvs", "walgreens", "doctor", "dental", "clinic", "gym", "fitness",
                       "medical", "hospital", "urgent care"]),
    ("Travel",        ["airline", "hotel", "airbnb", "delta", "united", "marriott", "hilton", "expedia"]),
    ("Fees",          ["fee", "service charge", "overdraft", "interest charged"]),
]


def categorize(payee: str, raw_description: str, amount: float = 0.0) -> str:
    text = f"{payee} {raw_description}".lower()
    for category, keywords in RULES:
        if any(k in text for k in keywords):
            # Don't call an inflow "spending"; unmatched positive amounts are Income.
            return category
    if amount > 0:
        return "Income"
    return "Uncategorized"
