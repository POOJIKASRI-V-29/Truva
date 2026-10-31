# Truva: 3-minute demo script

**Before recording,** run these from the project folder:

```bash
~/.local/bin/exasol start
```

```bash
.venv/bin/python -m kanakku demo
```

```bash
.venv/bin/python -m streamlit run app/streamlit_app.py --server.port 8510
```

Open http://localhost:8510 on the Overview page, and choose the latest run in the sidebar. Total spoken time is
about 2:50.

On Exception Details, check the current status of the top queue item. Analyst decisions carry forward between
runs, so it may already have one from earlier testing. During the demo, pick a status it doesn't have yet so the
change is visible.

---

**0:00–0:20 · Problem** (Overview page, hero visible)
> "An online merchant in India sees every sale three times: in its order ledger, in the payment gateway report and
> in the bank statement. Fees, GST, T+2 batch settlements, refunds, chargebacks and duplicates mean they never
> agree. Finance teams reconcile this by hand every month-end. Truva does it in Exasol, and explains every break."

**0:20–0:45 · Data in** (Data Import → Sample / synthetic data → Load bundled sample)
> "This is 10,000 synthetic orders with gateway and bank files. Ingestion validates every row. This table shows
> what each file lost: rejected rows, exact duplicates, conflicting duplicates. Loading uses MERGE on business keys,
> so reloading the same file changes nothing."
*(Open Data Quality & Evaluation and point at the rejected-records table.)*
> "Here are the rejects, with line numbers and reasons: a malformed amount, a bad date, a net that doesn't equal
> gross minus fee."

**0:45–1:10 · Reconcile inside Exasol** (Reconciliation → Run reconciliation)
> "One click runs four SQL stages inside Exasol Personal: exact, tolerance, settlement batch and a deliberately
> conservative fuzzy match. Batch matching pulls the batch id out of the bank narration with REGEXP_SUBSTR and
> compares it to the gateway's batch net. The whole run, scoring included, takes about a second."
*(Point at the stage table: 9,224 exact, 200 tolerance, 51 fuzzy; batches by reference and by constrained amount + date.)*

**1:10–1:35 · Overview**
> "94.7% of orders match. 670 exceptions remain, with ₹22.8 lakh of exposure. They're split by issue type and
> by priority, with ageing so you can see what's going stale before the books close."

**1:35–2:15 · Investigate** (Investigation Queue → top row → Exception Details)
> "The queue is ranked by a transparent priority score: amount, age, severity and close-period impact. It's not a
> fraud probability. The top item is a settlement batch of 78 payments, ₹99,700 net, that never reached the bank.
> The explanation says 'Confirmed', because the absence is visible in the records. Here's the evidence, here's
> the score breakdown, and here are the 78 gateway transactions in that batch."
*(Scroll to Resolution, choose a status the item doesn't have yet, e.g. INVESTIGATING, add a note, and click
"Save to resolution log". The status chip at the top changes and a history row appears.)*
> "The note goes into an append-only resolution log in Exasol and carries forward to every future run."

**2:15–2:40 · Honest evaluation** (Data Quality & Evaluation)
> "The generator writes a separate answer key that the matching SQL never reads. Against it: precision 97%,
> recall 100%. All 20 false positives come from pairs of identical orders two minutes apart where neither payment
> has a reference. Truva refuses to guess and flags both, on purpose. Because I wrote both the generator and the
> rules, this shows consistency, not real-world accuracy."

**2:40–2:50 · Close**
> "Truva: Exasol does the heavy matching, every exception is explained, and every number tells a story."
