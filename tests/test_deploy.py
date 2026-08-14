"""scripts/deploy.sh: refuses an empty or missing site/ before ever calling Vercel.

Every scenario here runs the real script through `subprocess` — no real network is
possible either way, because `PYTHON` and `NPX` are pointed at tiny local stub scripts
this file writes into a throwaway `tmp_path`, and `PROPERTYFINDER_ROOT` points the script
at that same sandbox rather than the real repository. The refusal path never reaches the
`NPX` stub at all; the one test that reaches it never lets it do anything but echo its
arguments, so "did it deploy" is provable without a deploy ever happening.
"""
from __future__ import annotations

import stat
import subprocess
from pathlib import Path

DEPLOY_SH = Path(__file__).resolve().parent.parent / "scripts" / "deploy.sh"


def _script(path: Path, body: str) -> Path:
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _run(tmp_path: Path, python_body: str):
    npx_stub = _script(tmp_path / "npx_stub.sh", 'echo "STUB NPX CALLED: $*"')
    python_stub = _script(tmp_path / "python_stub.sh", python_body)
    (tmp_path / "scripts").mkdir(exist_ok=True)
    return subprocess.run(
        ["bash", str(DEPLOY_SH)],
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "PROPERTYFINDER_ROOT": str(tmp_path),
            "PYTHON": str(python_stub),
            "NPX": str(npx_stub),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_refuses_when_build_site_never_creates_a_site_directory(tmp_path):
    """Stands in for build_site.py failing loudly (a bad or missing manifest) — whatever
    the reason, no site/ means nothing to deploy, and the script says so instead of
    guessing there is."""
    result = _run(tmp_path, "exit 0")  # the stand-in for build_site.py does nothing at all

    assert result.returncode == 1
    assert "refusing to deploy" in result.stderr
    assert "does not exist" in result.stderr
    assert "STUB NPX CALLED" not in result.stdout  # vercel was never reached


def test_refuses_when_the_manifest_is_effectively_empty(tmp_path):
    """build_site.py itself succeeds on an empty manifest — it just produces a site/ with
    only the generated index and nothing to link from it (tests/test_build_site.py proves
    that shape). Deploy's own guard is what stops *that* from reaching Vercel."""
    result = _run(
        tmp_path,
        f'mkdir -p "{tmp_path}/site" && echo hi > "{tmp_path}/site/index.html"',
    )

    assert result.returncode == 1
    assert "refusing to deploy" in result.stderr
    assert "no published page" in result.stderr
    assert "STUB NPX CALLED" not in result.stdout


def test_a_real_published_page_reaches_the_deploy_step(tmp_path):
    """The mirror image of both refusal tests: once there is something real to publish,
    the script proceeds — proven by the stub standing in for `npx` actually being called,
    with the arguments the task specifies, and nothing more."""
    result = _run(
        tmp_path,
        f'mkdir -p "{tmp_path}/site" && '
        f'echo hi > "{tmp_path}/site/index.html" && '
        f'echo page > "{tmp_path}/site/walsh-aledo.html"',
    )

    assert result.returncode == 0
    assert "refusing to deploy" not in result.stderr
    assert "STUB NPX CALLED: vercel deploy --prod --yes --cwd site" in result.stdout


def test_deploy_script_is_shellcheck_clean():
    """The house rule: a script this operational should read cleanly under shellcheck, not
    just happen to work on one machine's bash."""
    shellcheck = subprocess.run(["shellcheck", str(DEPLOY_SH)], capture_output=True, text=True)
    if shellcheck.returncode == 127:  # shellcheck itself is not installed here
        import pytest

        pytest.skip("shellcheck is not installed")
    assert shellcheck.returncode == 0, shellcheck.stdout + shellcheck.stderr
