"""The curated research file: read whole, never written, and every block says where it came from.

There is nothing to compute here, which is the point. `walsh-newcon-curated.yaml` holds the
part of the Walsh report that a person researched — the improvement-district assessment
schedule, the warranty terms, the builder profiles, sixty-eight plan pages' worth of detail —
and the failure this file guards against is not a wrong number, it is a *lost* one. The
original tool's first refresh rewrote its payload in place and the builder roster survived
only by luck (docs/PORTING-THE-REPORTS.md, lesson 8).

So these tests check the two properties that make that impossible to repeat: the blocks are
all there and all sourced, and nothing in the build path can write to the file.
"""
from pathlib import Path

import pytest
import yaml

from propertyfinder import dataquality
from propertyfinder.dataquality import curated

CURATED = "walsh-newcon-curated.yaml"

# Every block the report renders. Named here rather than derived from the file, so that
# deleting a block from the file fails a test instead of quietly shortening a page.
BLOCKS = (
    "place",
    "warranty",
    "pid",
    "carry",
    "practical",
    "method",
    "leverage",
    "builder_profiles",
    "off_feed_builders",
    "custom_builders",
    "plan_details",
)


def test_every_block_is_present():
    assert sorted(curated(CURATED)) == sorted(BLOCKS)


@pytest.mark.parametrize("block", BLOCKS)
def test_every_block_says_where_it_came_from_and_what_would_make_it_stale(block):
    """`stale_when` is the field that turns a citation into an instruction."""
    provenance = curated(CURATED)[block]["provenance"]

    assert provenance["source"].strip()
    assert provenance["verified_on"].strip()
    assert provenance["stale_when"].strip()


def test_the_research_that_was_lost_once_is_all_here():
    """Counts, not spot checks: a careless edit that halved a block would otherwise ship."""
    doc = curated(CURATED)

    assert len(doc["plan_details"]["plans"]) == 68
    assert len(doc["builder_profiles"]["profiles"]) == 8
    assert len(doc["pid"]["rows"]) == 3
    assert len(doc["warranty"]["rows"]) == 6
    assert len(doc["carry"]["dues"]) == 8
    assert len(doc["practical"]["items"]) == 3
    assert len(doc["off_feed_builders"]["builders"]) == 4
    assert len(doc["custom_builders"]["builders"]) == 5


def test_the_district_table_carries_the_plan_that_adopted_it():
    """The one number in this file that moved every monthly figure when it was corrected."""
    pid = curated(CURATED)["pid"]

    assert "Service and Assessment Plan" in pid["provenance"]["source"]
    today = next(r for r in pid["rows"] if r["lot"] == "70 ft" and r["annual"] == 3271)
    early = next(r for r in pid["rows"] if r["annual"] == 928)
    assert today["annual"] > early["annual"] * 3  # the fact the whole section exists to say


def test_curated_prose_never_states_a_number_the_database_knows():
    """Windows, sweep counts and rates are written as tokens the payload fills.

    A sentence in a template — or in a YAML file a template reads — does not know the
    database moved. The original hardcoded "Across 26 days" and "since 11 July", and both
    were wrong within a fortnight (lesson 6).
    """
    text = (dataquality.DATA_DIR / CURATED).read_text()
    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )

    for token in ("[[n_sweeps]]", "[[window_from]]", "[[window_to]]", "[[ad_valorem]]"):
        assert token in body, f"{token} is never filled from the payload"
    for drifted in ("Across 26 days", "five sweeps", "since 11 July"):
        assert drifted not in body


def test_no_build_path_writes_to_the_data_folder():
    """Builds write to reports/. Curated research is edited by people and versioned by git.

    Proved by reading the source of every module a build runs through, rather than by
    trusting the convention — the convention is exactly what the original had.
    """
    package = Path(dataquality.__file__).parent
    for module in sorted(package.glob("*.py")):
        source = module.read_text()
        for writer in ("DATA_DIR /", "DATA_DIR/"):
            for line in source.splitlines():
                if writer in line and "write" in line.lower():
                    raise AssertionError(f"{module.name} writes into data/: {line.strip()}")


def test_a_block_without_provenance_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(dataquality, "DATA_DIR", tmp_path)
    curated.cache_clear()
    (tmp_path / "thin.yaml").write_text(yaml.safe_dump({"place": {"heading": "hello"}}))

    with pytest.raises(ValueError, match="provenance"):
        curated("thin.yaml")
    curated.cache_clear()
