"""Offline capability-scoped registry tests: no real tools or health probes."""
from types import SimpleNamespace
import socket
import subprocess

import pytest

from tools.base_tool import ToolRuntime, ToolStatus
from tools.tool_registry import ToolRegistry


@pytest.fixture(autouse=True)
def prohibit_external_probes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unit tests must not run network or subprocess probes")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)


def tool(name, capability, *, available=True, info_calls=None, forbidden=False, **extra):
    def get_info():
        if forbidden:
            raise AssertionError(f"Unrelated tool was probed: {name}")
        if info_calls is not None:
            info_calls.append(name)
        return {"status": "available" if available else "unavailable", "dependencies": [], **extra}
    def get_status():
        raise AssertionError("get_info already contains the status; do not probe again")
    return SimpleNamespace(name=name, capability=capability, provider="fake", runtime=ToolRuntime.LOCAL,
                           best_for=[], install_instructions="", get_info=get_info, get_status=get_status)


def seeded(*tools):
    registry = ToolRegistry()
    for item in tools:
        registry.register(item)
    registry._discovered_packages.add("tools")
    return registry


def test_menu_uses_single_info_status_snapshot():
    calls = []
    registry = seeded(tool("encoder", "video_post", info_calls=calls))
    menu = registry.provider_menu()
    assert menu["video_post"]["configured"] == 1
    assert menu["video_post"]["available"][0]["status"] == ToolStatus.AVAILABLE.value
    assert calls == ["encoder"]


def test_capability_filter_runs_before_get_info():
    calls = []
    registry = seeded(tool("encoder", "video_post", info_calls=calls),
                      tool("expensive_provider", "video_generation", forbidden=True))
    menu = registry.provider_menu(capabilities=["video_post"])
    assert list(menu) == ["video_post"] and calls == ["encoder"]


def test_summary_passes_scope_and_does_not_probe_unrelated_runtime_metadata(monkeypatch):
    registry = seeded(tool("voice", "tts"),
                      tool("video_compose", "video_post", forbidden=True),
                      tool("hyperframes_compose", "video_post", forbidden=True))
    original = registry.provider_menu
    passed = []
    def menu(*, capabilities=None):
        passed.append(capabilities)
        return original(capabilities=capabilities)
    monkeypatch.setattr(registry, "provider_menu", menu)
    scope = ["tts"]
    summary = registry.provider_menu_summary(capabilities=scope)
    assert passed == [scope]
    assert set(summary) == {"composition_runtimes", "capabilities", "setup_offers", "runtime_warnings"}
    assert summary["composition_runtimes"] == {} and summary["runtime_warnings"] == []
    assert [entry["capability"] for entry in summary["capabilities"]] == ["tts"]


@pytest.mark.parametrize("scope", [[], ["not_registered"]])
def test_empty_or_unknown_scope_probes_nothing(scope):
    registry = seeded(tool("expensive_provider", "video_generation", forbidden=True))
    assert registry.provider_menu(capabilities=scope) == {}
    assert registry.provider_menu_summary(capabilities=scope) == {
        "composition_runtimes": {}, "capabilities": [], "setup_offers": [], "runtime_warnings": []}


def test_default_still_discovers_and_reports_all_capabilities(monkeypatch):
    registry = ToolRegistry()
    discoveries = []
    def discover(package_name="tools"):
        discoveries.append(package_name)
        registry.register(tool("voice", "tts"))
        registry.register(tool("encoder", "video_post"))
        registry._discovered_packages.add(package_name)
    monkeypatch.setattr(registry, "discover", discover)
    assert set(registry.provider_menu()) == {"tts", "video_post"}
    assert discoveries == ["tools"]
    assert {entry["capability"] for entry in registry.provider_menu_summary()["capabilities"]} == {"tts", "video_post"}
