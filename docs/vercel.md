# Serving the reports on Vercel (a URL for everything)

Property Finder's reports are self-contained HTML, so hosting them is pure static hosting —
the Python pipeline (sweeps, the hedonic model, the SQLite history database) never runs on
Vercel. That sidesteps the two things that make this app hostile to serverless: the
scientific stack (`numpy`/`scipy`/`scikit-learn`/`statsmodels`/`pandas`) far exceeds a
function bundle's size limit, and serverless has no durable filesystem for the history
database anyway. Instead, `scripts/build_site.py` builds a `site/` folder from
`site-manifest.yaml` and `scripts/deploy.sh` (commits 43–45) uploads just that.

```
reports/*.html  ──build_site.py (site-manifest.yaml)──▶  site/  ──vercel deploy──▶  https://…vercel.app
 (written by `report` / `map` / `daily`)                  (index + pages + auth)
```

## One-time setup

```bash
# 1. Build the static site from whatever report/map/daily has already produced
.venv/bin/python scripts/build_site.py           # writes site/ (git-ignored)

# 2. Log in + create the Vercel project (interactive — needs your account)
npx vercel login
npx vercel link --cwd site                        # pick/create a project, e.g. "property-finder"

# 3. Deploy
npx vercel deploy --cwd site                       # preview URL (sanity check)
npx vercel deploy --prod --cwd site --yes          # production URL
```

`npx vercel link` writes `.vercel/project.json` inside `site/`; `build_site.py` preserves
that folder across rebuilds (it wipes everything else in `site/` and starts fresh), so
linking is a true one-time step — every later `scripts/deploy.sh` run lands on the same
project without asking again.

`site/vercel.json` (written by `build_site.py`, from `site-manifest.yaml`) sets `cleanUrls`
(so `/walsh-aledo` serves `walsh-aledo.html`), `noindex` headers on any page marked
`visibility: public`, and immutable caching on anything named like a dated archive
(`*-YYYY-MM-DD.html`); everything else revalidates on every request, which is what lets a
rebuilt "latest" page actually replace what a browser or edge cache is holding. Deploy
**from `site/`** (`--cwd site`), never the repo root — the repo root holds `.env` and the
database, and the manifest-driven copier is what keeps them out of `site/` in the first
place (docs/REBUILD.md, post-mortem item 8).

### Verify with `vercel dev`

```bash
npx vercel dev --cwd site        # http://localhost:3000 — confirm /walsh-aledo (no .html) loads
```

## Access control — HTTP Basic Auth at the edge

Every page named in `site-manifest.yaml` with `visibility: private` sits behind a password.
Vercel's own **Deployment Protection** can't gate a *production* deployment on the Hobby
plan (Pro-only), so instead `build_site.py` writes `site/middleware.js` from
`propertyfinder/templates/site-middleware.js` — edge middleware that requires HTTP Basic
Auth **before any HTML is served**, and fails closed: if `SITE_PASSWORD` is unset, every
private path returns 401 rather than being served unprotected (`docs/REBUILD.md` rule 5,
"money models must express reality" — the same "assume the safer default" instinct applied
to access control instead of a mortgage rate). A page marked `visibility: public` is the one
carve-out: reachable by direct URL with no password, `noindex`-tagged, and never linked from
`site/index.html`.

Set the password once on the project (already done during setup):

```
SITE_PASSWORD=…          # required — the shared password (username defaults to "admin")
SITE_USER=…              # optional — override the username
```

Via dashboard: Project → Settings → Environment Variables. Or via CLI:
`echo -n "newpass" | npx vercel env add SITE_PASSWORD production --cwd site` (then redeploy).
Changing `SITE_PASSWORD` takes effect on the next deploy — `tests/test_site_middleware.py`
is where the fail-closed guarantee itself is proven, string-matched against the generated
file rather than run, since this suite never touches Vercel's actual edge runtime.

## Keeping it fresh

The site is a snapshot; it refreshes when you rebuild and redeploy.

```bash
scripts/deploy.sh                 # build_site.py, then npx vercel deploy --prod --yes --cwd site
propertyfinder daily --deploy     # the whole daily pipeline, chaining scripts/deploy.sh last
```

`scripts/deploy.sh` refuses outright — before ever calling `vercel` — if `site/` does not
exist or holds no real page (an empty `site-manifest.yaml` is valid and produces exactly
that shape: an index with nothing to link). Point your scheduler at
`propertyfinder daily --deploy` instead of plain `daily` to publish every morning
(`docs/scheduling.md`); a deploy always runs last, after the digest, so it only ever
publishes what that morning's digest already described. Put a token in `.env` for headless
deploys:

```
VERCEL_TOKEN=…        # from https://vercel.com/account/tokens
```

## Optional: run the whole thing in CI (no laptop needed)

Move the daily run to GitHub Actions (cron), committing the SQLite database back to a
**private** repo so the history (diffs, price-cut maps, predictions) persists between runs.
Set repo secrets `SEARCHAPI_API_KEY`, the SMTP five, and
`VERCEL_TOKEN`/`VERCEL_ORG_ID`/`VERCEL_PROJECT_ID`; remove `*.db` from `.gitignore`; and
disable the local launchd job so quota is not spent twice. (Not set up yet — a later stage
if laptop uptime becomes a problem.)
