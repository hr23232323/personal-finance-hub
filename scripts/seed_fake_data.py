#!/usr/bin/env python3
"""Seed the local DB with comprehensive, realistic fake finance data.

Generates ~a year of multi-account activity so you can explore the app without
touching a real bank: paychecks, rent, subscriptions, groceries, dining,
transport, travel, one-off purchases, and internal transfers — plus a few
deliberately *analyzable* patterns to make the advisor earn its keep:

  • lifestyle creep — dining spend drifts upward over the year
  • a forgotten subscription — "AudioMax Membership" quietly recurring monthly
  • a holiday spike in December and a July vacation
  • a couple of big one-offs (a laptop, a car repair, a medical bill)

    python scripts/seed_fake_data.py --reset --months 14

Writes only to your local DB. Amounts follow the app convention:
positive = money in, negative = money out.
"""
import argparse
import os
import random
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.models import NormalizedTransaction


def wipe():
    with db.get_conn() as conn:
        conn.execute("DELETE FROM transactions")
        conn.execute("DELETE FROM accounts")
        conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('transactions', 'accounts')")


def main():
    ap = argparse.ArgumentParser(description="Seed realistic fake finance data")
    ap.add_argument("--months", type=int, default=14, help="Months of history to generate")
    ap.add_argument("--reset", action="store_true", help="Wipe existing data first")
    ap.add_argument("--seed", type=int, default=42, help="RNG seed (reproducible output)")
    args = ap.parse_args()

    random.seed(args.seed)
    db.init_db()
    if args.reset:
        wipe()

    checking = db.create_account("Everyday Checking", "checking", "Acme Bank")
    savings = db.create_account("High-Yield Savings", "savings", "Acme Bank")
    credit = db.create_account("Sapphire Credit Card", "credit", "Chase")
    loan = db.create_account("Auto Loan", "loan", "Acme Bank")

    txs = {checking: [], savings: [], credit: [], loan: []}
    card_balance = 0.0  # accrues credit-card charges; paid down monthly

    def emit(acct, d, amount, payee):
        txs[acct].append(NormalizedTransaction(
            date=d.isoformat(), amount=round(amount, 2), payee=payee, raw_description=payee))

    def charge(d, amount, payee):
        """A purchase on the credit card. `amount` is a positive magnitude."""
        nonlocal card_balance
        emit(credit, d, -amount, payee)
        card_balance += amount

    pick = random.choice
    today = date.today()
    start = today - timedelta(days=args.months * 30)
    span = max(1, (today - start).days)

    d = start
    while d <= today:
        progress = (d - start).days / span
        dining_creep = 1 + 0.6 * progress  # spend drifts up over time

        # income: biweekly paycheck
        if (d - start).days % 14 == 0:
            emit(checking, d, random.uniform(2550, 2680), "Payroll - Acme Corp")

        # monthly fixed costs + internal transfers on the 1st
        if d.day == 1:
            emit(checking, d, -1950, "Rent - Oakwood Apartments")
            emit(loan, d, -425, "Auto Loan Payment")
            emit(checking, d, -500, "Transfer to Savings")
            emit(savings, d, 500, "Transfer from Checking")
            emit(savings, d, random.uniform(11, 19), "Interest Paid")

        # monthly bills (credit card) on the 3rd
        if d.day == 3:
            charge(d, 79.99, "Comcast Xfinity Internet")
            charge(d, 85.00, "Verizon Wireless")
            charge(d, random.uniform(70, 130), "PG&E Electric")
            charge(d, 142.00, "State Farm Insurance")

        # subscriptions (credit card)
        subs = {5: (15.99, "Netflix"), 6: (24.99, "Planet Fitness"),
                8: (11.99, "Spotify"), 9: (14.99, "AudioMax Membership"),
                12: (13.99, "Disney+"), 18: (54.99, "Adobe Creative Cloud"),
                20: (20.00, "OpenAI ChatGPT Plus"), 22: (13.99, "YouTube Premium")}
        if d.day in subs:
            charge(d, *subs[d.day])

        # pay off the card on the 16th (internal transfer)
        if d.day == 16 and card_balance > 5:
            pay = round(card_balance, 2)
            emit(checking, d, -pay, "Transfer - Chase Card Payment")
            emit(credit, d, pay, "Transfer - Card Payment Received")
            card_balance = 0.0

        # discretionary daily spending
        if random.random() < 0.55:
            charge(d, random.uniform(4.25, 7.5), pick(["Blue Bottle Coffee", "Starbucks"]))
        if d.weekday() in (2, 6):
            charge(d, random.uniform(45, 145), pick(["Whole Foods Market", "Trader Joe's", "Safeway"]))
        if random.random() < 0.33:
            charge(d, random.uniform(13, 52) * dining_creep,
                   pick(["Chipotle", "DoorDash", "Uber Eats", "Nonna's Pizza",
                         "The Corner Restaurant", "Sushi Bar"]))
        if (d - start).days % 10 == 0:
            charge(d, random.uniform(42, 66), pick(["Shell", "Chevron", "Exxon"]))
        if random.random() < 0.20:
            charge(d, random.uniform(9, 28), pick(["Uber", "Lyft"]))
        if random.random() < 0.25:
            charge(d, random.uniform(8, 95), "Amazon.com")
        if d.weekday() == 5 and random.random() < 0.6:
            charge(d, random.uniform(25, 120), pick(["Target", "Walmart"]))
        if random.random() < 0.06:
            charge(d, random.uniform(8, 46), pick(["CVS Pharmacy", "Walgreens"]))
        if random.random() < 0.08:
            charge(d, random.uniform(10, 58), pick(["AMC Cinema", "Steam Games", "Ticketmaster"]))

        # seasonal: December holiday spike
        if d.month == 12 and d.day in (5, 12, 18, 22):
            charge(d, random.uniform(90, 320), pick(["Amazon.com", "Target", "Best Buy"]))

        # seasonal: July vacation
        if d.month == 7 and 10 <= d.day <= 15:
            if d.day == 10:
                charge(d, random.uniform(430, 620), pick(["Delta Air Lines", "United Airlines"]))
            charge(d, random.uniform(180, 260), "Marriott Hotel")
            if random.random() < 0.7:
                charge(d, random.uniform(35, 90), pick(["The Corner Restaurant", "Sushi Bar"]))

        d += timedelta(days=1)

    # one-off events (dates relative to today, only if within range)
    def once(days_ago, fn):
        dd = today - timedelta(days=days_ago)
        if dd >= start:
            fn(dd)

    once(250, lambda dd: charge(dd, 1199.00, "Best Buy"))                       # laptop
    once(120, lambda dd: emit(checking, dd, -684.50, "Midtown Auto Repair"))
    once(60, lambda dd: emit(checking, dd, -320.00, "City Medical Center"))
    once(75, lambda dd: emit(checking, dd, 1200.00, "IRS Tax Refund"))
    once(40, lambda dd: emit(checking, dd, 65.00, "Venmo from Alex"))
    once(15, lambda dd: charge(dd, 38.00, "Overdraft Fee"))

    total = sum(db.insert_transactions(acct_id, lst) for acct_id, lst in txs.items())
    print(f"Seeded {total} transactions across 4 accounts "
          f"({start.isoformat()} → {today.isoformat()}). Run 'make run' to explore.")


if __name__ == "__main__":
    main()
