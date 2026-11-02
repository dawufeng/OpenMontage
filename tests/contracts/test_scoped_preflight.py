from tools.base_tool import BaseTool, ToolResult, ToolStatus
from tools.tool_registry import ToolRegistry
import pytest


class FakeTool(BaseTool):
    name = 'local'
    capability = 'video_post'
    provider = 'local'
    def __init__(self): self.calls = 0
    def get_status(self):
        self.calls += 1
        return ToolStatus.AVAILABLE
    def execute(self, inputs): return ToolResult(success=True)


def registry(monkeypatch):
    value = ToolRegistry()
    monkeypatch.setattr(value, 'ensure_discovered', lambda: None)
    return value


def test_provider_menu_does_not_probe_status_twice(monkeypatch):
    reg = registry(monkeypatch)
    tool = FakeTool(); reg.register(tool)
    assert reg.provider_menu()['video_post']['configured'] == 1
    assert tool.calls == 1


def test_scope_filters_before_expensive_status_calls(monkeypatch):
    reg = registry(monkeypatch)
    selected = FakeTool(); reg.register(selected)
    unrelated = FakeTool(); unrelated.name = 'remote'; unrelated.capability = 'music_generation'
    def forbidden(): pytest.fail('Unrelated provider status was probed')
    unrelated.get_status = forbidden; reg.register(unrelated)
    assert list(reg.provider_menu(capabilities=['video_post'])) == ['video_post']


def test_summary_uses_scoped_snapshot_once(monkeypatch):
    reg = registry(monkeypatch)
    tool = FakeTool(); tool.name = 'video_compose'; reg.register(tool)
    hyperframes = FakeTool(); hyperframes.name = 'hyperframes_compose'; reg.register(hyperframes)
    summary = reg.provider_menu_summary(capabilities=['video_post'])
    assert summary['capabilities'][0]['capability'] == 'video_post'
    assert tool.calls == 1
    assert hyperframes.calls == 1
    excluded = reg.provider_menu_summary(capabilities=[])
    assert excluded['capabilities'] == [] and excluded['composition_runtimes'] == {}
    assert tool.calls == 1


def test_default_menu_keeps_all_registered_capabilities(monkeypatch):
    reg = registry(monkeypatch)
    for name, capability in [('local', 'video_post'), ('voice', 'tts')]:
        tool = FakeTool(); tool.name = name; tool.capability = capability; reg.register(tool)
    assert set(reg.provider_menu()) == {'video_post', 'tts'}
