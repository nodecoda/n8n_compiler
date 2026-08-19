"""Strict JavaScript parse bridge (acorn via Node subprocess).

n8n Code nodes carry a heavy share of workflow logic, so JS is compiled as a
first-class subsystem: the compiler parses Code-node bodies with acorn
(ES2022+, strict) and treats any syntax error as a compile error with an
exact line/column position. There is no fallback tokenizer path: if acorn /
Node is unavailable the compiler fails as an infrastructure error instead of
silently degrading the check (same policy as coze_compiler's external gates).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "js_parse.mjs"


class JSInfraError(RuntimeError):
    """Node or acorn bridge unavailable — infrastructure, not source, failure."""


@dataclass(frozen=True)
class JSSyntaxError:
    line: int
    col: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"line": self.line, "col": self.col, "message": self.message}


@dataclass(frozen=True)
class JSParseResult:
    ok: bool
    errors: list[JSSyntaxError] = field(default_factory=list)
    ast: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [e.to_dict() for e in self.errors],
            "ast": self.ast,
        }


def find_node() -> str:
    """Locate the Node.js binary (env NODE overrides PATH lookup)."""
    env = os.environ.get("NODE")
    if env:
        return env
    found = shutil.which("node")
    if found:
        return found
    raise JSInfraError(
        "Node.js is required to parse n8n Code-node JavaScript (acorn bridge). "
        "Install Node >= 18 or set NODE=/path/to/node."
    )


def _run_bridge(input_bytes: bytes, timeout: float, *extra_args: str) -> dict[str, Any]:
    """Run the acorn bridge; raises JSInfraError on infra failures."""
    node = find_node()
    if not _SCRIPT.exists():
        raise JSInfraError(f"acorn bridge script missing: {_SCRIPT}")
    try:
        proc = subprocess.run(
            [node, str(_SCRIPT), *extra_args],
            input=input_bytes,
            capture_output=True,
            timeout=timeout,
            check=False,  # returncode 由下方显式判定（acorn 桥错误分类）
        )
    except subprocess.TimeoutExpired as exc:
        raise JSInfraError(f"acorn bridge timed out: {exc}") from exc
    except OSError as exc:
        raise JSInfraError(f"failed to run Node bridge: {exc}") from exc
    if proc.returncode != 0:
        raise JSInfraError(
            f"acorn bridge exited {proc.returncode}: "
            f"{proc.stderr.decode('utf-8', 'replace')[:500]}"
        )
    try:
        return json.loads(proc.stdout.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise JSInfraError(f"acorn bridge returned invalid JSON: {exc}") from exc


def _result_from_dict(data: dict[str, Any]) -> JSParseResult:
    if data.get("ok"):
        return JSParseResult(ok=True, ast=data.get("ast"))
    errors = [
        JSSyntaxError(line=e.get("line", 1), col=e.get("col", 1), message=e.get("message", "syntax error"))
        for e in data.get("errors", [])
    ]
    return JSParseResult(ok=False, errors=errors)


def parse_js(source: str) -> JSParseResult:
    """Strictly parse JS source with acorn. Raises JSInfraError if the bridge
    cannot run; returns JSParseResult(ok=False, errors=[...]) for syntax errors.
    """
    data = _run_bridge(source.encode("utf-8"), timeout=15)
    return _result_from_dict(data)


def parse_js_batch(sources: list[str]) -> list[JSParseResult]:
    """Strictly parse many JS sources in one Node process (--batch mode).

    Process startup (~50ms) dominates single parses; a workflow with many
    Code nodes should batch. Order matches `sources`; infra failure raises
    JSInfraError; per-script syntax errors are per-result.
    """
    if not sources:
        return []
    payload = json.dumps({"scripts": sources}).encode("utf-8")
    timeout = max(15.0, 2.0 * len(sources))
    data = _run_bridge(payload, timeout, "--batch")
    results = data.get("results")
    if not isinstance(results, list) or len(results) != len(sources):
        raise JSInfraError(f"acorn batch bridge returned {len(results) if isinstance(results, list) else 'non-list'} results for {len(sources)} scripts")
    return [_result_from_dict(r) for r in results]
