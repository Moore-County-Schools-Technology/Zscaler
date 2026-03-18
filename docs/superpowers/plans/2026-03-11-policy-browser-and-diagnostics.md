# Policy Browser & Diagnostic Tool — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Policy Browser (category-by-level matrix) and an "Is It Zscaler?" diagnostic tool to the existing K-12 URL Checker web app.

**Architecture:** Two independent features added to the existing Flask app. Policy Browser adds one new method to `ZscalerClient` and one new endpoint. Diagnostic tool adds a reachability helper and one new endpoint. Both are surfaced in the existing `index.html` via tabs and buttons.

**Tech Stack:** Python 3.10+, Flask, Requests, vanilla JS/HTML/CSS (matching existing patterns)

---

## File Structure

| File | Change | Responsibility |
|------|--------|---------------|
| `zscaler_client.py` | Modify | Add `get_policy_overview(school_mapping)` method |
| `web.py` | Modify | Add `diagnose_url()` helper, `/api/policy-overview` and `/api/diagnose` endpoints |
| `templates/index.html` | Modify | Add Diagnose button, diagnostic results UI, Policy Overview tab with table |

---

## Chunk 1: Backend — Policy Overview

### Task 1: Add `get_policy_overview()` to ZscalerClient

**Files:**
- Modify: `zscaler_client.py:95-106` (after URL Filtering section)

- [ ] **Step 1: Add the `get_policy_overview` method**

Add this method to `ZscalerClient` after the existing `get_url_categories` method (around line 106):

```python
def get_policy_overview(self, school_mapping: dict) -> list:
    """
    Build a category-by-school-level policy matrix.

    Returns a list of dicts:
        [{"category": str, "levels": {"Elementary": "BLOCKED", ...}, "rule_name": str}, ...]
    """
    all_cats = self.get_url_categories()
    url_rules = self.get_url_filtering_rules()

    # Build category name map
    cat_map = {}
    for c in all_cats:
        cid = c.get("id")
        cname = c.get("configuredName") or c.get("val") or str(cid)
        if isinstance(cname, str):
            cname = cname.replace("_", " ").replace("  ", " ")
        cat_map[cid] = cname

    # For each enabled rule, find which categories it affects and what action
    # Group by category -> {level -> action}
    cat_levels = {}  # cat_id -> {level_name -> (action, rule_name, order)}

    for r in url_rules:
        if not isinstance(r, dict) or r.get("state") != "ENABLED":
            continue
        action = r.get("action", "")
        if action not in ("BLOCK", "CAUTION", "ALLOW"):
            continue
        rule_name = r.get("name", "Unnamed")
        order = r.get("order", 9999)
        rule_cats = r.get("urlCategories", [])

        for level_name, level_config in school_mapping.items():
            if not level_config.get("ids"):
                continue
            scope_type = level_config.get("type", "")
            scope_ids = set(level_config.get("ids", []))

            if not self._rule_matches_scope(r, scope_type, scope_ids):
                continue

            # This rule applies to this level
            target_cats = rule_cats if rule_cats and "ANY" not in rule_cats else list(cat_map.keys())

            for cat_id in target_cats:
                if cat_id not in cat_map:
                    continue
                if cat_id not in cat_levels:
                    cat_levels[cat_id] = {}
                # Only keep the highest-priority (lowest order) rule per level
                existing = cat_levels[cat_id].get(level_name)
                if not existing or order < existing[2]:
                    cat_levels[cat_id][level_name] = (action, rule_name, order)

    # Build result list — only include categories that have at least one BLOCK or CAUTION
    level_names = list(school_mapping.keys())
    result = []
    for cat_id, levels in cat_levels.items():
        has_restriction = any(
            levels.get(ln, ("ALLOW",))[0] in ("BLOCK", "CAUTION")
            for ln in level_names
        )
        if not has_restriction:
            continue

        entry = {
            "category": cat_map.get(cat_id, str(cat_id)),
            "levels": {},
            "rule_name": "",
        }
        for ln in level_names:
            if ln in levels:
                entry["levels"][ln] = levels[ln][0]
                if not entry["rule_name"] and levels[ln][0] in ("BLOCK", "CAUTION"):
                    entry["rule_name"] = levels[ln][1]
            else:
                entry["levels"][ln] = "ALLOWED"
        result.append(entry)

    # Sort: blocked categories first, then by category name
    def sort_key(e):
        block_count = sum(1 for v in e["levels"].values() if v == "BLOCKED")
        return (-block_count, e["category"].lower())
    result.sort(key=sort_key)

    return result
```

- [ ] **Step 2: Verify syntax**

Run: `cd /Users/jonathan/Desktop/Zscaler && source venv/bin/activate && python -c "from zscaler_client import ZscalerClient; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add zscaler_client.py
git commit -m "feat: add get_policy_overview method to ZscalerClient"
```

### Task 2: Add `/api/policy-overview` endpoint to web.py

**Files:**
- Modify: `web.py` (after the `/api/policy-audit` endpoint, around line 283)

- [ ] **Step 1: Add the endpoint**

Add this before the `if __name__` block:

```python
@app.route("/api/policy-overview")
def api_policy_overview():
    """Return the category-by-level policy matrix."""
    config = ConfigManager.load()
    if not config:
        return jsonify({"error": "Not configured"}), 400
    if not config.get("school_mapping"):
        return jsonify({"error": "No school mapping configured. Complete setup first."}), 400

    client, err = get_client()
    if not client:
        return jsonify({"error": err}), 400

    try:
        overview = client.get_policy_overview(config["school_mapping"])
        client.logout()
        return jsonify({"overview": overview, "levels": list(config["school_mapping"].keys())})
    except Exception as e:
        try:
            client.logout()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500
```

- [ ] **Step 2: Verify syntax**

Run: `cd /Users/jonathan/Desktop/Zscaler && source venv/bin/activate && python -c "from web import app; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add web.py
git commit -m "feat: add /api/policy-overview endpoint"
```

---

## Chunk 2: Backend — Diagnostic Tool

### Task 3: Add `diagnose_url()` helper and `/api/diagnose` endpoint

**Files:**
- Modify: `web.py` (add helper function near top, add endpoint after policy-overview)

- [ ] **Step 1: Add the `diagnose_url` helper**

Add this after the `get_client()` function (around line 23) in `web.py`:

```python
import requests as ext_requests


def diagnose_url(url: str) -> dict:
    """
    Check if a URL is reachable from this server.
    Returns reachability info independent of Zscaler filtering.
    """
    # Ensure URL has a scheme
    test_url = url if url.startswith(("http://", "https://")) else f"https://{url}"

    result = {
        "reachable": False,
        "status_code": None,
        "ssl_error": False,
        "redirect_url": None,
        "error_message": None,
    }

    try:
        resp = ext_requests.get(test_url, timeout=5, allow_redirects=True,
                                headers={"User-Agent": "ZscalerURLChecker/1.0"})
        result["reachable"] = True
        result["status_code"] = resp.status_code
        if resp.history:
            result["redirect_url"] = resp.url
    except ext_requests.exceptions.SSLError as e:
        result["ssl_error"] = True
        result["error_message"] = "SSL certificate error — this may be caused by Zscaler SSL inspection"
    except ext_requests.exceptions.ConnectionError:
        result["error_message"] = "Could not connect to the site — it may be down"
    except ext_requests.exceptions.Timeout:
        result["error_message"] = "Connection timed out — the site may be slow or down"
    except ext_requests.exceptions.RequestException as e:
        result["error_message"] = f"Request failed: {str(e)}"

    return result
```

- [ ] **Step 2: Add the `/api/diagnose` endpoint**

Add this after the `/api/policy-overview` endpoint:

```python
@app.route("/api/diagnose", methods=["POST"])
def api_diagnose():
    """Diagnose a URL: is it Zscaler blocking it, or is the site actually down?"""
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    config = ConfigManager.load()
    if not config:
        return jsonify({"error": "Not configured"}), 400

    # Run both checks
    reachability = diagnose_url(url)

    client, err = get_client()
    zscaler_result = None
    if client:
        try:
            zscaler_result = client.check_url_full(url)
            client.logout()
        except Exception as e:
            try:
                client.logout()
            except Exception:
                pass
            zscaler_result = {"error": str(e)}

    # Build plain-language verdict
    zscaler_blocked = zscaler_result and zscaler_result.get("verdict") == "BLOCKED"
    zscaler_caution = zscaler_result and zscaler_result.get("verdict") == "CAUTION"
    site_down = not reachability["reachable"]
    ssl_issue = reachability["ssl_error"]

    if zscaler_blocked:
        rule = zscaler_result.get("verdict_rule", "a filtering rule")
        verdict = f"Zscaler is blocking this site ({rule})"
        verdict_type = "zscaler_blocked"
    elif zscaler_caution:
        rule = zscaler_result.get("verdict_rule", "a filtering rule")
        verdict = f"Zscaler shows a caution warning for this site ({rule})"
        verdict_type = "zscaler_caution"
    elif ssl_issue:
        verdict = "This site has an SSL certificate error — this may be caused by Zscaler SSL inspection"
        verdict_type = "ssl_error"
    elif site_down:
        verdict = "This site appears to be down or unreachable — not a Zscaler issue"
        verdict_type = "site_down"
    else:
        verdict = "This site is allowed by Zscaler and appears to be working — the issue may be something else"
        verdict_type = "ok"

    return jsonify({
        "url": url,
        "verdict": verdict,
        "verdict_type": verdict_type,
        "reachability": reachability,
        "zscaler": zscaler_result,
    })
```

- [ ] **Step 3: Verify syntax**

Run: `cd /Users/jonathan/Desktop/Zscaler && source venv/bin/activate && python -c "from web import app; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add web.py
git commit -m "feat: add diagnose_url helper and /api/diagnose endpoint"
```

---

## Chunk 3: Frontend — Diagnose Button & Results

### Task 4: Add Diagnose button to the single-URL search row

**Files:**
- Modify: `templates/index.html:351-358` (the single-mode search row)

- [ ] **Step 1: Add a Diagnose button next to the Check button**

Find the existing search row in `index.html`:
```html
<div class="search-row">
    <input type="text" class="search-input" id="url-input"
        placeholder="youtube.com, kahoot.it, tiktok.com ..."
        onkeydown="if(event.key==='Enter') checkSingle()" autofocus>
    <button class="btn btn-primary" id="btn-go" onclick="checkSingle()">Check</button>
</div>
```

Replace with:
```html
<div class="search-row">
    <input type="text" class="search-input" id="url-input"
        placeholder="youtube.com, kahoot.it, tiktok.com ..."
        onkeydown="if(event.key==='Enter') checkSingle()" autofocus>
    <button class="btn btn-primary" id="btn-go" onclick="checkSingle()">Check</button>
    <button class="btn btn-outline" id="btn-diagnose" onclick="runDiagnose()" title="Check if Zscaler is causing the issue">Diagnose</button>
</div>
```

- [ ] **Step 2: Add diagnostic result styles**

Add these styles inside the existing `<style>` block, before the closing `</style>`:

```css
/* ── Diagnostic Results ────────────────── */
.diag-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    box-shadow: var(--shadow);
    margin-bottom: 14px;
    overflow: hidden;
}
.diag-verdict {
    padding: 20px 24px;
    font-size: 16px;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 12px;
}
.diag-verdict .icon { font-size: 24px; }
.diag-type-zscaler_blocked .diag-verdict { background: var(--red-bg); color: var(--red); border-bottom: 2px solid var(--red-border); }
.diag-type-zscaler_caution .diag-verdict { background: var(--yellow-bg); color: var(--yellow); border-bottom: 2px solid var(--yellow-border); }
.diag-type-ssl_error .diag-verdict { background: var(--yellow-bg); color: var(--yellow); border-bottom: 2px solid var(--yellow-border); }
.diag-type-site_down .diag-verdict { background: var(--surface-alt); color: var(--text-secondary); border-bottom: 2px solid var(--border); }
.diag-type-ok .diag-verdict { background: var(--green-bg); color: var(--green); border-bottom: 2px solid var(--green-border); }

.diag-details {
    padding: 16px 24px;
}
.diag-details summary {
    font-size: 13px;
    font-weight: 500;
    color: var(--text-dim);
    cursor: pointer;
    padding: 4px 0;
}
.diag-detail-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-top: 12px;
}
.diag-detail-box {
    background: var(--surface-alt);
    border: 1px solid var(--border-light);
    border-radius: 8px;
    padding: 12px 14px;
}
.diag-detail-box h4 {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-dim);
    margin-bottom: 6px;
}
.diag-detail-box p {
    font-size: 13px;
    color: var(--text-secondary);
    line-height: 1.5;
}
.diag-note {
    font-size: 11px;
    color: var(--text-dim);
    margin-top: 12px;
    font-style: italic;
}

@media (max-width: 700px) {
    .diag-detail-grid { grid-template-columns: 1fr; }
}
```

- [ ] **Step 3: Add the `runDiagnose()` JavaScript function**

Add this function after the `checkSingle()` function in the `<script>` block:

```javascript
async function runDiagnose() {
    const url = document.getElementById('url-input').value.trim();
    if (!url) return;
    showLoading('Diagnosing ' + url + '...');

    try {
        const r = await fetch('/api/diagnose', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({url}) });
        const d = await r.json();
        hideLoading();
        if (d.error) { showError(d.error); return; }
        renderDiagnose(d);
    } catch(e) {
        hideLoading();
        showError('Network error: ' + e.message);
    }
}

function renderDiagnose(d) {
    const sec = document.getElementById('results');
    const list = document.getElementById('results-list');
    sec.classList.add('visible');
    document.getElementById('results-title').textContent = 'Diagnosis';
    document.getElementById('results-summary').textContent = '';
    document.getElementById('btn-csv').style.display = 'none';

    const icons = {
        zscaler_blocked: '&#128683;',
        zscaler_caution: '&#9888;&#65039;',
        ssl_error: '&#128274;',
        site_down: '&#128546;',
        ok: '&#9989;'
    };

    const reach = d.reachability || {};
    let reachText = '';
    if (reach.reachable) {
        reachText = `Site is up (HTTP ${reach.status_code})`;
        if (reach.redirect_url) reachText += `<br>Redirects to: ${esc(reach.redirect_url)}`;
    } else if (reach.ssl_error) {
        reachText = 'SSL certificate error';
    } else {
        reachText = reach.error_message || 'Not reachable';
    }

    const zs = d.zscaler || {};
    let zsText = '';
    if (zs.error) {
        zsText = `Error: ${esc(zs.error)}`;
    } else {
        zsText = `Verdict: <strong>${esc(zs.verdict || 'UNKNOWN')}</strong>`;
        if (zs.verdict_rule) zsText += `<br>Rule: ${esc(zs.verdict_rule)}`;
        if (zs.categories && zs.categories.length) zsText += `<br>Categories: ${zs.categories.map(c => esc(c)).join(', ')}`;
    }

    list.innerHTML = `
        <div class="diag-card diag-type-${d.verdict_type}">
            <div class="diag-verdict">
                <span class="icon">${icons[d.verdict_type] || ''}</span>
                <span>${esc(d.verdict)}</span>
            </div>
            <div class="diag-details">
                <details>
                    <summary>View details</summary>
                    <div class="diag-detail-grid">
                        <div class="diag-detail-box">
                            <h4>Site Reachability</h4>
                            <p>${reachText}</p>
                        </div>
                        <div class="diag-detail-box">
                            <h4>Zscaler Filtering</h4>
                            <p>${zsText}</p>
                        </div>
                    </div>
                    <div class="diag-note">
                        Reachability is checked from this server, not your device. Results may differ on student devices.
                    </div>
                </details>
            </div>
        </div>`;
}
```

- [ ] **Step 4: Update `setButtons` to include the diagnose button**

Find in `index.html`:
```javascript
function setButtons(disabled) {
    document.getElementById('btn-go').disabled = disabled;
    document.getElementById('btn-batch').disabled = disabled;
}
```

Replace with:
```javascript
function setButtons(disabled) {
    document.getElementById('btn-go').disabled = disabled;
    document.getElementById('btn-batch').disabled = disabled;
    document.getElementById('btn-diagnose').disabled = disabled;
}
```

- [ ] **Step 5: Manually test the diagnose flow**

Run: `cd /Users/jonathan/Desktop/Zscaler && source venv/bin/activate && python web.py`

1. Open http://localhost:5100
2. Type a URL in the input
3. Click "Diagnose"
4. Verify the verdict card appears with reachability and Zscaler details

- [ ] **Step 6: Commit**

```bash
git add templates/index.html
git commit -m "feat: add Diagnose button and diagnostic results UI"
```

---

## Chunk 4: Frontend — Policy Overview Tab

### Task 5: Add Policy Overview tab and table to index.html

**Files:**
- Modify: `templates/index.html`

- [ ] **Step 1: Add Policy Overview tab to the header**

Find in `index.html`:
```html
<div class="header">
    <h1>URL Checker</h1>
    <a href="/setup">Settings</a>
</div>
```

Replace with:
```html
<div class="header">
    <h1>URL Checker</h1>
    <div style="display:flex; gap:4px; align-items:center;">
        <a href="#" onclick="showView('checker'); return false;" id="nav-checker" style="font-weight:600; color:var(--text);">Check URLs</a>
        <a href="#" onclick="showView('policy'); return false;" id="nav-policy">Policy Overview</a>
        <a href="/setup">Settings</a>
    </div>
</div>
```

- [ ] **Step 2: Add Policy Overview styles**

Add these styles inside the `<style>` block:

```css
/* ── Policy Overview ───────────────────── */
#policy-view { display: none; }

.policy-toolbar {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 16px;
}
.policy-search {
    flex: 1;
    max-width: 320px;
    padding: 10px 14px;
    font-size: 14px;
    background: var(--surface);
    border: 2px solid var(--border);
    border-radius: 8px;
    outline: none;
}
.policy-search:focus { border-color: var(--accent); }
.policy-stats { font-size: 12px; color: var(--text-dim); }

.policy-tbl {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
}
.policy-tbl th {
    text-align: left;
    padding: 10px 14px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-dim);
    background: var(--surface-alt);
    border-bottom: 1px solid var(--border);
    position: sticky;
    top: 0;
}
.policy-tbl td {
    padding: 8px 14px;
    border-bottom: 1px solid var(--border-light);
}
.policy-tbl tr:last-child td { border-bottom: none; }
.policy-tbl tr:hover td { background: #fafbfc; }

.status-cell {
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.3px;
}
.status-BLOCKED { color: var(--red); }
.status-CAUTION { color: var(--yellow); }
.status-ALLOWED { color: var(--green); }
```

- [ ] **Step 3: Add the Policy Overview HTML section**

Add this right after the `<div class="main" id="app">` opening tag, before the hero section. Wrap the existing checker content in a div:

Find:
```html
<div class="main" id="app">
    <div class="hero">
```

Replace with:
```html
<div class="main" id="app">
    <!-- Policy Overview View -->
    <div id="policy-view">
        <div class="hero">
            <h2>Policy Overview</h2>
            <p>What's blocked at each school level.</p>
        </div>
        <div class="policy-toolbar">
            <input type="text" class="policy-search" id="policy-filter" placeholder="Search categories..." oninput="filterPolicy()">
            <span class="policy-stats" id="policy-stats"></span>
            <button class="btn btn-outline btn-sm" id="btn-refresh-policy" onclick="loadPolicy()">Refresh</button>
        </div>
        <div class="loading" id="policy-loading">
            <div class="spinner"></div>
            <p>Loading policy data...</p>
        </div>
        <div id="policy-table-wrap"></div>
    </div>

    <!-- URL Checker View -->
    <div id="checker-view">
    <div class="hero">
```

Then find the closing `</div>` for `<div class="main" id="app">` (the one right before `<script>`) and add a closing `</div>` for `checker-view` before it:

Find:
```html
        </div>
    </div>

<script>
```

Replace with:
```html
        </div>
    </div>
    </div>

<script>
```

- [ ] **Step 4: Add Policy Overview JavaScript**

Add these functions inside the `<script>` block, after the `renderDiagnose` function:

```javascript
// ── Policy Overview ──────────────────────
let policyData = null;

function showView(view) {
    document.getElementById('checker-view').style.display = view === 'checker' ? '' : 'none';
    document.getElementById('policy-view').style.display = view === 'policy' ? '' : 'none';
    document.getElementById('nav-checker').style.fontWeight = view === 'checker' ? '600' : '400';
    document.getElementById('nav-checker').style.color = view === 'checker' ? 'var(--text)' : 'var(--text-dim)';
    document.getElementById('nav-policy').style.fontWeight = view === 'policy' ? '600' : '400';
    document.getElementById('nav-policy').style.color = view === 'policy' ? 'var(--text)' : 'var(--text-dim)';

    if (view === 'policy' && !policyData) {
        loadPolicy();
    }
}

async function loadPolicy() {
    document.getElementById('policy-loading').classList.add('visible');
    document.getElementById('policy-table-wrap').innerHTML = '';

    try {
        const r = await fetch('/api/policy-overview');
        const d = await r.json();
        document.getElementById('policy-loading').classList.remove('visible');

        if (d.error) {
            document.getElementById('policy-table-wrap').innerHTML = `<div class="error-card">${esc(d.error)}</div>`;
            return;
        }

        policyData = d;
        renderPolicy(d.overview, d.levels);
    } catch(e) {
        document.getElementById('policy-loading').classList.remove('visible');
        document.getElementById('policy-table-wrap').innerHTML = `<div class="error-card">Error: ${esc(e.message)}</div>`;
    }
}

function renderPolicy(overview, levels) {
    const filter = (document.getElementById('policy-filter').value || '').toLowerCase();
    const filtered = filter
        ? overview.filter(row => row.category.toLowerCase().includes(filter))
        : overview;

    document.getElementById('policy-stats').textContent = `${filtered.length} of ${overview.length} categories`;

    if (!filtered.length) {
        document.getElementById('policy-table-wrap').innerHTML = '<p style="text-align:center; color:var(--text-dim); padding:32px;">No categories found.</p>';
        return;
    }

    const levelHeaders = levels.map(l => `<th>${esc(l)}</th>`).join('');
    const rows = filtered.map(row => {
        const cells = levels.map(l => {
            const status = row.levels[l] || 'ALLOWED';
            return `<td class="status-cell status-${status}">${status}</td>`;
        }).join('');
        return `<tr><td style="font-weight:500;">${esc(row.category)}</td>${cells}<td class="scope">${esc(row.rule_name)}</td></tr>`;
    }).join('');

    document.getElementById('policy-table-wrap').innerHTML = `
        <table class="policy-tbl">
            <thead><tr><th>Category</th>${levelHeaders}<th>Rule</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
}

function filterPolicy() {
    if (policyData) {
        renderPolicy(policyData.overview, policyData.levels);
    }
}
```

- [ ] **Step 5: Manually test the Policy Overview**

Run: `cd /Users/jonathan/Desktop/Zscaler && source venv/bin/activate && python web.py`

1. Open http://localhost:5100
2. Click "Policy Overview" in the header
3. Verify the table loads with categories and school level statuses
4. Try the search filter
5. Click "Check URLs" to go back to the checker

- [ ] **Step 6: Commit**

```bash
git add templates/index.html
git commit -m "feat: add Policy Overview tab with category-by-level matrix"
```

---

## Chunk 5: Integration & Polish

### Task 6: Final verification and cleanup

- [ ] **Step 1: End-to-end test all features**

Run: `cd /Users/jonathan/Desktop/Zscaler && source venv/bin/activate && python web.py`

Test checklist:
1. Single URL check still works
2. Batch URL check still works
3. Diagnose button returns verdict + details
4. Policy Overview loads and filters
5. Navigation between views works
6. Responsive layout (narrow browser window)

- [ ] **Step 2: Verify no import issues**

Run: `cd /Users/jonathan/Desktop/Zscaler && source venv/bin/activate && python -c "from web import app; from zscaler_client import ZscalerClient; print('All imports OK')"`

- [ ] **Step 3: Final commit if any polish was needed**

```bash
git add -A
git commit -m "polish: final integration tweaks for policy browser and diagnostics"
```
