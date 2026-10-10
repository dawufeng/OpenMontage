from tools.tool_registry import registry
import json

registry.discover()
envelope = registry.support_envelope()
menu = registry.provider_menu()
print("=== ENVELOPE ===")
print(json.dumps(envelope, indent=2, ensure_ascii=False))
print("=== MENU ===")
print(json.dumps(menu, indent=2, ensure_ascii=False))
