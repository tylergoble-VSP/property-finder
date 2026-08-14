# Vendored front-end assets

Third-party files, checked in at a pinned version, inlined into a built page by
`pagebuild.render` wherever a template writes `{{VENDOR:<filename>}}`.

They are vendored rather than linked because every page this tool produces is one
self-contained file that opens from a filesystem. A script tag pointing at a
content-delivery network would make yesterday's archived report depend on somebody else's
uptime, on the reader having a connection, and on a version nobody pinned — and the
archived reports are supposed to still be readable in five years.

| File | Version | Source | Vendored |
|---|---|---|---|
| `leaflet-1.9.4.js` | Leaflet 1.9.4 | `https://unpkg.com/leaflet@1.9.4/dist/leaflet.js` | 2026-08-14 |
| `leaflet-1.9.4.css` | Leaflet 1.9.4 | `https://unpkg.com/leaflet@1.9.4/dist/leaflet.css` | 2026-08-14 |

Leaflet is BSD-2-Clause licensed; the copyright banner is preserved at the top of the
minified file and travels into every page built from it.

## Upgrading

Download the new version under a new filename (the version is in the name on purpose —
two reports built a year apart should be able to disagree about which Leaflet they hold),
update the template's include token, update this table, and run the suite. Nothing else
refers to these files.

Leaflet's stylesheet references marker and layer-control images by relative URL. The map
template draws its own circle markers and uses no layers control, so no image is ever
requested and none is vendored. If a future page wants Leaflet's default pin, the image
has to be vendored too — as a data URI, or the page stops being self-contained.

## The one rule

Nothing here may contain `</script` or `</style`. Either sequence would close the element
the file is inlined into and truncate the page at that byte. `pagebuild.inline_vendor`
checks and refuses; this note is so nobody has to learn why from the error.
