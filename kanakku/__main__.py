"""Command line: python -m kanakku <command>.

  setup                       create tables in Exasol (idempotent)
  generate [--seed N] [--orders N] [--out DIR]
  load [--dir DIR] [--replace] [--no-truth]
  reconcile [--tolerance PAISE] [--delay-days N] [--no-fuzzy] [--as-of YYYY-MM-DD]
  evaluate [--run-id N]
  demo                        generate sample data, load (replace), reconcile, evaluate
"""

from __future__ import annotations

import argparse
from datetime import date

import pandas as pd

from . import db, evaluation, generator, reconciliation, schema
from .config import SAMPLE_DIR, ReconConfig
from .money import format_inr
from .pipeline import load_directory


def _fmt(v):
    return "—" if v is None else f"{v:.3f}"


def cmd_generate(a):
    cfg = generator.GeneratorConfig(seed=a.seed, n_orders=a.orders)
    paths = generator.write(generator.generate(cfg), a.out)
    for k, p in paths.items():
        print(f"  {k:<13} {p}")


def cmd_load(a):
    with db.session() as conn:
        for s in load_directory(conn, a.dir, replace=a.replace, with_truth=not a.no_truth):
            print(f"  {s['SOURCE_NAME']:<8} read={s['ROWS_READ']} valid={s['ROWS_VALID']} rejected={s['ROWS_REJECTED']} "
                  f"exact_dups={s['EXACT_DUPLICATES']} conflicts={s['CONFLICTING_DUPLICATES']} "
                  f"new={s['ROWS_INSERTED']} existing={s['ROWS_UPDATED']}")


def cmd_reconcile(a):
    cfg = ReconConfig(amount_tolerance_minor=a.tolerance, delay_threshold_days=a.delay_days,
                      fuzzy_enabled=not a.no_fuzzy,
                      as_of_date=date.fromisoformat(a.as_of) if a.as_of else None)
    with db.session() as conn:
        r = reconciliation.run(conn, cfg)
    rate = r["orders_matched"] / r["orders_total"] if r["orders_total"] else 0
    print(f"  run {r['run_id']} as of {r['as_of']} in {r['duration_ms']} ms: {r['orders_matched']}/{r['orders_total']} "
          f"orders matched ({rate:.2%}), {r['exceptions_total']} exceptions, exposure {format_inr(r['exposure_minor'])}")


def cmd_evaluate(a):
    with db.session() as conn:
        run_id = a.run_id or reconciliation.latest_run_id(conn)
        m = evaluation.evaluate_run(conn, run_id) if run_id else None
    if m is None:
        print("  nothing to evaluate (no completed run or no ground truth loaded)")
        return
    print(f"  run {run_id}: precision={_fmt(m['precision'])} recall={_fmt(m['recall'])} f1={_fmt(m['f1'])} "
          f"TP={m['true_positive']} FP={m['false_positive']} missed={m['missed']}")
    with pd.option_context("display.width", 200):
        print(m["per_code"].to_string(index=False))


def main(argv=None):
    p = argparse.ArgumentParser(prog="kanakku", description="Kanakku — every number tells a story.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    g = sub.add_parser("generate")
    g.add_argument("--seed", type=int, default=42)
    g.add_argument("--orders", type=int, default=10_000)
    g.add_argument("--out", default=str(SAMPLE_DIR))
    ld = sub.add_parser("load")
    ld.add_argument("--dir", default=str(SAMPLE_DIR))
    ld.add_argument("--replace", action="store_true", help="truncate Kanakku's source tables first")
    ld.add_argument("--no-truth", action="store_true")
    rc = sub.add_parser("reconcile")
    rc.add_argument("--tolerance", type=int, default=100, help="paise")
    rc.add_argument("--delay-days", type=int, default=2)
    rc.add_argument("--no-fuzzy", action="store_true")
    rc.add_argument("--as-of")
    ev = sub.add_parser("evaluate")
    ev.add_argument("--run-id", type=int)
    sub.add_parser("demo")
    a = p.parse_args(argv)

    if a.cmd == "setup":
        with db.session() as conn:
            schema.ensure_schema(conn)
        print("  schema ready")
    elif a.cmd == "generate":
        cmd_generate(a)
    elif a.cmd == "load":
        cmd_load(a)
    elif a.cmd == "reconcile":
        cmd_reconcile(a)
    elif a.cmd == "evaluate":
        cmd_evaluate(a)
    elif a.cmd == "demo":
        print("generate:"); cmd_generate(argparse.Namespace(seed=42, orders=10_000, out=str(SAMPLE_DIR)))
        print("load:"); cmd_load(argparse.Namespace(dir=str(SAMPLE_DIR), replace=True, no_truth=False))
        print("reconcile:"); cmd_reconcile(argparse.Namespace(tolerance=100, delay_days=2, no_fuzzy=False, as_of=None))
        print("evaluate:"); cmd_evaluate(argparse.Namespace(run_id=None))


if __name__ == "__main__":
    main()
