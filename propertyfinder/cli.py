"""Four commands, which is the whole tool at this stage.

    propertyfinder init                    build or update the database
    propertyfinder watches                 list what is configured
    propertyfinder sweep [--watch NAME]    look at the market and say what moved
    propertyfinder report [--watch NAME]   build the HTML report from what sweep stored

The original grew to fourteen commands, several of them one-offs that outlived their
question. This one adds a command when a person needs it, not when a module appears.

Two things happen here and nowhere else: the real `httpx.Client` is constructed (the
client is injected everywhere below this line, which is what lets the whole suite run
offline), and the run's call budget is decided. Every sweep is charged against a ceiling,
and the ceiling is stated out loud when the run ends — a tool that spends a shared
monthly allowance should never leave anyone guessing what a command cost.

`report` spends nothing — it only reads what `sweep` already stored — so it takes no
client and no budget.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import httpx

from propertyfinder.adapters import ZillowAdapter
from propertyfinder.budget import BudgetExceeded, CallBudget
from propertyfinder.config import Settings, build_engine, load_watch_config
from propertyfinder.pagebuild import render
from propertyfinder.reportdata import build_payload
from propertyfinder.store import run_migrations, schema_version, session_factory
from propertyfinder.sweep import SweepSummary, run_sweep
from propertyfinder.timeutil import utc_now_iso

log = logging.getLogger(__name__)

REPORTS_DIR = Path("reports")


def cmd_init(args, settings: Settings, _client) -> int:
    engine = build_engine(settings)
    applied = run_migrations(engine)
    state = (
        f"applied {len(applied)} migration(s)" if applied else "already current"
    )
    print(f"{settings.db_path}: schema version {schema_version(engine)} ({state})")
    return 0


def cmd_watches(args, settings: Settings, _client) -> int:
    config = load_watch_config(args.watch_config)
    for watch in config.watches:
        subdivision = f", subdivision {watch.subdivision}" if watch.subdivision else ""
        print(
            f"{watch.name}: {watch.listing_status} within {watch.radius_miles:g} mi of "
            f"{watch.center_address}{subdivision}"
        )
        print(f"    queries: {', '.join(watch.queries)}  (max {watch.max_pages} pages each)")
    return 0


def cmd_sweep(args, settings: Settings, client: httpx.Client | None) -> int:
    config = load_watch_config(args.watch_config)
    watches = [w for w in config.watches if not args.watch or w.name == args.watch]
    if not watches:
        known = ", ".join(w.name for w in config.watches)
        print(f"no watch named {args.watch!r}. Configured: {known}")
        return 1

    # The ceiling: what the caller asked for, or the whole monthly allowance if they did
    # not. Either way it is a number the adapter will refuse to exceed, rather than a
    # sentence in a README that cannot refuse anything.
    ceiling = args.budget if args.budget is not None else settings.quota_cap_searchapi_monthly
    budget = CallBudget(max_calls=ceiling, label=f"sweep of {len(watches)} watch(es)")
    adapter = ZillowAdapter.from_settings(settings, client=client, budget=budget)

    # Migrations are idempotent, so a sweep brings its own schema up to date rather than
    # lecturing someone about running `init` first.
    engine = build_engine(settings)
    run_migrations(engine)
    sessions = session_factory(engine)

    exit_code = 0
    for watch in watches:
        try:
            with sessions() as session:
                summary = run_sweep(session, adapter, watch)
        except BudgetExceeded as exc:
            print(f"stopped before spending more than the ceiling: {exc}")
            exit_code = 1
            break
        _print_summary(summary)
    print(f"budget: {budget}")
    return exit_code


def cmd_report(args, settings: Settings, _client) -> int:
    config = load_watch_config(args.watch_config)
    watches = [w for w in config.watches if not args.watch or w.name == args.watch]
    if not watches:
        known = ", ".join(w.name for w in config.watches)
        print(f"no watch named {args.watch!r}. Configured: {known}")
        return 1

    # Migrations are idempotent, same as `sweep` — a person who runs `report` before ever
    # running `init` should get a (probably empty) report, not a lecture.
    engine = build_engine(settings)
    run_migrations(engine)
    sessions = session_factory(engine)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    for watch in watches:
        now = utc_now_iso()
        with sessions() as session:
            payload = build_payload(session, watch, now, config.finance_for(watch))
        page = render("report.html", payload)

        # A dated archive that never changes once written, and a canonical name that
        # always means "the current one" — the same pairing the sweep's own history
        # depends on, one layer up: today's page should be findable by date forever, and
        # by name right now.
        dated = REPORTS_DIR / f"{watch.name}-{now[:10]}.html"
        latest = REPORTS_DIR / f"{watch.name}.html"
        dated.write_text(page)
        latest.write_text(page)
        print(
            f"{watch.name}: {payload['counts']['total']} listing(s) -> {dated} and {latest}"
        )
    return 0


def _print_summary(summary: SweepSummary, detail: int = 8) -> None:
    print(summary.headline())
    for cut in summary.cuts[:detail]:
        print(
            f"    down  {cut.address}: ${cut.previous:,.0f} -> ${cut.current:,.0f} "
            f"({cut.delta:+,.0f} since {cut.since[:10]})"
        )
    for flip in summary.status_changes[:detail]:
        print(f"    now   {flip.address}: {flip.previous} -> {flip.current}")


COMMANDS = {
    "init": cmd_init,
    "watches": cmd_watches,
    "sweep": cmd_sweep,
    "report": cmd_report,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="propertyfinder", description="Watch a housing market and remember it."
    )
    parser.add_argument(
        "--watch-config",
        default="watch-config.yaml",
        help="the YAML file defining the watches (default: watch-config.yaml)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="log what the sweep is doing"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="create or update the database schema")
    subparsers.add_parser("watches", help="list the configured watches")

    sweep = subparsers.add_parser("sweep", help="sweep one watch, or all of them")
    sweep.add_argument("--watch", help="sweep only this watch (default: every watch)")
    sweep.add_argument(
        "--budget",
        type=int,
        help="the most billable calls this run may spend (default: the monthly cap)",
    )

    report = subparsers.add_parser(
        "report", help="build the HTML report for one watch, or all of them"
    )
    report.add_argument("--watch", help="report only this watch (default: every watch)")
    return parser


def main(argv: list[str] | None = None, client: httpx.Client | None = None) -> int:
    """Run one command. `client` exists so tests can hand in a fake internet."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s"
    )
    return COMMANDS[args.command](args, Settings(), client)


if __name__ == "__main__":  # pragma: no cover - console entry point
    raise SystemExit(main())
