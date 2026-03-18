#!/usr/bin/env python3
"""
Zscaler URL Checker CLI — K-12 Edition

Check if a website is blocked for students at different school levels
(elementary, middle, high) by querying the Zscaler ZIA API.

Usage:
    python cli.py setup          # First-time configuration
    python cli.py discover       # Discover Zscaler groups/locations
    python cli.py check URL      # Check a single URL
    python cli.py check -f FILE  # Check URLs from a file (one per line)
    python cli.py batch          # Interactive batch mode
"""

import argparse
import csv
import json
import sys
import os
from getpass import getpass
from zscaler_client import ZscalerClient, ConfigManager


# ── ANSI Colors ─────────────────────────────────────────────────────

class C:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def banner():
    print(f"""
{C.CYAN}{C.BOLD}╔══════════════════════════════════════════════╗
║   🏫  Zscaler URL Checker — K-12 Edition    ║
╚══════════════════════════════════════════════╝{C.RESET}
""")


# ── Setup ───────────────────────────────────────────────────────────

def cmd_setup(args):
    """Interactive first-time setup."""
    banner()
    print(f"{C.BOLD}Let's configure your Zscaler connection.{C.RESET}\n")

    config = {}

    # Auth method
    print(f"Authentication method:")
    print(f"  {C.CYAN}1{C.RESET}) API Key + Username/Password (legacy)")
    print(f"  {C.CYAN}2{C.RESET}) OAuth 2.0 Client Credentials")
    choice = input(f"\n{C.BOLD}Choose [1/2]: {C.RESET}").strip()

    if choice == "2":
        config["auth_method"] = "oauth"
        config["identity_url"] = input(f"\n{C.BOLD}Zscaler Identity URL{C.RESET} (e.g., https://your-id.zslogin.net): ").strip()
        config["base_url"] = input(f"{C.BOLD}ZIA Base URL{C.RESET} (e.g., https://zsapi.zscaler.net): ").strip()
        config["client_id"] = input(f"{C.BOLD}OAuth Client ID: {C.RESET}").strip()
        config["client_secret"] = getpass(f"{C.BOLD}OAuth Client Secret: {C.RESET}")
    else:
        config["auth_method"] = "api_key"
        config["base_url"] = input(f"\n{C.BOLD}ZIA Base URL{C.RESET} (e.g., https://zsapi.zscaler.net): ").strip()
        config["username"] = input(f"{C.BOLD}Admin Username: {C.RESET}").strip()
        config["password"] = getpass(f"{C.BOLD}Admin Password: {C.RESET}")
        config["api_key"] = input(f"{C.BOLD}API Key: {C.RESET}").strip()

    # Test connection
    print(f"\n{C.DIM}Testing connection...{C.RESET}")
    try:
        client = ZscalerClient(config)
        client.authenticate()
        print(f"{C.GREEN}✓ Connected successfully!{C.RESET}")
        client.logout()
    except Exception as e:
        print(f"{C.RED}✗ Connection failed: {e}{C.RESET}")
        print(f"\n{C.YELLOW}Config NOT saved. Fix credentials and try again.{C.RESET}")
        return

    # School level mapping placeholder
    config["school_mapping"] = {}

    ConfigManager.save(config)
    print(f"\n{C.GREEN}✓ Config saved to {ConfigManager.CONFIG_FILE}{C.RESET}")
    print(f"\n{C.YELLOW}Next step:{C.RESET} Run {C.CYAN}python cli.py discover{C.RESET} to find your school-level groups,")
    print(f"then run {C.CYAN}python cli.py map{C.RESET} to assign them to Elementary/Middle/High.")


def cmd_discover(args):
    """Discover locations, location groups, user groups, and departments."""
    banner()
    config = _load_config_or_exit()
    client = ZscalerClient(config)

    print(f"{C.DIM}Authenticating...{C.RESET}")
    client.authenticate()

    discovered = {}

    # Location Groups
    print(f"\n{C.BOLD}{C.CYAN}── Location Groups ──{C.RESET}")
    try:
        loc_groups = client.get_location_groups()
        discovered["location_groups"] = [{"id": g["id"], "name": g["name"]} for g in loc_groups]
        for g in loc_groups:
            print(f"  {C.BOLD}[{g['id']}]{C.RESET} {g['name']}")
        if not loc_groups:
            print(f"  {C.DIM}(none found){C.RESET}")
    except Exception as e:
        print(f"  {C.RED}Error: {e}{C.RESET}")

    # Locations
    print(f"\n{C.BOLD}{C.CYAN}── Locations ──{C.RESET}")
    try:
        locations = client.get_locations()
        discovered["locations"] = [{"id": l["id"], "name": l["name"]} for l in locations]
        for l in locations:
            parent = f" {C.DIM}(sub-location){C.RESET}" if l.get("parentId") else ""
            print(f"  {C.BOLD}[{l['id']}]{C.RESET} {l['name']}{parent}")
        if not locations:
            print(f"  {C.DIM}(none found){C.RESET}")
    except Exception as e:
        print(f"  {C.RED}Error: {e}{C.RESET}")

    # User Groups
    print(f"\n{C.BOLD}{C.CYAN}── User Groups ──{C.RESET}")
    try:
        groups = client.get_groups()
        discovered["groups"] = [{"id": g["id"], "name": g["name"]} for g in groups]
        for g in groups:
            print(f"  {C.BOLD}[{g['id']}]{C.RESET} {g['name']}")
        if not groups:
            print(f"  {C.DIM}(none found){C.RESET}")
    except Exception as e:
        print(f"  {C.RED}Error: {e}{C.RESET}")

    # Departments
    print(f"\n{C.BOLD}{C.CYAN}── Departments ──{C.RESET}")
    try:
        depts = client.get_departments()
        discovered["departments"] = [{"id": d["id"], "name": d["name"]} for d in depts]
        for d in depts:
            print(f"  {C.BOLD}[{d['id']}]{C.RESET} {d['name']}")
        if not depts:
            print(f"  {C.DIM}(none found){C.RESET}")
    except Exception as e:
        print(f"  {C.RED}Error: {e}{C.RESET}")

    client.logout()

    # Save discovered data
    config["discovered"] = discovered
    ConfigManager.save(config)
    print(f"\n{C.GREEN}✓ Discovery data saved.{C.RESET}")
    print(f"\n{C.YELLOW}Next:{C.RESET} Run {C.CYAN}python cli.py map{C.RESET} to assign IDs to school levels.")


def cmd_map(args):
    """Map discovered Zscaler entities to school levels."""
    banner()
    config = _load_config_or_exit()

    if "discovered" not in config:
        print(f"{C.RED}Run 'python cli.py discover' first.{C.RESET}")
        return

    print(f"{C.BOLD}Map your Zscaler groups to school levels.{C.RESET}\n")

    # Show what's available
    disc = config["discovered"]
    all_types = ["location_groups", "locations", "groups", "departments"]
    type_labels = {
        "location_groups": "Location Groups",
        "locations": "Locations",
        "groups": "User Groups",
        "departments": "Departments",
    }

    print(f"Which entity type defines your school levels?")
    for i, t in enumerate(all_types, 1):
        count = len(disc.get(t, []))
        print(f"  {C.CYAN}{i}{C.RESET}) {type_labels[t]} ({count} found)")

    choice = int(input(f"\n{C.BOLD}Choose [1-4]: {C.RESET}").strip()) - 1
    scope_type = all_types[choice]
    entities = disc.get(scope_type, [])

    if not entities:
        print(f"{C.RED}No {type_labels[scope_type]} found. Choose a different type.{C.RESET}")
        return

    print(f"\nAvailable {type_labels[scope_type]}:")
    for e in entities:
        print(f"  {C.BOLD}[{e['id']}]{C.RESET} {e['name']}")

    school_mapping = {}
    for level in ["Elementary", "Middle School", "High School"]:
        ids_str = input(f"\n{C.BOLD}{level}{C.RESET} — enter ID(s) comma-separated (or blank to skip): ").strip()
        if ids_str:
            ids = [int(x.strip()) for x in ids_str.split(",")]
            school_mapping[level] = {"type": scope_type, "ids": ids}

    config["school_mapping"] = school_mapping
    ConfigManager.save(config)
    print(f"\n{C.GREEN}✓ School mapping saved!{C.RESET}")
    print(f"\n{C.YELLOW}Ready to go!{C.RESET} Run {C.CYAN}python cli.py check <URL>{C.RESET}")


def cmd_check(args):
    """Check one or more URLs against school-level filtering rules."""
    config = _load_config_or_exit()

    if not config.get("school_mapping"):
        print(f"{C.RED}No school mapping configured. Run 'python cli.py map' first.{C.RESET}")
        return

    # Collect URLs
    urls = []
    if args.file:
        with open(args.file, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)
    elif args.url:
        urls = [args.url]
    else:
        print(f"{C.RED}Provide a URL or use -f FILE.{C.RESET}")
        return

    client = ZscalerClient(config)
    client.authenticate()

    import time
    for i, url in enumerate(urls):
        result = client.check_url(url, config["school_mapping"])
        _print_result(result)
        if i < len(urls) - 1:
            print()
            time.sleep(0.3)  # Rate limiting

    client.logout()


def cmd_batch(args):
    """Interactive batch mode — keep checking URLs."""
    banner()
    config = _load_config_or_exit()

    if not config.get("school_mapping"):
        print(f"{C.RED}No school mapping configured. Run 'python cli.py map' first.{C.RESET}")
        return

    client = ZscalerClient(config)
    print(f"{C.DIM}Authenticating...{C.RESET}")
    client.authenticate()
    print(f"{C.GREEN}✓ Ready!{C.RESET} Enter URLs to check (or 'quit' to exit).\n")

    results_log = []

    while True:
        url = input(f"{C.BOLD}URL: {C.RESET}").strip()
        if url.lower() in ("quit", "exit", "q"):
            break
        if not url:
            continue

        result = client.check_url(url, config["school_mapping"])
        _print_result(result)
        results_log.append(result)
        print()

    client.logout()

    if results_log and input(f"\n{C.BOLD}Export results to CSV? [y/N]: {C.RESET}").lower() == "y":
        _export_csv(results_log, config["school_mapping"])


def _print_result(result: dict):
    """Pretty-print a URL check result."""
    url = result["url"]
    categories = ", ".join(result.get("categories", []))

    print(f"\n{C.BOLD}{'─' * 50}{C.RESET}")
    print(f"  {C.BOLD}URL:{C.RESET}        {C.CYAN}{url}{C.RESET}")
    print(f"  {C.BOLD}Categories:{C.RESET} {categories or 'Unknown'}")

    if result.get("security_alert"):
        print(f"  {C.RED}{C.BOLD}⚠ SECURITY ALERT{C.RESET}")

    if result.get("error"):
        print(f"  {C.RED}Error: {result['error']}{C.RESET}")
        return

    levels = result.get("levels", {})
    for level_name, status in levels.items():
        if status["blocked"]:
            icon = f"{C.RED}🚫 BLOCKED{C.RESET}"
            detail = f" {C.DIM}(rule: {status['blocking_rule']}){C.RESET}"
        elif status["cautioned"]:
            icon = f"{C.YELLOW}⚠️  CAUTION{C.RESET}"
            detail = f" {C.DIM}(rule: {status['caution_rule']}){C.RESET}"
        else:
            icon = f"{C.GREEN}✅ ALLOWED{C.RESET}"
            detail = ""

        print(f"  {C.BOLD}{level_name:15}{C.RESET} {icon}{detail}")

    print(f"{C.BOLD}{'─' * 50}{C.RESET}")


def _export_csv(results: list, school_mapping: dict):
    """Export batch results to CSV."""
    filename = "url_check_results.csv"
    levels = list(school_mapping.keys())

    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        header = ["URL", "Categories", "Security Alert"] + [f"{l} Status" for l in levels] + [f"{l} Rule" for l in levels]
        writer.writerow(header)

        for r in results:
            row = [
                r["url"],
                "; ".join(r.get("categories", [])),
                "YES" if r.get("security_alert") else "NO",
            ]
            for l in levels:
                status = r.get("levels", {}).get(l, {})
                if status.get("blocked"):
                    row.append("BLOCKED")
                elif status.get("cautioned"):
                    row.append("CAUTION")
                else:
                    row.append("ALLOWED")
            for l in levels:
                status = r.get("levels", {}).get(l, {})
                row.append(status.get("blocking_rule") or status.get("caution_rule") or "")
            writer.writerow(row)

    print(f"{C.GREEN}✓ Exported to {filename}{C.RESET}")


def _load_config_or_exit() -> dict:
    """Load config or exit with message."""
    config = ConfigManager.load()
    if not config:
        print(f"{C.RED}No config found. Run 'python cli.py setup' first.{C.RESET}")
        sys.exit(1)
    return config


# ── Main ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Zscaler URL Checker — K-12 Edition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  setup      First-time configuration (API credentials)
  discover   Discover Zscaler locations, groups, departments
  map        Map discovered entities to school levels
  check      Check if a URL is blocked per school level
  batch      Interactive mode — check URLs continuously
        """,
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("setup", help="First-time setup")
    sub.add_parser("discover", help="Discover Zscaler entities")
    sub.add_parser("map", help="Map entities to school levels")

    check_parser = sub.add_parser("check", help="Check a URL")
    check_parser.add_argument("url", nargs="?", help="URL to check")
    check_parser.add_argument("-f", "--file", help="File with URLs (one per line)")

    sub.add_parser("batch", help="Interactive batch mode")

    args = parser.parse_args()

    commands = {
        "setup": cmd_setup,
        "discover": cmd_discover,
        "map": cmd_map,
        "check": cmd_check,
        "batch": cmd_batch,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
