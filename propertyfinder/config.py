"""Settings (secrets, from .env) and watches (market definitions, from YAML).

Two configuration worlds, kept deliberately separate: `Settings` holds anything secret
or machine-specific and is loaded from the environment; `WatchConfig` holds the market
definitions a user edits and version-controls. A watch that fails validation must fail
loudly at load time — a misconfigured watch that sweeps anyway spends real API quota
collecting garbage.

Money assumptions live in both worlds at once: one global `finance:` block states what is
true of every market, and a watch may override the handful of fields that are local to it
— its verified tax rate, its improvement district, its insurance market. `finance_for`
**merges** the two rather than choosing between them, which is the part the original got
wrong: it replaced the global block outright, so a watch that set one field silently lost
the nine it had not mentioned, and every monthly figure for that market was quietly built
on the model's bare defaults.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import AliasChoices, BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from propertyfinder.costmodel import FinanceAssumptions, SpecialAssessment

__all__ = [
    "FinanceAssumptions",
    "Settings",
    "SpecialAssessment",
    "Watch",
    "WatchConfig",
    "build_engine",
    "load_watch_config",
]

VALID_STATUSES = {"for_sale", "for_rent", "sold"}

# A query that is only a ZIP code. The search provider mis-resolves these to whatever
# place it likes: we once asked about 76008 (Aledo, Texas) and were answered with
# Minerva, Ohio. Queries must anchor the place name: "Aledo, TX 76008".
_BARE_ZIP = re.compile(r"^\s*\d{5}(-\d{4})?\s*$")


class Settings(BaseSettings):
    """Secrets and machine-specific paths, read from the environment / .env.

    `db_path` also answers to the original tool's variable name so an existing
    .env keeps working unchanged.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    searchapi_api_key: str = ""
    db_path: str = Field(
        default="propertyfinder.db",
        validation_alias=AliasChoices(
            "PROPERTYFINDER_DB_PATH", "PROPERTYWATCH_DB_PATH", "db_path"
        ),
    )
    quota_cap_searchapi_monthly: int = 1000

    # mail, for the daily digest — absent means "print instead of send"
    smtp_host: str = ""
    smtp_username: str = ""
    smtp_password: str = ""
    alert_email_from: str = ""
    alert_email_to: str = ""


class Watch(BaseModel):
    """One market to watch: a centre, a radius, and the queries that cover it."""

    name: str
    center_address: str
    lat: float
    lon: float
    radius_miles: float = Field(gt=0)
    listing_status: str = "for_sale"
    max_pages: int = Field(default=10, ge=1)
    queries: list[str] = Field(min_length=1)
    subdivision: str | None = None
    filters: dict = Field(default_factory=dict)
    # Only the money facts that are local to this market. Everything left unstated is
    # inherited from the global block by `WatchConfig.finance_for`.
    finance: FinanceAssumptions | None = None

    @field_validator("listing_status")
    @classmethod
    def _status_known(cls, v: str) -> str:
        if v not in VALID_STATUSES:
            raise ValueError(
                f"listing_status {v!r} is not one of {sorted(VALID_STATUSES)}"
            )
        return v

    @field_validator("queries")
    @classmethod
    def _no_bare_zips(cls, qs: list[str]) -> list[str]:
        for q in qs:
            if _BARE_ZIP.match(q):
                raise ValueError(
                    f"query {q!r} is a bare ZIP code. The search provider mis-resolves "
                    f"bare ZIPs to the wrong region entirely (observed: 76008 resolving "
                    f"to Minerva, Ohio). Anchor the place: 'Aledo, TX 76008'."
                )
        return qs


class WatchConfig(BaseModel):
    currency: str = "USD"
    finance: FinanceAssumptions = FinanceAssumptions()
    watches: list[Watch]

    def watch(self, name: str) -> Watch:
        for w in self.watches:
            if w.name == name:
                return w
        raise KeyError(f"no watch named {name!r} in the watch config")

    def finance_for(self, watch: Watch) -> FinanceAssumptions:
        """This watch's money assumptions: its own fields laid over the global block.

        Merged, not replaced. `model_fields_set` is what makes that exact — it holds the
        fields the YAML actually mentioned, so a watch overrides precisely what it wrote
        down and inherits everything else. Asking instead for "the fields that differ from
        the model's defaults" would be subtly wrong in the direction that hurts: a watch
        deliberately restating a value that happens to equal a default would be treated as
        having said nothing, and would silently inherit the global figure instead.

        A nested block is one fact and is replaced whole. A watch naming its improvement
        district replaces the global district outright rather than blending the two — a
        lot's assessment comes from one service-and-assessment plan, and half of one plan
        mixed with half of another is not a bill anybody sends.
        """
        if watch.finance is None:
            return self.finance
        stated = {k: getattr(watch.finance, k) for k in watch.finance.model_fields_set}
        return self.finance.model_copy(update=stated)


def load_watch_config(path: str | Path = "watch-config.yaml") -> WatchConfig:
    raw = yaml.safe_load(Path(path).read_text())
    return WatchConfig.model_validate(raw)


def build_engine(settings: Settings) -> Engine:
    """SQLite engine with write-ahead logging and enforced foreign keys.

    WAL lets a report read while a sweep writes; foreign-key enforcement is off by
    default in SQLite and silently accepts orphan rows unless switched on per
    connection — so it is switched on for every connection, here, once.
    """
    engine = create_engine(f"sqlite:///{settings.db_path}")

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # pragma: no cover - exercised via queries
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine
