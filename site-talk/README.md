# So Easy A Grunt Could Do It — the talk deck

32 slides — the first is the title, the second is the recording — one self-contained HTML
file, published at
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

## The recording (slide 2)

The walkthrough is a Loom embed — the one thing in this deck that is not in this repository.
Two consequences worth knowing before editing it:

- **Its `src` is deferred.** The iframe carries `data-src`, and the nav script sets `src` the
  first time somebody walks to that slide. So opening the deck is still one request for one
  file, `verify_page.py` renders all 32 slides without reaching a third party, and nobody who
  never reaches slide 2 is announced to Loom. Setting `src` a second time would reload the
  video and lose the viewer's place, so the attribute is dropped once it is mounted.
- **It is blank in the artifact build.** claude.ai artifacts run under a CSP that blocks
  every external host, so the fragment keeps the slide and the player cannot load in it. The
  Vercel page is the one to share when the video is the point.

Its box is sized in container units rather than by the usual `padding-bottom: 56.25%`, because
a percentage padding is a fraction of *width* and these slides are constrained by *height* —
the width-driven version is 647px tall on a 1150px stage and overflows a projector. The
comment above `.videofit` in `index.html` has the arithmetic.

## Verify at the projector's resolution, not the laptop's

```bash
.venv/bin/python scripts/verify_page.py site-talk/index.html                    # 1280x720
.venv/bin/python scripts/verify_page.py site-talk/index.html --viewport 1920x1080
```

The harness measures every slide as a fixed frame and fails on any that overflows, in all four
theme states (system dark and light, plus an explicit choice against the opposite system
preference). Twelve of the deck's slides overflowed at 1280×720 — a projector's reality —
while every one of them fitted at the 1920×1080 they were authored at. The fix was the
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
