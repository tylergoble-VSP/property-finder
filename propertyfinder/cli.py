"""Eight commands, which is the whole tool at this stage.

    propertyfinder init                    build or update the database
    propertyfinder watches                 list what is configured
    propertyfinder sweep [--watch NAME]    look at the market and say what moved
    propertyfinder report [--watch NAME]   build the HTML report from what sweep stored
    propertyfinder map [--watch NAME]      the deal map, under its own dated name
    propertyfinder predictions             how wrong the valuation model has been
    propertyfinder enrich [--watch NAME]   pull year built, lot, dues and tax via detail
    propertyfinder daily [--no-sweep]      sweep everything, rebuild everything, one digest

The original grew to fourteen commands, several of them one-offs that outlived their
question. This one adds a command when a person needs it, not when a module appears.

`report` has one pipeline and no `--classic` twin. The original ended up with two report
builders and an arbitration function between them, and the fallback dance was its own
source of bugs (docs/REBUILD.md, post-mortem item 7). Here the *kind* of page is chosen by
what the data can support — a watch with a sold companion gets the map, one without gets
the table — and whichever is built says so on the output line, along with anything it had
to do without. Passing `--kind` overrides the choice; nothing overrides the honesty.

Two things happen here and nowhere else: the real `httpx.Client` is constructed (the
client is injected everywhere below this line, which is what lets the whole suite run
offline), and the run's call budget is decided. Every sweep or enrichment run is charged
against a ceiling, and the ceiling is stated out loud when the run ends — a tool that
spends a shared monthly allowance should never leave anyone guessing what a command cost.

`report` and `predictions` spend nothing — they only read what `sweep` already stored —
so they take no client and no budget. `daily` is the one command that does everything
above in order: it is what a scheduler calls once a morning (docs/scheduling.md).
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import httpx

from propertyfinder.adapters import ZillowAdapter
from propertyfinder.budget import BudgetExceeded, CallBudget
from propertyfinder.config import Settings, build_engine, load_watch_config
from propertyfinder.digest import build_digest
from propertyfinder.enrich import enrich_watch
from propertyfinder.mapdata import build_map_payload, sold_companion
from propertyfinder.notify import send_email
from propertyfinder.pagebuild import render
from propertyfinder.predictions import (
    calibration_report,
    format_calibration,
    record_predictions,
    resolve_predictions,
)
from propertyfinder.reportdata import build_payload
from propertyfinder.store import (
    latest_snapshot_rows,
    run_migrations,
    schema_version,
    session_factory,
)
from propertyfinder.sweep import SweepSummary, run_sweep
from propertyfinder.timeutil import utc_now_iso

log = logging.getLogger(__name__)

REPORTS_DIR = Path("reports")

# `daily` runs once a morning against a monthly allowance, so its default budget is a
# slice of that allowance rather than the whole thing — spending the entire month's quota
# on the first day would starve every day after it. `--budget` overrides this arithmetic
# outright, for a person who knows better than the default cadence.
DAILY_SLICE_DAYS = 30


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
    """One report per watch, of whichever kind the data can honestly support."""
    return _write_pages(args, settings, stem="{name}", kind=args.kind)


def cmd_map(args, settings: Settings, _client) -> int:
    """The deal map, under its own name.

    Separate from `report` only in what it is called on disk. `report` publishes the
    canonical page for a watch and may reasonably be a table; this always publishes the
    map, at `<watch>-map.html`, so a link to the map keeps meaning the map even on a day
    the market has nothing to score.
    """
    return _write_pages(args, settings, stem="{name}-map", kind="map")


def _write_pages(args, settings: Settings, stem: str, kind: str | None) -> int:
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
        chosen, why = _kind_for(config, watch, kind)
        with sessions() as session:
            page, count, note = _build_page(session, watch, config, now, chosen)

        dated, latest = _write_page_pair(stem.format(name=watch.name), now, page)
        print(
            f"{watch.name}: {count} listing(s) · {chosen} report ({why}{note}) "
            f"-> {dated} and {latest}"
        )
    return 0


def _write_page_pair(stem: str, now: str, page: str) -> tuple[Path, Path]:
    """Write `page` under both names and return them: (dated archive, canonical latest).

    A dated archive that never changes once written, and a canonical name that always
    means "the current one" — the same pairing the sweep's own history depends on, one
    layer up: today's page should be findable by date forever, and by name right now.
    """
    dated = REPORTS_DIR / f"{stem}-{now[:10]}.html"
    latest = REPORTS_DIR / f"{stem}.html"
    dated.write_text(page)
    latest.write_text(page)
    return dated, latest


def _kind_for(config, watch, requested: str | None) -> tuple[str, str]:
    """(which page to build, why) — decided by the data unless a caller overrode it.

    This is the whole of post-mortem item 7. There is no second report pipeline and no
    arbitration between two of them: there is one question, "are there sales to value this
    market against", and the answer picks the page and gets printed either way.
    """
    if requested:
        return requested, f"asked for with --kind {requested}"
    companion = sold_companion(config, watch)
    if companion:
        return "map", f"valued against {companion}"
    return "table", "no sold companion watch, so nothing here is valued against sales"


def _build_page(session, watch, config, now: str, kind: str) -> tuple[str, int, str]:
    """(the page, how many homes are on it, what it had to do without).

    The note is what makes the degradation visible from a terminal. A map built on a market
    with too few sales to fit is still a map, and the line that announces it says why it
    carries no scores — rather than leaving someone to open the file and wonder.
    """
    finance = config.finance_for(watch)
    if kind == "map":
        payload = build_map_payload(session, watch, config, now)
        note = "" if payload["model"]["fitted"] else f"; not scored: {payload['model']['reason']}"
        return render("map.html", payload), payload["counts"]["active"], note

    payload = build_payload(session, watch, now, finance)
    return render("report.html", payload), payload["counts"]["total"], ""


def cmd_enrich(args, settings: Settings, client: httpx.Client | None) -> int:
    config = load_watch_config(args.watch_config)
    watches = [w for w in config.watches if not args.watch or w.name == args.watch]
    if not watches:
        known = ", ".join(w.name for w in config.watches)
        print(f"no watch named {args.watch!r}. Configured: {known}")
        return 1

    ceiling = args.budget if args.budget is not None else settings.quota_cap_searchapi_monthly
    budget = CallBudget(max_calls=ceiling, label=f"enrich of {len(watches)} watch(es)")
    adapter = ZillowAdapter.from_settings(settings, client=client, budget=budget)

    # Migrations are idempotent, same as `sweep` and `report` — a person who runs
    # `enrich` first should get an enrichment run, not a lecture about `init`.
    engine = build_engine(settings)
    run_migrations(engine)
    sessions = session_factory(engine)

    for watch in watches:
        with sessions() as session:
            summary = enrich_watch(session, adapter, watch, limit=args.limit)
        note = " (stopped: budget exhausted)" if summary["stopped_by_budget"] else ""
        print(
            f"{summary['watch']}: {summary['ok']} enriched, {summary['miss']} miss, "
            f"{summary['fields_filled']} field(s) filled{note}"
        )
        if summary["stopped_by_budget"]:
            break
    print(f"budget: {budget}")
    return 0


def cmd_predictions(args, settings: Settings, _client) -> int:
    """Print how wrong the valuation model has been, per segment and per basis.

    Deliberately its own command rather than a footnote on the report. A person who wants
    to know whether to trust the scores should be able to ask directly, and get an answer
    that says "nothing resolved yet" when that is the truth.
    """
    engine = build_engine(settings)
    run_migrations(engine)
    sessions = session_factory(engine)

    with sessions() as session:
        print(format_calibration(calibration_report(session)))
    return 0


def cmd_daily(args, settings: Settings, client: httpx.Client | None) -> int:
    """Sweep everything, rebuild everything, mail one digest — the whole tool, once.

    This is what a scheduler calls (docs/scheduling.md): every watch swept, sold
    companions included; the calibration loop frozen and resolved; a report and a map
    rebuilt for every for-sale watch; one digest, sent if mail is configured and printed
    otherwise. Nothing here is new arithmetic — it is `sweep`, `predictions`, `report`,
    and `map` run in the order a morning needs them, against one clock (`now`) so every
    page, every prediction, and the digest that describes them all agree on what day it is.

    A budget that runs out partway through sweeping is not a failure: the remaining
    sweeps are skipped, and everything after — predictions, pages, the digest — still
    runs against whatever the database already holds. A `daily` that sends no digest
    because a mid-month sweep tripped a ceiling would be a worse outcome than a slightly
    stale one that still arrives.
    """
    config = load_watch_config(args.watch_config)
    engine = build_engine(settings)
    run_migrations(engine)
    sessions = session_factory(engine)
    now = utc_now_iso()

    # The day's slice of the shared monthly allowance, not the whole thing — spending the
    # entire month's quota on the first day would starve every day after it.
    default_ceiling = max(settings.quota_cap_searchapi_monthly // DAILY_SLICE_DAYS, 1)
    ceiling = args.budget if args.budget is not None else default_ceiling
    budget = CallBudget(max_calls=ceiling, label="daily run")

    if args.no_sweep:
        print(f"--no-sweep: rebuilding from what {settings.db_path} already holds")
    else:
        adapter = ZillowAdapter.from_settings(settings, client=client, budget=budget)
        for watch in config.watches:
            try:
                with sessions() as session:
                    summary = run_sweep(session, adapter, watch, now=now)
            except BudgetExceeded as exc:
                print(f"stopped sweeping before spending more than the ceiling: {exc}")
                print("rebuilding pages from whatever the database already has")
                break
            _print_summary(summary)

    # The calibration loop: freeze today's expectation for every active listing, and mark
    # yesterday's against whatever turned up sold. Spends no quota — read-only over what
    # the sweep above (or a previous one, on --no-sweep) already wrote.
    for watch in config.watches:
        if watch.listing_status != "for_sale":
            continue
        companion = sold_companion(config, watch)
        if companion is None:
            continue
        with sessions() as session:
            model = _fit_for_predictions(latest_snapshot_rows(session, companion), now)
            recorded = record_predictions(session, watch.name, now, model)
            resolved = resolve_predictions(session, companion, now)
        if recorded or resolved:
            print(f"{watch.name}: predictions +{recorded} recorded, {resolved} resolved")

    # Every for-sale watch gets both names: the canonical report, whichever kind the data
    # earns, and the `-map` alias `map` would have written on its own. When the report is
    # already the map they are the same bytes, so the second build is skipped rather than
    # re-fitting a model the first build already fit.
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    for watch in config.watches:
        if watch.listing_status != "for_sale":
            continue
        chosen, why = _kind_for(config, watch, None)
        with sessions() as session:
            page, count, note = _build_page(session, watch, config, now, chosen)
        _write_page_pair(watch.name, now, page)
        print(f"{watch.name}: {count} listing(s) · {chosen} report ({why}{note})")

        if chosen == "map":
            _write_page_pair(f"{watch.name}-map", now, page)
        else:
            with sessions() as session:
                map_page, map_count, map_note = _build_page(session, watch, config, now, "map")
            _write_page_pair(f"{watch.name}-map", now, map_page)
            print(
                f"{watch.name}: {map_count} listing(s) · map report "
                f"(kept under -map alias{map_note})"
            )

    with sessions() as session:
        subject, body = build_digest(session, config, now)
    sent = send_email(settings, subject, body)
    print(f"digest {'emailed' if sent else 'printed (SMTP unconfigured)'}: {subject}")
    if not sent:
        print()
        print(body)

    print(f"budget: {budget}")
    return 0


def _fit_for_predictions(sold_rows: list[dict], now_iso: str):
    """The model predictions are recorded against, or None — degrading exactly the way
    the report does: too few sold comps, or the `stats` extra not installed, and
    `record_predictions` already treats a missing model as "nothing to record", not an
    error. The import stays deferred here for the same reason it does in `mapdata.py`:
    a core-only install must be able to run `daily` at all, just with no predictions.
    """
    try:
        from propertyfinder.stats import HedonicModel
    except ImportError:  # pragma: no cover - exercised only on a core-only install
        return None
    return HedonicModel.fit(sold_rows, now_iso=now_iso)


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
    "map": cmd_map,
    "predictions": cmd_predictions,
    "enrich": cmd_enrich,
    "daily": cmd_daily,
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
    report.add_argument(
        "--kind",
        choices=("map", "table"),
        help="which page to build (default: the map where a sold companion watch exists, "
        "the table where none does)",
    )

    mapper = subparsers.add_parser(
        "map", help="build the deal map for one watch, or all of them"
    )
    mapper.add_argument("--watch", help="map only this watch (default: every watch)")

    subparsers.add_parser(
        "predictions", help="how wrong the valuation model has been, per segment"
    )

    enrich = subparsers.add_parser(
        "enrich", help="pull year built, lot size, dues and tax rate for a bounded batch"
    )
    enrich.add_argument("--watch", help="enrich only this watch (default: every watch)")
    enrich.add_argument(
        "--limit",
        type=int,
        default=60,
        help="the most homes to pull detail for in this run (default: 60)",
    )
    enrich.add_argument(
        "--budget",
        type=int,
        help="the most billable calls this run may spend (default: the monthly cap)",
    )

    daily = subparsers.add_parser(
        "daily", help="sweep everything, rebuild every page, mail one digest"
    )
    daily.add_argument(
        "--no-sweep",
        action="store_true",
        help="skip the sweep and rebuild predictions/pages/digest from the database as-is",
    )
    daily.add_argument(
        "--budget",
        type=int,
        help="the most billable calls this run may spend "
        "(default: the monthly cap divided across 30 days)",
    )
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
