import os
import json
import sys

repo_root = r"D:\mywork\video-agent\GlowStudio-workspace\OpenMontage"
os.chdir(repo_root)
sys.path.insert(0, repo_root)

from tools.tool_registry import registry

registry.discover()
print(json.dumps(registry.provider_menu_summary(), indent=2))
