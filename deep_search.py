from zscaler_client import ZscalerClient, ConfigManager
import json

config = ConfigManager.load()
client = ZscalerClient(config)
client.authenticate()

# Search ALL categories for the path or keyword
resp = client.session.get(f"{client.base_url}/api/v1/urlCategories")
cats = resp.json()

target_path = "justice.gov/epstein"
target_keyword = "epstein"

results = []
for c in cats:
    urls = [u.lower() for u in c.get("urls", []) if isinstance(u, str)]
    # Check for direct path match or keyword in the configured name
    if any(target_keyword in u for u in urls) or target_keyword in c.get("configuredName", "").lower():
        results.append({
            "category_name": c.get("configuredName") or c.get("id"),
            "matches": [u for u in urls if target_keyword in u]
        })

print(json.dumps(results, indent=2))
client.logout()
