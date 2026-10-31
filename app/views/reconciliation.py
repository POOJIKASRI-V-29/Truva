from datetime import date

import pandas as pd
import streamlit as st

import common
from kanakku import queries, reconciliation
from kanakku.config import ReconConfig

common.page("Reconciliation", "Runs exact → tolerance → settlement-batch → conservative fuzzy matching as SQL "
                              "inside Exasol, then scores every exception.")
c = common.conn()

with st.form("params"):
    a, b, d = st.columns(3)
    tol = a.number_input("Amount tolerance (₹)", 0.0, 10_000.0, 1.0, step=0.5,
                         help="Order vs gateway differences up to this amount count as matched (rounding).")
    delay = b.number_input("Delay threshold (days)", 0, 60, 2,
                           help="A settlement credited later than this after its expected date is DELAYED.")
    window = d.number_input("Batch date window (days)", 0, 60, 7,
                            help="An unreferenced bank credit may match a batch credited within this window.")
    a, b, d = st.columns(3)
    fee_pct = a.number_input("Fee band (% of batch gross)", 0.0, 20.0, 2.0, step=0.5,
                             help="A batch short-paid by at most this share is a suspected fee difference.")
    fuzzy = b.toggle("Conservative fuzzy matching", True)
    fuzzy_min = d.number_input("Fuzzy time window (minutes)", 1, 1440, 15)
    use_asof = st.checkbox("Override as-of date (default: latest bank value date)")
    as_of = st.date_input("As-of date", date.today())
    submitted = st.form_submit_button("Run reconciliation", type="primary")

if submitted:
    cfg = ReconConfig(amount_tolerance_minor=int(round(tol * 100)), delay_threshold_days=int(delay),
                      batch_window_days=int(window), fee_difference_max_pct=float(fee_pct), fuzzy_enabled=fuzzy,
                      fuzzy_window_minutes=int(fuzzy_min), as_of_date=as_of if use_asof else None)
    with st.spinner("Matching inside Exasol…"):
        try:
            r = reconciliation.run(c, cfg)
        except Exception as e:
            st.error(f"Run failed and was rolled back: {e}")
            st.stop()
    st.session_state["run_id"] = r["run_id"]
    rate = r["orders_matched"] / r["orders_total"] if r["orders_total"] else 0
    st.success(f"Run {r['run_id']} completed in {r['duration_ms']:,} ms — {r['orders_matched']:,}/{r['orders_total']:,} "
               f"orders matched ({rate:.2%}), {r['exceptions_total']:,} exceptions, "
               f"exposure {common.inr(r['exposure_minor'])}.")

run_id = common.current_run_id()
if run_id:
    st.subheader(f"Results of run {run_id} by stage")
    bs = queries.by_stage(c, run_id)
    pivot = bs.pivot_table(index=["entity_level", "issue_code"], columns="match_stage", values="n",
                           aggfunc="sum", fill_value=0)
    st.dataframe(pivot, width="stretch")
    st.caption("Stages: EXACT = reference + amount equal · TOLERANCE = amount within tolerance · KEY_ONLY = reference "
               "matched, amount did not · BATCH_REF / BATCH_CONSTRAINED = settlement batch linked by narration "
               "reference / by unique amount+date · FUZZY = unique same-amount payment near the order time · "
               "NONE = no counterpart. Each entity appears exactly once per run.")

st.subheader("Run history")
runs = queries.runs(c)
if not runs.empty:
    runs = runs[["run_id", "run_status", "started_at", "as_of_date", "orders_total", "orders_matched",
                 "exceptions_total", "exposure_minor", "duration_ms", "params", "error_message"]].copy()
    runs["exposure_inr"] = pd.to_numeric(runs.pop("exposure_minor"), errors="coerce") / 100
    st.dataframe(runs, hide_index=True, width="stretch")
