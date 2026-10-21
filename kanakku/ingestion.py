"""Validate the three source CSVs and load them into Exasol without duplicating records.

Validation is done row by row in pandas so every rejected record keeps its line
number and reason. Loading goes CSV -> STG_* staging table -> MERGE into the
target on its business key, so re-loading the same file changes nothing.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pyexasol

from . import db
from .money import MoneyError, to_minor

SUPPORTED_CURRENCIES = {"INR"}
ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")
TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


class CsvSchemaError(ValueError):
    """The file is not a CSV with the expected header — nothing in it is loaded."""


@dataclass
class SourceSpec:
    name: str            # ORDERS | GATEWAY | BANK
    table: str
    key: str             # business key column (lower-case CSV name)
    columns: list[str]   # required CSV header


SPECS = {
    "orders": SourceSpec("ORDERS", "ORDERS", "order_id",
                         ["order_id", "order_ts", "customer_ref", "amount", "currency",
                          "order_status", "payment_method", "channel"]),
    "gateway": SourceSpec("GATEWAY", "GATEWAY_TRANSACTIONS", "gateway_txn_id",
                          ["gateway_txn_id", "order_ref", "txn_type", "txn_status", "txn_ts",
                           "gross_amount", "fee", "tax", "net_amount", "currency", "payment_method",
                           "settlement_mode", "settlement_batch_id", "expected_settlement_date"]),
    "bank": SourceSpec("BANK", "BANK_TRANSACTIONS", "bank_txn_id",
                       ["bank_txn_id", "value_date", "posted_ts", "amount", "direction", "currency",
                        "narration", "bank_reference"]),
}


@dataclass
class ValidationResult:
    source: str
    rows_read: int
    valid: pd.DataFrame
    rejects: list[dict] = field(default_factory=list)
    exact_duplicates: int = 0
    conflicting_duplicates: int = 0

    @property
    def rows_valid(self) -> int:
        return len(self.valid)

    @property
    def rows_rejected(self) -> int:
        return len(self.rejects)


def _ts(v: str) -> datetime:
    for fmt in TS_FORMATS:
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            pass
    raise ValueError(f"timestamp {v!r} is not YYYY-MM-DD HH:MM:SS")


def _date(v: str):
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"date {v!r} is not YYYY-MM-DD") from None


def _id(v: str, label: str) -> str:
    if not v:
        raise ValueError(f"{label} is missing")
    if not ID_RE.match(v):
        raise ValueError(f"{label} {v!r} has invalid characters")
    return v


def _currency(v: str) -> str:
    if v not in SUPPORTED_CURRENCIES:
        raise ValueError(f"currency {v!r} is not supported (expected INR)")
    return v


def _money(v: str, label: str, positive: bool = False, allow_zero: bool = True) -> int:
    try:
        m = to_minor(v)
    except MoneyError as e:
        raise ValueError(f"{label}: {e}") from None
    if m < 0 or (positive and m == 0 and not allow_zero):
        raise ValueError(f"{label} must be {'positive' if not allow_zero else 'non-negative'}")
    return m


def _choice(v: str, label: str, allowed: set[str], optional: bool = False):
    if optional and not v:
        return None
    if v not in allowed:
        raise ValueError(f"{label} {v!r} not in {sorted(allowed)}")
    return v


def _parse_order(r: dict) -> dict:
    return {
        "ORDER_ID": _id(r["order_id"], "order_id"),
        "ORDER_TS": _ts(r["order_ts"]),
        "CUSTOMER_REF": r["customer_ref"] or None,
        "AMOUNT_MINOR": _money(r["amount"], "amount", positive=True, allow_zero=False),
        "CURRENCY": _currency(r["currency"]),
        "ORDER_STATUS": _choice(r["order_status"], "order_status", {"COMPLETED", "CANCELLED", "REFUNDED"}),
        "PAYMENT_METHOD": _choice(r["payment_method"], "payment_method",
                                  {"UPI", "CARD", "NETBANKING", "WALLET"}, optional=True),
        "CHANNEL": r["channel"] or None,
    }


def _parse_gateway(r: dict) -> dict:
    out = {
        "GATEWAY_TXN_ID": _id(r["gateway_txn_id"], "gateway_txn_id"),
        "ORDER_REF": r["order_ref"] or None,  # kept raw; the engine normalises it
        "TXN_TYPE": _choice(r["txn_type"], "txn_type", {"PAYMENT", "REFUND", "CHARGEBACK"}),
        "TXN_STATUS": _choice(r["txn_status"], "txn_status", {"CAPTURED", "FAILED"}),
        "TXN_TS": _ts(r["txn_ts"]),
        "GROSS_AMOUNT_MINOR": _money(r["gross_amount"], "gross_amount"),
        "FEE_MINOR": _money(r["fee"], "fee"),
        "TAX_MINOR": _money(r["tax"], "tax"),
        "NET_AMOUNT_MINOR": _money(r["net_amount"], "net_amount"),
        "CURRENCY": _currency(r["currency"]),
        "PAYMENT_METHOD": _choice(r["payment_method"], "payment_method",
                                  {"UPI", "CARD", "NETBANKING", "WALLET"}, optional=True),
        "SETTLEMENT_MODE": _choice(r["settlement_mode"], "settlement_mode", {"BATCH", "INSTANT"}, optional=True),
        "SETTLEMENT_BATCH_ID": r["settlement_batch_id"] or None,
        "EXPECTED_SETTLEMENT_DATE": _date(r["expected_settlement_date"]) if r["expected_settlement_date"] else None,
    }
    if out["TXN_STATUS"] == "CAPTURED":
        expected_net = out["GROSS_AMOUNT_MINOR"] - out["FEE_MINOR"] - out["TAX_MINOR"]
        if out["NET_AMOUNT_MINOR"] != expected_net:
            raise ValueError(f"net_amount {r['net_amount']} != gross - fee - tax")
        if out["SETTLEMENT_MODE"] is None or out["EXPECTED_SETTLEMENT_DATE"] is None:
            raise ValueError("captured transaction needs settlement_mode and expected_settlement_date")
        if out["SETTLEMENT_MODE"] == "BATCH" and not out["SETTLEMENT_BATCH_ID"]:
            raise ValueError("batch-settled transaction has no settlement_batch_id")
    return out


def _parse_bank(r: dict) -> dict:
    return {
        "BANK_TXN_ID": _id(r["bank_txn_id"], "bank_txn_id"),
        "VALUE_DATE": _date(r["value_date"]),
        "POSTED_TS": _ts(r["posted_ts"]) if r["posted_ts"] else None,
        "AMOUNT_MINOR": _money(r["amount"], "amount", positive=True, allow_zero=False),
        "DIRECTION": _choice(r["direction"], "direction", {"CR", "DR"}),
        "CURRENCY": _currency(r["currency"]),
        "NARRATION": r["narration"] or None,
        "BANK_REFERENCE": r["bank_reference"] or None,
    }


PARSERS = {"orders": _parse_order, "gateway": _parse_gateway, "bank": _parse_bank}


def read_csv(source: str | Path | bytes | io.IOBase) -> pd.DataFrame:
    """Read a CSV keeping every value as text (no float conversion of money)."""
    if isinstance(source, bytes):
        source = io.BytesIO(source)
    try:
        df = pd.read_csv(source, dtype=str, keep_default_na=False, skipinitialspace=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as e:
        raise CsvSchemaError(f"not a readable CSV: {e}") from None
    df.columns = [c.strip().lower() for c in df.columns]
    return df


def validate(kind: str, raw: pd.DataFrame) -> ValidationResult:
    spec = SPECS[kind]
    missing = [c for c in spec.columns if c not in raw.columns]
    if missing:
        raise CsvSchemaError(f"{spec.name} file is missing columns: {', '.join(missing)}")
    raw = raw[spec.columns].copy()
    for c in spec.columns:
        if c != "order_ref":  # leading/trailing spaces in order_ref are real gateway noise
            raw[c] = raw[c].str.strip()

    parser = PARSERS[kind]
    rows, rejects = [], []
    seen: dict[str, tuple[int, tuple]] = {}
    exact = conflicting = 0
    for i, rec in enumerate(raw.to_dict("records")):
        line = i + 2  # header is line 1
        key = rec[spec.key]
        signature = tuple(rec.values())
        if key and key in seen:
            first_line, first_sig = seen[key]
            if signature == first_sig:
                exact += 1
                continue
            conflicting += 1
            rejects.append(_reject(spec, line, key, f"conflicting duplicate of {key} (first seen on line {first_line})", rec))
            continue
        try:
            rows.append(parser(rec))
        except ValueError as e:
            rejects.append(_reject(spec, line, key, str(e), rec))
            continue
        seen[key] = (line, signature)
    valid = pd.DataFrame(rows)
    return ValidationResult(kind, len(raw), valid, rejects, exact, conflicting)


def _reject(spec: SourceSpec, line: int, key: str, reason: str, rec: dict) -> dict:
    return {"SOURCE_NAME": spec.name, "LINE_NO": line, "RECORD_ID": key or None,
            "REJECT_REASON": reason[:500], "RAW_RECORD": json.dumps(rec)[:2000]}


# ---------------------------------------------------------------------- loading

def _next_load_id(conn: pyexasol.ExaConnection) -> int:
    return int(db.scalar(conn, "SELECT COALESCE(MAX(LOAD_ID), 0) + 1 FROM INGESTION_LOG"))


def _sql_text(v):
    if v is None or (not isinstance(v, str) and pd.isna(v)):
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date):
        return v.isoformat()
    return v


def _for_import(df: pd.DataFrame) -> pd.DataFrame:
    """Render dates/timestamps in Exasol's default literal formats; missing -> NULL."""
    return pd.DataFrame({c: df[c].astype(object).map(_sql_text) for c in df.columns})


def load(conn: pyexasol.ExaConnection, result: ValidationResult, file_name: str) -> dict:
    """Merge validated rows into the target table and log the load. Returns load stats."""
    spec = SPECS[result.source]
    load_id = _next_load_id(conn)
    stg = f"STG_{spec.table}"
    key = spec.key.upper()
    inserted = updated = 0
    if result.rows_valid:
        conn.execute(f"TRUNCATE TABLE {stg}")
        conn.import_from_pandas(_for_import(result.valid), stg,
                                import_params={"columns": list(result.valid.columns)})
        updated = int(db.scalar(conn, f"SELECT COUNT(*) FROM {stg} s JOIN {spec.table} t ON t.{key} = s.{key}"))
        inserted = result.rows_valid - updated
        cols = list(result.valid.columns)
        set_clause = ", ".join(f"t.{c} = s.{c}" for c in cols if c != key)
        conn.execute(
            f"""MERGE INTO {spec.table} t USING {stg} s ON t.{key} = s.{key}
                WHEN MATCHED THEN UPDATE SET {set_clause}, t.LOAD_ID = {{load_id!d}},
                     t.SOURCE_FILE = {{file}}, t.UPDATED_AT = CURRENT_TIMESTAMP
                WHEN NOT MATCHED THEN INSERT ({", ".join(cols)}, LOAD_ID, SOURCE_FILE, LOADED_AT, UPDATED_AT)
                     VALUES ({", ".join("s." + c for c in cols)}, {{load_id!d}}, {{file}},
                             CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            {"load_id": load_id, "file": file_name[:200]},
        )
        conn.execute(f"TRUNCATE TABLE {stg}")
    if result.rejects:
        rej = pd.DataFrame(result.rejects)
        rej.insert(0, "LOAD_ID", load_id)
        conn.import_from_pandas(rej, "INGESTION_REJECTS", import_params={"columns": list(rej.columns)})
    stats = {
        "LOAD_ID": load_id, "SOURCE_NAME": spec.name, "FILE_NAME": file_name[:200],
        "LOADED_AT": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ROWS_READ": result.rows_read, "ROWS_VALID": result.rows_valid, "ROWS_REJECTED": result.rows_rejected,
        "EXACT_DUPLICATES": result.exact_duplicates, "CONFLICTING_DUPLICATES": result.conflicting_duplicates,
        "ROWS_INSERTED": inserted, "ROWS_UPDATED": updated,
    }
    log = pd.DataFrame([stats])
    conn.import_from_pandas(log, "INGESTION_LOG", import_params={"columns": list(log.columns)})
    return stats


def load_ground_truth(conn: pyexasol.ExaConnection, truth: pd.DataFrame) -> int:
    """Replace the evaluation answer key. Only GROUND_TRUTH is touched."""
    df = truth.rename(columns=str.upper)[["ENTITY_LEVEL", "ENTITY_ID", "TRUE_ISSUE_CODE", "SCENARIO"]]
    conn.execute("DELETE FROM GROUND_TRUTH")
    conn.import_from_pandas(df, "GROUND_TRUTH", import_params={"columns": list(df.columns)})
    return len(df)
