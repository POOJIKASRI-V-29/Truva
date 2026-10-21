"""Shared Streamlit helpers: connection, branding, formatting."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kanakku import db, reconciliation  # noqa: E402
from kanakku.money import format_inr  # noqa: E402

NAVY = "#0B1F3A"
BLUE = "#1E3A8A"
SKY = "#3B82F6"
MIST = "#DBEAFE"
SLATE = "#64748B"
BAND_COLORS = {"HIGH": "#B42318", "MEDIUM": "#B54708", "LOW": "#475467"}
SEQ = [NAVY, BLUE, SKY, "#60A5FA", "#93C5FD", "#BFDBFE", SLATE, "#94A3B8"]

CSS = f"""
<style>
  .block-container {{ padding-top: 1.6rem; max-width: 1400px; }}
  [data-testid="stSidebar"] {{ background: {NAVY}; }}
  [data-testid="stSidebar"] * {{ color: #E2E8F0 !important; }}
  [data-testid="stSidebar"] [data-baseweb="select"] * {{ color: {NAVY} !important; }}
  [data-testid="stMetricValue"] > div {{ font-size: 1.55rem; }}
  [data-testid="stSidebarNav"] a[aria-current="page"] {{ background: rgba(255,255,255,0.12); border-radius: 6px; }}
  .kk-hero {{ background: linear-gradient(120deg, {NAVY} 0%, {BLUE} 100%); color: white; padding: 18px 24px;
             border-radius: 12px; margin-bottom: 18px; }}
  .kk-hero h1 {{ color: white; margin: 0; font-size: 1.65rem; font-weight: 700; letter-spacing: .2px; }}
  .kk-hero p {{ color: #CBD5E1; margin: 4px 0 0 0; font-size: .95rem; }}
  .kk-chip {{ display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: .78rem; font-weight: 600;
             color: white; margin-right: 6px; }}
  [data-testid="stMetric"] {{ background: #F8FAFF; border: 1px solid #E2E8F0; border-radius: 10px;
                             padding: 12px 14px; }}
  [data-testid="stMetricLabel"] p {{ color: {SLATE}; font-weight: 600; font-size: .82rem; text-transform: uppercase;
                                    letter-spacing: .4px; }}
  [data-testid="stMetricValue"] {{ color: {NAVY}; }}
</style>
"""


def page(title: str, subtitle: str) -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(f'<div class="kk-hero"><h1>{title}</h1><p>{subtitle}</p></div>', unsafe_allow_html=True)


def chip(text: str, color: str) -> str:
    return f'<span class="kk-chip" style="background:{color}">{text}</span>'


@st.cache_resource(show_spinner=False)
def _connection():
    return db.connect()


def conn():
    """Shared PyExasol connection; reconnects if Exasol dropped it."""
    try:
        c = _connection()
        if c.is_closed:
            raise ConnectionError
        c.execute("SELECT 1")
        return c
    except Exception:
        _connection.clear()
        try:
            return _connection()
        except Exception as exc:  # pragma: no cover - shown in UI
            st.error(f"Cannot connect to Exasol: {exc}\n\nStart it with `exasol start` and check `.env`.")
            st.stop()


def current_run_id() -> int | None:
    """Run chosen in the sidebar (defaults to the latest completed run)."""
    c = conn()
    runs = db.query_df(c, "SELECT RUN_ID, AS_OF_DATE, STARTED_AT FROM RECON_RUNS WHERE RUN_STATUS = 'COMPLETED' "
                          "ORDER BY RUN_ID DESC")
    if runs.empty:
        return None
    labels = {int(r.run_id): f"Run {int(r.run_id)} · as of {r.as_of_date}" for r in runs.itertuples()}
    ids = list(labels)
    if st.session_state.get("run_id") not in ids:
        st.session_state["run_id"] = ids[0]
    return st.sidebar.selectbox("Reconciliation run", ids, format_func=labels.get, key="run_id")


def require_run() -> int:
    rid = current_run_id()
    if rid is None:
        st.info("No reconciliation run yet. Load data on **Data Import**, then run **Reconciliation**.")
        st.stop()
    return rid


def inr(v) -> str:
    return "—" if v is None or (isinstance(v, float) and pd.isna(v)) else format_inr(int(v))


def money_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Turn *_minor columns into rupee floats for display only (stored values stay integer paise)."""
    out = df.copy()
    for c in [c for c in out.columns if c.endswith("_minor")]:
        out[c.replace("_minor", "_inr")] = pd.to_numeric(out[c], errors="coerce") / 100
        out = out.drop(columns=c)
    return out


def latest_completed(c) -> int | None:
    return reconciliation.latest_run_id(c)
