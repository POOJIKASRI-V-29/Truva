import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import common
from kanakku import queries, resolution

common.page("Exception Details", "Source records side by side, the evidence behind the verdict, and the "
                                 "resolution trail.")
c = common.conn()
run_id = common.require_run()

default = st.session_state.get("selected_result_id")
if default is None:
    top = queries.queue(c, run_id, limit=1)
    default = int(top.result_id.iloc[0]) if not top.empty else None
rid = st.number_input("Result id", min_value=0, value=int(default or 0), step=1,
                      help="Pick a row on the Investigation Queue, or type a result id.")
r = queries.result(c, rid)
if r is None:
    st.warning("No result with that id.")
    st.stop()

band = r.get("priority_band") or "NONE"
evidence_color = common.BLUE if r["evidence_type"] == "CONFIRMED" else "#B54708"
st.markdown(
    common.chip(r["issue_code"], common.NAVY) + common.chip(band, common.BAND_COLORS.get(band, common.SLATE)) +
    common.chip(r["evidence_type"] or "", evidence_color) + common.chip(r["resolution_status"] or "", common.SLATE) +
    f" &nbsp; **{r['entity_level']} {r['entity_id']}** · stage {r['match_stage']} · run {int(r['run_id'])}",
    unsafe_allow_html=True)

m = st.columns(5)
m[0].metric("Priority score", int(r["priority_score"] or 0))
m[1].metric("Exposure", common.inr(r["exposure_minor"]))
m[2].metric("Expected", common.inr(r["expected_amount_minor"]))
m[3].metric("Actual", common.inr(r["actual_amount_minor"]))
m[4].metric("Age", f"{int(r['age_days'] or 0)} days")

(st.info if r["evidence_type"] == "CONFIRMED" else st.warning)(r["explanation"])
st.caption("**Confirmed** = the fact is directly visible in the source records. **Suspected** = a likely cause "
           "inferred by a rule; it needs an analyst to confirm. Neither is a finding of fraud.")

left, right = st.columns([3, 2])
with left:
    st.subheader("Evidence")
    pairs = [p.split("=", 1) for p in (r["evidence"] or "").split("; ") if "=" in p]
    st.dataframe(pd.DataFrame(pairs, columns=["Field", "Value"]), hide_index=True, width="stretch")
with right:
    st.subheader("Why this priority")
    if r["is_exception"]:
        parts = {"Amount": r["score_amount"], "Ageing": r["score_age"], "Severity": r["score_severity"],
                 "Close impact": r["score_close"]}
        fig = go.Figure(go.Bar(x=list(parts.values()), y=list(parts), orientation="h", marker_color=common.BLUE,
                               text=[int(v) for v in parts.values()], textposition="outside"))
        fig.update_layout(height=220, margin=dict(l=0, r=20, t=0, b=0), xaxis=dict(range=[0, 45]), plot_bgcolor="white")
        st.plotly_chart(fig, width="stretch")
        st.caption(r["priority_reason"])
    else:
        st.write("Not an exception — no priority.")

st.subheader("Source records")
src = queries.source_records(c, r)
cols = st.columns(3)
for col, (key, title) in zip(cols, [("orders", "Order ledger"), ("gateway", "Payment gateway"), ("bank", "Bank statement")]):
    with col:
        st.markdown(f"**{title}**")
        df = src.get(key)
        if df is None or df.empty:
            st.caption("No matching record.")
        else:
            drop = [x for x in ("source_file", "load_id", "loaded_at", "updated_at") if x in df.columns]
            st.dataframe(common.money_columns(df.drop(columns=drop)).T, width="stretch")
if "batch_transactions" in src:
    with st.expander(f"Gateway transactions in batch {r['settlement_batch_id']} ({len(src['batch_transactions'])})"):
        st.dataframe(common.money_columns(src["batch_transactions"]), hide_index=True, width="stretch")

st.subheader("Resolution")
if r["is_exception"]:
    with st.form("resolve", clear_on_submit=True):
        a, b = st.columns([1, 2])
        new_status = a.selectbox("Status", resolution.STATUSES, index=resolution.STATUSES.index(r["resolution_status"])
                                 if r["resolution_status"] in resolution.STATUSES else 0)
        analyst = a.text_input("Analyst", value=st.session_state.get("analyst", ""))
        note = b.text_area("Note", placeholder="What did you check? What was the outcome?")
        if st.form_submit_button("Save to resolution log", type="primary"):
            st.session_state["analyst"] = analyst
            out = resolution.update_status(c, rid, new_status, note, analyst)
            st.success(f"Saved: {out['previous']} → {out['new']}. Carried forward to future runs.")
            st.rerun()
    hist = resolution.history(c, r["exception_key"])
    if hist.empty:
        st.caption("No resolution history yet.")
    else:
        st.dataframe(hist, hide_index=True, width="stretch")
else:
    st.caption("Matched records need no resolution.")
