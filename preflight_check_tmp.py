from tools.tool_registry import registry
import json

registry.discover()
print(json.dumps(registry.provider_menu_summary(), indent=2))
