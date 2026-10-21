# Kanakku: beginner's guide and viva preparation

This guide explains Kanakku in plain language so you can defend every part of it honestly.

---

## 1. The story in one paragraph

A shop sells online. For every sale, three systems keep a record:

- **the shop's order ledger:** "Order ORD-0001234, ₹1,499, paid by card"
- **the payment gateway:** "I captured ₹1,499 for ORD-0001234, kept ₹33.62 as fee + GST, and will pay the shop ₹1,465.38 in batch STL-20260903-02"
- **the bank statement:** "Received ₹1,32,410.55 from SYNTHPAY, narration: SETTLEMENT/STL-20260903-02"

Reconciliation means proving these three agree and, where they don't, explaining why. Kanakku automates that and
ranks the problems.

## 2. The important files

| File | In one sentence |
|---|---|
| `kanakku/generator.py` | Invents 10,000 orders plus gateway and bank records, plants known problems in them, and writes the answer key separately |
| `kanakku/ingestion.py` | Reads each CSV, checks every row, rejects bad ones with a reason, and loads the good ones into Exasol without creating duplicates |
| `sql/schema.sql` | Creates the tables (only if they don't exist yet) |
| `sql/reconcile.sql` | **The heart:** the SQL that matches, classifies and explains, run entirely inside Exasol |
| `kanakku/reconciliation.py` | Runs that SQL as one transaction and records the run |
| `kanakku/priority.py` | The scoring rules, in one place, used by both Python and SQL |
| `kanakku/evaluation.py` | Compares Kanakku's answers to the answer key |
| `kanakku/resolution.py` | Saves analyst decisions to the log |
| `app/` | The Streamlit dashboard |
| `tests/` | 111 automated tests |

## 3. The tables

- **ORDERS / GATEWAY_TRANSACTIONS / BANK_TRANSACTIONS:** the three sources, one row per record.
- **RECONCILIATION_RESULTS:** Kanakku's verdict. For each run there is exactly one row per order, per settlement batch, and per gateway or bank record that has a problem.
- **RESOLUTION_LOG:** a diary of what analysts did. Rows are only ever added, never edited.
- **RECON_RUNS:** one row per reconciliation run, with its settings and totals.
- **INGESTION_LOG / INGESTION_REJECTS:** what each load accepted and rejected, and why.
- **FEE_RATE_CARD:** the gateway's published price list (card 1.90%, UPI 0%, …).
- **GROUND_TRUTH:** the answer key. Only the evaluation reads it.

## 4. SQL joins used

- **LEFT JOIN** keeps every row from the left table even with no partner. `ORDERS LEFT JOIN payments` keeps orders that have no payment, and those become `MISSING_FROM_GATEWAY`.
- **JOIN (inner)** keeps only pairs. It is used for fuzzy candidates and for batch ↔ bank candidates.
- **Window functions:** `ROW_NUMBER() OVER (PARTITION BY order ORDER BY time)` numbers the payments of each order 1, 2, 3, …, so #2 and later are possible duplicates. `COUNT(*) OVER (PARTITION BY order)` counts how many candidates an order has, and a fuzzy match is kept only if the count is 1 on both sides.
- **MERGE** means "update if the key exists, insert if not". It makes loading safe to repeat.
- **REGEXP_SUBSTR** pulls `STL-20260903-02` out of a long bank narration.

## 5. The algorithm, step by step

1. **Clean the reference:** `"  ord-0001234 "` becomes `ORD-0001234` (trim + upper case).
2. **Exact match:** same order id and the same amount, so MATCHED (EXACT).
3. **Tolerance:** same id, amount differs by ≤ ₹1.00 (rounding), so MATCHED (TOLERANCE). ₹1.01 is not within tolerance.
4. **Fee check:** if the gateway amount is short by exactly the expected fee + GST from the rate card, the result is POSSIBLE_FEE_DIFFERENCE (*suspected*). Any other difference is AMOUNT_MISMATCH (*confirmed*).
5. **Batches:** add up the gateway's net amounts per batch (payments − fees − GST − refunds − chargebacks) and look for a bank line naming that batch. If the bank line names no batch, accept it only if the amount is identical, the date is within 7 days, and it is the *only* such pair.
6. **Fuzzy (last resort):** a payment with a blank or broken reference is linked to an order only if it is the *only* payment with the same amount and method within 15 minutes, and the order has no other candidate.
7. **Everything else** gets a specific code: missing, delayed, duplicate, reversal or unresolved.
8. **Each entity gets exactly one code,** so no exception is counted twice.

## 6. The risk (priority) score

Score = amount points (max 40) + age points (max 25) + severity points (max 25) + close-period points (max 10).

Example: a missing batch of ₹99,700, 33 days old, due in September while September is being closed, scores
40 + 25 + 25 + 10 = **100 → High**.

A delayed settlement that arrived with the right amount has ₹0 at risk, so it scores low. The score only decides
**what to look at first**. It is not the probability of fraud, and it has not been calibrated on real data.

## 7. Test results (measured)

- 92 unit tests: money parsing, CSV validation, generator, score thresholds, evaluation maths.
- 19 integration tests against real Exasol: every rule on a hand-built dataset, tolerance boundary, reload safety, rerun stability, resolution carry-forward, rollback on failure, and SQL-vs-Python score agreement.
- **All 111 passed** (`python -m pytest -q`, about 9 seconds).
- On the 10,000-order sample: precision 0.970, recall 1.000. The 20 false positives are the deliberately ambiguous pairs.

## 8. Commands

```bash
exasol start
```

```bash
.venv/bin/python -m kanakku demo
```

```bash
.venv/bin/python -m streamlit run app/streamlit_app.py
```

```bash
.venv/bin/python -m pytest -q
```

```bash
.venv/bin/python -m kanakku generate --seed 7 --orders 5000 --out data/generated/seed7
```

---

## 9. Viva questions and honest answers

**Q1. Why Exasol rather than doing everything in pandas?**
Matching is set-based work: joins, aggregations and window functions over every record. That is what an analytical
database does well, and it keeps the results next to the data for the dashboard. The full four-stage run plus
scoring takes about 1 second on 10,000 orders in the local Exasol VM. pandas is used only where per-row error
messages matter: CSV validation.

**Q2. Which Exasol features did you actually use?**
`MERGE`, window functions (`ROW_NUMBER`, `COUNT OVER`, `FIRST_VALUE`), `REGEXP_SUBSTR`, `DAYS_BETWEEN`,
`SECONDS_BETWEEN`, `ADD_DAYS`, `CREATE OR REPLACE TABLE AS`, an `IDENTITY` column, a user-defined function
(`FMT_INR`), transactions with rollback, and PyExasol's `import_from_pandas` bulk load.

**Q3. Did you hit any Exasol dialect limits?**
Yes. Exasol rejected a correlated `EXISTS` inside the SELECT list ("Feature not supported"), so I rewrote that step
with LEFT JOINs. `DAY` is a reserved word, so a column alias had to change. Exasol also treats empty strings as
NULL, which matters for blank references.

**Q4. How do you avoid floating-point errors with money?**
Every amount is integer paise, stored as `DECIMAL(18,0)`. Parsing goes through `Decimal` and rejects more than 2
decimal places. Fees use integer half-up arithmetic, identical in Python and SQL. A test checks that adding 0.01
a hundred thousand times gives exactly ₹1,000.00.

**Q5. How do you make sure the engine doesn't cheat with the ground truth?**
Labels go to a separate file and table. `reconcile.sql` never names `GROUND_TRUTH`, and a test fails if any
matching SQL file mentions it. Evaluation reads results and labels only after the run.

**Q6. Your precision is 97% and recall 100%. Isn't that too good?**
It is good *on scenarios I designed*. The generator and the rules come from the same person, so this shows
consistency, not real-world accuracy. To be honest about trade-offs, I planted 5 ambiguous pairs that a
conservative matcher should refuse to link. Those cause all 20 false positives. Real gateway exports would bring
messier references, partial settlements and multi-currency.

**Q7. What is the difference between "Confirmed" and "Suspected"?**
Confirmed means the fact is directly visible: no bank line carries this batch reference, or the gateway amount is
₹X instead of ₹Y. Suspected means a rule proposes a likely cause, for example "the shortfall equals the card fee",
"this looks like a second charge" or "this is the only unreferenced credit of that exact amount". Suspected
items need a human.

**Q8. Could Kanakku say someone committed fraud?**
No. It never uses the word, and a test checks the explanations. Duplicates and unexplained cash can have innocent
causes (retries, manual transfers). The score ranks investigation order, nothing more.

**Q9. How do you avoid double-counting an exception?**
Each entity (order, batch, gateway transaction, bank line) gets exactly one result per run. A precedence-ordered
CASE decides the single code. Batch-settled cash is judged once at batch level, not again for each order inside
the batch. A test asserts there are no duplicate entities in a run.

**Q10. What happens if I load the same file twice, or run reconciliation twice?**
Loading MERGEs on the business key, so the second load inserts 0 rows (tested). Each reconciliation run gets a
new RUN_ID and leaves old runs untouched, and two runs on the same data give identical results (tested).

**Q11. What if a run crashes halfway?**
The run executes with autocommit off. On any error it rolls back, so no partial results remain, and the run is
recorded as FAILED with the error. A test injects a failure to check this.

**Q12. How do resolution notes survive a rerun?**
Each exception has a stable key, `level:entity:issue`, for example `ORDER:ORD-0001234:AMOUNT_MISMATCH`. Notes go
to an append-only log. After each run, a MERGE copies the latest status per key onto the new results.

**Q13. Why is fuzzy matching so strict?**
A wrong automatic match hides a real problem, while an extra exception only costs a minute of review. So a fuzzy
link needs the same amount, a compatible method, a time within 15 minutes, and uniqueness on both sides.

**Q14. How are batch settlements matched when the bank narration has no batch id?**
Constrained matching: the bank amount must equal the batch net exactly, the value date must fall within 7 days
after the expected date, and the pair must be unique both ways. 8 of 113 batches in the sample matched this way.

**Q15. How did you pick the priority weights and bands?**
By judgement, documented in `priority.py` and the README. Amount carries the most weight (40), then age and
severity (25 each), then close impact (10). The first bands (High ≥ 60) put 58% of exceptions in High because
almost every synthetic exception is over 30 days old. I raised them to High ≥ 70 and Medium ≥ 50, which gives
25% / 54% / 21%. They are not calibrated on real outcomes.

**Q16. What would you build next?**
Column mappings for real gateway formats, partial and multi-part settlements, multi-currency, per-merchant fee
cards, user accounts with an approval workflow, and scheduled runs.

**Q17. What did you verify yourself, and what didn't you?**
I verified: Exasol install and connectivity, loading, all four stages, scoring, evaluation, resolution persistence,
111 passing tests, and every dashboard page loading with live data, including a status update made through the UI.
I did not verify: behaviour on real bank or gateway files, performance beyond 10,000 orders, or other operating
systems.
