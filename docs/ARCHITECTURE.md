# Truva architecture

## Components

| Layer | Module | Responsibility |
|---|---|---|
| Configuration | `kanakku/config.py` | Reads `EXASOL_*` env vars (via `.env`); holds `ReconConfig` matching parameters |
| Money | `kanakku/money.py` | Decimal string ↔ integer paise; Indian digit grouping for display |
| Database access | `kanakku/db.py` | PyExasol connection (TLS, self-signed cert allowed for local), script runner, query helpers |
| Schema | `kanakku/schema.py`, `sql/schema.sql`, `sql/functions.sql` | Idempotent `CREATE TABLE IF NOT EXISTS`, `FMT_INR` UDF, rate-card seed |
| Data generation | `kanakku/generator.py` | Reproducible synthetic orders, gateway, bank, ground truth and manifest |
| Ingestion | `kanakku/ingestion.py` | Row validation, duplicate handling, staging + `MERGE`, audit tables |
| Reconciliation | `kanakku/reconciliation.py`, `sql/reconcile.sql` | Four matching stages and classification in Exasol SQL; one transaction per run |
| Prioritisation | `kanakku/priority.py` | Weight tables → Python reference score and generated SQL `UPDATE` |
| Resolution | `kanakku/resolution.py` | Append-only status/notes log |
| Evaluation | `kanakku/evaluation.py` | Engine output vs ground truth: detection and per-code metrics |
| Recurring breaks | `kanakku/patterns.py`, `sql/patterns.sql` | Groups unresolved exceptions in Exasol; Wilson/Bonferroni support test; hypothesis wording |
| UI data | `kanakku/queries.py` | All read-only SQL the dashboard uses |
| UI | `app/` | Streamlit multipage app |

## Tables (schema `KANAKKU`)

| Table | Grain | Notes |
|---|---|---|
| `ORDERS` | one row per merchant order | PK `ORDER_ID`; status COMPLETED / CANCELLED / REFUNDED |
| `GATEWAY_TRANSACTIONS` | one row per gateway event | PK `GATEWAY_TXN_ID`; type PAYMENT / REFUND / CHARGEBACK; status CAPTURED / FAILED; `ORDER_REF` kept raw; settlement mode BATCH / INSTANT + batch id + expected date |
| `BANK_TRANSACTIONS` | one row per statement line | PK `BANK_TXN_ID`; positive amount + direction CR / DR; narration holds the reference |
| `STG_*` | scratch | Truncated before and after each load |
| `INGESTION_LOG`, `INGESTION_REJECTS` | per load / per rejected row | Line number, reason and raw record for every reject |
| `FEE_RATE_CARD` | per payment method | Published MDR (bps) and GST %, used to recognise fee differences |
| `RECON_RUNS` | per run | Parameters (JSON), as-of date, close period, counts, exposure, duration, status / error |
| `RECONCILIATION_RESULTS` | per run × entity | One row for every order, batch, unmatched/duplicate gateway transaction and unmatched/duplicate bank line. Ids, amounts, discrepancy, exposure, dates, stage, evidence type, explanation, evidence, score parts, band, status |
| `RESOLUTION_LOG` | per analyst action | Append-only; keyed by `EXCEPTION_KEY = level:entity:issue`, so a decision survives reruns |
| `GROUND_TRUTH` | per seeded entity | Evaluation only, never read by `reconcile.sql` (a test greps for it) |

Money is `DECIMAL(18,0)` paise everywhere. Rupee floats appear only in display code.

## Reconciliation SQL (`sql/reconcile.sql`)

The script is plain SQL with `{placeholders}`. These are filled only with validated ints, floats, booleans and
dates (`db.render` refuses strings).

1. **Normalise.** `WRK_GW` holds captured gateway rows with `NORM_REF = UPPER(TRIM(ORDER_REF))` and a signed net
   (refunds and chargebacks negative). `WRK_BANK` holds bank lines with a signed amount, plus `BATCH_REF` and
   `PAY_REF` extracted with `REGEXP_SUBSTR`. `WRK_BANK_RANKED` ranks lines per reference
   (`ROW_NUMBER() OVER (PARTITION BY ref ORDER BY value_date, posted_ts)`), so a second line with the same
   reference is a duplicate candidate.
2. **Stages 1 and 2: order ↔ gateway by key.** `WRK_PAY` is a LEFT JOIN of payments to `ORDERS` on
   `ORDER_ID = NORM_REF`. Exact versus tolerance is decided later from `GW_DIFF = gross − order amount`.
3. **Stage 4: fuzzy.** It only considers orders without a keyed payment and payments without a valid key.
   Candidates need the same amount, a compatible method and `ABS(SECONDS_BETWEEN) ≤ window`. A pair is kept
   only when `COUNT(*) OVER (PARTITION BY order) = 1` and `COUNT(*) OVER (PARTITION BY payment) = 1`, and it is
   applied with `MERGE`.
4. **Primary versus duplicate payments.** `WRK_PAY_RANKED` ranks payments per order by time. Rank 1 is the
   primary; ranks > 1 become gateway-level `POSSIBLE_DUPLICATE` rows that point at the primary.
5. **Stage 3: batches.** `WRK_BATCH` aggregates captured batch-mode rows: gross, fees + GST, reversals and
   expected net. `WRK_BATCH_LINK` first links by narration reference (rank 1 only), then constrained:
   unreferenced lines with an identical amount and a value date in `[expected, expected + window]`, unique
   both ways.
6. **Classify.** One CASE expression per level. Its precedence (missing → reversal → fee → amount → bank-leg
   missing / awaiting / amount / delayed → matched) guarantees exactly one code per entity, so nothing is
   double-counted. The order level judges the order ↔ gateway leg, plus the bank leg for *instant*
   settlements. Batch-settled cash is judged once, at batch level.
7. **Explain.** Explanations and evidence strings are built in SQL with `FMT_INR`. They start with
   `Confirmed:`, `Suspected:` or `Unresolved:`.
8. **Persist.** Exposure and discrepancy are computed, rows are inserted with
   `RESULT_ID = run_id × 10⁷ + n` and age = `DAYS_BETWEEN(as_of, reference_date)`, and the latest
   `RESOLUTION_LOG` status per exception key is `MERGE`d in.

`reconciliation.run` then executes the priority `UPDATE`s, writes run statistics and commits. Any error rolls the
whole run back and records the run as `FAILED`.

## Issue codes

| Code | Level | Evidence | Rule (defaults) |
|---|---|---|---|
| `MISSING_FROM_GATEWAY` | order | confirmed | Non-cancelled order with no captured payment by key or unique fuzzy candidate |
| `REVERSAL_OR_CANCELLATION` | order | confirmed | Cancelled order with a captured payment not fully refunded; any chargeback; refunded order whose refunds ≠ amount |
| `POSSIBLE_FEE_DIFFERENCE` | order | suspected | Shortfall vs order amount equals the rate-card fee + GST (± tolerance) |
| `POSSIBLE_FEE_DIFFERENCE` | batch | suspected | Bank short of batch net by ≤ 2% of batch gross |
| `AMOUNT_MISMATCH` | order / batch | confirmed | Difference > tolerance and not explained as a fee |
| `MISSING_FROM_BANK` | order (instant) / batch | confirmed | No bank line and more than 2 days past the expected date |
| `DELAYED_SETTLEMENT` | order (instant) / batch | confirmed | Credited correctly but more than 2 days late |
| `POSSIBLE_DUPLICATE` | gateway / bank | suspected | Second captured payment for an order, or a second bank line with the same reference |
| `UNRESOLVED` | gateway / bank | suspected | Payment or reversal pointing at no ledger order; bank line with no usable reference or match |
| `AWAITING_SETTLEMENT` | order / batch | n/a | Not yet credited but still inside the grace period (not an exception) |

## Recurring-break intelligence (`sql/patterns.sql`, `kanakku/patterns.py`)

The feature is read-only: it reads `RECONCILIATION_RESULTS` and `FEE_RATE_CARD` for one run and writes nothing, so it
cannot change a reconciliation result.

1. **`open_exc`** holds the run's exceptions whose status is not `RESOLVED` or `ACCEPTED_DIFFERENCE`.
   **`run_orders`** holds the run's order rows, which give the denominators. Using the run's own rows keeps the
   counts consistent even if the source data is reloaded later.
2. **Rate dimensions** (payment method, settlement batch) build a grid: each issue `CROSS JOIN` each group, with a
   `LEFT JOIN` to the exception counts. Window sums over the grid give the issue's total, the population and the
   number of groups compared, so groups with zero exceptions still count in the baseline. For fee differences the
   grid drops methods the rate card charges 0 bps.
3. **Share dimensions** (amount-difference shape, ageing) use `SUM(COUNT(*)) OVER (PARTITION BY issue)` for the
   issue's total.
4. **Python** (`assess`) computes the group rate and its Wilson interval with
   `z = Φ⁻¹(1 − α / groups_compared)`. A group is supported when it has at least `min_support` exceptions and the
   lower bound beats the overall rate (rate groups) or 50% (share groups). It is labelled *Strong* when the lower
   bound is at least 2× the overall rate, or at least 75% for share groups.
5. **`narrative`** fills a template keyed by (dimension, issue) with four parts: evidence, a hypothesis that always
   starts "Hypothesis (not verified)", a suggested investigation, and what would confirm or refute it. Templates
   avoid causal certainty, and a test enforces that.

Dimensions deliberately **not** grouped, because the result would restate a rule instead of finding something:

- fee differences by amount shape (the shape is the rule that flags them)
- bank-leg issues by settlement mode (the engine only judges instant settlements per order)

## Design decisions

- **SQL in Exasol, not pandas,** for matching. That is the analytical workload Exasol is built for, and it keeps
  results next to the data. pandas is used only for row-level CSV validation, where per-row error messages
  matter.
- **One result per entity per run.** History is kept by `RUN_ID`, and a rerun never overwrites an earlier run.
- **An append-only resolution log keyed by a stable exception key** means analyst work survives reruns and data
  reloads.
- **Conservative matching.** Every non-exact link must be unique in both directions, because a wrong automatic
  match hides money while an extra exception only costs a look.
- **Integer paise** end to end, with integer half-up fee arithmetic mirrored in SQL (`FLOOR((x·bps + 5000)/10000)`).
- **Only Truva's own schema is touched.** `--replace` truncates only the three source tables, and the tests use
  a separate `KANAKKU_TEST` schema.
