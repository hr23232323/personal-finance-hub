"""SimpleFIN connector — the low-friction automatic sync (US/Canada).

Flow:
  1. User gets a one-time "setup token" from https://beta-bridge.simplefin.org
  2. claim() exchanges it ONCE for a long-lived access URL (has creds embedded).
  3. We store that access URL locally (settings table) and never need the token again.
  4. fetch() pulls accounts + transactions, read-only.

Honest note: SimpleFIN (and MX behind it) retrieve your data on THEIR servers;
your local app then downloads a copy. This is convenience, not max privacy.
Uses only the stdlib — no extra HTTP dependency.
"""
import base64
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from ..models import NormalizedAccount, NormalizedTransaction

SETTING_ACCESS_URL = "simplefin_access_url"


def claim_setup_token(setup_token: str) -> str:
    """Exchange a one-time setup token for a durable access URL."""
    setup_token = setup_token.strip()
    try:
        claim_url = base64.b64decode(setup_token).decode("utf-8").strip()
    except Exception as e:
        raise ValueError(f"That doesn't look like a valid SimpleFIN setup token: {e}")
    if not claim_url.startswith("http"):
        raise ValueError("Decoded setup token is not a URL — double-check what you pasted.")

    req = urllib.request.Request(claim_url, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        access_url = resp.read().decode("utf-8").strip()
    if not access_url.startswith("http"):
        raise ValueError("SimpleFIN did not return a valid access URL.")
    return access_url


def _authed_request(access_url: str, path: str, params: dict | None = None):
    """Build a GET against the access URL, moving embedded creds into a header."""
    parsed = urllib.parse.urlparse(access_url)
    userinfo = ""
    netloc = parsed.netloc
    if "@" in netloc:
        userinfo, netloc = netloc.split("@", 1)
    base = urllib.parse.urlunparse((parsed.scheme, netloc, parsed.path.rstrip("/"), "", "", ""))
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    req = urllib.request.Request(url)
    if userinfo:
        token = base64.b64encode(userinfo.encode("utf-8")).decode("ascii")
        req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _guess_type(name: str) -> str:
    n = (name or "").lower()
    if "credit" in n or "card" in n:
        return "credit"
    if "sav" in n:
        return "savings"
    if "check" in n or "chequing" in n:
        return "checking"
    if "loan" in n or "mortgage" in n:
        return "loan"
    if "invest" in n or "brokerage" in n or "401" in n:
        return "investment"
    return "other"


def fetch(access_url: str, start: str | None = None, end: str | None = None):
    """Return list[NormalizedAccount] with transactions. Dates are ISO 'YYYY-MM-DD'."""
    params = {}
    if start:
        params["start-date"] = int(datetime.strptime(start, "%Y-%m-%d")
                                   .replace(tzinfo=timezone.utc).timestamp())
    if end:
        params["end-date"] = int(datetime.strptime(end, "%Y-%m-%d")
                                 .replace(tzinfo=timezone.utc).timestamp())

    data = _authed_request(access_url, "/accounts", params)

    accounts = []
    for a in data.get("accounts", []):
        org = a.get("org", {}) or {}
        acct = NormalizedAccount(
            name=a.get("name", "Account"),
            type=_guess_type(a.get("name", "")),
            institution=org.get("name", "") or org.get("domain", ""),
            currency=a.get("currency", "USD"),
            external_id=str(a.get("id", "")),
            source="simplefin",
        )
        for t in a.get("transactions", []):
            posted = t.get("posted")
            date = (datetime.fromtimestamp(int(posted), tz=timezone.utc).strftime("%Y-%m-%d")
                    if posted else "")
            payee = (t.get("payee") or t.get("description") or "").strip()
            acct.transactions.append(NormalizedTransaction(
                date=date,
                amount=float(t.get("amount", 0)),
                payee=payee,
                raw_description=(t.get("description") or payee).strip(),
                external_id=str(t.get("id", "")) or None,
                source="simplefin",
            ))
        accounts.append(acct)
    return accounts
