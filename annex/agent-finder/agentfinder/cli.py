"""Four commands — the whole annex at this stage.

    agentfinder init                        create/update the shared schema (core + annex)
    agentfinder sweep   [--budget N]        find luxury listings in the ring, store them
    agentfinder resolve [--limit N] [--budget N]   find out who lists them (google + TREC)
    agentfinder report                      build the outreach page from what is stored

Discipline borrowed from core's cli.py: the real httpx client and the run's CallBudget are
constructed here and nowhere else (so everything below is injectable and the tests run
offline); `report` spends no quota and takes no client; a command adds itself when a person
needs it, not when a module appears. `resolve` is deliberately separate from `sweep` — they
spend on different work and `resolve` is the only command that touches the attribution engine.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import httpx

from propertyfinder.budget import BudgetExceeded, CallBudget
from propertyfinder.config import Settings, build_engine
from propertyfinder.store import latest_snapshot_rows, session_factory

from agentfinder import store
from agentfinder.adapters import SearchApi
from agentfinder.attribution import attribute
from agentfinder.config import load_luxury_config
from agentfinder.reportdata import build_payload
from agentfinder.trec import verify

try:
    from propertyfinder.timeutil import utc_now_iso
except ImportError:  # pragma: no cover
    from datetime import datetime, timezone

    def utc_now_iso() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

log = logging.getLogger(__name__)
REPORTS_DIR = Path("reports")


def _pagebuild_render(payload: dict) -> str:
    from propertyfinder.pagebuild import render
    tdir = Path(__file__).parent / "templates"
    return render("agents.html", payload, templates_dir=tdir, vendor_dir=tdir)


def cmd_init(args, settings, _client):
    engine = build_engine(settings)
    store.migrate(engine)
    print(f"{settings.db_path}: schema up to date (core + annex)")
    return 0


def cmd_sweep(args, settings, client):
    cfg = load_luxury_config(args.config)
    ceiling = args.budget if args.budget is not None else settings.quota_cap_searchapi_monthly
    budget = CallBudget(max_calls=ceiling, label=f"sweep of {cfg.name}")
    api = SearchApi(settings.searchapi_api_key, client=client, budget=budget)
    engine = build_engine(settings)
    store.migrate(engine)
    sessions = session_factory(engine)
    now = utc_now_iso()
    from agentfinder.discover import collect_luxury
    try:
        rows = collect_luxury(api, cfg)
    except BudgetExceeded as exc:
        print(f"stopped before exceeding the ceiling: {exc}")
        rows = []
    if rows:
        with sessions() as session:
            n = store.record_luxury_sweep(session, rows, cfg.name, now)
            session.commit()
        print(f"{cfg.name}: {n} luxury listing(s) >= ${cfg.price_floor:,.0f} stored")
    print(f"budget: {budget}")
    return 0


def cmd_resolve(args, settings, client):
    cfg = load_luxury_config(args.config)
    ceiling = args.budget if args.budget is not None else settings.quota_cap_searchapi_monthly
    budget = CallBudget(max_calls=ceiling, label=f"resolve for {cfg.name}")
    api = SearchApi(settings.searchapi_api_key, client=client, budget=budget)
    engine = build_engine(settings)
    store.migrate(engine)
    sessions = session_factory(engine)
    now = utc_now_iso()

    with sessions() as session:
        addr_by_zpid = {r["zpid"]: r.get("address")
                        for r in latest_snapshot_rows(session, cfg.name)}
        todo = store.zpids_needing_attribution(session, cfg.name)
    if args.limit:
        todo = todo[: args.limit]

    done = 0
    for zpid in todo:
        address = addr_by_zpid.get(zpid)
        if not address:
            continue
        try:
            organic = api.google(f'"{address}" "listed by"')
        except BudgetExceeded as exc:
            print(f"stopped at the ceiling: {exc}")
            break
        attr = attribute(address, organic)
        trec = verify(client, attr.licence, attr.agent) if attr.licence else None
        with sessions() as session:
            store.record_attribution(session, zpid, cfg.name, attr, trec, now)
            session.commit()
        done += 1
        tag = f" · TREC {trec.status}" if trec and trec.found else ""
        print(f"  [{attr.tier:<10}] {attr.agent or attr.brokerage or '-'}{tag}  {address[:40]}")
    print(f"resolved {done} listing(s) · budget: {budget}")
    return 0


def cmd_report(args, settings, _client):
    cfg = load_luxury_config(args.config)
    engine = build_engine(settings)
    store.migrate(engine)
    sessions = session_factory(engine)
    now = utc_now_iso()
    with sessions() as session:
        payload = build_payload(session, cfg, now)
    html = _pagebuild_render(payload)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    dated = REPORTS_DIR / f"{cfg.name}-{now[:10]}.html"
    latest = REPORTS_DIR / f"{cfg.name}.html"
    dated.write_text(html)
    latest.write_text(html)
    print(f"{cfg.name}: {payload['counts']['luxury_listings']} listings, "
          f"{payload['concentration']['unique_agents']} agents -> {latest} and {dated}")
    return 0


COMMANDS = {"init": cmd_init, "sweep": cmd_sweep, "resolve": cmd_resolve, "report": cmd_report}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentfinder",
                                description="Find luxury listing agents around a point.")
    p.add_argument("--config", default="agent-config.yaml", help="the luxury market YAML")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create or update the database schema")
    sw = sub.add_parser("sweep", help="find luxury listings in the ring")
    sw.add_argument("--budget", type=int, default=None)
    rs = sub.add_parser("resolve", help="find out who lists them")
    rs.add_argument("--limit", type=int, default=None)
    rs.add_argument("--budget", type=int, default=None)
    sub.add_parser("report", help="build the outreach report")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(message)s")
    settings = Settings()
    needs_client = args.command in ("sweep", "resolve")
    client = httpx.Client(timeout=90) if needs_client else None
    try:
        return COMMANDS[args.command](args, settings, client)
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
