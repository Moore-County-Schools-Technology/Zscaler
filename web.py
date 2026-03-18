#!/usr/bin/env python3
"""
Zscaler URL Checker — Web Interface for Teachers
"""
import argparse
import time
import requests as ext_requests
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from zscaler_client import ZscalerClient, ConfigManager
from auth import auth_bp, init_auth, login_required, admin_required, policy_required

app = Flask(__name__)
app.config["PREFERRED_URL_SCHEME"] = "https"

from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

app.register_blueprint(auth_bp)
init_auth(app)


def get_client():
    config = ConfigManager.load()
    if not config:
        return None, "Not configured. Complete setup first."
    try:
        client = ZscalerClient(config)
        client.authenticate()
        return client, None
    except Exception as e:
        return None, str(e)


def diagnose_url(url: str) -> dict:
    """
    Check if a URL is reachable from this server.
    Returns reachability info independent of Zscaler filtering.
    """
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
    except ext_requests.exceptions.SSLError:
        result["ssl_error"] = True
        result["error_message"] = "SSL certificate error — this may be caused by Zscaler SSL inspection"
    except ext_requests.exceptions.ConnectionError:
        result["error_message"] = "Could not connect to the site — it may be down"
    except ext_requests.exceptions.Timeout:
        result["error_message"] = "Connection timed out — the site may be slow or down"
    except ext_requests.exceptions.RequestException as e:
        result["error_message"] = f"Request failed: {str(e)}"

    return result


# ── Pages ───────────────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    config = ConfigManager.load()
    if not config:
        return redirect(url_for("setup_page"))
    return render_template("index.html")


@app.route("/setup")
@admin_required
def setup_page():
    return render_template("setup.html")


# ── API: Connection & Config ────────────────────────────────────────

@app.route("/api/config-status")
@login_required
def api_config_status():
    config = ConfigManager.load()
    if not config:
        return jsonify({"configured": False})
    return jsonify({
        "configured": True,
        "has_mapping": bool(config.get("school_mapping")),
        "discovered": config.get("discovered"),
        "school_mapping": config.get("school_mapping"),
    })


@app.route("/api/test-connection", methods=["POST"])
@admin_required
def api_test_connection():
    data = request.json
    try:
        client = ZscalerClient(data)
        client.authenticate()
        client.logout()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/save-config", methods=["POST"])
@admin_required
def api_save_config():
    data = request.json
    # Preserve existing discovered data and mappings
    existing = ConfigManager.load() or {}
    for key in ["discovered", "school_mapping"]:
        if key in existing and key not in data:
            data[key] = existing[key]
    ConfigManager.save(data)
    return jsonify({"success": True})


# ── API: Discovery ──────────────────────────────────────────────────

@app.route("/api/discover", methods=["POST"])
@admin_required
def api_discover():
    client, err = get_client()
    if not client:
        return jsonify({"error": err}), 400

    discovered = {}
    errors = []

    try:
        loc_groups = client.get_location_groups()
        discovered["location_groups"] = [
            {"id": g["id"], "name": g["name"]} for g in loc_groups
        ]
    except Exception as e:
        discovered["location_groups"] = []
        errors.append(f"Location groups: {e}")

    time.sleep(1)

    try:
        locations = client.get_locations()
        discovered["locations"] = [
            {"id": l["id"], "name": l["name"]} for l in locations
        ]
    except Exception as e:
        discovered["locations"] = []
        errors.append(f"Locations: {e}")

    time.sleep(1)

    try:
        groups = client.get_groups()
        discovered["groups"] = [
            {"id": g["id"], "name": g["name"]} for g in groups
        ]
    except Exception as e:
        discovered["groups"] = []
        errors.append(f"User groups: {e}")

    time.sleep(1)

    try:
        depts = client.get_departments()
        discovered["departments"] = [
            {"id": d["id"], "name": d["name"]} for d in depts
        ]
    except Exception as e:
        discovered["departments"] = []
        errors.append(f"Departments: {e}")

    client.logout()

    # Save to config
    config = ConfigManager.load() or {}
    config["discovered"] = discovered
    ConfigManager.save(config)

    return jsonify({
        "discovered": discovered,
        "errors": errors,
    })


@app.route("/api/auto-map", methods=["POST"])
@admin_required
def api_auto_map():
    """Try to auto-detect school levels from entity names."""
    config = ConfigManager.load()
    if not config or not config.get("discovered"):
        return jsonify({"error": "Run discovery first"}), 400

    discovered = config["discovered"]
    mapping = {
        "Elementary": {"type": "", "ids": []},
        "Middle School": {"type": "", "ids": []},
        "High School": {"type": "", "ids": []},
    }

    keywords = {
        "Elementary": ["elementary", "elem", "primary", "es ", "es-"],
        "Middle School": ["middle", "ms ", "ms-", "junior", "intermediate"],
        "High School": ["high", "hs ", "hs-", "senior", "secondary"],
    }

    count = 0
    # Try each entity type, prefer location_groups first
    for entity_type in ["location_groups", "locations", "groups", "departments"]:
        entities = discovered.get(entity_type, [])
        for entity in entities:
            name_lower = entity["name"].lower()
            for level, kws in keywords.items():
                if any(kw in name_lower for kw in kws):
                    mapping[level]["type"] = entity_type
                    if entity["id"] not in mapping[level]["ids"]:
                        mapping[level]["ids"].append(entity["id"])
                        count += 1

    return jsonify({"mapping": mapping, "count": count})


@app.route("/api/save-mapping", methods=["POST"])
@admin_required
def api_save_mapping():
    data = request.json
    config = ConfigManager.load() or {}
    config["school_mapping"] = data.get("school_mapping", {})
    ConfigManager.save(config)
    return jsonify({"success": True})


@app.route("/api/discover-memberships", methods=["POST"])
@admin_required
def api_discover_memberships():
    """
    For each school-mapped group, find a sample user and discover what other
    groups they belong to. Stores the additional group IDs as 'also_in_groups'
    in the school mapping so rule evaluation accounts for shared group memberships
    (e.g., Elementary students who are also in ActiveStudents).
    """
    config = ConfigManager.load()
    if not config or not config.get("school_mapping"):
        return jsonify({"error": "No school mapping configured"}), 400

    client, err = get_client()
    if not client:
        return jsonify({"error": err}), 400

    mapping = config["school_mapping"]
    results = {}

    try:
        for level_name, level_config in mapping.items():
            if level_config.get("type") != "groups" or not level_config.get("ids"):
                continue

            level_group_ids = set(level_config["ids"])
            discovered_groups = []

            for group_id in level_config["ids"]:
                users = client.get_users_in_group(group_id, page_size=5)
                time.sleep(1)
                if not users:
                    continue

                # Find a user who actually belongs to this specific group
                user = None
                for u in users:
                    u_group_ids = {g.get("id") for g in u.get("groups", [])}
                    if group_id in u_group_ids:
                        user = u
                        break
                if not user:
                    continue

                user_groups = user.get("groups", [])
                for g in user_groups:
                    gid = g.get("id")
                    if gid and gid not in level_group_ids:
                        discovered_groups.append({"id": gid, "name": g.get("name", "")})

                break  # One sample user is enough

            # Deduplicate
            seen = set()
            unique_groups = []
            for g in discovered_groups:
                if g["id"] not in seen:
                    seen.add(g["id"])
                    unique_groups.append(g)

            level_config["also_in_groups"] = [g["id"] for g in unique_groups]
            results[level_name] = unique_groups

        client.logout()
    except Exception as e:
        try:
            client.logout()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500

    # Save updated mapping
    config["school_mapping"] = mapping
    ConfigManager.save(config)

    return jsonify({"success": True, "memberships": results})


# ── API: URL Checking ───────────────────────────────────────────────

@app.route("/api/check", methods=["POST"])
@login_required
def api_check():
    """Check a single URL — works with or without school mapping."""
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    config = ConfigManager.load()
    if not config:
        return jsonify({"error": "Not configured. Go to /setup first."}), 400

    client, err = get_client()
    if not client:
        return jsonify({"error": err}), 400

    try:
        result = client.check_url_full(url)
        client.logout()
        return jsonify(result)
    except Exception as e:
        try:
            client.logout()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500


@app.route("/api/check-user", methods=["POST"])
@login_required
def api_check_user():
    """Check a URL for a specific user by email."""
    data = request.json
    url = data.get("url", "").strip()
    email = data.get("email", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    if not email:
        return jsonify({"error": "No email provided"}), 400

    client, err = get_client()
    if not client:
        return jsonify({"error": err}), 400

    try:
        user = client.lookup_user(email)
        if not user:
            client.logout()
            return jsonify({"error": f"User not found: {email}"}), 404

        result = client.check_url_for_user(url, user)
        client.logout()
        return jsonify(result)
    except Exception as e:
        try:
            client.logout()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500


@app.route("/api/check-group", methods=["POST"])
@login_required
def api_check_group():
    """Check a URL for a specific group by ID."""
    data = request.json
    url = data.get("url", "").strip()
    group_id = data.get("group_id")
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    if not group_id:
        return jsonify({"error": "No group ID provided"}), 400

    client, err = get_client()
    if not client:
        return jsonify({"error": err}), 400

    try:
        # Build a fake user with this group + any also_in_groups from school mapping
        gid = int(group_id)
        groups = [{"id": gid, "name": data.get("group_name", "")}]

        # Look up additional group memberships from school mapping
        config = ConfigManager.load() or {}
        mapping = config.get("school_mapping", {})
        discovered = config.get("discovered", {})
        discovered_groups = {g["id"]: g["name"] for g in discovered.get("groups", [])}
        for level_config in mapping.values():
            if gid in (level_config.get("ids") or []):
                for also_id in level_config.get("also_in_groups", []):
                    if also_id != gid:
                        groups.append({"id": also_id, "name": discovered_groups.get(also_id, "")})
                break

        # Always include ActiveStudents — all students belong to it
        for dg in discovered.get("groups", []):
            if dg.get("name") == "ActiveStudents" and dg["id"] != gid:
                if not any(g["id"] == dg["id"] for g in groups):
                    groups.append({"id": dg["id"], "name": dg["name"]})
                break

        fake_user = {
            "name": "",
            "email": "",
            "groups": groups,
        }
        result = client.check_url_for_user(url, fake_user)
        # Override user display with group info
        result["user"] = {
            "name": data.get("group_name", ""),
            "email": "",
            "groups": [data.get("group_name", "")],
        }
        client.logout()
        return jsonify(result)
    except Exception as e:
        try:
            client.logout()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500


@app.route("/api/check-batch", methods=["POST"])
@login_required
def api_check_batch():
    """Check multiple URLs. Expects {"urls": ["url1", "url2", ...]}."""
    data = request.json
    urls = data.get("urls", [])
    if not urls:
        return jsonify({"error": "No URLs provided"}), 400

    config = ConfigManager.load()
    if not config:
        return jsonify({"error": "Not configured"}), 400

    client, err = get_client()
    if not client:
        return jsonify({"error": err}), 400

    results = []
    for url in urls[:50]:
        url = url.strip()
        if not url:
            continue
        try:
            result = client.check_url_full(url)
            results.append(result)
            time.sleep(0.3)
        except Exception as e:
            results.append({"url": url, "error": str(e), "categories": [], "rules": [], "verdict": "ERROR"})

    try:
        client.logout()
    except Exception:
        pass

    return jsonify({"results": results})


# ── API: Policy audit (comprehensive view) ──────────────────────────

@app.route("/api/policy-audit", methods=["POST"])
@policy_required
def api_policy_audit():
    """Full policy audit — shows all matching rules for a URL."""
    data = request.json
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    config = ConfigManager.load()
    if not config:
        return jsonify({"error": "Not configured"}), 400

    client, err = get_client()
    if not client:
        return jsonify({"error": err}), 400

    try:
        # If we have school mapping, use check_url for the full picture
        if config.get("school_mapping"):
            result = client.check_url(url, config["school_mapping"])
        else:
            # Fallback to basic policy comparison
            result = client.get_policy_comparison(url)
        client.logout()
        return jsonify(result)
    except Exception as e:
        try:
            client.logout()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500


@app.route("/api/policy-overview")
@policy_required
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


@app.route("/api/all-rules")
@policy_required
def api_all_rules():
    """Return all enabled URL filtering rules in order."""
    client, err = get_client()
    if not client:
        return jsonify({"error": err}), 400

    try:
        rules = client.get_all_rules_display()
        client.logout()
        return jsonify({"rules": rules})
    except Exception as e:
        try:
            client.logout()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500


@app.route("/api/compare-groups", methods=["POST"])
@policy_required
def api_compare_groups():
    """Compare filtering rules between two groups."""
    data = request.json
    group_a_id = data.get("group_a_id")
    group_b_id = data.get("group_b_id")
    if not group_a_id or not group_b_id:
        return jsonify({"error": "Two group IDs required"}), 400

    client, err = get_client()
    if not client:
        return jsonify({"error": err}), 400

    try:
        result = client.compare_groups(int(group_a_id), int(group_b_id))
        client.logout()
        return jsonify(result)
    except Exception as e:
        try:
            client.logout()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500


@app.route("/api/diagnose", methods=["POST"])
@login_required
def api_diagnose():
    """Diagnose a URL: is it Zscaler blocking it, or is the site actually down?"""
    data = request.json
    url = data.get("url", "").strip()
    group_id = data.get("group_id", "")
    group_name = data.get("group_name", "")
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    config = ConfigManager.load()
    if not config:
        return jsonify({"error": "Not configured"}), 400

    # Run both checks
    reachability = diagnose_url(url)

    client, err = get_client()
    zscaler_result = None
    groups_with_access = []
    if client:
        try:
            # Find ActiveStudents group (all students belong to this)
            discovered = config.get("discovered", {})
            active_students = None
            for dg in discovered.get("groups", []):
                if dg.get("name") == "ActiveStudents":
                    active_students = dg
                    break

            # If a specific group is selected, check for that group
            if group_id and group_id != "all":
                gid = int(group_id)
                groups = [{"id": gid, "name": group_name}]
                # Add also_in_groups from school mapping
                mapping = config.get("school_mapping", {})
                disc_groups = {g["id"]: g["name"] for g in discovered.get("groups", [])}
                for level_config in mapping.values():
                    if gid in (level_config.get("ids") or []):
                        for also_id in level_config.get("also_in_groups", []):
                            if also_id != gid:
                                groups.append({"id": also_id, "name": disc_groups.get(also_id, "")})
                        break
                # Always include ActiveStudents — all students belong to it
                if active_students and active_students["id"] != gid:
                    if not any(g["id"] == active_students["id"] for g in groups):
                        groups.append({"id": active_students["id"], "name": active_students["name"]})
                fake_user = {
                    "name": "",
                    "email": "",
                    "groups": groups,
                }
                zscaler_result = client.check_url_for_user(url, fake_user)
            else:
                zscaler_result = client.check_url_full(url)

            # If blocked/cautioned, check which groups DO have access
            verdict_val = zscaler_result.get("verdict", "")
            if verdict_val in ("BLOCKED", "CAUTION"):
                all_groups = discovered.get("groups", [])
                mapping = config.get("school_mapping", {})
                disc_groups = {g["id"]: g["name"] for g in all_groups}
                student_groups = [
                    'Elementary School Students', 'Primary School Students',
                    'Middle School Restrict', 'Middle School Students',
                    'High School Students', 'Advanced Students',
                    'High School Restrict',
                    'StudentRestrict', 'Locked'
                ]
                print(f"[DEBUG] Checking {len(all_groups)} groups for access to '{url}'", flush=True)
                for g in all_groups:
                    if g["name"] not in student_groups:
                        continue
                    # Skip the group we already checked
                    if str(g["id"]) == str(group_id):
                        print(f"[DEBUG] Skipping '{g['name']}' (already checked)", flush=True)
                        continue
                    try:
                        cross_gid = int(g["id"])
                        cross_groups = [{"id": cross_gid, "name": g["name"]}]
                        # Add also_in_groups from school mapping
                        for level_config in mapping.values():
                            if cross_gid in (level_config.get("ids") or []):
                                for also_id in level_config.get("also_in_groups", []):
                                    if also_id != cross_gid:
                                        cross_groups.append({"id": also_id, "name": disc_groups.get(also_id, "")})
                                break
                        # Always include ActiveStudents
                        if active_students and not any(cg["id"] == active_students["id"] for cg in cross_groups):
                            cross_groups.append({"id": active_students["id"], "name": active_students["name"]})
                        fake = {
                            "name": "", "email": "",
                            "groups": cross_groups,
                        }
                        grp_result = client.check_url_for_user(url, fake)
                        print(f"[DEBUG] Group '{g['name']}' (id={g['id']}): groups={[cg['name'] for cg in cross_groups]}, verdict={grp_result.get('verdict')}, rule={grp_result.get('verdict_rule')}", flush=True)
                        if grp_result.get("verdict") == "ALLOWED":
                            groups_with_access.append(g["name"])
                    except Exception as ex:
                        print(f"[DEBUG] Error checking group '{g['name']}': {ex}", flush=True)
                    time.sleep(0.3)

            client.logout()
        except Exception as e:
            try:
                client.logout()
            except Exception:
                pass
            zscaler_result = zscaler_result or {"error": str(e)}

    # Build plain-language verdict
    zscaler_blocked = zscaler_result and zscaler_result.get("verdict") == "BLOCKED"
    zscaler_caution = zscaler_result and zscaler_result.get("verdict") == "CAUTION"
    site_down = not reachability["reachable"]
    ssl_issue = reachability["ssl_error"]

    # Use group name in messages, fall back to "students"
    display_group = group_name if (group_name and group_name != "All (global)") else "students"

    zscaler_allowed = zscaler_result and zscaler_result.get("verdict") == "ALLOWED"

    if zscaler_blocked:
        verdict = f"This website is blocked for {display_group}. If you need it for a lesson, contact IT to request access."
        verdict_type = "zscaler_blocked"
    elif zscaler_caution:
        verdict = f"This website shows a warning page for {display_group}. Students can still click through, but it's flagged. Contact IT if this is a problem."
        verdict_type = "zscaler_caution"
    elif zscaler_allowed:
        # Zscaler says allowed — that's the answer. SSL errors from this
        # server are usually caused by Zscaler's own SSL inspection and
        # don't reflect the student experience.
        if site_down and not ssl_issue:
            verdict = "This website is allowed by the filter but appears to be down. This isn't a filtering issue — the site itself may be having problems. Try again later."
            verdict_type = "site_down"
        else:
            verdict = f"This website is allowed for {display_group}. Students should be able to access it. If they still can't, the issue may be with their device or network."
            verdict_type = "ok"
    elif ssl_issue:
        verdict = "This website has a security certificate issue. It may not load correctly on student devices. Try a different link or contact IT."
        verdict_type = "ssl_error"
    elif site_down:
        verdict = "This website appears to be down or not responding. This isn't a filtering issue — the site itself may be having problems. Try again later."
        verdict_type = "site_down"
    else:
        verdict = f"This website is allowed and working! {display_group} students should be able to access it. If they still can't, the issue may be with their device or network."
        verdict_type = "ok"

    return jsonify({
        "url": url,
        "group_name": display_group,
        "verdict": verdict,
        "verdict_type": verdict_type,
        "reachability": reachability,
        "zscaler": zscaler_result,
        "groups_with_access": groups_with_access,
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5100)
    args = parser.parse_args()
    import socket
    local_ip = socket.gethostbyname(socket.gethostname())
    print(f"\n  Zscaler URL Checker running at:")
    print(f"    Local:   http://localhost:{args.port}")
    print(f"    Network: http://{local_ip}:{args.port}\n")
    app.run(host="0.0.0.0", port=args.port, debug=True)
