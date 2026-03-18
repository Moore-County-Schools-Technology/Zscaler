# Zscaler URL Checker — Deployment Guide

## Prerequisites

- Docker and Docker Compose installed on the host server
- A wildcard SSL certificate for your domain (`.crt` and `.key` files)
- DNS access to create a subdomain (e.g., `urlchecker.ncmcs.org`)
- Zscaler ZIA API credentials (API key, username, password, cloud name)

---

## 1. Clone and Configure

```bash
git clone <your-repo-url> zscaler-url-checker
cd zscaler-url-checker
```

### Zscaler Config

Create `zscaler_checker_config.json` in the project root:

```json
{
  "cloud": "zscaler.net",
  "api_key": "YOUR_ZIA_API_KEY",
  "username": "admin@ncmcs.org",
  "password": "YOUR_PASSWORD"
}
```

> **Security:** This file is mounted read-only into the container and excluded from the Docker image via `.dockerignore`. Never commit this file to git.

---

## 2. Shared Caddy Reverse Proxy

This setup uses a single Caddy instance to serve multiple apps (URL Checker, Wazuh, etc.) on different subdomains. All apps communicate over a shared Docker network — no ports exposed to the host except 80/443 on Caddy.

```
Internet → Caddy (:443)
             ├── urlchecker.ncmcs.org → zscaler-url-checker:5100
             ├── wazuh.ncmcs.org      → wazuh-dashboard:443
             └── otherapp.ncmcs.org   → other-container:port
```

### 2a. Create the shared Docker network

```bash
docker network create proxy
```

All containers that Caddy needs to reach must join this network.

### 2b. Set up Caddy

Create a directory for the shared Caddy instance (reference files are in `deploy/caddy/`):

```bash
sudo mkdir -p /opt/caddy/certs
sudo cp deploy/caddy/docker-compose.yml /opt/caddy/
sudo cp deploy/caddy/Caddyfile /opt/caddy/
```

Place your wildcard certificate:

```bash
sudo cp /path/to/wildcard.crt /opt/caddy/certs/wildcard.crt
sudo cp /path/to/wildcard.key /opt/caddy/certs/wildcard.key
```

### 2c. Edit the Caddyfile

Edit `/opt/caddy/Caddyfile` to match your subdomains. Each app gets its own block:

```
urlchecker.ncmcs.org {
    tls /etc/caddy/certs/wildcard.crt /etc/caddy/certs/wildcard.key
    reverse_proxy zscaler-url-checker:5100
    header {
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "strict-origin-when-cross-origin"
        -Server
    }
}

wazuh.ncmcs.org {
    tls /etc/caddy/certs/wildcard.crt /etc/caddy/certs/wildcard.key
    reverse_proxy wazuh-dashboard:443 {
        transport http {
            tls_insecure_skip_verify
        }
    }
}
```

> **Note:** `tls_insecure_skip_verify` is needed for Wazuh because its dashboard uses a self-signed cert internally. Caddy terminates the real SSL for the browser.

### 2d. Start Caddy

```bash
cd /opt/caddy
docker compose up -d
```

### 2e. Connect existing containers

For apps already running in Docker (like Wazuh), connect their containers to the `proxy` network:

```bash
# Find container names
docker ps --format '{{.Names}}'

# Connect them
docker network connect proxy <wazuh-dashboard-container>
```

To make this permanent, add the network to each app's `docker-compose.yml`:

```yaml
services:
  dashboard:
    # ... existing config ...
    networks:
      - default
      - proxy

networks:
  proxy:
    external: true
```

### Adding new apps later

To add another app behind Caddy:

1. Add a new block to `/opt/caddy/Caddyfile`
2. Make sure the app container joins the `proxy` network
3. Reload Caddy: `docker exec caddy caddy reload --config /etc/caddy/Caddyfile`

---

## 3. DNS

Create A records pointing your subdomains to the server:

```
urlchecker.ncmcs.org → <server-ip>
wazuh.ncmcs.org      → <server-ip>
```

Or if using a wildcard DNS record: `*.ncmcs.org → <server-ip>`

---

## 4. Deploy the URL Checker

```bash
cd /path/to/zscaler-url-checker
docker compose up -d
```

Verify the container is running and connected:

```bash
docker compose ps
docker network inspect proxy
```

The app should now be accessible at `https://urlchecker.ncmcs.org`.

### Useful Commands

```bash
# View app logs
docker compose logs -f

# Restart after config changes
docker compose restart app

# Rebuild after code changes
docker compose up -d --build

# Stop the app
docker compose down

# View Caddy logs
docker logs caddy -f

# Reload Caddy after editing the Caddyfile (no downtime)
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```

---

## 5. Google OAuth (Optional)

Adds Google login with group-based access control. Without this, the app is open to anyone who can reach it.

### 5a. Google Cloud Console Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (or use an existing one)
3. Enable the **Admin SDK API**:
   - APIs & Services → Library → search "Admin SDK API" → Enable
4. Configure the **OAuth consent screen**:
   - APIs & Services → OAuth consent screen
   - User type: Internal (restricts to your Workspace domain)
   - Fill in app name, support email, etc.

### 5b. Create OAuth Client

1. APIs & Services → Credentials → **Create Credentials** → OAuth 2.0 Client ID
2. Application type: **Web application**
3. Authorized redirect URIs: `https://urlchecker.ncmcs.org/auth/callback`
4. Note the **Client ID** and **Client Secret**

### 5c. Create Service Account (for group membership checking)

1. APIs & Services → Credentials → **Create Credentials** → Service Account
2. Name it (e.g., `url-checker-groups`)
3. Click into the service account → Keys → **Add Key** → JSON
4. Download the key file and save it as `service-account.json` in the project root
5. Note the service account's **Client ID** (found under Details → Unique ID)

### 5d. Domain-Wide Delegation

1. Go to [Google Workspace Admin Console](https://admin.google.com)
2. Security → Access and data control → API controls → **Manage Domain-wide Delegation**
3. Add new:
   - Client ID: the service account's Unique ID from step 5c
   - Scopes: `https://www.googleapis.com/auth/admin.directory.group.member.readonly`

### 5e. Create Auth Config

Create `auth_config.json` in the project root:

```json
{
  "google_client_id": "YOUR_CLIENT_ID.apps.googleusercontent.com",
  "google_client_secret": "YOUR_CLIENT_SECRET",
  "secret_key": "generate-a-random-string-here",
  "service_account_file": "/app/service-account.json",
  "admin_email": "an-admin@ncmcs.org"
}
```

| Field | Description |
|-------|-------------|
| `google_client_id` | OAuth Client ID from step 5b |
| `google_client_secret` | OAuth Client Secret from step 5b |
| `secret_key` | Random string for Flask sessions (run `python3 -c "import secrets; print(secrets.token_hex(32))"`) |
| `service_account_file` | Path to the key file *inside the container* (`/app/service-account.json`) |
| `admin_email` | A Google Workspace admin email the service account impersonates to check groups |

### 5f. Mount Service Account in Docker

In `docker-compose.yml`, uncomment the service account volume:

```yaml
volumes:
  - ./zscaler_checker_config.json:/app/zscaler_checker_config.json:ro
  - ./auth_config.json:/app/auth_config.json:ro
  - ./service-account.json:/app/service-account.json:ro  # uncomment this
```

### 5g. Restart

```bash
docker compose up -d --build
```

### Access Control Summary

| Google Group | Role | Access |
|---|---|---|
| `engineering@ncmcs.org` | admin | Full access: URL checker, Policy Overview, Settings |
| `mcsallstaff@ncmcs.org` | user | URL checker and Policy Overview only |
| All others | — | Denied at login |

Only `@ncmcs.org` and `@ncmcs.support` email domains are allowed to sign in.

---

## Troubleshooting

### App won't start
```bash
docker compose logs app
```
Check that `zscaler_checker_config.json` exists and has valid credentials.

### Container can't be reached by Caddy
```bash
# Verify the container is on the proxy network
docker network inspect proxy | grep -A2 "zscaler-url-checker"

# If not connected
docker network connect proxy zscaler-url-checker
```

### Google login fails with redirect error
- Verify the redirect URI in Google Cloud Console matches exactly: `https://urlchecker.ncmcs.org/auth/callback`
- Make sure the OAuth consent screen is configured (step 5a)
- Check that consent screen user type is set to **Internal**

### Group membership check fails
- Verify domain-wide delegation is set up (step 5d)
- The `admin_email` in `auth_config.json` must be a Workspace super admin
- Check that Admin SDK API is enabled (step 5a)

### SSL / certificate issues
- Verify cert files are readable: `ls -la /opt/caddy/certs/`
- Check Caddy logs: `docker logs caddy`
- Make sure the cert matches the domain in `Caddyfile`

### Can't reach the app from the network
- Verify DNS resolves: `nslookup urlchecker.ncmcs.org`
- Check that ports 80 and 443 are open on the server firewall
- Check Caddy is running: `docker ps | grep caddy`

### Adding Caddy reload to a cron (optional)
If you edit the Caddyfile frequently, you can auto-reload:
```bash
# Reload Caddy config without downtime
docker exec caddy caddy reload --config /etc/caddy/Caddyfile
```
