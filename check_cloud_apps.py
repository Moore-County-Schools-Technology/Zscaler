from zscaler_client import ZscalerClient, ConfigManager
import json

config = ConfigManager.load()
client = ZscalerClient(config)
client.authenticate()

# Check Social Media and Discussion Forum rules
apps = ["socialNetworking", "discussionForums"]
all_app_rules = []

for app in apps:
    try:
        resp = client.session.get(f"{client.base_url}/api/v1/cloudAppControlRules/{app}")
        if resp.status_code == 200:
            all_app_rules.extend(resp.json())
    except Exception as e:
        print(f"Error fetching {app}: {e}")

print(json.dumps(all_app_rules, indent=2))
client.logout()
