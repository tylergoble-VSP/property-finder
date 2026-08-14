"""The daily digest: one email a day, computed from nothing but the database.

`build_digest` is pure over DB state — database and watch config in, a subject line and a
plain-text body out. It reads the clock nowhere and opens no socket, which is what makes it
testable against a synthetic database and safe to call from `daily` every morning without
worrying that a second call might behave differently than the first. `notify.py`, in the
next file, is the only thing that ever mails the result.

The digest deliberately says less than a per-property alert stream would. One roll-up a day
— movement since the last sweep, the sharpest cuts, and the best-scored deals where a sold
companion exists to score against — is the whole of it (docs/REBUILD.md, "no per-property
alert stream": the daily email was a deliberate choice against notification fatigue in the
original, and it held up).

A for-sale watch is paired with its sold-comps companion by the same `<name>-sold`
convention `mapdata.py` already owns (`sold_companion`) — restated here would be a second
place that rule could drift out of sync with the map it describes. Reusing
`mapdata.build_map_payload` for the deals section, rather than re-deriving scores, means the
digest can never disagree with the report a reader opens five minutes later: one calculation,
read twice. A watch with no companion configured, or a companion too thin to fit a model on,
gets no deals section — the same "not scored" honesty `mapdata` already renders on the page,
said instead in a sentence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from propertyfinder.config import Watch, WatchConfig
from propertyfinder.mapdata import build_map_payload, sold_companion
from propertyfinder.predictions import calibration_report, format_calibration
from propertyfinder.store import latest_snapshot_rows, sweep_changes

# How many cuts, and how many deals, earn their own line. Past this a digest reads like a
# database dump instead of a morning briefing — the counts above each list still say how
# much more there is.
TOP_N = 5

GOOD_VERDICTS = ("GREAT", "GOOD")


@dataclass(frozen=True)
class WatchSection:
    """One for-sale watch's slice of the digest: what moved, and what looks good."""

    name: str
    active: int
    changes: dict
    companion: str | None
    deals: list[dict] = field(default_factory=list)
    not_scored_reason: str | None = None


def build_digest(session: Session, cfg: WatchConfig, generated_ts: str) -> tuple[str, str]:
    """(subject, body) for the day, across every for-sale watch in `cfg`.

    `generated_ts` fixes the clock the same way it does everywhere else this pattern
    appears (`reportdata.build_payload`, `mapdata.build_map_payload`): so the digest, the
    pages it links to in spirit, and the model's idea of a home's age all agree on what
    day it is, and two builds from identical data produce identical text.
    """
    forsale = [w for w in cfg.watches if w.listing_status == "for_sale"]
    sections = [_watch_section(session, watch, cfg, generated_ts) for watch in forsale]
    calibration = calibration_report(session)

    dt = datetime.strptime(generated_ts, "%Y-%m-%dT%H:%M:%SZ")
    total_deals = sum(len(s.deals) for s in sections)
    subject = (
        f"Property Finder — {dt.strftime('%b %d')} · "
        f"{total_deals} deal{'' if total_deals == 1 else 's'}"
    )
    return subject, _format(sections, calibration, dt)


def _watch_section(
    session: Session, watch: Watch, cfg: WatchConfig, generated_ts: str
) -> WatchSection:
    rows = latest_snapshot_rows(session, watch.name)
    sweep_ts = max((r["snapshot_ts"] for r in rows), default=None)
    active = [r for r in rows if r["snapshot_ts"] == sweep_ts] if sweep_ts else []
    changes = sweep_changes(session, watch.name)

    companion = sold_companion(cfg, watch)
    deals: list[dict] = []
    not_scored_reason: str | None = None
    if companion:
        # The same payload the map report renders from, so a "GREAT" here is the exact
        # "GREAT" a reader would find by opening the page — never a second opinion computed
        # a different way. Degrades the same way, too: too few sold comps to fit a model,
        # or the `stats` extra not installed, and the page's own reason is repeated here
        # rather than a bare "no deals" that would look identical to a market with nothing
        # good in it right now.
        payload = build_map_payload(session, watch, cfg, generated_ts)
        if payload["model"]["fitted"]:
            deals = sorted(
                (
                    row
                    for row in payload["listings"]
                    if row["deal"] and row["deal"]["verdict"] in GOOD_VERDICTS
                ),
                key=lambda row: -row["deal"]["score"],
            )
        else:
            not_scored_reason = payload["model"]["reason"]
    return WatchSection(
        name=watch.name,
        active=len(active),
        changes=changes,
        companion=companion,
        deals=deals,
        not_scored_reason=not_scored_reason,
    )


def _format(sections: list[WatchSection], calibration, dt: datetime) -> str:
    lines = [f"PROPERTY FINDER — daily digest — {dt.strftime('%A, %b %d, %Y')}", ""]
    for section in sections:
        lines.append(f"== {section.name} · {section.active} active ==")
        lines.extend(_movement_lines(section.changes))
        lines.extend(_deal_lines(section))
        lines.append("")
    lines.append(format_calibration(calibration))
    return "\n".join(lines)


def _movement_lines(changes: dict) -> list[str]:
    if not changes["history_began"]:
        return ["  History begins today — this is the first sweep on record."]
    lines = [
        f"  since last sweep: {len(changes['new'])} new · {len(changes['cuts'])} cuts · "
        f"{len(changes['rises'])} raised · {len(changes['status_changes'])} status changes · "
        f"{len(changes['gone'])} gone"
    ]
    for cut in changes["cuts"][:TOP_N]:
        lines.append(
            f"    down  {cut['address']}: ${cut['previous']:,.0f} -> "
            f"${cut['current']:,.0f} ({cut['delta']:+,.0f} since {cut['since'][:10]})"
        )
    return lines


def _deal_lines(section: WatchSection) -> list[str]:
    if not section.companion:
        return []
    if section.not_scored_reason:
        return [f"  not scored: {section.not_scored_reason}"]
    if not section.deals:
        return [f"  no GREAT/GOOD deals against {section.companion} today"]
    lines = [f"  deals ({len(section.deals)}, valued against {section.companion}):"]
    for row in section.deals[:TOP_N]:
        deal = row["deal"]
        price = f"${row['price']:,.0f}" if row["price"] is not None else "—"
        lines.append(
            f"    [{deal['verdict']}] {row['address']} — {price} · score {deal['score']:.0f}"
        )
    return lines
