# 🏫 Zscaler URL Checker — K-12 Edition

Check if a website is blocked for students at **Elementary**, **Middle School**, or **High School** levels by querying your Zscaler ZIA instance via API.

Includes both a **CLI tool** and a **web interface**.

---

## Prerequisites

- Python 3.10+
- Zscaler ZIA admin access with API enabled
- API credentials (either API Key or OAuth 2.0)

### Getting Your Zscaler API Credentials

1. Log into the **ZIA Admin Portal**
2. Go to **Administration → Cloud Service API Security**
3. For **API Key auth**: Copy the API key from the "Cloud Service API Key" tab, and use your admin username/password
4. For **OAuth 2.0**: Create an OAuth client in the "OAuth 2.0 Authorization Servers" tab

Your **Base URL** depends on your Zscaler cloud:
| Cloud | Base URL |
|-------|----------|
| zscaler.net | `https://zsapi.zscaler.net` |
| zscalerone.net | `https://zsapi.zscalerone.net` |
| zscalertwo.net | `https://zsapi.zscalertwo.net` |
| zscalerthree.net | `https://zsapi.zscalerthree.net` |
| zscloud.net | `https://zsapi.zscloud.net` |

---

## Installation

```bash
cd zscaler-checker
pip install -r requirements.txt
```

---

## Option A: Web Interface

```bash
python web.py
# Opens at http://localhost:5100
```

The web UI walks you through a 3-step setup:

1. **Connect** — Enter your Zscaler API credentials and test the connection
2. **Discover** — Pull your locations, location groups, user groups, and departments from Zscaler
3. **Map** — Assign the discovered entities to Elementary / Middle School / High School

Then use the main dashboard to check URLs individually or in batch.

### Custom port:
```bash
python web.py --port 8080
```

---

## Option B: CLI

### First-time setup:
```bash
# 1. Configure API credentials
python cli.py setup

# 2. Discover your Zscaler entities (locations, groups, etc.)
python cli.py discover

# 3. Map entities to school levels
python cli.py map
```

### Checking URLs:
```bash
# Single URL
python cli.py check youtube.com

# From a file (one URL per line)
python cli.py check -f urls.txt

# Interactive batch mode (with CSV export)
python cli.py batch
```

### Example output:
```
──────────────────────────────────────────────────
  URL:        tiktok.com
  Categories: Social Networking
  Elementary     🚫 BLOCKED  (rule: Block Social - Elementary)
  Middle School  🚫 BLOCKED  (rule: Block Social - MS)
  High School    ⚠️  CAUTION  (rule: Caution Social - HS)
──────────────────────────────────────────────────
```

---

## How It Works

1. **URL Lookup** → Sends the URL to Zscaler's `/api/v1/urlLookup` endpoint to get its category classification (e.g., "Streaming Media", "Social Networking", "Gaming")

2. **Rule Matching** → Pulls all URL filtering rules from `/api/v1/urlFilteringRules` and checks which rules:
   - Are **ENABLED**
   - Match the URL's **category**
   - Are scoped to the school level's **location group / user group / department / location**
   - Have a **BLOCK** or **CAUTION** action

3. **Result** → Reports blocked/cautioned/allowed status per school level

---

## Configuration File

Config is stored in `zscaler_checker_config.json` in the project directory. It contains:
- API credentials
- Discovered entities from Zscaler
- School level → entity mappings

⚠️ **This file contains sensitive credentials.** Keep it secure and don't commit it to version control.

---

## File Structure

```
zscaler-checker/
├── cli.py                  # CLI tool
├── web.py                  # Flask web server
├── zscaler_client.py       # Core Zscaler API client
├── requirements.txt
├── templates/
│   ├── index.html          # Main checker dashboard
│   └── setup.html          # Setup wizard
└── README.md
```

---

## Limitations & Notes

- The tool checks URL **categories** against **filtering rules**. If your district uses custom URL categories or inline exceptions, those are factored in too.
- Rule evaluation follows Zscaler's published API structure but may not capture every edge case of the actual inline enforcement engine (e.g., time-based rules, device trust levels, SSL inspection exceptions).
- The API has rate limits — the client adds delays between batch requests automatically.
- URL lookup results reflect Zscaler's current categorization database. A URL's category can change over time.
