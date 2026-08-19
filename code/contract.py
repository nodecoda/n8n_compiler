"""Typed contract for statically-compiled Code nodes (S2/S3).

The contract is the authoritative view of a Code node's semantics. The JS
source travels as an opaque payload and is never evaluated by the compiler.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CodeEffect(str, Enum):
    """Static side-effect classification of the JS body.

    PURE   - no observable side effects (no network/IO/global mutation):
             eligible for constant folding and parallel execution.
    IO     - may perform network or external I/O (fetch, XMLHttpRequest,
             fs access via require): requires an external executor.
    UNKNOWN - could not be classified statically: treated as IO.
    """

    PURE = "pure"
    IO = "io"
    UNKNOWN = "unknown"


class OutputShapeKind(str, Enum):
    VOID = "void"          # no return (statement body) -> no data emitted
    OBJECT = "object"      # return {literal} -> typed props
    LIST = "list"          # return items.map(...) / array expression
    ANY = "any"            # cannot be determined statically


@dataclass(frozen=True)
class FieldDep:
    """One statically-extracted input dependency path.

    ``base`` is the access root (``items`` / ``$json`` / ``$input`` / ``item``),
    ``path`` the dotted field path below it (e.g. ``["body","id"]``).
    """

    base: str
    path: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutputShape:
    kind: OutputShapeKind = OutputShapeKind.ANY
    props: dict[str, str] = field(default_factory=dict)  # field name -> static type
    elem: str | None = None                               # list element type when LIST


@dataclass(frozen=True)
class Contract:
    """S2 static contract of one Code node."""

    deps: list[FieldDep] = field(default_factory=list)          # input dependencies
    output: OutputShape = field(default_factory=OutputShape)    # output shape
    effect: CodeEffect = CodeEffect.UNKNOWN                     # PURE / IO / UNKNOWN
    runtime: str = "external"                                    # external | direct | static-only


@dataclass(frozen=True)
class CodePayload:
    """Opaque payload carried into the IR (S3)."""

    language: str = "js"
    source: str = ""


@dataclass(frozen=True)
class StaticContract:
    """Full result of static JS compilation (S1 + S2)."""

    contract: Contract
    payload: CodePayload
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        deps = [
            {"base": d.base, "path": list(d.path)}
            for d in self.contract.deps
        ]
        output = {
            "kind": self.contract.output.kind.value,
            "props": dict(self.contract.output.props),
            "elem": self.contract.output.elem,
        }
        return {
            "contract": {
                "deps": deps,
                "output": output,
                "effect": self.contract.effect.value,
                "runtime": self.contract.runtime,
            },
            "payload": {
                "language": self.payload.language,
                "source": self.payload.source,
            },
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }
