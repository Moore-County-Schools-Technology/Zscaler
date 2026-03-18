from zscaler_client import ZscalerClient, ConfigManager
import json

config = ConfigManager.load()
if not config:
    print("No config found.")
    exit()

client = ZscalerClient(config)
client.authenticate()

rules = client.get_url_filtering_rules()
global_blocks = []

for r in rules:
    if r.get("state") == "ENABLED" and r.get("action") == "BLOCK":
        # Rule applies to everyone if no specific scopes are defined
        scopes = []
        if r.get("locationGroups"): scopes.append(f"LocGroups: {', '.join([g['name'] for g in r['locationGroups']])}")
        if r.get("groups"): scopes.append(f"UserGroups: {', '.join([g['name'] for g in r['groups']])}")
        if r.get("departments"): scopes.append(f"Depts: {', '.join([d['name'] for d in r['departments']])}")
        if r.get("locations"): scopes.append(f"Locations: {', '.join([l['name'] for l in r['locations']])}")
        
        if not scopes:
            global_blocks.append({
                "order": r.get("order"),
                "name": r.get("name"),
                "categories": r.get("urlCategories", []),
                "description": r.get("description", "No description")
            })

print(json.dumps(global_blocks, indent=2))
client.logout()
