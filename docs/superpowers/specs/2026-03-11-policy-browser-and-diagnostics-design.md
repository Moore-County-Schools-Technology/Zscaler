# Policy Browser & Diagnostic Tool — Design Spec

## Problem

Teachers need to know "what's blocked for my students?" without checking URLs one at a time. When a site doesn't work, both teachers and IT admins need to quickly determine if Zscaler is the cause.

## Audience

- **Teachers**: Simple answers, no jargon. "Is this site blocked?" and "Why isn't this working?"
- **IT Admins**: Policy overview, rule auditing, troubleshooting details.

## Feature 1: Policy Browser

### What It Does

Displays all URL filtering rules as a category-by-school-level matrix showing what's blocked, cautioned, or allowed at each level (Elementary, Middle School, High School).

### UI

- Accessible via a "Policy Overview" tab/link from the main page.
- Table format:

| Category | Elementary | Middle School | High School | Rule Name |
|----------|-----------|---------------|-------------|-----------|
| Social Networking | BLOCKED | BLOCKED | CAUTION | Social Media Block |
| Streaming Media | BLOCKED | CAUTION | ALLOWED | Media Policy |

- Search/filter box to find categories quickly.
- Color-coded status cells (red=blocked, yellow=caution, green=allowed).

### Backend

- New method `ZscalerClient.get_policy_overview(school_mapping)`:
  - Fetches all URL categories and all URL filtering rules.
  - For each category, evaluates which rules apply at each school level.
  - Returns a list of `{category, elementary_status, middle_status, high_status, rule_name}`.
- New endpoint `GET /api/policy-overview` in `web.py`.

## Feature 2: "Is It Zscaler?" Diagnostic Tool

### What It Does

Runs three checks on a URL and provides a plain-language verdict:

1. **Zscaler filtering check** (existing logic) — blocked/cautioned/allowed per level, which rule.
2. **Site reachability check** — HTTP request from the server to determine if the site is up, down, redirecting, or has SSL issues.
3. **Verdict summary** — Combines both into a clear answer:
   - "Zscaler is blocking this site for Elementary students (rule: X)"
   - "This site appears to be down — not a Zscaler issue"
   - "This site is allowed but has an SSL certificate error — may be caused by SSL inspection"
   - "This site is allowed and working — the issue is likely something else"

### UI

- "Diagnose" button next to existing "Check" button on the main page.
- Same URL input field.
- Results show the plain-language verdict prominently, with expandable details (reachability info, Zscaler rule details) below.

### Backend

- New function `diagnose_url(url)` in `web.py` (or a utility module):
  - Makes an HTTP GET to the URL with a short timeout (5s).
  - Catches connection errors, timeouts, SSL errors separately.
  - Returns `{reachable, status_code, ssl_error, redirect_url, error_message}`.
- New endpoint `POST /api/diagnose` in `web.py`:
  - Calls existing `check_url_full()` for Zscaler status.
  - Calls `diagnose_url()` for reachability.
  - Combines into a verdict.

## Files Modified

- `zscaler_client.py` — Add `get_policy_overview()` method.
- `web.py` — Add `/api/policy-overview` and `/api/diagnose` endpoints, plus `diagnose_url()` helper.
- `templates/index.html` — Add Diagnose button, diagnostic results display, and Policy Overview tab/link.

## No New Files

Everything fits into the existing structure.

## Constraints

- Only config APIs available (URL filtering rules, URL categories, locations, groups). No reporting/log APIs.
- Reachability check runs from the server, not the client browser — so it reflects server-side connectivity, not the user's network. This is noted in the UI.
