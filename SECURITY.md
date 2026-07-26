# Security policy

Bolig Tracker is a **static site** (HTML/CSS/JS on GitHub Pages) with no backend,
no database, no user accounts, and no server-side processing of user input. Data
is refreshed by a scheduled GitHub Action that writes JSON files to the repo.

## Reporting a vulnerability

Please report suspected vulnerabilities privately rather than opening a public
issue. Use GitHub's **"Report a vulnerability"** (Security → Advisories) on this
repository, or the contact address on the site's *Om & forbehold* page.

Useful things to include: affected URL/file, steps to reproduce, and impact.

## Hardening notes (maintainer checklist)

**Repository / account**
- Enable **2FA** on the GitHub account that owns the repo and Pages.
- Keep branch protection on `main`; only the owner + the Action push.
- The Action token is scoped `contents: write` only (see `.github/workflows/build.yml`).
- Third-party actions are **pinned to commit SHAs** (not floating tags). When
  bumping, update the `# vX.Y.Z` comment too. Consider enabling Dependabot for
  GitHub Actions so pins get PRs instead of going stale.

**Frontend**
- All externally-sourced text (addresses, city/station names, user input) is
  HTML-escaped before it reaches innerHTML / Leaflet tooltips (`esc()`).
- Leaflet is **vendored** locally (no third-party CDN at runtime).
- No cookies, no analytics, no third-party trackers.

**Deployment (recommended for the custom domain)**

GitHub Pages can't set HTTP response headers. Put the `.dk` domain behind
**Cloudflare** (free) for DDoS/bot mitigation, caching, and to add security
headers via a *Transform Rule → Modify Response Header* (or a Cloudflare Worker):

    Content-Security-Policy: default-src 'self'; img-src 'self' data: https://*.basemaps.cartocdn.com; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'
    X-Content-Type-Options: nosniff
    Referrer-Policy: strict-origin-when-cross-origin
    Permissions-Policy: geolocation=(), microphone=(), camera=()
    Strict-Transport-Security: max-age=31536000; includeSubDomains

Notes:
- `img-src` must allow the CARTO basemap tile host (`*.basemaps.cartocdn.com`).
- `style-src 'unsafe-inline'` is needed because the map/charts set inline styles;
  tighten later with hashes/nonces if you move off inline styles.
- Test with the CSP in **Report-Only** mode first so you don't break the map.
