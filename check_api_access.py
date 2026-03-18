#!/usr/bin/env python3
"""
Quick diagnostic: check which Zscaler ZIA API endpoints you have access to.
"""
from zscaler_client import ZscalerClient, ConfigManager

ENDPOINTS = [
    # Reporting / Logs
    ("GET", "/api/v1/auditlogEntryReport", "Audit Logs"),
    ("POST", "/api/v1/webApplicationRules", "Cloud App Control Rules"),
    ("GET", "/api/v1/firewallRules", "Firewall Rules"),
    ("GET", "/api/v1/dlpDictionaries", "DLP Dictionaries"),
    ("GET", "/api/v1/dlpEngines", "DLP Engines"),
    ("GET", "/api/v1/securityPolicySettings", "Security Policy Settings"),
    ("GET", "/api/v1/advancedThreatSettings", "Advanced Threat Protection"),
    ("GET", "/api/v1/malwarePolicy", "Malware Policy"),
    ("GET", "/api/v1/sandboxReport/quota", "Sandbox Report Quota"),
    # Config you already use (for comparison)
    ("GET", "/api/v1/urlFilteringRules", "URL Filtering Rules (already using)"),
    ("GET", "/api/v1/urlCategories", "URL Categories (already using)"),
]


def main():
    config = ConfigManager.load()
    if not config:
        print("No config found. Run setup first.")
        return

    client = ZscalerClient(config)
    print("Authenticating...")
    client.authenticate()
    print()

    print(f"{'Endpoint':<40} {'Status'}")
    print("-" * 60)

    for method, path, label in ENDPOINTS:
        try:
            if method == "GET":
                resp = client.session.get(f"{client.base_url}{path}")
            else:
                resp = client.session.post(f"{client.base_url}{path}", json={})

            if resp.status_code in (200, 201):
                status = "✅ ACCESS"
            elif resp.status_code == 403:
                status = "🚫 FORBIDDEN (no access)"
            elif resp.status_code == 404:
                status = "❓ NOT FOUND (may not be in your plan)"
            else:
                status = f"⚠️  HTTP {resp.status_code}"
        except Exception as e:
            status = f"❌ ERROR: {e}"

        print(f"  {label:<38} {status}")

    client.logout()
    print("\nDone.")


if __name__ == "__main__":
    main()
