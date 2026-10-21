import numpy as np
import plotly.express as px
import streamlit as st

import common
from kanakku import evaluation, queries

common.page("Data Quality & Evaluation", "What ingestion rejected, what the engine flagged as duplicates, and how "
                                         "the engine scores against the synthetic answer key.")
c = common.conn()

st.subheader("Ingestion")
log = queries.ingestion_log(c)
if log.empty:
    st.info("Nothing loaded yet.")
else:
    latest = log.sort_values("load_id").groupby("source_name").tail(1).sort_values("source_name")
    cols = st.columns(len(latest))
    for col, r in zip(cols, latest.itertuples()):
        col.metric(f"{r.source_name} rows valid", f"{int(r.rows_valid):,} / {int(r.rows_read):,}")
        col.caption(f"{int(r.rows_rejected)} rejected · {int(r.exact_duplicates)} exact duplicates dropped · "
                    f"{int(r.conflicting_duplicates)} conflicting duplicates rejected")
    rej = queries.latest_rejects(c)
    if not rej.empty:
        st.markdown("**Rejected records (latest load per source)**")
        st.dataframe(rej[["source_name", "line_no", "record_id", "reject_reason", "raw_record"]], hide_index=True,
                     use_container_width=True)
    with st.expander("Full load history"):
        st.dataframe(log, hide_index=True, use_container_width=True)

run_id = common.current_run_id()
if run_id is None:
    st.stop()

st.subheader("Duplicates detected by the engine")
dups = queries.queue(c, run_id, codes=["POSSIBLE_DUPLICATE"], limit=5000)
if dups.empty:
    st.caption("None in this run.")
else:
    st.write(f"{len(dups)} possible duplicates — "
             f"{int((dups.entity_level == 'GATEWAY_TXN').sum())} gateway payments, "
             f"{int((dups.entity_level == 'BANK_TXN').sum())} bank credits; exposure {common.inr(dups.exposure_minor.sum())}.")

st.subheader(f"Evaluation of run {run_id} against ground truth")
m = evaluation.evaluate_run(c, run_id)
if m is None:
    st.info("No ground truth loaded. The bundled sample includes `ground_truth.csv`; it is used only here.")
    st.stop()

def pct(v):
    return "—" if v is None else f"{v:.1%}"

k = st.columns(5)
k[0].metric("Precision", pct(m["precision"]), help="Of everything flagged, the share that really was an exception")
k[1].metric("Recall", pct(m["recall"]), help="Of all seeded exceptions, the share the engine flagged")
k[2].metric("F1", pct(m["f1"]))
k[3].metric("False positives", m["false_positive"])
k[4].metric("Missed seeded issues", m["missed"])
st.caption(f"{m['entities']:,} entities compared. Code accuracy on correctly detected exceptions: "
           f"{pct(m['code_accuracy_on_detected'])}. Labels come from the generator and are never read by the matching SQL. "
           "Because the generator and the rules were written by the same author, these numbers measure consistency on known "
           "scenarios — not accuracy on real-world data.")

per = m["per_code"].copy()
st.dataframe(per, hide_index=True, use_container_width=True, column_config={
    "precision": st.column_config.NumberColumn(format="%.3f"), "recall": st.column_config.NumberColumn(format="%.3f"),
    "f1": st.column_config.NumberColumn(format="%.3f")})

conf = m["confusion"]
fig = px.imshow(np.log10(conf + 1), text_auto=False, color_continuous_scale=["#FFFFFF", common.MIST, common.BLUE, common.NAVY],
                labels=dict(x="Engine said", y="Ground truth", color="log10(n+1)"), aspect="auto")
fig.update_traces(text=conf.values, texttemplate="%{text}")
fig.update_layout(height=430, margin=dict(l=0, r=0, t=10, b=0))
st.markdown("**Confusion matrix** (colour is log-scaled so the large MATCHED cell does not hide the rest)")
st.plotly_chart(fig, use_container_width=True)

expl = queries.result_explanations(c, run_id)
scen = queries.ground_truth_scenarios(c)
for title, key in [("False positives / wrong code", "false_positive_rows"), ("Missed seeded issues", "missed_rows")]:
    df = m[key]
    st.markdown(f"**{title}: {len(df)}**")
    if not df.empty:
        df = (df.merge(scen, on=["entity_level", "entity_id"], how="left")
                .merge(expl, on=["entity_level", "entity_id"], how="left"))
        st.dataframe(df[["entity_level", "entity_id", "actual", "predicted", "scenario", "result_id", "explanation"]],
                     hide_index=True, use_container_width=True)
