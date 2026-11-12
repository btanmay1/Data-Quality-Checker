# Data Quality Checker

A config-driven data-quality engine for tabular extracts (CSV folders or SQLite). You write the rules in JSON. The
engine scores them across seven DQ dimensions, lists the exact failing records, reconciles the extract to the source
system's control totals, and gives a verdict (**fit for use**, **use with caution**, or **not fit for use**). It can
also stop a pipeline through its exit code.

It is designed for the check that should happen before an extract feeds an MIS report, a model or a regulatory return.

## 1. Rule types

| Type | Example from the demo rule set | Dimension |
|---|---|---|
| `not_null` | Every customer has an ID; email captured | Completeness |
| `unique` | Customer ID, account ID and transaction ID are unique | Uniqueness |
| `regex` | PAN matches the individual-PAN format; email is well formed | Validity |
| `allowed_values` | KYC status, risk rating and currency come from reference lists | Validity |
| `range` | Credit limit is numeric and within policy; amount is positive | Validity |
| `date_valid` | Date of birth is a real calendar date | Validity |
| `compare` | Close date is not before open date | Consistency |
| `conditional_not_null` | Verified customers have a verification date; closed accounts have a close date | Consistency |
| `foreign_key` | Every account has a known customer; every transaction has a known account | Integrity |
| `freshness` | The extract includes yesterday's transactions | Timeliness |
| `reconcile` | Record count and amount total agree with the source system's control totals | Accuracy |
| `sql` | Any custom SQL that returns failing rows, e.g. customer under 18 at onboarding, or transaction dated before the account opened | Any |

Every rule has a **severity** (Critical / Major / Minor) and a **tolerance**, the maximum failure rate allowed. The
defaults are 0% for Critical, 0.5% for Major and 2% for Minor. A rule passes when its failure rate is within tolerance.
The failing records are still listed either way, so they can be fixed.

## 2. Demo results

The demo builds a card-servicing extract with 3,000 customers, 4,000 accounts and 20,000 transactions, plants
**19 kinds of issue** with known counts, and runs the 24 rules in
[`config/card_servicing_rules.json`](config/card_servicing_rules.json).

| Table | Records | Records with any issue | Clean-record rate | Rules failed | Critical failures |
|---|---:|---:|---:|---:|---:|
| customers | 3,000 | 139 | 95.4% | 5 / 10 | 3 |
| accounts | 4,000 | 45 | 98.9% | 4 / 7 | 2 |
| transactions | 20,000 | 62 | 99.7% | 4 / 7 | 4 |

- **All 19 planted issue counts are matched exactly**, and there are no failures that weren't planted
  (see `planted_vs_flagged.csv`).
- The reconciliation catches a transaction that was lost in transfer: the extract has one record fewer, and Rs. 1,834.50
  less, than the source system's control totals.
- **Verdict: NOT FIT FOR USE**, because of critical failures: duplicate customer IDs, orphan accounts and transactions,
  under-18 customers, verified customers with no KYC date, invalid limits and amounts, and the reconciliation break.

## 3. Outputs

| File | Contents |
|---|---|
| `dq_report.xlsx` | Summary and verdict, Table Scorecard, **Dimension Heatmap** (table × dimension pass rate, colour-scaled), Rule Results (PASS/FAIL highlighted), Failing Records (rule, CSV line number, full record) |
| `rule_results.csv` | One row per rule: records tested and failed, fail rate, tolerance, status |
| `failing_records.csv` | Up to 200 failing records per failed rule, with the CSV line number |
| `table_scorecard.csv` | Per-table records, records with any issue, clean-record rate, DQ score |
| `planted_vs_flagged.csv` | Demo only: planted vs detected counts |

## 4. How to run

```bash
pip install -r requirements.txt
python -m dqc.main                                            # demo
python -m dqc.main --data my_extract/ --rules my_rules.json   # your own CSV folder (one CSV per table)
python -m dqc.main --data warehouse.db --rules my_rules.json  # or a SQLite database
python -m dqc.main --fail-on Critical                         # exit code 1 if any Critical rule fails
python -m pytest -q                                           # tests for every rule type plus the demo end to end
```

Put `control_totals.json` next to the CSVs to turn on `reconcile` rules. Without it, they are skipped.

## 5. Design notes

- CSVs are read **as text**, so a value like `"1 lakh"` in a numeric column is caught as invalid and isn't silently coerced.
- Value checks skip nulls. Missing values are the job of the completeness rules, so one bad cell isn't counted twice.
- Freshness and reconciliation are **table-level** rules. Each counts as one test, not one failure per record.
- Every failure can be traced back to a CSV line number.

## 6. Structure

```
dqc/engine.py     rule implementations, scorecards, failing-record extraction
dqc/main.py       CLI, control totals, Excel report, verdict and exit code
dqc/demo.py       demo extract with planted issues
config/card_servicing_rules.json   24 rules across the 7 dimensions
demo_data/        the generated demo extract (CSV), control totals and planted counts
tests/            unit tests per rule type plus an end-to-end test
```
