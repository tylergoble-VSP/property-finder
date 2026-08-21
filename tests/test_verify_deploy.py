"""scripts/verify_deploy.py: what a visitor gets, checked against a stub that lies in every
way the real host has.

No network. A `http.server` on a loopback port stands in for Vercel and is told, per test, to
behave the way the real deployment behaved on the day the lesson was learned: to SSO-redirect a
path that should be open, to serve a path that should be gated, to return a page whose og:image
is relative. Each of those was invisible to every local check and visible only by fetching the
live URL and reading the response (docs/PORTING-THE-REPORTS.md, lesson 12).
"""
from __future__ import annotations

import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import verify_deploy  # noqa: E402

PAGE = "<!doctype html><html><head><title>A page</title>{extra}</head><body>" + "x" * 800 + "</body></html>"
ABSOLUTE_OG = '<meta property="og:image" content="https://example.test/card.png">'
RELATIVE_OG = '<meta property="og:image" content="/card.png">'


class _Stub(BaseHTTPRequestHandler):
    """Answers from a routing table the test sets: path -> (status, headers, body)."""

    routes: dict = {}

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's own naming
        status, headers, body = self.routes.get(self.path, (404, {}, "not found"))
        payload = body.encode()
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):  # keep the suite's output clean
        pass


@pytest.fixture
def stub():
    def _serve(routes: dict):
        _Stub.routes = routes
        server = HTTPServer(("127.0.0.1", 0), _Stub)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return f"http://127.0.0.1:{server.server_port}", server

    servers = []

    def _make(routes):
        base, server = _serve(routes)
        servers.append(server)
        return base

    yield _make
    for server in servers:
        server.shutdown()


def _manifest(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "site-manifest.yaml"
    path.write_text(yaml.safe_dump(entries, sort_keys=False))
    return path


PUBLIC_ENTRY = {"source": "reports/a.html", "dest": "open.html", "visibility": "public"}
PRIVATE_ENTRY = {"source": "reports/b.html", "dest": "shut.html", "visibility": "private"}


def _run(base: str, manifest: Path) -> int:
    return verify_deploy.main([base, "--manifest", str(manifest), "--timeout", "5"])


# -- the deployment behaving correctly ---------------------------------------------------


def test_a_correct_deployment_passes(stub, tmp_path, capsys):
    base = stub({
        "/open": (200, {}, PAGE.format(extra=ABSOLUTE_OG)),
        "/shut": (401, {"WWW-Authenticate": 'Basic realm="x"'}, "unauthorised"),
    })

    assert _run(base, _manifest(tmp_path, [PUBLIC_ENTRY, PRIVATE_ENTRY])) == 0
    assert "all clear as a visitor sees it" in capsys.readouterr().out


def test_paths_are_the_manifest_dests_without_their_extension():
    """Vercel serves `walsh-aledo.html` at `/walsh-aledo`, so that is what gets fetched."""
    path = Path(verify_deploy.MANIFEST_PATH)
    expected = verify_deploy.expectations(path)

    assert expected
    assert all(not e.path.endswith(".html") for e in expected)
    assert any(e.public for e in expected), "the real manifest publishes nothing openly"


# -- the deployment lying, in each of the ways it actually did ----------------------------


def test_an_sso_redirect_on_a_public_path_fails_the_deploy(stub, tmp_path, capsys):
    """The exact incident: the long deployment URL 302s to a login page, the short alias does
    not, and `vercel deploy`'s success line says nothing about which one you have."""
    base = stub({"/open": (302, {"Location": "https://vercel.com/login?next=/open"}, "")})

    assert _run(base, _manifest(tmp_path, [PUBLIC_ENTRY])) == 1
    assert "password prompt" in capsys.readouterr().err


def test_a_login_page_served_with_a_200_also_fails(stub, tmp_path, capsys):
    """Some gates answer 200 and put the login in the body. A status check alone misses it."""
    base = stub({"/open": (200, {}, PAGE.format(extra='<form action="/sso-api">'))})

    assert _run(base, _manifest(tmp_path, [PUBLIC_ENTRY])) == 1
    assert "password prompt" in capsys.readouterr().err


def test_a_relative_og_image_fails_the_deploy(stub, tmp_path, capsys):
    """It passes every local check, and then every link preview silently produces nothing."""
    base = stub({"/open": (200, {}, PAGE.format(extra=RELATIVE_OG))})

    assert _run(base, _manifest(tmp_path, [PUBLIC_ENTRY])) == 1
    assert "relative" in capsys.readouterr().err


def test_a_private_page_answering_a_stranger_fails_the_deploy(stub, tmp_path, capsys):
    """Fail closed cuts both ways. This is the more serious of the two failures."""
    base = stub({"/shut": (200, {}, PAGE.format(extra=""))})

    assert _run(base, _manifest(tmp_path, [PRIVATE_ENTRY])) == 1
    assert "marked private answered 200" in capsys.readouterr().err


def test_an_empty_page_behind_a_200_fails_the_deploy(stub, tmp_path, capsys):
    """A host that serves an empty body with a 200 is a deploy that uploaded nothing."""
    base = stub({"/open": (200, {}, "<html></html>")})

    assert _run(base, _manifest(tmp_path, [PUBLIC_ENTRY])) == 1
    assert "that is not a page" in capsys.readouterr().err


def test_a_host_that_cannot_be_reached_at_all_is_an_error_not_a_pass(tmp_path):
    with pytest.raises(SystemExit, match="could not be reached"):
        _run("http://127.0.0.1:9", _manifest(tmp_path, [PUBLIC_ENTRY]))


def test_a_manifest_publishing_nothing_verifies_nothing(tmp_path, capsys):
    assert _run("http://127.0.0.1:9", _manifest(tmp_path, [])) == 0
    assert "nothing to verify" in capsys.readouterr().out
