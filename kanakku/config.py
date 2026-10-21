"""Runtime settings: database credentials come from environment variables (or .env)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = PROJECT_ROOT / "sql"
SAMPLE_DIR = PROJECT_ROOT / "data" / "sample"

load_dotenv(PROJECT_ROOT / ".env")

_IDENT = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


@dataclass(frozen=True)
class DbSettings:
    dsn: str
    user: str
    password: str
    schema: str
    cert_validation: bool

    @classmethod
    def from_env(cls, schema: str | None = None) -> "DbSettings":
        schema = (schema or os.getenv("EXASOL_SCHEMA", "KANAKKU")).upper()
        if not _IDENT.match(schema):
            raise ValueError(f"Invalid schema name: {schema!r}")
        password = os.getenv("EXASOL_PASSWORD")
        if not password:
            raise RuntimeError(
                "EXASOL_PASSWORD is not set. Copy .env.example to .env and fill it in."
            )
        return cls(
            dsn=os.getenv("EXASOL_DSN", "127.0.0.1:8563"),
            user=os.getenv("EXASOL_USER", "sys"),
            password=password,
            schema=schema,
            cert_validation=os.getenv("EXASOL_CERT_VALIDATION", "false").lower() == "true",
        )


@dataclass(frozen=True)
class ReconConfig:
    """Matching parameters. All money values are in minor units (paise)."""

    # Stage 2: order vs gateway amounts within this many paise count as matched.
    amount_tolerance_minor: int = 100
    # A settlement credited more than this many days after the expected date is delayed.
    delay_threshold_days: int = 2
    # Stage 3: an unreferenced bank credit may match a batch credited within this window.
    batch_window_days: int = 7
    # A batch short-paid by at most this share of its gross is a suspected fee difference.
    fee_difference_max_pct: float = 2.0
    # Stage 4 (conservative fuzzy): candidate payments must be within this many minutes.
    fuzzy_enabled: bool = True
    fuzzy_window_minutes: int = 15
    # Reference date for ageing. None = latest bank value date in the data.
    as_of_date: date | None = None
    # Accounting period being closed. None = calendar month before the as-of month.
    close_period_start: date | None = None
    close_period_end: date | None = None
    extra: dict = field(default_factory=dict)
