from pathlib import Path

import pandas as pd
import streamlit as st

import common
from kanakku import generator, ingestion, queries, schema
from kanakku.config import PROJECT_ROOT, SAMPLE_DIR
from kanakku.pipeline import load_directory

common.page("Data Import", "Upload the order ledger, gateway report and bank statement. Every row is validated "
                           "before it reaches Exasol; reloading the same file never creates duplicates.")
c = common.conn()
schema.ensure_schema(c)

counts = queries.source_counts(c)
cols = st.columns(4)
cols[0].metric("Orders in Exasol", f"{counts.get('ORDERS', 0):,}")
cols[1].metric("Gateway transactions", f"{counts.get('GATEWAY', 0):,}")
cols[2].metric("Bank lines", f"{counts.get('BANK', 0):,}")
cols[3].metric("Ground-truth labels", f"{counts.get('GROUND_TRUTH', 0):,}", help="Used only by the Evaluation page")

tab_upload, tab_sample = st.tabs(["Upload CSV files", "Sample / synthetic data"])

with tab_upload:
    st.markdown("Expected headers — **orders**: `" + ", ".join(ingestion.SPECS["orders"].columns) +
                "` · **gateway**: `" + ", ".join(ingestion.SPECS["gateway"].columns) +
                "` · **bank**: `" + ", ".join(ingestion.SPECS["bank"].columns) + "`")
    files = {}
    u = st.columns(3)
    files["orders"] = u[0].file_uploader("Order ledger", type="csv", key="up_orders")
    files["gateway"] = u[1].file_uploader("Gateway transactions", type="csv", key="up_gateway")
    files["bank"] = u[2].file_uploader("Bank statement", type="csv", key="up_bank")
    truth_file = st.file_uploader("Ground truth (optional, evaluation only)", type="csv", key="up_truth")

    results = {}
    for kind, f in files.items():
        if f is None:
            continue
        try:
            res = ingestion.validate(kind, ingestion.read_csv(f.getvalue()))
        except ingestion.CsvSchemaError as e:
            st.error(f"**{f.name}** rejected: {e}")
            continue
        results[kind] = (f.name, res)
        with st.expander(f"{f.name}: {res.rows_valid:,} valid · {res.rows_rejected} rejected · "
                         f"{res.exact_duplicates} exact duplicates", expanded=res.rows_rejected > 0):
            if res.rejects:
                st.dataframe(pd.DataFrame(res.rejects)[["LINE_NO", "RECORD_ID", "REJECT_REASON"]], hide_index=True)
            else:
                st.success("All rows valid.")

    replace = st.checkbox("Replace Kanakku's existing source data before loading",
                          help="Truncates ORDERS, GATEWAY_TRANSACTIONS and BANK_TRANSACTIONS in the Kanakku schema "
                               "only. Reconciliation history and resolution notes are kept.")
    if st.button("Load validated rows into Exasol", type="primary", disabled=not results):
        if replace:
            schema.clear_source_data(c)
        for kind, (name, res) in results.items():
            s = ingestion.load(c, res, name)
            st.success(f"{s['SOURCE_NAME']}: {s['ROWS_INSERTED']:,} new, {s['ROWS_UPDATED']:,} existing keys refreshed, "
                       f"{s['ROWS_REJECTED']} rejected (load {s['LOAD_ID']}).")
        if truth_file is not None:
            n = ingestion.load_ground_truth(c, ingestion.read_csv(truth_file.getvalue()))
            st.success(f"Ground truth: {n:,} labels loaded.")
        st.info("Now run the matching pipeline on **Reconciliation**.")

with tab_sample:
    st.markdown("The bundled sample (seed 42, 10,000 orders) lives in `data/sample/`. "
                "You can also generate a fresh reproducible dataset with a different seed.")
    a, b = st.columns(2)
    with a:
        st.markdown("**Bundled sample**")
        if st.button("Load bundled sample (replace source data)"):
            with st.spinner("Validating and loading…"):
                stats = load_directory(c, SAMPLE_DIR, replace=True)
            st.dataframe(pd.DataFrame(stats), hide_index=True)
    with b:
        st.markdown("**Generate new synthetic data**")
        seed = st.number_input("Seed", 1, 10_000_000, 7)
        n = st.number_input("Orders", 500, 50_000, 10_000, step=500)
        if st.button("Generate and load (replace source data)"):
            out = PROJECT_ROOT / "data" / "generated" / f"seed_{seed}_{n}"
            with st.spinner("Generating…"):
                generator.write(generator.generate(generator.GeneratorConfig(seed=int(seed), n_orders=int(n))), out)
                stats = load_directory(c, out, replace=True)
            st.success(f"Written to {Path(out).relative_to(PROJECT_ROOT)} and loaded.")
            st.dataframe(pd.DataFrame(stats), hide_index=True)
