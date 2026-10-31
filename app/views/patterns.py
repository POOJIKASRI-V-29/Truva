import streamlit as st

import common
from kanakku import patterns

common.page("Recurring Breaks", "Where unresolved exceptions cluster: counts and money grouped in Exasol, "
                                "with hypotheses to test, not conclusions.")
c = common.conn()
run_id = common.require_run()

a, b, _ = st.columns([1, 1, 2])
min_support = a.number_input("Minimum exceptions per pattern", 2, 50, 3,
                             help="Groups with fewer unresolved exceptions are never reported as patterns.")
confidence = b.selectbox("Confidence", [0.90, 0.95, 0.99], index=1, format_func=lambda v: f"{v:.0%}",
                         help="Lower bounds are Wilson intervals, tightened for the number of groups compared.")

df = patterns.analyse(c, run_id, min_support=int(min_support), alpha=1 - confidence)
if df.empty:
    st.success("This run has no unresolved exceptions.")
    st.stop()
sup = df[df.supported]

m = st.columns(4)
m[0].metric("Groups checked", f"{len(df):,}")
m[1].metric("Supported patterns", f"{len(sup):,}")
m[2].metric("Strong", f"{int((sup.verdict == 'Strong').sum()):,}")
m[3].metric("Exposure in patterns", common.inr(sup.exposure_minor.sum()),
            help="Patterns can overlap (one exception can sit in a method group and an ageing group), so this is not additive.")

st.info("Each pattern is a **hypothesis** built from counts. It says where to look first, not why the breaks "
        "happened. A cause is established only when the suggested check confirms it. A group is reported only "
        "when its lower confidence bound clears the comparison, so chance clusters among ~100 batches are filtered out.")

if sup.empty:
    st.success(f"No group clears the bar in run {run_id}: the unresolved exceptions look spread out, not "
               "concentrated. See every group checked below.")

for i, r in enumerate(sup.to_dict("records")):
    with st.container(border=True):
        color = common.BAND_COLORS["HIGH"] if r["verdict"] == "Strong" else common.BAND_COLORS["MEDIUM"]
        st.markdown(common.chip(r["verdict"], color) + common.chip(patterns.DIMENSION_LABELS[r["dimension"]], common.NAVY)
                    + f" &nbsp; **{r['issue_code']} · {r['dimension_value']}**", unsafe_allow_html=True)
        st.markdown(f"**Evidence.** {r['evidence']}")
        st.warning(r["hypothesis"])
        st.markdown(f"**Suggested investigation.** {r['investigate']}")
        st.markdown(f"**What would confirm or refute it.** {r['confirm_or_refute']}")
        ids = patterns.example_ids(r, 3)
        cols = st.columns(len(ids) + 1)
        cols[0].caption("Largest examples:")
        for col, rid in zip(cols[1:], ids):
            if col.button(f"Open {rid}", key=f"pattern-{i}-{rid}"):
                st.session_state["selected_result_id"] = rid
                st.switch_page("views/details.py")

st.subheader("Every group checked")
dims = st.multiselect("Dimension", list(patterns.DIMENSION_LABELS), format_func=patterns.DIMENSION_LABELS.get)
table = df if not dims else df[df.dimension.isin(dims)]
view = table[["verdict", "dimension", "issue_code", "dimension_value", "n", "group_size", "rate", "baseline",
              "lift", "ci_low", "exposure_minor", "reason"]].copy()
view["exposure_inr"] = view.pop("exposure_minor") / 100
st.dataframe(view, hide_index=True, width="stretch", column_config={
    "verdict": "Verdict", "dimension": "Dimension", "issue_code": "Issue", "dimension_value": "Group",
    "n": st.column_config.NumberColumn("Exceptions", format="%d"),
    "group_size": st.column_config.NumberColumn("Out of", format="%d",
                                                help="Orders in the group (rate) or the issue's total (share)"),
    "rate": st.column_config.NumberColumn("Rate / share", format="percent"),
    "baseline": st.column_config.NumberColumn("Compared with", format="percent",
                                              help="Overall rate (rate groups) or 50% (share groups)"),
    "lift": st.column_config.NumberColumn("Lift", format="%.2f×"),
    "ci_low": st.column_config.NumberColumn("Lower bound", format="percent"),
    "exposure_inr": st.column_config.NumberColumn("Exposure ₹", format="%.2f"),
    "reason": st.column_config.TextColumn("Why not supported", width="large"),
})
st.caption("Rate groups (payment method, settlement batch) compare exceptions per order with the rate across all "
           "comparable orders; methods the rate card charges no fee are left out of the fee-difference baseline. "
           "Share groups (amount-difference shape, ageing) need a clear majority of one issue. Fee differences are "
           "not grouped by amount shape, because that shape is how they were flagged.")
