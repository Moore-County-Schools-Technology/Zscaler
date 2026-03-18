# URL Check Logic — Detailed Reference

Use this document to trace exactly what happens when a URL is checked, step by step. Each section maps directly to code in `zscaler_client.py` and `web.py`.

---

## Table of Contents

1. [Check Modes Overview](#1-check-modes-overview)
2. [Step 1: URL Cleanup](#2-step-1-url-cleanup)
3. [Step 2: URL Classification (Zscaler Lookup)](#3-step-2-url-classification-zscaler-lookup)
4. [Step 3: Custom Category Matching](#4-step-3-custom-category-matching)
5. [Step 4: Rule Filtering (Category Match)](#5-step-4-rule-filtering-category-match)
6. [Step 5: Scope Evaluation](#6-step-5-scope-evaluation)
7. [Step 6: Verdict Determination](#7-step-6-verdict-determination)
8. [Diagnose Mode (Full Troubleshooting)](#8-diagnose-mode-full-troubleshooting)
9. [Policy Overview Logic](#9-policy-overview-logic)
10. [Common Troubleshooting Scenarios](#10-common-troubleshooting-scenarios)

---

## 1. Check Modes Overview

There are **four distinct check paths**, each used by a different API endpoint:

| Endpoint | Method | What It Does | Code Path |
|----------|--------|-------------|-----------|
| `POST /api/check` | `check_url_full()` | Global check — no specific user/group. Verdict based on broad rules only. | `zscaler_client.py:532` |
| `POST /api/check-user` | `check_url_for_user()` | Check for a specific user (by email). Resolves their groups/dept, applies scoped rules. | `zscaler_client.py:135` |
| `POST /api/check-group` | `check_url_for_user()` | Check for a specific group. Builds a synthetic user with that group + `also_in_groups` + ActiveStudents. | `web.py:384` |
| `POST /api/diagnose` | `check_url_for_user()` or `check_url_full()` + reachability | Full troubleshooting: Zscaler verdict + HTTP reachability + cross-group comparison. | `web.py:591` |

---

## 2. Step 1: URL Cleanup

**Code:** Every check method starts with this:
```python
clean_url = url.lower().replace("https://", "").replace("http://", "").rstrip("/")
```

**What happens:**
- Lowercased: `Reddit.com` → `reddit.com`
- Protocol stripped: `https://reddit.com` → `reddit.com`
- Trailing slash removed: `reddit.com/` → `reddit.com`

**Important:** The `clean_url` is used for custom category matching. The original `url` (unchanged) is sent to Zscaler's `urlLookup` API.

---

## 3. Step 2: URL Classification (Zscaler Lookup)

**Code:** `zscaler_client.py:524-528` (`lookup_url`)

```python
resp = self.session.post(f"{self.base_url}/api/v1/urlLookup", json=[url])
```

**What happens:**
- Sends the URL to Zscaler's cloud classification engine
- Returns two sets of categories:
  - `urlClassifications` — standard content categories (e.g., `SOCIAL_NETWORKING`, `STREAMING_MEDIA`)
  - `urlClassificationsWithSecurityAlert` — security-flagged categories (e.g., `MALWARE`, `PHISHING`)
- Both sets are **merged** into a single `categories` set

**Troubleshooting:** If Zscaler classifies a URL differently than expected, the issue is upstream in Zscaler's database — not in this tool's logic.

---

## 4. Step 3: Custom Category Matching

**Code:** `zscaler_client.py:318-330` (`_url_in_category`)

After the Zscaler lookup, **all custom URL categories** are scanned to see if the URL matches any manually-added URL lists.

**Matching rules for custom categories:**

| Pattern in Category | URL Being Checked | Match? | Why |
|---------------------|-------------------|--------|-----|
| `.reddit.com` | `reddit.com` | Yes | Dot-prefix: matches base domain |
| `.reddit.com` | `www.reddit.com` | Yes | Dot-prefix: `www.reddit.com` ends with `.reddit.com` |
| `.reddit.com` | `old.reddit.com/r/test` | Yes | Dot-prefix: `old.reddit.com` ends with `.reddit.com` |
| `.reddit.com` | `notreallyreddit.com` | No | Doesn't end with `.reddit.com` |
| `reddit.com` | `reddit.com` | Yes | Exact match |
| `reddit.com` | `reddit.com/r/test` | Yes | URL starts with `reddit.com/` |
| `reddit.com` | `www.reddit.com` | No | No dot-prefix, so `www.` doesn't match |
| `docs.google.com/forms` | `docs.google.com/forms/d/abc` | Yes | URL starts with pattern + `/` |
| `docs.google.com/forms` | `docs.google.com/spreadsheets` | No | Doesn't match |

**Fields checked:** Both `urls` and `dbCategorizedUrls` arrays within each category object.

**Key insight:** If a custom category adds a URL, it effectively **adds that category ID** to the URL's classification set — just as if Zscaler's cloud had classified it that way. This merged set is what's used for rule matching in the next step.

---

## 5. Step 4: Rule Filtering (Category Match)

**Code:** `zscaler_client.py:569-577` (in `check_url_full`) and similar blocks in other methods

All **enabled** URL filtering rules are checked to see if they apply to this URL's categories:

```
For each rule:
  1. Skip if rule.state != "ENABLED"
  2. Get rule's urlCategories list
  3. If urlCategories is empty OR contains "ANY" → rule matches (applies to all categories)
  4. Otherwise → rule matches only if ANY of the URL's categories appear in the rule's urlCategories
```

**Result:** A list of `matching_rules` — all enabled rules whose category scope includes this URL.

**Important:** At this stage, we've only matched on **categories**. We haven't yet checked **who** the rule applies to (groups, locations, etc.). That's next.

---

## 6. Step 5: Scope Evaluation

This is where the three check methods **diverge significantly**.

### 6a. Global Check (`check_url_full`) — Broad vs. Narrow

**Code:** `zscaler_client.py:579-600`

For the global check (no specific user/group), rules are classified as:

| Rule Scope | Treated As | Reason |
|------------|-----------|--------|
| No scope at all (unscoped) | **Broad** — counts toward verdict | Applies to everyone |
| Location groups or locations only | **Broad** — counts toward verdict | Location-based = effectively district-wide |
| User groups or departments | **Narrow** — skipped for verdict | Only applies to specific users |
| User groups + locations | **Narrow** — skipped for verdict | Has user-group restriction |

**Verdict logic:** Only BLOCK/CAUTION actions from **broad** rules set the verdict. The first matching broad rule (by `order`) wins.

**All matching rules** (both broad and narrow) are still included in the `rules` display list for transparency.

### 6b. User/Group Check (`check_url_for_user`) — Two-Pass System

**Code:** `zscaler_client.py:177-259`

This is the most complex path. Rules are evaluated against the user's specific groups and department.

#### Pass 1: Classify each matching rule

Each rule gets a **match type**:

| Condition | Match Type | Meaning |
|-----------|-----------|---------|
| Rule has no scope at all | `MATCH_DIRECT` | Unscoped = applies to everyone including this user |
| Rule's `groups` overlap with user's groups | `MATCH_DIRECT` | Rule explicitly targets this user's group |
| Rule's `departments` includes user's department | `MATCH_DIRECT` | Rule explicitly targets this user's department |
| Rule scoped to locations only (no groups/depts), and name doesn't contain "byod" or "wireless" | `MATCH_LOCATION` | Location-scoped — might apply, can't confirm |
| Rule scoped to locations only, name contains "byod" or "wireless" | **Skipped entirely** | BYOD/wireless rules don't reflect student filtering |
| Rule scoped to groups/depts that don't match this user | **Skipped entirely** | Doesn't apply to this user |

#### Pass 2: Determine verdict (two-pass priority)

**First pass — Direct rules only** (sorted by `order`):
```
For each DIRECT rule (lowest order first):
  ALLOW → verdict = ALLOWED, stop
  BLOCK → verdict = BLOCKED, stop
  CAUTION → verdict = CAUTION, stop
```

**Second pass — Location rules only** (only if no direct rule gave a verdict):
```
For each LOCATION rule (lowest order first):
  BLOCK → verdict = BLOCKED, stop
  CAUTION → verdict = CAUTION, stop
  (ALLOW from location rules is ignored — can't confirm user is at that location)
```

**Default:** If no rule matches at all, verdict = `ALLOWED`.

#### Display filtering

After the verdict is determined, the display rules list is filtered:
- If a **direct ALLOW** rule won the verdict, location-scoped BLOCK/CAUTION rules are **hidden** from the display (they don't actually apply to this user, showing them would be confusing)

### 6c. Scope Matching for School Levels (`_rule_matches_scope`)

**Code:** `zscaler_client.py:782-813`

Used by `check_url` and `get_policy_overview` for per-school-level evaluation.

```
If rule has NO scope at all → matches everyone (return True)
If rule scopes on a DIFFERENT entity type than what we're checking → return True (conservative)
If rule scopes on the SAME entity type → match only if IDs overlap
```

**The `also_in_groups` factor:** When checking for a school level (e.g., "Elementary"), the scope IDs include:
- The level's own group IDs (from `school_mapping[level].ids`)
- Any additional group IDs from `school_mapping[level].also_in_groups` (discovered via the membership discovery feature — e.g., Elementary students are also in `ActiveStudents`)

---

## 7. Step 6: Verdict Determination — Summary

| Check Type | Default Verdict | What Sets BLOCKED | What Sets ALLOWED | What Sets CAUTION |
|-----------|----------------|-------------------|-------------------|-------------------|
| Global (`check_url_full`) | ALLOWED | First broad BLOCK rule (by order) | No broad block/caution found | First broad CAUTION rule (by order) |
| User/Group (`check_url_for_user`) | ALLOWED | First direct BLOCK, or first location BLOCK if no direct rules | First direct ALLOW rule (by order) | First direct/location CAUTION |
| Per-level (`check_url`) | Not blocked/cautioned | Any matching rule with BLOCK action | (No explicit allow tracking) | Any matching rule with CAUTION action |

### Rule Order Matters

Rules are always sorted by their `order` field (ascending). **Lower order = higher priority.** The first matching rule wins — later rules with conflicting actions are ignored for the verdict.

---

## 8. Diagnose Mode (Full Troubleshooting)

**Code:** `web.py:591-747` (`api_diagnose`)

The diagnose endpoint does **three things**:

### 8a. HTTP Reachability Check

**Code:** `web.py:34-66` (`diagnose_url`)

Makes an actual HTTP GET request to the URL from the server:

| Result | Meaning |
|--------|---------|
| `reachable: true` | Site responded (may include redirects) |
| `ssl_error: true` | SSL certificate error — often caused by Zscaler SSL inspection on the server itself |
| `reachable: false` + connection error | Site is down or unreachable |
| `reachable: false` + timeout | Site is slow or unreachable |

**Important caveat:** SSL errors from this server are usually caused by Zscaler's own SSL inspection certificate and do **not** reflect the student experience. The verdict logic accounts for this — if Zscaler says ALLOWED but there's an SSL error, it still reports "allowed."

### 8b. Zscaler Policy Check

Uses either `check_url_for_user()` (if a group is selected) or `check_url_full()` (if "All" is selected).

When checking for a specific group, a **synthetic user** is constructed:
```python
groups = [
    {"id": selected_group_id, "name": group_name},
    # + also_in_groups from school mapping
    # + ActiveStudents (always added for student groups)
]
```

### 8c. Cross-Group Comparison (if blocked/cautioned)

If the URL is blocked or cautioned for the selected group, the diagnose endpoint checks **all other student groups** to find which ones **do** have access:

**Groups checked (hardcoded list):**
- Elementary School Students
- Primary School Students
- Middle School Restrict
- Middle School Students
- High School Students
- Advanced Students
- High School Restrict
- StudentRestrict
- Locked

For each group, it builds the same synthetic user (with `also_in_groups` + ActiveStudents) and runs `check_url_for_user()`. Groups with an `ALLOWED` verdict are returned in `groups_with_access`.

### 8d. Plain-Language Verdict

The final verdict combines Zscaler + reachability:

| Zscaler Says | Site Reachable | SSL Error | Final Verdict |
|-------------|---------------|-----------|---------------|
| BLOCKED | any | any | "blocked for [group]" |
| CAUTION | any | any | "shows a warning page" |
| ALLOWED | yes | any | "allowed, students should be able to access it" |
| ALLOWED | no (not SSL) | no | "allowed but site appears down" |
| ALLOWED | no | yes | "allowed" (SSL error from server-side inspection, ignored) |
| (error) | no | yes | "security certificate issue" |
| (error) | no | no | "site appears to be down" |
| (error) | yes | no | "allowed and working" |

---

## 9. Policy Overview Logic

**Code:** `zscaler_client.py:427-522` (`get_policy_overview`)

Builds the category-by-school-level matrix shown on the Policy tab.

### How it works:

1. For each **enabled rule** with a BLOCK/CAUTION/ALLOW action:
2. For each **school level** in the mapping:
   - Check if the rule's scope matches this level (via `_rule_matches_scope`)
   - If the rule has specific `urlCategories`, apply to those; if `ANY` or empty, apply to **all** known categories
3. Per category per level, keep only the **highest-priority rule** (lowest `order` number)
4. Only include categories that have at least one BLOCK or CAUTION across levels
5. Sort: most-blocked categories first, then alphabetically

---

## 10. Common Troubleshooting Scenarios

### "The tool says ALLOWED but the student can't access it"

**Check:**
1. Is the student in additional groups not accounted for? Run "Discover Memberships" on the setup page to update `also_in_groups`
2. Is there a location-specific rule? The tool can't know which location the student is at. Check the `rules` list in the response for location-scoped blocks
3. Is the site actually down? Use the diagnose endpoint — check `reachability`
4. Is there a device-level restriction (MDM, DNS, etc.) outside Zscaler?

### "The tool says BLOCKED but the student can access it"

**Check:**
1. Is the blocking rule location-scoped? The student may not be at that location
2. Is there an ALLOW rule with lower order (higher priority) that the tool is missing? Check if the student belongs to a group with an explicit allow
3. Is the student using a different network/device that bypasses Zscaler?

### "Different results for group check vs. user check"

**Why this happens:**
- **Group check** builds a synthetic user with: `[selected_group] + [also_in_groups] + [ActiveStudents]`
- **User check** uses the user's **actual** group memberships from Zscaler
- If the user belongs to extra groups not in `also_in_groups`, the user check may match different rules

**Fix:** Run "Discover Memberships" to refresh the `also_in_groups` data.

### "Custom category URL isn't being detected"

**Check the pattern matching:**
- `.example.com` matches `example.com`, `www.example.com`, `sub.example.com`
- `example.com` matches only `example.com` and `example.com/path` — NOT `www.example.com`
- If you need to match `www.example.com`, use `.example.com` (dot-prefix) in the custom category

### "BYOD/wireless rules showing unexpected results"

In `check_url_for_user()`, rules with "byod" or "wireless" in the name that are **location-only scoped** (no group/dept scope) are **skipped entirely**. This is intentional — these rules apply to network segments, not student groups.

### "Rule order seems wrong"

Rules are processed in `order` field ascending. If two rules conflict:
- The one with the **lower order number** wins
- For user checks: direct-match rules (group/dept/unscoped) are checked **before** location-scoped rules, regardless of order number

### "Policy overview shows different results than URL check"

The policy overview uses `_rule_matches_scope()` which is **more conservative** than the user-check logic:
- If a rule is scoped to an entity type different from the school level's type, it's treated as matching (conservative = assume it applies)
- The URL check for a specific user is more precise because it checks exact group membership

---

## Appendix: Data Flow Diagram

```
User enters URL
        │
        ▼
   ┌─────────────┐
   │ URL Cleanup  │  lowercase, strip protocol/slash
   └──────┬──────┘
          │
          ▼
   ┌──────────────────┐
   │ Zscaler urlLookup │  → standard categories + security categories
   └──────┬───────────┘
          │
          ▼
   ┌────────────────────────┐
   │ Custom Category Scan    │  → additional category IDs from URL pattern matching
   └──────┬─────────────────┘
          │
          ▼
   ┌──────────────────────────┐
   │ Merge All Categories      │  (standard ∪ security ∪ custom)
   └──────┬───────────────────┘
          │
          ▼
   ┌──────────────────────────────┐
   │ Filter Rules by Category      │  Keep rules where urlCategories overlaps
   └──────┬───────────────────────┘
          │
          ├── Global check ──────────────────┐
          │                                   ▼
          │                    ┌──────────────────────────────┐
          │                    │ Broad rules only (unscoped    │
          │                    │ + location-scoped) → verdict  │
          │                    └──────────────────────────────┘
          │
          └── User/Group check ──────────────┐
                                              ▼
                               ┌──────────────────────────────┐
                               │ Classify: DIRECT vs LOCATION  │
                               │ Skip: BYOD/wireless, no-match │
                               └──────┬───────────────────────┘
                                      │
                                      ▼
                               ┌──────────────────────────────┐
                               │ Pass 1: Direct rules → verdict│
                               │ Pass 2: Location rules        │
                               │         (if no direct match)  │
                               └──────────────────────────────┘
```
