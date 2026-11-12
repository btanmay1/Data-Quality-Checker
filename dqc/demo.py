"""Create a demo card-servicing extract (customers, accounts, transactions) with a known number of planted
data-quality issues, plus the control totals the 'source system' sent with the extract.

The planted counts are written to planted_issues.json so the engine's results can be checked exactly.
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

L = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
FIRST = ["Aarav", "Diya", "Kabir", "Isha", "Vivaan", "Anika", "Reyansh", "Myra", "Arnav", "Sara", "Dev", "Tara"]
LAST = ["Sharma", "Iyer", "Gupta", "Khan", "Das", "Nair", "Singh", "Bose", "Patil", "Menon"]


def build(out: Path, seed: int = 7) -> dict:
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    planted: dict[str, int] = {}

    # ---------------- customers
    cust = []
    for i in range(1, 3001):
        f, l = rng.choice(FIRST), rng.choice(LAST)
        dob = date(1960, 1, 1) + timedelta(days=rng.randint(0, 365 * 39))  # all 18+ by 2018
        created = date(2018, 1, 1) + timedelta(days=rng.randint(0, 3000))
        verified = rng.random() < 0.9
        cust.append({"customer_id": f"C{i:06d}", "full_name": f"{f} {l}", "dob": dob.isoformat(),
                     "pan": "".join(rng.choice(L) for _ in range(3)) + "P" + l[0] + f"{rng.randint(1, 9999):04d}" + rng.choice(L),
                     "email": f"{f.lower()}.{l.lower()}{i}@example.com", "mobile": f"9{rng.randint(100000000, 999999999)}",
                     "city": rng.choice(["Delhi", "Mumbai", "Pune", "Chennai", "Kolkata"]),
                     "kyc_status": "VERIFIED" if verified else "PENDING",
                     "kyc_verified_date": (created + timedelta(days=rng.randint(0, 10))).isoformat() if verified else "",
                     "risk_rating": rng.choice(["Low", "Medium", "High"]), "created_date": created.isoformat()})
    c = pd.DataFrame(cust)
    idx = list(range(len(c)))
    rng.shuffle(idx)
    take = lambda n: [idx.pop() for _ in range(n)]

    def plant(name, rows, col, fn):
        planted[name] = len(rows)
        for r in rows:
            c.at[r, col] = fn(r)

    plant("customers.email_not_null", take(40), "email", lambda r: "")
    plant("customers.email_format", take(30), "email", lambda r: c.at[r, "email"].replace("@", " at "))
    plant("customers.pan_format", take(25), "pan", lambda r: c.at[r, "pan"][:3] + "C" + c.at[r, "pan"][4:])
    plant("customers.dob_valid", take(5), "dob", lambda r: "1990-02-30")
    plant("customers.adult_at_onboarding", take(8), "dob", lambda r: (date(2010, 1, 1) + timedelta(days=rng.randint(0, 900))).isoformat())
    verified_rows = [r for r in idx if c.at[r, "kyc_status"] == "VERIFIED"][:15]
    for r in verified_rows:
        idx.remove(r)
    plant("customers.kyc_date_when_verified", verified_rows, "kyc_verified_date", lambda r: "")
    plant("customers.risk_rating_values", take(10), "risk_rating", lambda r: rng.choice(["MED", "low", "Unknown"]))
    dup_src = take(3)
    dup_tgt = take(3)
    for s, t in zip(dup_src, dup_tgt):
        c.at[t, "customer_id"] = c.at[s, "customer_id"]
    planted["customers.customer_id_unique"] = 6
    c.to_csv(out / "customers.csv", index=False)

    # ---------------- accounts
    valid_cust = sorted(set(c.customer_id))
    acc = []
    for i in range(1, 4001):
        opened = date(2019, 1, 1) + timedelta(days=rng.randint(0, 2400))
        closed = rng.random() < 0.1
        lim = rng.choice([50000, 100000, 200000, 500000])
        acc.append({"account_id": f"A{i:07d}", "customer_id": rng.choice(valid_cust), "product": rng.choice(["Classic", "Gold", "Platinum"]),
                    "open_date": opened.isoformat(), "status": "CLOSED" if closed else "ACTIVE",
                    "close_date": (opened + timedelta(days=rng.randint(30, 600))).isoformat() if closed else "",
                    "credit_limit": str(lim), "current_balance": f"{lim * rng.uniform(0, 0.9):.2f}"})
    a = pd.DataFrame(acc)
    aidx = list(range(len(a)))
    rng.shuffle(aidx)
    atake = lambda n: [aidx.pop() for _ in range(n)]
    rows = atake(12)
    a.loc[rows, "customer_id"] = [f"C9{rng.randint(10000, 99999)}" for _ in rows]
    planted["accounts.customer_fk"] = 12
    closed_rows = [r for r in aidx if a.at[r, "status"] == "CLOSED"]
    for r in closed_rows[:7]:
        a.at[r, "close_date"] = (date.fromisoformat(a.at[r, "open_date"]) - timedelta(days=rng.randint(1, 90))).isoformat()
        aidx.remove(r)
    planted["accounts.close_after_open"] = 7
    for r in closed_rows[7:16]:
        a.at[r, "close_date"] = ""
        aidx.remove(r)
    planted["accounts.close_date_when_closed"] = 9
    rows = atake(6)
    a.loc[rows[:3], "credit_limit"] = "-50000"
    a.loc[rows[3:], "credit_limit"] = "1 lakh"
    planted["accounts.credit_limit_range"] = 6
    rows = atake(11)
    for r in rows:
        a.at[r, "current_balance"] = f"{float(a.at[r, 'credit_limit']) * rng.uniform(1.6, 2.5):.2f}"
    planted["accounts.balance_within_limit"] = 11
    a.to_csv(out / "accounts.csv", index=False)

    # ---------------- transactions
    good_acc = a[~a.index.isin(rows) & a.credit_limit.str.fullmatch(r"\d+")]
    tx = []
    for i in range(1, 20001):
        ac = good_acc.iloc[rng.randrange(len(good_acc))]
        start = max(date.fromisoformat(ac.open_date), date(2026, 1, 1))
        d = start + timedelta(days=rng.randint(0, max(0, (date(2026, 10, 30) - start).days)))
        tx.append({"txn_id": f"T{i:08d}", "account_id": ac.account_id, "txn_date": d.isoformat(),
                   "amount": f"{rng.lognormvariate(7.5, 1.1):.2f}", "currency": "INR",
                   "mcc": rng.choice(["5411", "5812", "4111", "5732", "5999"]), "status": rng.choice(["POSTED"] * 9 + ["REVERSED"])})
    t = pd.DataFrame(tx)
    tx_last = t.txn_date.max()
    tidx = list(range(len(t)))
    rng.shuffle(tidx)
    ttake = lambda n: [tidx.pop() for _ in range(n)]
    rows = ttake(20)
    t.loc[rows, "account_id"] = [f"A9{rng.randint(100000, 999999)}" for _ in rows]
    planted["transactions.account_fk"] = 20
    rows = ttake(5)
    t.loc[rows, "amount"] = "N/A"
    rows2 = ttake(8)
    t.loc[rows2, "amount"] = [f"-{rng.uniform(10, 900):.2f}" for _ in rows2]
    planted["transactions.amount_positive"] = 13
    rows = ttake(15)
    t.loc[rows, "currency"] = [rng.choice(["USD", "inr", "EUR"]) for _ in rows]
    planted["transactions.currency_values"] = 15
    # transactions dated before the account was opened
    open_map = dict(zip(a.account_id, a.open_date))
    cand = [r for r in tidx if t.at[r, "account_id"] in open_map and open_map[t.at[r, "account_id"]] > "2019-06-01"][:10]
    for r in cand:
        tidx.remove(r)
        t.at[r, "txn_date"] = (date.fromisoformat(open_map[t.at[r, "account_id"]]) - timedelta(days=rng.randint(1, 60))).isoformat()
    planted["transactions.after_account_open"] = 10
    s, d = ttake(2), ttake(2)
    for x, y in zip(s, d):
        t.at[y, "txn_id"] = t.at[x, "txn_id"]
    planted["transactions.txn_id_unique"] = 4
    t.to_csv(out / "transactions.csv", index=False)

    # Control totals from the "source system": one transaction was lost in transfer
    amounts = pd.to_numeric(t.amount, errors="coerce").sum()
    lost = 1834.50
    controls = {"transactions": {"record_count": len(t) + 1, "amount_total": round(amounts + lost, 2)},
                "as_of": "2026-10-31", "latest_transaction": tx_last}
    (out / "control_totals.json").write_text(json.dumps(controls, indent=2))
    planted["transactions.reconcile_to_source"] = 1
    (out / "planted_issues.json").write_text(json.dumps(planted, indent=2))
    return planted


if __name__ == "__main__":
    p = build(Path(__file__).resolve().parent.parent / "demo_data")
    print(json.dumps(p, indent=2))
