"""A run that says it happened, and the two things that read what it said.

The incident: the scheduled job pointed at a virtualenv whose interpreter path had stopped
resolving after the project folder moved, and launchd swallowed exit 127 every morning for a
fortnight. Nothing was broken loudly. "It runs every morning" had simply stopped being true
(docs/PORTING-THE-REPORTS.md, lesson 16).

What is tested here is not that the run works — the rest of the suite does that — but that a
run which did NOT happen becomes visible. Three mechanisms: `daily` leaves a mark, the digest
reads the mark out loud, and `deploy.sh` warns when the mark is old. And the wrapper the
scheduler points at refuses loudly rather than exiting 127 quietly.
"""
from __future__ import annotations

import json
import stat
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from propertyfinder import heartbeat

DAILY_SH = Path(__file__).resolve().parent.parent / "scripts" / "daily.sh"
NOW = "2026-08-21T07:00:00Z"
FMT = "%Y-%m-%dT%H:%M:%SZ"


def test_a_run_leaves_a_mark_something_else_can_read(tmp_path):
    heartbeat.write(tmp_path / "reports", NOW, exit_status=0, calls_spent=32)

    beat = heartbeat.read(tmp_path / "reports")

    assert beat.finished_ts == NOW and beat.ok and beat.calls_spent == 32
    assert "32 billable calls" in beat.sentence()


def test_a_failed_run_leaves_a_mark_too(tmp_path):
    """"It ran and it failed" and "it never ran" are different problems.

    A heartbeat written only on success cannot tell them apart, which makes it useless for the
    one thing it exists to detect.
    """
    heartbeat.write(tmp_path / "reports", NOW, exit_status=1, calls_spent=4)

    beat = heartbeat.read(tmp_path / "reports")

    assert not beat.ok
    assert "FAILED (exit 1)" in beat.sentence()


def test_no_heartbeat_at_all_is_an_answer_not_a_crash(tmp_path):
    assert heartbeat.read(tmp_path / "reports") is None


def test_a_corrupt_heartbeat_is_treated_as_none(tmp_path):
    """A claim that cannot be read is not a weaker claim, it is none."""
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / heartbeat.HEARTBEAT_NAME).write_text("{ this is not json")

    assert heartbeat.read(reports) is None


@pytest.mark.parametrize(
    "hours, stale",
    [(1, False), (24, False), (36, False), (37, True), (24 * 14, True)],
)
def test_staleness_is_a_missed_morning_and_not_a_missed_hour(hours, stale, tmp_path):
    """Generous enough to survive a laptop shut for a day, tight enough that a fortnight of
    exit 127 cannot hide inside it."""
    written = "2026-08-01T07:00:00Z"
    heartbeat.write(tmp_path / "reports", written, 0, 32)
    beat = heartbeat.read(tmp_path / "reports")

    assert beat.age(written) == timedelta(0)  # a sanity anchor before the arithmetic
    now = (datetime.strptime(written, FMT) + timedelta(hours=hours)).strftime(FMT)
    assert beat.is_stale(now) is stale


# -- the wrapper a scheduler points at ----------------------------------------------------


def _run_wrapper(tmp_path: Path, **env):
    return subprocess.run(
        ["bash", str(DAILY_SH)],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_a_missing_project_folder_is_loud_rather_than_a_swallowed_127(tmp_path):
    result = _run_wrapper(tmp_path, PROPERTYFINDER_ROOT=str(tmp_path / "moved-away"))

    assert result.returncode == 78, "EX_CONFIG, not the 127 that hid for a fortnight"
    assert "PROPERTYFINDER DAILY ABORTED" in result.stderr
    assert "did the project folder move" in result.stderr
    assert "moved-away" in result.stderr  # the line names what it could not find


def test_a_stale_venv_is_loud_even_though_its_interpreter_exists(tmp_path):
    """The exact shape of the incident: a .venv that is present and unusable.

    An interpreter path is hard-coded into a virtualenv at creation, so a moved folder leaves
    one that exists and cannot run anything. A check for the file's presence would pass; this
    checks that it can import the CLI, which is what needs the venv's own packages.
    """
    fake = tmp_path / "python"
    fake.write_text("#!/usr/bin/env bash\nexit 1\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "watch-config.yaml").write_text("watches: []\n")

    result = _run_wrapper(tmp_path, PROPERTYFINDER_ROOT=str(tmp_path), PYTHON=str(fake))

    assert result.returncode == 78
    assert "the venv is stale or incomplete" in result.stderr


def test_a_missing_watch_config_is_loud(tmp_path):
    fake = tmp_path / "python"
    fake.write_text("#!/usr/bin/env bash\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    result = _run_wrapper(tmp_path, PROPERTYFINDER_ROOT=str(tmp_path), PYTHON=str(fake))

    assert result.returncode == 78
    assert "no watch config" in result.stderr


def test_every_abort_is_one_greppable_shape(tmp_path):
    """So that "did the morning run" is one grep, whichever thing went wrong."""
    aborts = [
        _run_wrapper(tmp_path, PROPERTYFINDER_ROOT=str(tmp_path / "gone")),
        _run_wrapper(tmp_path, PROPERTYFINDER_ROOT=str(tmp_path), PYTHON="/nope/python"),
    ]

    for result in aborts:
        assert result.stderr.startswith("PROPERTYFINDER DAILY ABORTED: ")
        assert "root=" in result.stderr and "python=" in result.stderr


def test_the_wrapper_passes_the_run_through_and_returns_its_status(tmp_path):
    fake = tmp_path / "python"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'if [[ "$*" == *"import propertyfinder.cli"* ]]; then exit 0; fi\n'
        'echo "ran: $*"\nexit 3\n'
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "watch-config.yaml").write_text("watches: []\n")

    result = _run_wrapper(tmp_path, PROPERTYFINDER_ROOT=str(tmp_path), PYTHON=str(fake),
                          DAILY_ARGS="--no-sweep")

    assert result.returncode == 3
    assert "daily --no-sweep" in result.stdout
    assert "finished with exit 3" in result.stdout


def test_the_wrapper_is_shellcheck_clean():
    check = subprocess.run(["which", "shellcheck"], capture_output=True, text=True)
    if check.returncode != 0:
        pytest.skip("shellcheck is not installed")
    result = subprocess.run(["shellcheck", str(DAILY_SH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
