export const config = { matcher: "/(.*)" };

// Public carve-out, read straight from site-manifest.yaml's own "public" entries and
// spliced in here by scripts/build_site.py — never hand-maintained in this file, and
// never anything this file invents on its own. An empty list (the default: a manifest
// that names nothing public) means every path below falls through to the password check.
const PUBLIC_PATHS = /*__PUBLIC_PATHS__*/[];

function isPublic(pathname) {
  var bare = pathname.replace(/\/+$/, "");
  return PUBLIC_PATHS.some(function (path) {
    var clean = "/" + path;
    return bare === clean || bare === clean + ".html";
  });
}

export default function middleware(req) {
  var pathname = new URL(req.url).pathname;
  if (isPublic(pathname)) {
    return; // public -> serve the static asset, no password needed
  }

  var user = process.env.SITE_USER || "admin";
  var pass = process.env.SITE_PASSWORD || "";
  var header = req.headers.get("authorization") || "";
  var expected = "Basic " + btoa(user + ":" + pass);

  // Fail closed. An unset SITE_PASSWORD leaves `pass` empty, which can never equal a real
  // Authorization header (nobody sends "Basic <base64 of "admin:">" by accident) — so a
  // site with no password configured serves 401 to every private path, rather than the
  // opposite mistake of serving them all in the clear because nobody set one yet.
  if (pass && header === expected) {
    return; // authenticated -> serve the static asset
  }

  return new Response("Authentication required.", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="Property Finder", charset="UTF-8"' },
  });
}
