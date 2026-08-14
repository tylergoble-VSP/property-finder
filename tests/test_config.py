"""The config layer must fail loudly before any quota is spent."""
import pytest
from pydantic import ValidationError
from sqlalchemy import text

from propertyfinder.config import Settings, WatchConfig, build_engine, load_watch_config


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


def test_engine_enforces_foreign_keys(tmp_path):
    s = Settings(_env_file=None, db_path=str(tmp_path / "t.db"))
    eng = build_engine(s)
    with eng.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
