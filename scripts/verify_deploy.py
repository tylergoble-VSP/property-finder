"""Verify a deployment from OUTSIDE, as a visitor. Nothing the deploying machine saw counts.

    .venv/bin/python scripts/verify_deploy.py https://property-finder.vercel.app

Run by `scripts/deploy.sh` after the upload, and runnable by hand against any base URL. Every
check here exists because the thing it checks was wrong on a live URL while every local check
passed:

  * **The production alias, not the deployment URL.** Vercel leaves a project's production
    alias public and SSO-gates the per-deployment URL: the long `project-hash.vercel.app`
    address returns a 302 to a login page while the short alias returns 200. The deploy tool's
    success message distinguishes neither, so sharing the wrong one hands an audience a
    password prompt (docs/PORTING-THE-REPORTS.md, lesson 12).
  * **Public means public.** A page marked `public` in the manifest must return 200 and real
    bytes with no redirect to a login screen.
  * **Private means private, and it fails closed both ways.** A page marked `private` must be
    refused. A private page that answers 200 to a stranger is the more serious failure of the
    two, and a check that only looked for the first kind would never see it.
  * **`og:image` must be absolute.** A relative path passes every local check and then produces
    no link preview at all, silently. So it is checked in the *served* bytes.

The base URL is an argument and `--pages` can override which paths are expected, so the whole
thing runs against a local stub server in the test suite — the same reason `deploy.sh` takes
`PYTHON` and `NPX` from the environment.
"""
from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "site-manifest.yaml"

# Vercel's clean URLs: `walsh-aledo.html` is served at `/walsh-aledo`.
def _clean(dest: str) -> str:
    return dest.removesuffix(".html")


# What "you are being asked to log in" looks like from outside: either the edge middleware's
# own Basic-Auth challenge, or Vercel's SSO redirect to its login page.
AUTH_STATUSES = {401, 403}
LOGIN_HOSTS = ("vercel.com/login", "vercel.com/sso", "/sso-api")

# An og:image whose value does not start with a scheme. Matched on the served bytes, because a
# relative path is only ever wrong once the page has an origin.
OG_IMAGE = re.compile(r'<meta[^>]+property="og:image"[^>]+content="([^"]*)"', re.I)


@dataclass(frozen=True)
class Expectation:
    path: str
    public: bool


@dataclass
class Response:
    status: int
    location: str
    body: str


def expectations(manifest_path: Path = MANIFEST_PATH) -> list[Expectation]:
    """What the manifest says the world should be able to reach, and what it should not."""
    raw = yaml.safe_load(manifest_path.read_text()) or []
    return [
        Expectation(path="/" + _clean(entry["dest"]), public=entry["visibility"] == "public")
        for entry in raw
    ]


def fetch(url: str, timeout: float) -> Response:
    """One GET, with redirects NOT followed — the redirect is half of what is being tested."""

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": "property-finder-verify/1"})
    try:
        with opener.open(request, timeout=timeout) as response:
            return Response(response.status, response.headers.get("Location", ""),
                            response.read(200_000).decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        # A 401 or a 302 is an answer, not a failure — for a private page it is the answer we
        # are hoping for. Only a connection that never happened is an error.
        body = exc.read(4_000).decode("utf-8", "replace") if exc.fp else ""
        return Response(exc.code, exc.headers.get("Location", "") if exc.headers else "", body)
    except (urllib.error.URLError, OSError) as exc:
        raise SystemExit(f"{url}: could not be reached from here at all — {exc}")


def _is_login(response: Response) -> bool:
    if response.status in AUTH_STATUSES:
        return True
    if 300 <= response.status < 400:
        return True
    return any(host in response.body for host in LOGIN_HOSTS)


def check(base: str, expected: list[Expectation], timeout: float) -> list[str]:
    problems: list[str] = []
    for item in expected:
        url = base.rstrip("/") + item.path
        response = fetch(url, timeout)

        if item.public:
            if _is_login(response):
                problems.append(
                    f"{url}: a page marked public answered {response.status}"
                    + (f" -> {response.location}" if response.location else "")
                    + " — a visitor gets a password prompt, not the page"
                )
                continue
            if response.status != 200:
                problems.append(f"{url}: public page answered {response.status}, not 200")
                continue
            if len(response.body) < 500:
                problems.append(
                    f"{url}: answered 200 with {len(response.body)} bytes — that is not a page"
                )
            for image in OG_IMAGE.findall(response.body):
                if not image.startswith(("http://", "https://")):
                    problems.append(
                        f"{url}: og:image is {image!r}, which is relative — every link preview "
                        "for this page silently fails"
                    )
        else:
            # Fail closed cuts both ways: a private page that lets a stranger in is worse than
            # a public one that does not.
            if not _is_login(response):
                problems.append(
                    f"{url}: a page marked private answered {response.status} with "
                    f"{len(response.body)} bytes to a request carrying no password"
                )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch a deployed site as a visitor and check what it actually serves.",
    )
    parser.add_argument(
        "base_url",
        help="the PRODUCTION ALIAS, not a per-deployment URL — the long project-hash address "
        "is SSO-gated and will fail every public check here, correctly",
    )
    parser.add_argument(
        "--manifest", type=Path, default=MANIFEST_PATH, help="which manifest to expect"
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)

    expected = expectations(args.manifest)
    if not expected:
        print("the manifest publishes nothing — nothing to verify")
        return 0

    problems = check(args.base_url, expected, args.timeout)
    public = sum(1 for e in expected if e.public)
    print(
        f"checked {len(expected)} path(s) on {args.base_url} — {public} public, "
        f"{len(expected) - public} behind the password"
    )
    if not problems:
        print("all clear as a visitor sees it")
        return 0
    print(f"\n{len(problems)} problem(s):", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
