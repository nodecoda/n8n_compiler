"""零依赖覆盖率测量 — 标准库 trace 模块驱动全量测试。

用法：
    python3 tests/coverage.py            # 跑全量测试 + 模块级覆盖率
    python3 tests/coverage.py --quiet    # 只输出汇总行
    python3 tests/coverage.py --threshold 80   # 低于阈值 exit 1（CI 门禁）

按模块聚合语句覆盖率；只统计本项目源码（parser/checker/compiler/code/...），
不含测试与第三方。trace 模块是解释级计数，比率天然保守（import 即计入未执行
行），阈值治理以「相对基线」为准（见 TESTING.md）。
"""
from __future__ import annotations

import argparse
import ast
import sys
import trace
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PACKAGES = (
    "parser", "checker", "compiler", "jscode",  # jscode：JS 静态编译（原 code/，v5 改名避 stdlib 冲突）
    "ast_nodes", "type_system", "values", "scope",
    "runtime",  # 架构审核 P2-N3：decompile/deploy 是部署闭环最险层，必须受门禁
)
TOP_MODULES = ("typed_ir", "manifest", "cli")

# 需要 Node/acorn 的测试（无 Node 时该组 skip，覆盖率会略降）
NODE_DEPENDENT = ("tests.test_code_js",)


def _is_project_module(filename: str) -> bool:
    path = Path(filename).resolve()
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        return False
    first = rel.parts[0] if rel.parts else ""
    # rel.name 带 ".py" 而 TOP_MODULES 不含扩展名——比 stem（去扩展名）才匹配。
    # 回归测试 test_core_packages_counted 抓出此前 typed_ir/manifest/cli 从未进报表。
    return first in PACKAGES or rel.stem in TOP_MODULES


def _executable_lines(path: Path) -> set[int]:
    """用 ast 统计真实可执行行（不含注释/空白/纯文档串）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.If, ast.For, ast.AsyncFor,
                             ast.While, ast.Try, ast.With, ast.AsyncWith,
                             ast.ExceptHandler, ast.Match)):
            continue
        lineno = getattr(node, "lineno", None)
        if isinstance(lineno, int):
            lines.add(lineno)
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="zero-dependency coverage")
    parser.add_argument("--quiet", action="store_true", help="summary line only")
    parser.add_argument("--threshold", type=float, default=0.0,
                        help="exit 1 if total coverage below this percent")
    parser.add_argument("--include", default=",".join((*PACKAGES, *TOP_MODULES)),
                        help="comma-separated package prefixes to report")
    args = parser.parse_args(argv)

    tracer = trace.Trace(count=True, trace=False, ignoredirs=[sys.prefix, str(ROOT / "tests")])

    def _run_all() -> unittest.TestResult:
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
        return unittest.TextTestRunner(verbosity=0).run(suite)

    result = tracer.runfunc(_run_all)
    counts = tracer.results().counts

    # 按模块聚合：total = 文件可执行行（ast），executed = 至少执行一次的行
    executed_lines: dict[str, set[int]] = {}
    for (filename, lineno), count in counts.items():
        if not _is_project_module(filename) or lineno == 0 or count == 0:
            continue
        mod = str(Path(filename).resolve().relative_to(ROOT))
        executed_lines.setdefault(mod, set()).add(lineno)

    module_lines: dict[str, tuple[int, int]] = {}
    for mod, executed in executed_lines.items():
        total = len(_executable_lines(ROOT / mod))
        module_lines[mod] = (total, len(executed))

    include_prefixes = tuple(args.include.split(","))
    rows = sorted(
        ((mod, ex, tot) for mod, (tot, ex) in module_lines.items()
         if mod.startswith(include_prefixes)),
        key=lambda r: (r[1] / r[2] if r[2] else 1.0, r[0]),
    )
    total_ex = sum(r[1] for r in rows)
    total_tot = sum(r[2] for r in rows)
    overall = (total_ex / total_tot * 100) if total_tot else 0.0

    if not args.quiet:
        print(f"\n{'module':<40}{'cov%':>7}{'exec':>7}{'total':>7}")
        print("-" * 61)
        for mod, ex, tot in rows:
            pct = ex / tot * 100 if tot else 100.0
            print(f"{mod:<40}{pct:>6.1f}%{ex:>7}{tot:>7}")
        print("-" * 61)
        skipped = len(result.skipped)
        suffix = f" (skipped: {skipped})" if skipped else ""
        print(f"TOTAL{suffix:<28}{overall:>6.1f}%{total_ex:>7}{total_tot:>7}")
        print(f"tests: ran={result.testsRun} failures={len(result.failures)} "
              f"errors={len(result.errors)} skipped={skipped}")
    else:
        print(f"COVERAGE {overall:.1f}% ({total_ex}/{total_tot}) "
              f"ran={result.testsRun} fail={len(result.failures)} err={len(result.errors)} "
              f"skip={len(result.skipped)}")

    if result.failures or result.errors:
        return 1
    if args.threshold and overall < args.threshold:
        print(f"coverage {overall:.1f}% below threshold {args.threshold:.1f}%", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
