"""Truva dashboard entry point:  streamlit run app/streamlit_app.py"""

import streamlit as st

st.set_page_config(page_title="Truva · Reconciliation", page_icon="₹", layout="wide")

nav = st.navigation({
    "Truva": [
        st.Page("views/overview.py", title="Overview", icon=":material/dashboard:", default=True),
        st.Page("views/data_import.py", title="Data Import", icon=":material/upload_file:"),
        st.Page("views/reconciliation.py", title="Reconciliation", icon=":material/sync_alt:"),
    ],
    "Investigate": [
        st.Page("views/queue.py", title="Investigation Queue", icon=":material/format_list_numbered:"),
        st.Page("views/details.py", title="Exception Details", icon=":material/search:"),
        st.Page("views/patterns.py", title="Recurring Breaks", icon=":material/hub:"),
        st.Page("views/quality.py", title="Data Quality & Evaluation", icon=":material/verified:"),
    ],
})
st.sidebar.markdown("### ₹ Truva")
st.sidebar.caption("Every number tells a story.")
nav.run()
st.sidebar.divider()
st.sidebar.caption("Synthetic data only · Exasol Personal · ExaThon India 2026")
