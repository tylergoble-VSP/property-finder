"""Detail-engine enrichment — the model's best predictors are one call away.

The `zillow` search engine gives price, size, beds, baths and coordinates for free — one
call covers a page of homes. `zillow_property`, the detail engine, adds year built, lot
size, monthly dues and the effective tax rate, but only one home at a time, so every fact
this module recovers costs a billable call and is bounded by `limit`.

The endpoint is flaky: roughly one pull in five comes back HTTP 200, "Success", and
nothing useful in it — `SchemaDrift` at the adapter's own boundary (`adapters/zillow.py`).
THE RULE THIS MODULE EXISTS TO KEEP: every *attempt* stamps `enriched_ts`, whether it
filled a field or came back empty. An unstamped miss is indistinguishable from a home
never tried, and a batch that keeps re-asking a home the endpoint has already refused is
spending quota to relearn the same "no". Coverage instead fills in gradually — a stale
window rolls forward, and the flaky fifth eventually answers on some later pass.

The budget is the adapter's, not this module's. `adapter.property(zpid)` asks its
`CallBudget` before sending, and a `BudgetExceeded` here means nothing went out for that
zpid — it is not an attempt, and is not stamped. The batch stops there rather than
pressing into the rest of the queue; whatever earlier homes in the same run already wrote
is committed as it stands, because a ceiling that only half a household's budget report
should read is worse than one honestly stopped early.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from propertyfinder.adapters import PropertyDetail, SchemaDrift, ZillowAdapter, ZillowHTTPError
from propertyfinder.budget import BudgetExceeded
from propertyfinder.config import Watch
from propertyfinder.domain import WatchedProperty
from propertyfinder.store import latest_snapshot_rows
from propertyfinder.timeutil import TS_FORMAT, utc_now_iso

log = logging.getLogger(__name__)

# After this long even a filled-in home is worth asking about again — a dues increase or
# a tax reassessment does not announce itself, and the detail pull is the only way to see
# one land.
STALE_DAYS = 30


def _num(value) -> float | None:
    """A number, however the feed chose to dress it up — "$92 monthly", "8,712 sqft"."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[\d,.]+", str(value))
    return float(match.group(0).replace(",", "")) if match else None


def extract_detail(detail: PropertyDetail) -> dict:
    """The four fields this tool keeps from a detail body, or `None` where absent.

    Read by path rather than modelled: `PropertyDetail` deliberately keeps the raw body
    (`adapters/zillow.py`) because the useful facts sit at different depths on different
    homes, and the detail engine's own shape is not one this tool controls. A lot the feed
    labels "Acres" is converted to square feet so it lands on the same footing as the
    search feed's own `lot_sqft` — but only when the number is small enough to plausibly
    be acreage; a lot already in the thousands under an "Acres" label is square feet
    mislabelled, not a real residential twelve-thousand-acre back yard, and is left alone.
    """
    year = detail.get("property", "year_built") or detail.get(
        "property", "facts_and_features", "year_built"
    )
    lot = _num(
        detail.get("property", "lot_size")
        or detail.get("property", "facts_and_features", "lot_size")
    )
    units = (detail.get("property", "lot_size_units") or "").lower()
    if lot and "acre" in units and lot < 2000:
        lot *= 43560.0
    return {
        "year_built": int(year) if year else None,
        "lot_sqft": lot,
        "hoa_monthly": _num(
            detail.get("property", "monthly_hoa_fee")
            or detail.get("property", "facts_and_features", "hoa_fee")
        ),
        "tax_rate": _num(detail.get("property", "property_tax_rate")),
    }


def _targets(session: Session, watch_name: str, stale_days: int, limit: int) -> list[str]:
    """Up to `limit` zpids worth pulling detail for: never-tried first, then the ones it
    has been longest since anyone tried. Scoped to the *latest* sweep only — a home that
    has since left the market is not worth a call to describe better."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_days)).strftime(TS_FORMAT)
    zpids = [r["zpid"] for r in latest_snapshot_rows(session, watch_name)]
    if not zpids:
        return []
    rows = (
        session.execute(
            select(WatchedProperty).where(
                WatchedProperty.zpid.in_(zpids),
                or_(
                    WatchedProperty.enriched_ts.is_(None),
                    WatchedProperty.enriched_ts < cutoff,
                ),
            )
        )
        .scalars()
        .all()
    )
    rows.sort(key=lambda r: (r.enriched_ts is not None, r.enriched_ts or ""))
    return [r.zpid for r in rows[:limit]]


def enrich_watch(
    session: Session,
    adapter: ZillowAdapter,
    watch: Watch,
    limit: int,
    stale_days: int = STALE_DAYS,
) -> dict:
    """Pull detail for up to `limit` homes under `watch` and persist what comes back.

    Every attempt — a success, a `SchemaDrift` the endpoint hands back for its own flaky
    fifth, or an HTTP failure — stamps `enriched_ts`, so nothing here is retried inside
    the window it was just tried in. A `BudgetExceeded` is different in kind: nothing was
    sent, so nothing is stamped, and the batch stops rather than treating the next zpid as
    though the ceiling did not apply to it too. Either way, the commit at the end lands
    once, and it lands whatever already happened before the stop.
    """
    targets = _targets(session, watch.name, stale_days, limit)
    now = utc_now_iso()
    attempted = ok = miss = filled = 0
    stopped_by_budget = False

    for zpid in targets:
        row = session.get(WatchedProperty, zpid)
        if row is None:
            continue  # gone from identity between selection and pull; nothing to enrich

        try:
            detail = adapter.property(zpid)
        except BudgetExceeded:
            stopped_by_budget = True
            break
        except (SchemaDrift, ZillowHTTPError) as exc:
            attempted += 1
            miss += 1
            row.enriched_ts = now
            log.debug("enrich %s: %s", zpid, exc)
            continue

        attempted += 1
        ok += 1
        row.enriched_ts = now
        for field, value in extract_detail(detail).items():
            if value is not None:
                setattr(row, field, value)
                filled += 1

    session.commit()
    log.info(
        "enrich %s: %d attempted, %d ok, %d miss, %d field(s) filled%s",
        watch.name,
        attempted,
        ok,
        miss,
        filled,
        " (stopped: budget exhausted)" if stopped_by_budget else "",
    )
    return {
        "watch": watch.name,
        "attempted": attempted,
        "ok": ok,
        "miss": miss,
        "fields_filled": filled,
        "stopped_by_budget": stopped_by_budget,
    }
