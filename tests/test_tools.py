"""Offline tests — no network, no API key. Exercise the tool layer + loop wiring."""

from __future__ import annotations

import json

import pytest

from agent.loop import AgentResult, run_agent
from agent.config import Settings
from agent.tools.base import ToolRegistry


def test_schema_is_derived_from_signature_and_docstring():
    reg = ToolRegistry()

    @reg.tool
    def add(a: int, b: int = 0) -> int:
        """Add two numbers.

        Args:
            a: the first number
            b: the second number
        """
        return a + b

    (schema,) = reg.schemas()
    fn = schema["function"]
    assert fn["name"] == "add"
    assert fn["description"] == "Add two numbers."
    props = fn["parameters"]["properties"]
    assert props["a"] == {"type": "integer", "description": "the first number"}
    assert props["b"]["type"] == "integer"
    # `a` is required (no default), `b` is not.
    assert fn["parameters"]["required"] == ["a"]


def test_tool_run_serializes_non_strings():
    reg = ToolRegistry()

    @reg.tool
    def pair(x: int) -> dict:
        """Return a dict.

        Args:
            x: anything
        """
        return {"x": x}

    assert reg.get("pair").run(x=3) == json.dumps({"x": 3})


def test_default_toolset_loads():
    from agent.tools import registry

    names = registry.names()
    for expected in ("read_file", "write_file", "list_dir", "web_search", "fetch_url", "run_python"):
        assert expected in names


def test_run_python_tool_captures_stdout():
    from agent.tools import registry

    out = registry.get("run_python").run(code="print(6 * 7)")
    assert out == "42"


def test_file_tools_roundtrip(tmp_path, monkeypatch):
    # Re-point the workspace and re-import so the sandbox root picks it up.
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    import importlib

    import agent.tools.files as files

    importlib.reload(files)
    assert "Wrote" in files.write_file("a/b.txt", "hello")
    assert files.read_file("a/b.txt") == "hello"
    assert "b.txt" in files.list_dir("a")


def test_file_tools_refuse_to_escape_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    import importlib

    import agent.tools.files as files

    importlib.reload(files)
    with pytest.raises(ValueError, match="outside the workspace"):
        files.read_file("../../etc/hosts")


class _FakeClient:
    """Stands in for the OpenAI client: scripts one tool call, then an answer."""

    def __init__(self, script):
        self._script = list(script)
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kwargs):  # noqa: ANN003
        return self._script.pop(0)


def _completion(content=None, tool_calls=None):
    msg = type("Msg", (), {"content": content, "tool_calls": tool_calls})()
    choice = type("Choice", (), {"message": msg})()
    return type("Completion", (), {"choices": [choice]})()


def _tool_call(call_id, name, args):
    fn = type("Fn", (), {"name": name, "arguments": json.dumps(args)})()
    return type("TC", (), {"id": call_id, "function": fn})()


def test_run_agent_executes_a_tool_then_answers():
    reg = ToolRegistry()
    seen = {}

    @reg.tool
    def echo(text: str) -> str:
        """Echo text.

        Args:
            text: input
        """
        seen["text"] = text
        return f"echoed:{text}"

    client = _FakeClient(
        [
            _completion(tool_calls=[_tool_call("c1", "echo", {"text": "hi"})]),
            _completion(content="all done"),
        ]
    )
    settings = Settings("test", "http://x", "m", "k", max_steps=5)
    result = run_agent("do it", registry=reg, settings=settings, client=client)

    assert isinstance(result, AgentResult)
    assert result.answer == "all done"
    assert result.steps == 2
    assert seen["text"] == "hi"
