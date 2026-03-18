# Zscaler URL Checker — K-12 Edition

## What This Is

A web tool for K-12 IT staff and teachers to check if URLs are blocked/allowed by Zscaler ZIA for different student groups (Elementary, Middle, High School, etc.).

## Architecture

```
Browser → Caddy (HTTPS :443) → Flask/Gunicorn (:5100) → Zscaler ZIA API
```

### Key Files

| File | Purpose |
|------|---------|
| `zscaler_client.py` | Core API client — auth, URL lookup, rule evaluation, group comparison |
| `web.py` | Flask routes — all API endpoints and page rendering |
| `auth.py` | Google OAuth + group-based access control (admin vs user roles) |
| `cli.py` | Command-line interface (standalone, not used by web) |
| `templates/index.html` | Main web UI — URL checker, policy overview, dark/light theme |
| `templates/setup.html` | Setup wizard — Zscaler credentials, discovery, school mapping |
| `templates/login.html` | Google OAuth login page |

### Config Files (NEVER commit these)

| File | Purpose |
|------|---------|
| `zscaler_checker_config.json` | Zscaler API credentials, discovered entities, school mappings |
| `auth_config.json` | Google OAuth client ID/secret, service account path |
| `service-account.json` | Google service account key for group membership checks |

## Deployment

- **Docker**: `Dockerfile` + `docker-compose.yml` — runs with gunicorn
- **Reverse proxy**: Caddy config in `deploy/caddy/` — shared across multiple apps via `proxy` Docker network
- **Full instructions**: `DEPLOYMENT.md`

### Docker Commands

```bash
# Build and start
docker compose up -d --build

# View logs
docker compose logs -f

# Restart after config changes
docker compose restart app

# Rebuild after code changes
docker compose up -d --build
```

## Development Rules

### Security
- NEVER log, print, or commit credentials (`*config*.json`, `service-account.json`)
- NEVER expose API keys in responses or error messages
- Auth config files are mounted read-only in Docker

### Zscaler API
- Use `ZscalerClient` class for ALL API interactions
- Add `time.sleep(1)` between sequential API calls to avoid 429 rate limits
- Always call `client.logout()` after API operations (in try/finally)
- URL pattern matching: dot-prefix (`.reddit.com`) must match both `reddit.com` and `www.reddit.com`

### Web UI
- Templates are in `templates/` directory
- Dark mode is the default — theme saved in localStorage
- Settings page (`/setup`) is admin-only when auth is enabled
- Group dropdown is filtered to specific student groups (hardcoded allowed list in index.html)

### Access Control (when Google OAuth is enabled)
- `engineering@ncmcs.org` → admin role (full access including Settings)
- `mcsallstaff@ncmcs.org` → user role (checker + policy views only)
- Allowed domains: `@ncmcs.org`, `@ncmcs.support`
- Auth disabled = open access (no login required)

### Code Style
- Python: PEP 8, clear variable names
- Frontend: vanilla JS, no frameworks, CSS custom properties for theming
- Keep `web.py` route decorators in sync: `@login_required` for general routes, `@admin_required` for Settings/config routes

## Common Tasks

### Add a new API endpoint
1. Add the method to `ZscalerClient` in `zscaler_client.py`
2. Add the Flask route in `web.py` with appropriate auth decorator
3. Add the frontend call in `templates/index.html`

### Add a new group to the dropdown
Edit the `allowed` array in the `loadGroupDropdown()` function in `templates/index.html`

### Change auth groups or domains
Edit `ALLOWED_DOMAINS`, `ADMIN_GROUP`, and `USER_GROUP` constants in `auth.py`
