# So Easy A Grunt Could Do It — the talk deck

31 slides, one self-contained HTML file, published at
`so-easy-a-grunt-could-do-it.vercel.app` and also as a claude.ai artifact.

## One source, two builds

`index.html` is **canonical**. It is a whole document: its own CSS reset, its own metadata, a
`<title>` in the head where a browser reads it, and an absolute `og:image`. That is what a
static host needs, because a static host serves your bytes and nothing else.

The claude.ai artifact host is different: it injects the doctype, `<html>`, `<head>`, `<body>`
and a reset around whatever you give it. So the artifact version is **derived** from this one,
never maintained beside it:

```bash
.venv/bin/python scripts/build_artifact_fragment.py site-talk/index.html
```

That strips the skeleton, the reset and the document-only `<meta>`/`<link>` elements, leaves
every style block and script alone, and prints the title to type into the artifact interface.
Deriving downward can only remove things the host supplies. Deriving upward — authoring the
fragment and pasting it at a URL — is how this deck first shipped with no reset, no metadata,
and a `<title>` sitting in the body where a browser ignores it entirely
(`docs/PORTING-THE-REPORTS.md`, lesson 11).

## Verify at the projector's resolution, not the laptop's

```bash
.venv/bin/python scripts/verify_page.py site-talk/index.html                    # 1280x720
.venv/bin/python scripts/verify_page.py site-talk/index.html --viewport 1920x1080
```

The harness measures every slide as a fixed frame and fails on any that overflows, in both
theme states. Twelve of these thirty-one slides overflowed at 1280×720 — a projector's reality
— while all thirty-one fitted at the 1920×1080 they were authored at. The fix was the
`@media (max-height: 820px)` block in `index.html`, which tightens vertical rhythm and caps
figure heights rather than touching the full-size design, and the harness is what keeps it
honest.

It also found a horizontal scrollbar nothing else would have: inactive slides sit at
`translateX(26px)` so they can slide in, which extended the document 26px past the viewport at
every width. `.stage { overflow: hidden }` is the fix, and the comment there says so.

## Deploying

Its own Vercel project, its own `vercel.json` — separate from the reports site because it has
no relationship to `site-manifest.yaml`, publishes an image asset the manifest copier would
refuse, and is a talk rather than a report.

```bash
npx vercel deploy --prod --yes --cwd site-talk
.venv/bin/python scripts/verify_deploy.py https://so-easy-a-grunt-could-do-it.vercel.app \
    --manifest site-talk/publish-manifest.yaml
```

The second line is the outside-in check (`docs/PORTING-THE-REPORTS.md`, lesson 12): fetch the
**production alias**, not the long per-deployment URL, which Vercel SSO-gates and which will
hand an audience a login page.
