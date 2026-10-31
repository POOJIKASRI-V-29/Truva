import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import common
from kanakku import queries

common.page("Overview", "Three-way reconciliation of order ledger, payment gateway and bank statement — live from Exasol.")
c = common.conn()
run_id = common.require_run()
k = queries.kpis(c, run_id)
run = queries.runs(c).set_index("run_id").loc[run_id]

orders, matched = int(k["orders"]), int(k["orders_matched"])
rate = matched / orders if orders else 0
cols = st.columns(5)
cols[0].metric("Orders matched", f"{matched:,} / {orders:,}")
cols[1].metric("Order match rate", f"{rate:.2%}")
cols[2].metric("Unresolved exceptions", f"{int(k['unresolved']):,}", help="Exceptions not marked RESOLVED or ACCEPTED_DIFFERENCE")
cols[3].metric("Open discrepancy exposure", common.inr(k["open_exposure"]),
               help="Sum of money at risk on unresolved exceptions (missing amounts, mismatches, duplicates)")
cols[4].metric("High-priority open", f"{int(k['high_open']):,}")
st.caption(f"Run {run_id} · as of {run['as_of_date']} · close period {run['close_period_start']} → "
           f"{run['close_period_end']} · {int(k['batches_matched'])}/{int(k['batches'])} settlement batches matched · "
           f"{int(k['tolerance_matched'])} matched within tolerance · {int(k['fuzzy_matched'])} fuzzy (suspected) matches · "
           f"pipeline time {int(run['duration_ms']):,} ms")

left, right = st.columns([3, 2])
with left:
    st.subheader("Exceptions by issue")
    bi = queries.by_issue(c, run_id)
    if bi.empty:
        st.success("No exceptions in this run.")
    else:
        bi["exposure_inr"] = bi.exposure_minor.astype(float) / 100
        fig = px.bar(bi, x="n", y="issue_code", color="entity_level", orientation="h",
                     color_discrete_sequence=common.SEQ, hover_data={"exposure_inr": ":,.2f"},
                     labels={"n": "Exceptions", "issue_code": "", "entity_level": "Level"})
        fig.update_layout(height=360, margin=dict(l=0, r=0, t=10, b=0), yaxis={"categoryorder": "total ascending"},
                          legend=dict(orientation="h", y=-0.2), plot_bgcolor="white")
        st.plotly_chart(fig, width="stretch")
with right:
    st.subheader("Priority mix")
    bb = queries.by_band(c, run_id)
    if not bb.empty:
        fig = go.Figure(go.Pie(labels=bb.priority_band, values=bb.n, hole=.6, sort=False,
                               marker_colors=[common.BAND_COLORS.get(b, common.SLATE) for b in bb.priority_band]))
        fig.update_layout(height=360, margin=dict(l=0, r=0, t=10, b=0), legend=dict(orientation="h", y=-0.1))
        st.plotly_chart(fig, width="stretch")

left, right = st.columns([3, 2])
with left:
    st.subheader("Daily orders and exceptions")
    tr = queries.daily_trend(c, run_id)
    fig = go.Figure()
    fig.add_bar(x=tr.order_day, y=tr.matched_orders, name="Matched", marker_color=common.MIST)
    fig.add_bar(x=tr.order_day, y=tr.exceptions, name="Exceptions", marker_color=common.BLUE)
    fig.update_layout(barmode="stack", height=320, margin=dict(l=0, r=0, t=10, b=0), plot_bgcolor="white",
                      legend=dict(orientation="h", y=1.1))
    st.plotly_chart(fig, width="stretch")
with right:
    st.subheader("Ageing of unresolved exceptions")
    ag = queries.ageing(c, run_id)
    if not ag.empty:
        ag["exposure_inr"] = ag.exposure_minor.astype(float) / 100
        fig = px.bar(ag, x="bucket", y="n", color_discrete_sequence=[common.NAVY], hover_data={"exposure_inr": ":,.2f"},
                     labels={"bucket": "", "n": "Exceptions"})
        fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0), plot_bgcolor="white")
        st.plotly_chart(fig, width="stretch")

st.subheader("Resolution status")
sc = queries.status_counts(c, run_id)
st.dataframe(sc.rename(columns={"resolution_status": "Status", "n": "Exceptions"}), hide_index=True)
