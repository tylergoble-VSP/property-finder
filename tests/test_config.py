"""The config layer must fail loudly before any quota is spent."""
import pytest
from pydantic import ValidationError
from sqlalchemy import text

from propertyfinder.config import (
    FinanceAssumptions,
    Settings,
    SpecialAssessment,
    WatchConfig,
    build_engine,
    load_watch_config,
)


def _watch(**over):
    base = dict(
        name="test",
        center_address="1 Main St, Aledo, TX 76008",
        lat=32.7,
        lon=-97.5,
        radius_miles=2.0,
        listing_status="for_sale",
        max_pages=2,
        queries=["Aledo, TX 76008"],
    )
    base.update(over)
    return base


def test_bare_zip_query_is_rejected_with_the_story():
    with pytest.raises(ValidationError) as exc:
        WatchConfig(watches=[_watch(queries=["76008"])])
    assert "Minerva, Ohio" in str(exc.value)


def test_zip_plus_four_is_also_bare():
    with pytest.raises(ValidationError):
        WatchConfig(watches=[_watch(queries=["76008-1234"])])


def test_anchored_query_is_accepted():
    cfg = WatchConfig(watches=[_watch()])
    assert cfg.watch("test").queries == ["Aledo, TX 76008"]


def test_unknown_listing_status_rejected():
    with pytest.raises(ValidationError):
        WatchConfig(watches=[_watch(listing_status="pending")])


def test_missing_watch_lookup_is_a_keyerror():
    cfg = WatchConfig(watches=[_watch()])
    with pytest.raises(KeyError):
        cfg.watch("nope")


def test_repo_watch_config_loads():
    cfg = load_watch_config("watch-config.yaml")
    names = [w.name for w in cfg.watches]
    assert "walsh-aledo" in names and "walsh-aledo-sold" in names


def test_settings_honour_original_env_names(monkeypatch):
    monkeypatch.setenv("PROPERTYWATCH_DB_PATH", "/tmp/old-name.db")
    assert Settings(_env_file=None).db_path == "/tmp/old-name.db"


# -- money assumptions: merged over the global block, never replacing it -----------------
#
# The original replaced instead of merging, so a watch that set its tax rate silently lost
# the mortgage rate, the term, the insurance figure and the rest, and every monthly number
# for that market was quietly built on the model's bare defaults. These are the tests that
# would have caught it.

GLOBAL = FinanceAssumptions(
    mortgage_rate=7.25,
    down_pct=25.0,
    loan_term_years=15,
    insurance_annual_per_1000=9.0,
    default_tax_rate=1.55,
    tax_rate_citation="the global citation",
    hoa_default_monthly=85.0,
    special_assessment=SpecialAssessment(pct=0.4, citation="the global district"),
)


def test_a_watch_with_no_finance_block_gets_the_global_one():
    cfg = WatchConfig(finance=GLOBAL, watches=[_watch()])
    assert cfg.finance_for(cfg.watch("test")) == GLOBAL


def test_a_watch_overriding_one_field_keeps_every_other_one():
    cfg = WatchConfig(
        finance=GLOBAL,
        watches=[_watch(finance={"default_tax_rate": 2.339427})],
    )
    merged = cfg.finance_for(cfg.watch("test"))

    assert merged.default_tax_rate == 2.339427
    for field in FinanceAssumptions.model_fields:
        if field == "default_tax_rate":
            continue
        assert getattr(merged, field) == getattr(GLOBAL, field), field


def test_restating_a_value_that_equals_the_models_default_still_overrides():
    """The subtle half of merging. `model_fields_set` records what the YAML actually said,
    so a watch writing `mortgage_rate: 6.5` overrides a global 7.25 even though 6.5 is
    also the model's own default. Asking instead for "fields differing from the defaults"
    would treat that line as unwritten and inherit 7.25 — a wrong number, from a file that
    plainly states the right one."""
    assert FinanceAssumptions().mortgage_rate == 6.5
    cfg = WatchConfig(finance=GLOBAL, watches=[_watch(finance={"mortgage_rate": 6.5})])
    assert cfg.finance_for(cfg.watch("test")).mortgage_rate == 6.5


def test_a_watchs_district_replaces_the_global_district_whole():
    """A nested block is one fact. A lot's assessment comes from one service-and-assessment
    plan, so a watch naming a flat bill must not end up carrying the global percentage too
    — which `SpecialAssessment` would refuse to construct anyway."""
    cfg = WatchConfig(
        finance=GLOBAL,
        watches=[_watch(finance={"special_assessment": {"flat_annual": 3271}})],
    )
    district = cfg.finance_for(cfg.watch("test")).special_assessment

    assert district.flat_annual == 3271
    assert district.pct is None and district.citation == ""


def test_merging_does_not_mutate_the_global_block():
    cfg = WatchConfig(finance=GLOBAL, watches=[_watch(finance={"down_pct": 3.5})])
    assert cfg.finance_for(cfg.watch("test")).down_pct == 3.5
    assert cfg.finance.down_pct == 25.0


def test_the_repo_config_carries_the_verified_walsh_numbers():
    cfg = load_watch_config("watch-config.yaml")
    fin = cfg.finance_for(cfg.watch("walsh-aledo"))

    assert fin.default_tax_rate == 2.339427  # the adopted 2025 stack, not the guessed 2.9%
    assert "2026-08-06" in fin.tax_rate_citation
    assert fin.special_assessment.flat_annual == 3271  # dollars per lot, not a percentage
    assert fin.special_assessment.pct is None
    assert "928" in fin.special_assessment.citation  # the early-phase tier, stated
    assert "verify per lot" in fin.special_assessment.citation
    # and the global block still reaches this watch
    assert (fin.mortgage_rate, fin.down_pct, fin.loan_term_years) == (6.5, 20.0, 30)
    assert fin.insurance_annual_per_1000 == 5.0


def test_engine_enforces_foreign_keys(tmp_path):
    s = Settings(_env_file=None, db_path=str(tmp_path / "t.db"))
    eng = build_engine(s)
    with eng.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
