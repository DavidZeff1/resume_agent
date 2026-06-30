"""The tool abstraction.

A *tool* is a plain Python function plus a JSON schema the model uses to call it.
You don't write the schema by hand — ``@registry.tool`` derives it from the
function's type hints and docstring:

    @registry.tool
    def add(a: int, b: int) -> int:
        '''Add two numbers.

        Args:
            a: the first number
            b: the second number
        '''
        return a + b

Param types and required-ness come from the signature; the description and the
per-arg help come from the docstring (Google-style ``Args:`` section).
"""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, get_type_hints

_PY_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable[..., Any]

    def schema(self) -> dict:
        """The OpenAI-compatible function-tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def run(self, **kwargs: Any) -> str:
        result = self.func(**kwargs)
        if isinstance(result, str):
            return result
        return json.dumps(result, default=str, ensure_ascii=False)


class ToolRegistry:
    """Holds the tools an agent may call and exposes their schemas."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def tool(self, func: Callable | None = None, *, name: str | None = None):
        """Register a function as a tool. Usable bare (``@reg.tool``) or called."""

        def decorate(fn: Callable) -> Callable:
            t = _build_tool(fn, name=name)
            self._tools[t.name] = t
            return fn

        return decorate(func) if func is not None else decorate

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    def __len__(self) -> int:
        return len(self._tools)


def _build_tool(fn: Callable, *, name: str | None) -> Tool:
    hints = get_type_hints(fn)
    sig = inspect.signature(fn)
    doc = inspect.getdoc(fn) or ""
    summary, arg_help = _parse_docstring(doc)

    properties: dict[str, dict] = {}
    required: list[str] = []
    for pname, param in sig.parameters.items():
        if pname == "self":
            continue
        json_type = _PY_TO_JSON.get(hints.get(pname, str), "string")
        spec: dict[str, Any] = {"type": json_type}
        if pname in arg_help:
            spec["description"] = arg_help[pname]
        properties[pname] = spec
        if param.default is inspect.Parameter.empty:
            required.append(pname)

    parameters = {"type": "object", "properties": properties}
    if required:
        parameters["required"] = required

    return Tool(
        name=name or fn.__name__,
        description=summary or fn.__name__,
        parameters=parameters,
        func=fn,
    )


def _parse_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """Return (summary, {arg: description}) from a Google-style docstring."""
    if not doc:
        return "", {}
    parts = re.split(r"\n\s*Args:\s*\n", doc, maxsplit=1)
    summary = parts[0].strip()
    arg_help: dict[str, str] = {}
    if len(parts) == 2:
        for line in parts[1].splitlines():
            m = re.match(r"\s*(\w+)\s*(?:\([^)]*\))?\s*:\s*(.+)", line)
            if m:
                arg_help[m.group(1)] = m.group(2).strip()
    return summary, arg_help
