"""
Zscaler ZIA API Client — K-12 URL Checker
"""
import time
import requests
import json
import os
from typing import Optional


class ZscalerClient:
    def __init__(self, config: dict):
        self.base_url = config["base_url"].rstrip("/")
        self.auth_method = config.get("auth_method", "api_key")
        self.session = requests.Session()
        self.authenticated = False
        self._url_categories_cache = None
        self._url_filtering_rules_cache = None

        if self.auth_method == "oauth":
            self.identity_url = config["identity_url"].rstrip("/")
            self.client_id = config["client_id"]
            self.client_secret = config["client_secret"]
        else:
            self.username = config["username"]
            self.password = config["password"]
            self.api_key = config["api_key"]

    # ── Auth ────────────────────────────────────────────────────────

    def authenticate(self):
        if self.auth_method == "oauth":
            resp = requests.post(
                f"{self.identity_url}/oauth2/v1/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
            resp.raise_for_status()
            self.session.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
        else:
            now = str(int(time.time() * 1000))
            n = now[-6:]
            r = str(int(n) >> 1).zfill(6)
            key = ""
            for i in range(len(n)):
                key += self.api_key[int(n[i])]
            for i in range(len(r)):
                key += self.api_key[int(r[i]) + 2]
            payload = {
                "apiKey": key,
                "username": self.username,
                "password": self.password,
                "timestamp": now,
            }
            self.session.post(
                f"{self.base_url}/api/v1/authenticatedSession", json=payload
            ).raise_for_status()
        self.authenticated = True

    def logout(self):
        if self.auth_method == "api_key" and self.authenticated:
            try:
                self.session.delete(f"{self.base_url}/api/v1/authenticatedSession")
            except Exception:
                pass
        self.authenticated = False

    # ── Discovery endpoints ─────────────────────────────────────────

    def get_location_groups(self) -> list:
        resp = self.session.get(f"{self.base_url}/api/v1/locations/groups")
        resp.raise_for_status()
        return resp.json()

    def get_locations(self) -> list:
        resp = self.session.get(f"{self.base_url}/api/v1/locations")
        resp.raise_for_status()
        data = resp.json()
        # ZIA may wrap in a list or object
        if isinstance(data, dict):
            return data.get("locations", data.get("list", []))
        return data

    def get_groups(self) -> list:
        resp = self.session.get(f"{self.base_url}/api/v1/groups")
        resp.raise_for_status()
        return resp.json()

    def get_departments(self) -> list:
        resp = self.session.get(f"{self.base_url}/api/v1/departments")
        resp.raise_for_status()
        return resp.json()

    def get_users_in_group(self, group_id: int, page_size: int = 1) -> list:
        """Get users belonging to a specific group. Returns a list of user dicts."""
        resp = self.session.get(
            f"{self.base_url}/api/v1/users",
            params={"groupId": group_id, "page": 1, "pageSize": page_size},
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else data.get("list", [])

    def lookup_user(self, email: str) -> Optional[dict]:
        """Look up a user by email. Returns user dict with groups, or None."""
        # Try exact name match first (most reliable)
        resp = self.session.get(
            f"{self.base_url}/api/v1/users",
            params={"name": email, "page": 1, "pageSize": 5},
        )
        resp.raise_for_status()
        data = resp.json()
        users = data if isinstance(data, list) else data.get("list", [])
        for u in users:
            if u.get("email", "").lower() == email.lower():
                return u
            if u.get("name", "").lower() == email.lower():
                return u
        # Fallback: broader search
        resp = self.session.get(
            f"{self.base_url}/api/v1/users",
            params={"search": email, "page": 1, "pageSize": 10},
        )
        resp.raise_for_status()
        data = resp.json()
        users = data if isinstance(data, list) else data.get("list", [])
        for u in users:
            if u.get("email", "").lower() == email.lower():
                return u
        return None

    def check_url_for_user(self, url: str, user: dict) -> dict:
        """
        Check a URL against filtering rules for a specific user's groups.

        Returns same structure as check_url_full, plus user info.
        """
        clean_url = url.lower().replace("https://", "").replace("http://", "").rstrip("/")
        user_groups = {g["id"] for g in user.get("groups", [])}
        user_dept = user.get("department", {})
        user_dept_id = user_dept.get("id") if user_dept else None

        # 1. URL classification
        lookup = self.lookup_url(url)
        standard_cats = set(lookup.get("urlClassifications", []))
        security_cats = set(lookup.get("urlClassificationsWithSecurityAlert", []))
        categories = standard_cats | security_cats

        # 2. Custom categories
        all_cats = self.get_url_categories()
        cat_map = {}
        for c in all_cats:
            cid = c.get("id")
            cname = c.get("configuredName") or str(cid)
            cname = str(cname).replace("_", " ").replace("  ", " ")
            cat_map[cid] = cname
            if self._url_in_category(clean_url, c):
                categories.add(cid)

        # 3. Get rules
        url_rules = self.get_url_filtering_rules()

        # 4. Find rules matching categories
        matching_rules = []
        for r in url_rules:
            if not isinstance(r, dict) or r.get("state") != "ENABLED":
                continue
            rule_cats = r.get("urlCategories", [])
            if rule_cats and "ANY" not in rule_cats:
                if not any(c in rule_cats for c in categories):
                    continue
            matching_rules.append(r)

        # 5. Filter to rules that apply to this user.
        #    Track match reason so the verdict logic knows which ALLOWs to trust.
        MATCH_DIRECT = "direct"     # unscoped, or matched user's group/dept
        MATCH_LOCATION = "location" # location-only scope (can't confirm user is there)
        user_rules = []
        rule_match_type = {}  # rule id(r) -> match type
        for r in matching_rules:
            has_any_scope = any(
                r.get(k) for k in ["locationGroups", "locations", "groups", "departments"]
            )
            if not has_any_scope:
                # Unscoped = applies to everyone
                user_rules.append(r)
                rule_match_type[id(r)] = MATCH_DIRECT
                continue
            # Check group match
            rule_groups = {g.get("id") for g in (r.get("groups") or [])}
            if rule_groups & user_groups:
                user_rules.append(r)
                rule_match_type[id(r)] = MATCH_DIRECT
                continue
            # Check department match
            if user_dept_id:
                rule_depts = {d.get("id") for d in (r.get("departments") or [])}
                if user_dept_id in rule_depts:
                    user_rules.append(r)
                    rule_match_type[id(r)] = MATCH_DIRECT
                    continue
            # Location-scoped rules: include for display but skip BYOD/wireless
            # rules that only apply to specific network segments, not students
            if r.get("locationGroups") or r.get("locations"):
                if not r.get("groups") and not r.get("departments"):
                    # Skip BYOD/wireless-only rules — they don't reflect student filtering
                    rname = (r.get("name") or "").lower()
                    if "byod" in rname or "wireless" in rname:
                        continue
                    user_rules.append(r)
                    rule_match_type[id(r)] = MATCH_LOCATION

        # 6. Determine verdict from user's rules.
        #    Two-pass approach:
        #    - First, check only DIRECT rules (group/dept match or unscoped).
        #      These are definitive — they target this user specifically.
        #    - If no direct rule gives a verdict, fall back to location-scoped
        #      rules (conservative: assume user could be at any location).
        verdict = "ALLOWED"
        verdict_rule = None
        sorted_rules = sorted(user_rules, key=lambda x: x.get("order", 9999))

        # Pass 1: direct rules only
        for r in sorted_rules:
            match_type = rule_match_type.get(id(r), MATCH_LOCATION)
            if match_type != MATCH_DIRECT:
                continue
            action = r.get("action", "")
            if action == "ALLOW":
                verdict = "ALLOWED"
                verdict_rule = r.get("name", "")
                break
            elif action == "BLOCK":
                verdict = "BLOCKED"
                verdict_rule = r.get("name", "")
                break
            elif action == "CAUTION":
                verdict = "CAUTION"
                verdict_rule = r.get("name", "")
                break

        # Pass 2: if no direct rule matched, check location-scoped rules
        if not verdict_rule:
            for r in sorted_rules:
                match_type = rule_match_type.get(id(r), MATCH_LOCATION)
                if match_type != MATCH_LOCATION:
                    continue
                action = r.get("action", "")
                if action == "BLOCK":
                    verdict = "BLOCKED"
                    verdict_rule = r.get("name", "")
                    break
                elif action == "CAUTION":
                    verdict = "CAUTION"
                    verdict_rule = r.get("name", "")
                    break

        # 7. Build display — only show rules relevant to the verdict.
        #    If ALLOWED by a direct rule, location-scoped blocks (like Global
        #    Block Rule) don't actually apply — omit them to avoid confusion.
        has_direct_allow = verdict == "ALLOWED" and verdict_rule
        display_rules = []
        for r in sorted(user_rules, key=lambda x: x.get("order", 9999)):
            action = r.get("action", "")
            match_type = rule_match_type.get(id(r), MATCH_LOCATION)
            if has_direct_allow and action in ("BLOCK", "CAUTION") and match_type == MATCH_LOCATION:
                continue  # This block doesn't actually apply — skip it
            display_rules.append({
                "name": r.get("name", "Unnamed"),
                "action": action,
                "order": r.get("order"),
                "scope": self._describe_scope(r),
                "categories": [cat_map.get(c, c) for c in r.get("urlCategories", []) if c in categories],
            })

        display_cats = sorted(set(
            str(cat_map.get(c, c)).replace("_", " ").replace("  ", " ")
            for c in categories
        ))

        group_names = [g.get("name", "") for g in user.get("groups", [])]

        return {
            "url": url,
            "categories": display_cats,
            "security_alert": bool(security_cats),
            "verdict": verdict,
            "verdict_rule": verdict_rule,
            "rules": display_rules,
            "user": {
                "name": user.get("name", ""),
                "email": user.get("email", ""),
                "groups": group_names,
            },
        }

    # ── URL Filtering ───────────────────────────────────────────────

    def get_url_filtering_rules(self) -> list:
        if self._url_filtering_rules_cache is not None:
            return self._url_filtering_rules_cache
        resp = self.session.get(f"{self.base_url}/api/v1/urlFilteringRules")
        resp.raise_for_status()
        self._url_filtering_rules_cache = resp.json()
        return self._url_filtering_rules_cache

    def get_url_categories(self) -> list:
        if self._url_categories_cache is not None:
            return self._url_categories_cache
        resp = self.session.get(f"{self.base_url}/api/v1/urlCategories")
        resp.raise_for_status()
        self._url_categories_cache = resp.json()
        return self._url_categories_cache

    def _url_in_category(self, clean_url: str, category: dict) -> bool:
        """Check if a URL matches a custom category's URL lists (urls + dbCategorizedUrls)."""
        for field in ("urls", "dbCategorizedUrls"):
            for u in category.get(field, []):
                if not isinstance(u, str):
                    continue
                u = u.lower()
                if u.startswith("."):
                    if clean_url.endswith(u) or clean_url == u[1:] or clean_url.startswith(u[1:] + "/"):
                        return True
                elif u == clean_url or clean_url.startswith(u + "/"):
                    return True
        return False

    def get_all_rules_display(self) -> list:
        """
        Return all enabled URL filtering rules in order, with human-readable details.
        """
        url_rules = self.get_url_filtering_rules()
        all_cats = self.get_url_categories()

        # Build category name map
        cat_map = {}
        for c in all_cats:
            cid = c.get("id")
            cname = c.get("configuredName") or str(cid)
            cname = str(cname).replace("_", " ").replace("  ", " ")
            cat_map[cid] = cname

        result = []
        for r in sorted(url_rules, key=lambda x: x.get("order", 9999)):
            if not isinstance(r, dict) or r.get("state") != "ENABLED":
                continue
            cats = r.get("urlCategories", [])
            cat_names = [cat_map.get(c, str(c).replace("_", " ").title()) for c in cats]
            result.append({
                "order": r.get("order"),
                "name": r.get("name", "Unnamed"),
                "action": r.get("action", ""),
                "scope": self._describe_scope(r),
                "categories": cat_names,
                "category_count": len(cats),
            })
        return result

    def compare_groups(self, group_a_id: int, group_b_id: int) -> dict:
        """
        Compare filtering rules between two groups.
        Returns rules that differ in how they apply to each group.
        """
        url_rules = self.get_url_filtering_rules()
        all_cats = self.get_url_categories()

        cat_map = {}
        for c in all_cats:
            cid = c.get("id")
            cname = c.get("configuredName") or str(cid)
            cname = str(cname).replace("_", " ").replace("  ", " ")
            cat_map[cid] = cname

        def rule_applies_to_group(rule, group_id):
            """Check if a rule applies to a specific group."""
            rule_groups = {g.get("id") for g in (rule.get("groups") or [])}
            has_any_scope = any(
                rule.get(k) for k in ["locationGroups", "locations", "groups", "departments"]
            )
            if not has_any_scope:
                return True  # Unscoped = everyone
            if group_id in rule_groups:
                return True
            # Location-scoped rules without group restrictions apply to everyone
            if (rule.get("locationGroups") or rule.get("locations")) and not rule.get("groups") and not rule.get("departments"):
                return True
            return False

        def format_rule(r):
            cats = r.get("urlCategories", [])
            return {
                "order": r.get("order"),
                "name": r.get("name", "Unnamed"),
                "action": r.get("action", ""),
                "scope": self._describe_scope(r),
                "categories": [cat_map.get(c, str(c).replace("_", " ").title()) for c in cats],
                "category_count": len(cats),
            }

        only_a = []
        only_b = []
        shared = []

        for r in sorted(url_rules, key=lambda x: x.get("order", 9999)):
            if not isinstance(r, dict) or r.get("state") != "ENABLED":
                continue
            applies_a = rule_applies_to_group(r, group_a_id)
            applies_b = rule_applies_to_group(r, group_b_id)

            if applies_a and applies_b:
                shared.append(format_rule(r))
            elif applies_a:
                only_a.append(format_rule(r))
            elif applies_b:
                only_b.append(format_rule(r))

        return {
            "only_a": only_a,
            "only_b": only_b,
            "shared": shared,
        }

    def get_policy_overview(self, school_mapping: dict) -> list:
        """
        Build a category-by-school-level policy matrix.

        Returns a list of dicts:
            [{"category": str, "levels": {"Elementary": "BLOCKED", ...}, "rule_name": str}, ...]
        """
        all_cats = self.get_url_categories()
        url_rules = self.get_url_filtering_rules()

        # Build category name map (keyed by both int ID and string ID)
        cat_map = {}
        for c in all_cats:
            cid = c.get("id")
            cname = c.get("configuredName") or str(cid)
            cname = str(cname).replace("_", " ").replace("  ", " ")
            cat_map[cid] = cname
            # Also map string-based identifiers (e.g. "ADULT_THEMES")
            # which is what urlCategories in rules use
            if isinstance(cid, str):
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
                # Include additional group memberships
                scope_ids |= set(level_config.get("also_in_groups", []))

                if not self._rule_matches_scope(r, scope_type, scope_ids):
                    continue

                # This rule applies to this level
                target_cats = rule_cats if rule_cats and "ANY" not in rule_cats else list(cat_map.keys())

                for cat_id in target_cats:
                    # Handle string category IDs not in cat_map — use a friendly name
                    if cat_id not in cat_map:
                        if isinstance(cat_id, str):
                            cat_map[cat_id] = cat_id.replace("_", " ").replace("  ", " ").title()
                        else:
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

    def lookup_url(self, url: str) -> dict:
        resp = self.session.post(f"{self.base_url}/api/v1/urlLookup", json=[url])
        resp.raise_for_status()
        data = resp.json()
        return data[0] if data else {}

    # ── Core ───────────────────────────────────────────────────────

    def check_url_full(self, url: str, school_mapping: dict = None) -> dict:
        """
        Comprehensive URL check. Works with or without school mapping.

        Returns:
            {
                "url": str,
                "categories": [str],
                "security_alert": bool,
                "verdict": "BLOCKED" | "CAUTION" | "ALLOWED",
                "verdict_rule": str | None,
                "rules": [{name, action, order, scope, categories}],
                "levels": {level: {blocked, cautioned, ...}} (only if mapping provided)
            }
        """
        clean_url = url.lower().replace("https://", "").replace("http://", "").rstrip("/")

        # 1. Zscaler's URL classification
        lookup = self.lookup_url(url)
        standard_cats = set(lookup.get("urlClassifications", []))
        security_cats = set(lookup.get("urlClassificationsWithSecurityAlert", []))
        categories = standard_cats | security_cats

        # 2. Scan custom categories for URL matches
        all_cats = self.get_url_categories()
        cat_map = {}
        for c in all_cats:
            cid = c.get("id")
            cname = c.get("configuredName") or cid
            cat_map[cid] = cname
            if self._url_in_category(clean_url, c):
                categories.add(cid)

        # 3. Get all URL filtering rules
        url_rules = self.get_url_filtering_rules()

        # 4. Find rules matching this URL's categories
        matching_rules = []
        for r in url_rules:
            if not isinstance(r, dict) or r.get("state") != "ENABLED":
                continue
            rule_cats = r.get("urlCategories", [])
            if rule_cats and "ANY" not in rule_cats:
                if not any(c in rule_cats for c in categories):
                    continue
            matching_rules.append(r)

        # 5. Determine global verdict.
        #    Rules scoped to specific user groups or departments (e.g. "NoINET Block"
        #    for User Groups: Locked) are narrow — they don't represent the general
        #    student experience. Skip those for the verdict.
        #    Rules scoped to location groups or locations ARE effectively district-wide
        #    (e.g. "Global Block Rule" for Locations_SSL covers everyone).
        #    Unscoped rules also count.
        verdict = "ALLOWED"
        verdict_rule = None
        for r in sorted(matching_rules, key=lambda x: x.get("order", 9999)):
            action = r.get("action", "")
            if action not in ("BLOCK", "CAUTION"):
                continue
            # Narrow scope = user groups or departments only (not location-based)
            has_narrow_scope = r.get("groups") or r.get("departments")
            has_broad_scope = r.get("locationGroups") or r.get("locations")
            is_unscoped = not has_narrow_scope and not has_broad_scope
            # Count if: unscoped, or location-scoped (broad), but NOT if only user group/dept scoped
            if is_unscoped or has_broad_scope:
                verdict = "BLOCKED" if action == "BLOCK" else "CAUTION"
                verdict_rule = r.get("name", "")
                break

        # 6. Build rules list for display
        display_rules = []
        for r in sorted(matching_rules, key=lambda x: x.get("order", 9999)):
            display_rules.append({
                "name": r.get("name", "Unnamed"),
                "action": r.get("action", ""),
                "order": r.get("order"),
                "scope": self._describe_scope(r),
                "categories": [cat_map.get(c, c) for c in r.get("urlCategories", []) if c in categories],
            })

        # 7. Friendly category names
        display_cats = sorted(set(
            (cat_map.get(c, c) if isinstance(cat_map.get(c, c), str) else str(c))
            .replace("_", " ").replace("  ", " ")
            for c in categories
        ))

        result = {
            "url": url,
            "categories": display_cats,
            "security_alert": bool(security_cats),
            "verdict": verdict,
            "verdict_rule": verdict_rule,
            "rules": display_rules,
        }

        # 8. Per-level breakdown (only if mapping provided)
        if school_mapping:
            levels = {}
            for level_name, level_config in school_mapping.items():
                if not level_config.get("ids"):
                    continue
                level_result = {
                    "blocked": False,
                    "cautioned": False,
                    "blocking_rule": "",
                    "caution_rule": "",
                    "rules": [],
                }
                scope_type = level_config.get("type", "")
                scope_ids = set(level_config.get("ids", []))
                # Include additional group memberships (e.g., Elementary students also in ActiveStudents)
                scope_ids |= set(level_config.get("also_in_groups", []))

                for r in sorted(matching_rules, key=lambda x: x.get("order", 9999)):
                    if self._rule_matches_scope(r, scope_type, scope_ids):
                        action = r.get("action", "")
                        rule_name = r.get("name", "Unnamed")
                        level_result["rules"].append({"name": rule_name, "action": action})
                        if action == "BLOCK" and not level_result["blocked"]:
                            level_result["blocked"] = True
                            level_result["blocking_rule"] = rule_name
                        elif action == "CAUTION" and not level_result["cautioned"]:
                            level_result["cautioned"] = True
                            level_result["caution_rule"] = rule_name

                levels[level_name] = level_result
            result["levels"] = levels

        return result

    def check_url(self, url: str, school_mapping: dict) -> dict:
        """
        Check a URL against Zscaler filtering rules for each school level.

        Returns:
            {
                "url": "...",
                "categories": ["Social Networking", ...],
                "security_alert": bool,
                "levels": {
                    "Elementary": {"blocked": bool, "cautioned": bool, "blocking_rule": str, "caution_rule": str},
                    ...
                },
                "all_matching_rules": [...]
            }
        """
        clean_url = url.lower().replace("https://", "").replace("http://", "").rstrip("/")

        # 1. Zscaler's URL classification
        lookup = self.lookup_url(url)
        standard_cats = set(lookup.get("urlClassifications", []))
        security_cats = set(lookup.get("urlClassificationsWithSecurityAlert", []))
        categories = standard_cats | security_cats

        # 2. Scan custom categories for URL matches
        all_cats = self.get_url_categories()
        cat_map = {}
        for c in all_cats:
            cid = c.get("id")
            cname = c.get("configuredName") or cid
            cat_map[cid] = cname
            if self._url_in_category(clean_url, c):
                categories.add(cid)

        # Also map standard category IDs
        for c in all_cats:
            cid = c.get("id")
            cname = c.get("configuredName") or cid
            if cid not in cat_map:
                cat_map[cid] = cname

        # 3. Get all URL filtering rules
        url_rules = self.get_url_filtering_rules()

        # 4. Find rules that match this URL's categories
        matching_rules = []
        for r in url_rules:
            if not isinstance(r, dict) or r.get("state") != "ENABLED":
                continue
            rule_cats = r.get("urlCategories", [])
            if rule_cats and "ANY" not in rule_cats:
                if not any(c in rule_cats for c in categories):
                    continue
            matching_rules.append(r)

        # 5. Evaluate per school level
        levels = {}
        for level_name, level_config in school_mapping.items():
            level_result = {
                "blocked": False,
                "cautioned": False,
                "blocking_rule": "",
                "caution_rule": "",
                "matching_rules": [],
            }

            scope_type = level_config.get("type", "")
            scope_ids = set(level_config.get("ids", []))
            # Include additional group memberships (e.g., Elementary students also in ActiveStudents)
            scope_ids |= set(level_config.get("also_in_groups", []))

            for r in matching_rules:
                if self._rule_matches_scope(r, scope_type, scope_ids):
                    action = r.get("action", "")
                    rule_name = r.get("name", "Unnamed Rule")
                    rule_info = {
                        "name": rule_name,
                        "action": action,
                        "order": r.get("order"),
                        "categories": [cat_map.get(c, c) for c in r.get("urlCategories", [])],
                    }
                    level_result["matching_rules"].append(rule_info)

                    if action == "BLOCK" and not level_result["blocked"]:
                        level_result["blocked"] = True
                        level_result["blocking_rule"] = rule_name
                    elif action == "CAUTION" and not level_result["cautioned"]:
                        level_result["cautioned"] = True
                        level_result["caution_rule"] = rule_name

            levels[level_name] = level_result

        # Friendly category names
        display_cats = []
        for c in categories:
            name = cat_map.get(c, c)
            if isinstance(name, str):
                display_cats.append(name.replace("_", " ").replace("  ", " "))
            else:
                display_cats.append(str(name))

        return {
            "url": url,
            "categories": sorted(display_cats),
            "security_alert": bool(security_cats),
            "levels": levels,
            "all_matching_rules": [
                {
                    "name": r.get("name"),
                    "action": r.get("action"),
                    "order": r.get("order"),
                    "scope": self._describe_scope(r),
                    "categories": [cat_map.get(c, c) for c in r.get("urlCategories", [])],
                }
                for r in matching_rules
            ],
        }

    def _rule_matches_scope(self, rule: dict, scope_type: str, scope_ids: set) -> bool:
        """Check if a rule applies to the given scope (location group, user group, etc.)."""
        # Rules with no scope apply to everyone
        has_any_scope = any(
            rule.get(k) for k in ["locationGroups", "locations", "groups", "departments"]
        )
        if not has_any_scope:
            return True

        type_to_field = {
            "location_groups": "locationGroups",
            "locations": "locations",
            "groups": "groups",
            "departments": "departments",
        }

        field = type_to_field.get(scope_type)
        if not field:
            return not has_any_scope

        rule_entities = rule.get(field, [])
        if not rule_entities:
            # This rule scopes on a different entity type — check if it's global on our type
            # If the rule has scopes but not for our type, it still might apply
            # Conservative: if rule doesn't mention our scope type at all, it applies
            other_fields = [v for k, v in type_to_field.items() if k != scope_type]
            if any(rule.get(f) for f in other_fields):
                return True  # Scoped on something else, but not excluding us
            return True

        rule_ids = {e.get("id") for e in rule_entities}
        return bool(rule_ids & scope_ids)

    def _describe_scope(self, rule: dict) -> str:
        """Human-readable scope description for a rule."""
        parts = []
        if rule.get("locationGroups"):
            names = [g["name"] for g in rule["locationGroups"] if "name" in g]
            if names:
                parts.append(f"Location Groups: {', '.join(names)}")
        if rule.get("locations"):
            names = [l["name"] for l in rule["locations"] if "name" in l]
            if names:
                parts.append(f"Locations: {', '.join(names)}")
        if rule.get("groups"):
            names = [g["name"] for g in rule["groups"] if "name" in g]
            if names:
                parts.append(f"User Groups: {', '.join(names)}")
        if rule.get("departments"):
            names = [d["name"] for d in rule["departments"] if "name" in d]
            if names:
                parts.append(f"Departments: {', '.join(names)}")
        return " | ".join(parts) if parts else "Everyone (Global)"


class ConfigManager:
    CONFIG_FILE = "zscaler_checker_config.json"

    @classmethod
    def load(cls):
        if os.path.exists(cls.CONFIG_FILE):
            with open(cls.CONFIG_FILE, "r") as f:
                return json.load(f)
        return None

    @classmethod
    def save(cls, config: dict):
        with open(cls.CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
