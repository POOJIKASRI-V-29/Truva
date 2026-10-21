import streamlit as st

import common
from kanakku import queries
from kanakku.resolution import STATUSES

common.page("Investigation Queue", "Open exceptions ranked by investigation priority (a transparent heuristic, "
                                   "not a fraud probability).")
c = common.conn()
run_id = common.require_run()

f1, f2, f3, f4 = st.columns(4)
codes = f1.multiselect("Issue", queries.ISSUE_CODES)
bands = f2.multiselect("Priority", queries.BANDS)
statuses = f3.multiselect("Status", STATUSES, default=["OPEN", "INVESTIGATING", "ESCALATED"])
levels = f4.multiselect("Level", queries.LEVELS)
search = st.text_input("Search id, batch, or explanation text", placeholder="e.g. ORD-0001234, STL-20260915-02, chargeback")

q = queries.queue(c, run_id, codes, bands, statuses, levels, search)
total_exposure = int(q.exposure_minor.fillna(0).astype(int).sum()) if not q.empty else 0
m = st.columns(4)
m[0].metric("Exceptions in view", f"{len(q):,}")
m[1].metric("Exposure in view", common.inr(total_exposure))
m[2].metric("High", f"{int((q.priority_band == 'HIGH').sum()):,}" if not q.empty else "0")
m[3].metric("Suspected (not confirmed)", f"{int((q.evidence_type == 'SUSPECTED').sum()):,}" if not q.empty else "0")

if q.empty:
    st.info("Nothing matches these filters.")
    st.stop()

view = q.copy()
view["exposure_inr"] = view.pop("exposure_minor").astype(float) / 100
view["discrepancy_inr"] = view.pop("discrepancy_minor").astype(float) / 100
view = view[["result_id", "priority_score", "priority_band", "issue_code", "entity_level", "entity_id",
             "evidence_type", "exposure_inr", "discrepancy_inr", "age_days", "resolution_status", "match_stage",
             "explanation"]]
event = st.dataframe(
    view, hide_index=True, use_container_width=True, height=520, on_select="rerun", selection_mode="single-row",
    column_config={
        "result_id": st.column_config.NumberColumn("Result", format="%d"),
        "priority_score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
        "priority_band": "Band", "issue_code": "Issue", "entity_level": "Level", "entity_id": "Entity",
        "evidence_type": "Evidence",
        "exposure_inr": st.column_config.NumberColumn("Exposure ₹", format="%.2f"),
        "discrepancy_inr": st.column_config.NumberColumn("Discrepancy ₹", format="%.2f"),
        "age_days": st.column_config.NumberColumn("Age (d)", format="%d"),
        "resolution_status": "Status", "match_stage": "Stage",
        "explanation": st.column_config.TextColumn("Explanation", width="large"),
    })
st.download_button("Download this view (CSV)", view.to_csv(index=False), f"kanakku_run{run_id}_queue.csv", "text/csv")

rows = event.selection.rows if event and event.selection else []
if rows:
    rid = int(view.iloc[rows[0]].result_id)
    st.session_state["selected_result_id"] = rid
    if st.button(f"Open result {rid} in Exception Details →", type="primary"):
        st.switch_page("views/details.py")
else:
    st.caption("Select a row to open its details.")
