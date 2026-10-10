from tools.tool_registry import registry
import json

registry.discover()
print("=== PROVIDER MENU SUMMARY ===")
print(json.dumps(registry.provider_menu_summary(), indent=2))

print("\n=== VIDEO_COMPOSE RENDER ENGINES ===")
try:
    info = registry._tools['video_compose'].get_info()
    print("Render engines:", info.get('render_engines'))
    print("Remotion note:", info.get('remotion_note'))
    print("HyperFrames note:", info.get('hyperframes_note'))
except Exception as e:
    print("video_compose info error:", e)
