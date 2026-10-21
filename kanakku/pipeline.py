"""High-level steps shared by the CLI and the dashboard."""

from __future__ import annotations

from pathlib import Path

import pyexasol

from . import generator, ingestion, schema
from .config import SAMPLE_DIR

SOURCE_FILES = {"orders": "orders.csv", "gateway": "gateway_transactions.csv", "bank": "bank_statement.csv"}


def load_directory(conn: pyexasol.ExaConnection, directory: Path | str = SAMPLE_DIR,
                   replace: bool = False, with_truth: bool = True) -> list[dict]:
    """Validate and load the three CSVs (and ground truth, if present) from a directory."""
    directory = Path(directory)
    schema.ensure_schema(conn)
    if replace:
        schema.clear_source_data(conn)
    stats = []
    for kind, fname in SOURCE_FILES.items():
        result = ingestion.validate(kind, ingestion.read_csv(directory / fname))
        stats.append(ingestion.load(conn, result, fname))
    truth = directory / generator.FILE_NAMES["ground_truth"]
    if with_truth and truth.exists():
        ingestion.load_ground_truth(conn, ingestion.read_csv(truth))
    return stats
