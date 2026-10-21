# Kanakku

**Every number tells a story.**

Kanakku (கணக்கு, "accounts") is a three-way payment reconciliation tool built on **Exasol Personal**. It loads a
merchant's order ledger, payment-gateway report and bank statement, matches them inside Exasol with SQL, explains
every mismatch with evidence, ranks the open exceptions by investigation priority, and keeps an audit trail of
analyst decisions.

*ExaThon India 2026 · Track 01 — Money, Risk & Regulation · Problem: three-way payment reconciliation with break explanations.*

> All data in this repository is synthetic. There are no real customers, bank accounts or credentials.

---

## The problem

An Indian online merchant takes payments through a gateway (UPI, cards, net-banking, wallets). Money then arrives in
the bank in **settlement batches**, after fees and GST are deducted, usually T+2. The three records rarely agree:

| What goes wrong | What it looks like |
|---|---|
| Fees and GST netted at source | Gateway or bank amount is short of the order amount |
| Batch settlements | One bank credit covers ~90 payments, net of refunds and chargebacks |
| Delays | Settlement arrives days after the expected date |
| Duplicates | A customer is charged twice, or a batch is credited twice |
| Missing records | An order with no payment; a payment with no order; a batch that never arrives |
| Reversals | A cancelled order whose payment was captured; a chargeback the ledger doesn't know about |

Finance teams reconcile this by hand in spreadsheets every month-end. The cost is close delays, unrecovered refunds
and cash nobody can explain. Kanakku automates the matching and, more importantly, tells the analyst **why** each
break happened and **which ones to look at first**.

## What Kanakku does

1. **Ingest:** validates each CSV row by row (money must be a decimal with ≤ 2 places, dates must parse, currency must be INR, gateway net must equal gross − fee − GST, …). It rejects bad rows with a reason and line number, drops exact duplicates, rejects conflicting duplicates, and `MERGE`s the rest into Exasol on business keys, so reloading a file is a no-op.
2. **Reconcile inside Exasol, in four stages:**
   1. **Exact:** order reference (trimmed, upper-cased) and amount equal.
   2. **Tolerance:** reference equal, amount within a configurable tolerance (default ₹1.00).
   3. **Settlement batches:** the gateway batch net (payments − fees − GST − refunds − chargebacks) is matched to the bank line whose narration carries the batch id. A credit with *no* reference is linked only when its amount is identical, its value date falls inside the window, and the pair is unique in both directions (constrained matching).
   4. **Conservative fuzzy:** a payment with a blank or mistyped reference is linked to an order only if it is the single same-amount, same-method candidate within 15 minutes, *and* that order has no other candidate. Ambiguous pairs are left as exceptions on purpose.
3. **Classify** every order, gateway transaction, settlement batch and bank line exactly once per run: `MATCHED`, `MISSING_FROM_GATEWAY`, `MISSING_FROM_BANK`, `AMOUNT_MISMATCH`, `POSSIBLE_DUPLICATE`, `DELAYED_SETTLEMENT`, `POSSIBLE_FEE_DIFFERENCE`, `REVERSAL_OR_CANCELLATION`, `UNRESOLVED` (plus the non-exception `AWAITING_SETTLEMENT`).
4. **Explain:** each result stores the source ids, the expected, actual and discrepancy amounts, the dates, the batch, the matching stage, a plain-English explanation and an evidence string. Explanations are tagged **Confirmed** (visible in the records) or **Suspected** (a likely cause inferred by a rule). Kanakku never claims fraud.
5. **Prioritise:** a transparent 0–100 score (amount + ageing + severity + close-period impact), banded High/Medium/Low, with the reason shown. It is an investigation-priority heuristic, not a fraud probability.
6. **Resolve:** analysts set a status and a note. Every change is appended to `RESOLUTION_LOG`, and the latest decision is carried forward to future runs by a stable exception key.
7. **Evaluate:** compares a run against the generator's separate ground-truth labels (precision, recall, confusion matrix, false positives, missed issues). The matching SQL never reads the labels, and a test enforces that.

## Measured results (bundled sample, seed 42)

All figures were measured on 2026-10-21 on an Apple M4 Mac (16 GB) running Exasol Personal 2026.2.0 locally in a
4-vCPU / 6 GB VM. Reproduce them with `python -m kanakku demo`.

| Metric | Value |
|---|---|
| Orders loaded (valid / rows read) | 10,005 / 10,021 (10 rejected, 6 exact duplicates dropped) |
| Gateway transactions | 10,503 / 10,515 (6 rejected, 6 exact duplicates dropped) |
| Bank lines | 947 / 954 (4 rejected, 3 exact duplicates dropped) |
| Orders matched | **9,475 / 10,005 (94.70%)**: 9,224 exact, 200 within tolerance, 51 fuzzy (suspected) |
| Settlement batches matched | 96 / 113 (88 by reference, 8 by constrained amount + date) |
| Exceptions | 670 (165 High, 364 Medium, 141 Low) |
| Open discrepancy exposure | ₹22,81,681.69 |
| Reconciliation time inside Exasol | ~1.0–1.3 s per run (all four stages + scoring) |
| Detection precision / recall / F1 vs ground truth | **0.970 / 1.000 / 0.985** (650 TP, 20 FP, 0 missed) |
| Issue-code accuracy on detected exceptions | 100% |
| Automated tests | 111 passed (92 unit + 19 Exasol integration) |

**Read these numbers honestly.** The generator and the rules were written by the same person, so the scores show
that the engine handles the *known* scenarios consistently. They do not measure accuracy on real-world data. All 20
false positives come from five deliberately ambiguous pairs: two same-amount, same-method orders two minutes apart
whose payments both lack a reference. Kanakku refuses to guess and raises both as exceptions. That trade-off is
intended.

## Architecture

```mermaid
flowchart LR
    subgraph Sources["CSV sources (synthetic)"]
        O[orders.csv]
        G[gateway_transactions.csv]
        B[bank_statement.csv]
    end
    O & G & B --> V["ingestion.py<br/>row validation, dedupe"]
    V -->|import_from_pandas| S[(STG_* staging)]
    S -->|MERGE on business key| T[(ORDERS / GATEWAY_TRANSACTIONS / BANK_TRANSACTIONS)]
    T --> R["sql/reconcile.sql<br/>4 matching stages, classification, explanations"]
    RC[(FEE_RATE_CARD)] --> R
    R --> RR[(RECONCILIATION_RESULTS)]
    P["priority.py → SQL UPDATE"] --> RR
    RL[(RESOLUTION_LOG)] -->|latest status per key| RR
    RR --> UI["Streamlit dashboard"]
    UI -->|status + note| RL
    GT[(GROUND_TRUTH)] -.->|evaluation only| E["evaluation.py"]
    RR --> E
```

Everything Kanakku owns lives in one Exasol schema (`KANAKKU` by default). See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for tables, the SQL stages and design decisions.

### How Exasol is used

- **Primary store** for sources, staging, results, run history, ingestion audit and the resolution log.
- **All matching and aggregation runs as SQL in Exasol:** window functions (`ROW_NUMBER`, `COUNT(*) OVER`, `FIRST_VALUE`) for duplicate ranking and uniqueness checks, `REGEXP_SUBSTR` to pull batch and payment references out of bank narrations, `DAYS_BETWEEN` / `SECONDS_BETWEEN` / `ADD_DAYS` for ageing and time windows, `MERGE` for idempotent loads and status carry-forward, and `CREATE OR REPLACE TABLE … AS` work tables.
- **A user-defined SQL function** (`FMT_INR`) formats rupee amounts inside explanations.
- **Bulk loading** through PyExasol's `import_from_pandas` (HTTP transport) into staging tables.
- **Transactions:** each run executes with autocommit off. A failure rolls back every partial result and marks the run `FAILED` (covered by a test).
- **Scoring in SQL:** the priority UPDATE is generated from the same weight tables as the Python reference implementation, and a test checks they agree on every row.

## Setup

Requirements: macOS 15+ on Apple silicon with ≥ 8 GB RAM (Exasol Personal local mode; Linux and Windows also
work), Python 3.11+, and roughly **10 GB free disk** for the Exasol VM image and data.

```bash
# 1. Exasol Personal (official launcher, installs to ~/.local/bin)
curl https://downloads.exasol.com/exasol-personal/installer.sh | sh
```

```bash
# 2. Start a local database (smaller VM than the default; adjust to your machine)
~/.local/bin/exasol install local --cpu-count 4 --memory-mb 6144 --data-size-gb 20
```

```bash
# 3. Python environment
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
# 4. Credentials: copy the template, then paste the password from
#    ~/.exasol/personal/deployments/default/secrets.json (key "dbPassword")
cp .env.example .env
```

`exasol info` shows the host and port (default `127.0.0.1:8563`, user `sys`). The local database uses a self-signed
certificate, so `.env.example` sets `EXASOL_CERT_VALIDATION=false`.

## Run

```bash
# Everything in one go: generate → load (replace) → reconcile → evaluate
.venv/bin/python -m kanakku demo
```

```bash
# The dashboard
.venv/bin/python -m streamlit run app/streamlit_app.py
```

Individual steps:

```bash
.venv/bin/python -m kanakku generate --seed 42 --orders 10000   # writes data/sample/*.csv
```

```bash
.venv/bin/python -m kanakku load --replace                       # validate + MERGE into Exasol
```

```bash
.venv/bin/python -m kanakku reconcile --tolerance 100 --delay-days 2
```

```bash
.venv/bin/python -m kanakku evaluate
```

Stop or restart the database with `exasol stop` and `exasol start`.

## Dashboard

| Page | What it shows |
|---|---|
| **Overview** | Matched count, match rate, unresolved exceptions, open exposure, exceptions by issue, priority mix, daily trend, ageing |
| **Data Import** | Upload the three CSVs (plus optional ground truth). Shows per-row validation results; loads into Exasol. One-click bundled sample or freshly generated data |
| **Reconciliation** | Tune tolerance, delay, batch window, fee band and fuzzy matching. Runs the pipeline and shows results by stage and the run history |
| **Investigation Queue** | Prioritised, filterable (issue, band, status, level) and searchable exceptions; CSV export |
| **Exception Details** | Order, gateway and bank records side by side; explanation (Confirmed or Suspected), evidence, score breakdown; status and notes with full history |
| **Data Quality & Evaluation** | Rejected rows with reasons, duplicates, precision and recall per issue code, confusion matrix, false-positive and missed lists |

Every number on every page is queried live from Exasol.

## Priority score

| Component | Points | Rule |
|---|---|---|
| Amount at risk (exposure) | 0–40 | ≥ ₹50,000 → 40 · ≥ ₹10,000 → 32 · ≥ ₹2,000 → 24 · ≥ ₹500 → 16 · ≥ ₹100 → 8 · > 0 → 4 |
| Ageing | 2–25 | ≥ 30 days → 25 · ≥ 14 → 18 · ≥ 7 → 12 · ≥ 3 → 6 · else 2 |
| Severity | 6–25 | Missing from bank 25 · Reversal/cancellation 22 · Duplicate 20 · Amount mismatch 18 · Missing from gateway 15 · Unresolved 15 · Fee difference 8 · Delayed 6 |
| Financial-close impact | 3–10 | Dated in the period being closed (default: the month before the as-of date) 10 · earlier, already-closed period 7 · after the close period 3 |

**Band:** High ≥ 70, Medium ≥ 50, Low < 50. Delayed-but-arrived settlements have zero exposure, so they rank low. The
weights live in [`kanakku/priority.py`](kanakku/priority.py).

## Tests

```bash
.venv/bin/python -m pytest -q                 # all tests (Exasol tests skip if the DB is down)
```

```bash
.venv/bin/python -m pytest -q -m "not exasol" # unit tests only
```

The integration tests build a throw-away `KANAKKU_TEST` schema, load a hand-built scenario where every rule should
fire exactly once, and drop the schema afterwards. They cover:

- exact, tolerance (100 vs 101 paise), batch-by-reference, constrained-batch and fuzzy matching, including an ambiguous pair that must *not* match
- duplicates (gateway and bank), fee differences, chargebacks, cancellations, refunds, and instant settlements that are missing or delayed
- monetary round-trip, idempotent reloads, stable reruns, resolution logging and carry-forward
- rollback of a failed run, and SQL-vs-Python priority parity

The unit tests cover money parsing, malformed CSVs, generator reproducibility, score thresholds and the evaluation
maths.

## Project layout

```
kanakku/            Python package
  config.py         env-based settings, matching parameters
  money.py          paise parsing/formatting (no floats)
  db.py             PyExasol connection, script runner
  schema.py         idempotent DDL + rate card
  generator.py      synthetic data + ground truth
  ingestion.py      CSV validation, staging, MERGE
  reconciliation.py runs sql/reconcile.sql atomically
  priority.py       score weights (Python + generated SQL)
  evaluation.py     precision / recall vs ground truth
  resolution.py     status changes → RESOLUTION_LOG
  queries.py        read-only SQL for the dashboard
sql/                schema.sql, reconcile.sql, functions.sql
app/                Streamlit dashboard (streamlit_app.py + views/)
tests/              pytest (unit + Exasol integration)
data/sample/        bundled synthetic CSVs, ground truth, manifest
docs/               architecture, demo script, viva guide
deck/               pitch deck (.pptx)
```

## Limitations

- **Synthetic data only.** The scenarios mirror the rules, so the evaluation measures consistency, not real-world accuracy. Real gateway exports (Razorpay, PayU, Cashfree, …) would each need a column mapping.
- **INR only.** Multi-currency, FX and partial settlements are out of scope. A batch credited in two parts is flagged as a possible duplicate.
- **Fuzzy matching is deliberately narrow** (exact amount, same method, ±15 minutes, unique on both sides). It misses typo'd references with near-identical neighbours. That is by design.
- **Constrained batch matching needs an exact amount,** so an unreferenced *and* short-paid credit shows up as a missing batch plus an unresolved bank line.
- **The fee check uses a static rate card** (`FEE_RATE_CARD`). Negotiated or tiered pricing would need per-merchant rates.
- **No authentication or multi-user locking.** It is a single-analyst tool, as scoped for a two-day build.
- **The priority score is a heuristic** with hand-chosen weights. It has not been calibrated against real outcomes.

## License

[MIT](LICENSE)
