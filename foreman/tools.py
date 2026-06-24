"""Tools — the agents' action space (actuators).

Each Tool is a typed action an agent may take. The set of tools an agent holds
*is* its action space, so the surface is also the guardrail: there is no merge
or push tool anywhere, therefore no agent can perform the irreversible step
(the same trick jobagent uses by having no submit tool).

Security lives here, in one place:
  * file paths are confined to the workspace root (``Workspace.safe_path``)
  * ``Bash`` runs behind an allowlist and rejects shell operators / pushes
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any

from .blackboard import Blackboard
from .environment import Workspace

# ---- bash safety ---------------------------------------------------------- #
_SHELL_METACHARS = set("&|;`$><\n(){}")
_ALLOWED_BINS = {"python", "python3", "pytest", "mypy", "ruff", "ls", "cat", "git"}
# git is read-only here: these subcommands are refused outright.
_FORBIDDEN_GIT = {"push", "reset", "clean"}


class UnsafeCommand(PermissionError):
    """Raised when a bash command is outside the allowlist / would be destructive."""


def assert_safe_command(command: str) -> list[str]:
    if any(ch in command for ch in _SHELL_METACHARS):
        raise UnsafeCommand(f"shell operators are not allowed: {command!r}")
    parts = shlex.split(command)
    if not parts:
        raise UnsafeCommand("empty command")
    binary = parts[0].split("/")[-1]
    if binary not in _ALLOWED_BINS:
        raise UnsafeCommand(f"{binary!r} is not on the allowlist")
    if binary == "git" and len(parts) > 1 and parts[1] in _FORBIDDEN_GIT:
        raise UnsafeCommand(f"git {parts[1]} is forbidden (the human merges)")
    if "push" in parts:
        raise UnsafeCommand("push is forbidden (the human merges)")
    return parts


# ---- tool framework ------------------------------------------------------- #
@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class Tool:
    name: str = "tool"
    description: str = ""

    def run(self, env: Workspace, bb: Blackboard, **args: Any) -> ToolResult:
        raise NotImplementedError

    def schema(self) -> dict:
        """JSON-schema description for exposing this tool to the Messages API."""
        return {"name": self.name, "description": self.description,
                "input_schema": {"type": "object", "properties": {}}}


class ReadFile(Tool):
    name = "read_file"
    description = "Read a file from the workspace."

    def run(self, env, bb, *, path: str) -> ToolResult:
        try:
            return ToolResult(True, env.read_text(path))
        except (FileNotFoundError, PermissionError) as exc:
            return ToolResult(False, str(exc))

    def schema(self):
        return {"name": self.name, "description": self.description, "input_schema": {
            "type": "object", "properties": {"path": {"type": "string"}},
            "required": ["path"]}}


class EditFile(Tool):
    name = "edit_file"
    description = ("Edit a workspace file: pass `content` to write the whole file, "
                   "or `find`/`replace` to substitute a substring.")

    def run(self, env, bb, *, path: str, content: str | None = None,
            find: str | None = None, replace: str | None = None) -> ToolResult:
        try:
            if content is not None:
                env.write_text(path, content)
                return ToolResult(True, f"wrote {path}")
            if find is not None and replace is not None:
                cur = env.read_text(path)
                if find not in cur:
                    return ToolResult(False, f"{find!r} not found in {path}")
                env.write_text(path, cur.replace(find, replace))
                return ToolResult(True, f"edited {path}")
            return ToolResult(False, "need either `content` or `find`+`replace`")
        except PermissionError as exc:        # path escape
            return ToolResult(False, str(exc))

    def schema(self):
        return {"name": self.name, "description": self.description, "input_schema": {
            "type": "object", "properties": {
                "path": {"type": "string"}, "content": {"type": "string"},
                "find": {"type": "string"}, "replace": {"type": "string"}},
            "required": ["path"]}}


class RunTests(Tool):
    """The truth signal. Runs the workspace's test command; records the verdict."""

    name = "run_tests"
    description = "Run the project's tests. Returns whether they pass."

    def run(self, env, bb, **_: Any) -> ToolResult:
        res = env.run_tests()
        verdict = {"passed": res.ok, "returncode": res.returncode,
                   "output": (res.stdout + res.stderr)[-2000:]}
        bb.set("test_result", verdict)
        bb.log("run_tests", "tests", {"passed": res.ok, "rc": res.returncode})
        return ToolResult(res.ok, verdict["output"], data=verdict)


class Bash(Tool):
    name = "bash"
    description = "Run an allowlisted shell command in the workspace."

    def run(self, env, bb, *, command: str) -> ToolResult:
        try:
            parts = assert_safe_command(command)
        except UnsafeCommand as exc:
            return ToolResult(False, f"blocked: {exc}")
        res = env.run_cmd(parts)
        return ToolResult(res.ok, (res.stdout + res.stderr)[-2000:])

    def schema(self):
        return {"name": self.name, "description": self.description, "input_schema": {
            "type": "object", "properties": {"command": {"type": "string"}},
            "required": ["command"]}}


class SubmitReview(Tool):
    """The reviewer's only actuator: post a verdict to the blackboard."""

    name = "submit_review"
    description = "Submit a review verdict ('approve' or 'request_changes') with findings."

    def run(self, env, bb, *, verdict: str, findings: list | None = None) -> ToolResult:
        review = {"verdict": verdict, "findings": findings or []}
        bb.set("review", review)
        bb.log("reviewer", "review", review)
        return ToolResult(True, f"review: {verdict}", data=review)

    def schema(self):
        return {"name": self.name, "description": self.description, "input_schema": {
            "type": "object", "properties": {
                "verdict": {"type": "string", "enum": ["approve", "request_changes"]},
                "findings": {"type": "array", "items": {"type": "string"}}},
            "required": ["verdict"]}}
