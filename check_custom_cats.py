from zscaler_client import ZscalerClient, ConfigManager
import json

config = ConfigManager.load()
client = ZscalerClient(config)
client.authenticate()

# Get ALL custom categories
resp = client.session.get(f"{client.base_url}/api/v1/urlCategories")
cats = resp.json()

# Look for 'reddit' in any custom category
found = []
for c in cats:
    if "reddit" in str(c.get("urls", [])).lower() or "reddit" in c.get("configuredName", "").lower():
        found.append({
            "id": c.get("id"),
            "name": c.get("configuredName"),
            "urls": c.get("urls")
        })

print(json.dumps(found, indent=2))
client.logout()
