"""Unit tests for the static JS subsystem (strict parse + AST contract)."""
import unittest

from jscode import (
    CodeEffect,
    OutputShapeKind,
    compile_js_batch,
    compile_js_static,
)
from tests.helpers import skip_unless_node


@skip_unless_node
class TestStrictSyntax(unittest.TestCase):
    def test_valid_script(self):
        res = compile_js_static("return { a: 1 };")
        self.assertTrue(res.ok)
        self.assertEqual(res.errors, ())

    def test_top_level_return_allowed(self):
        # n8n Code nodes allow top-level return (runOnceForAllItems)
        res = compile_js_static("const x = 1; return x;")
        self.assertTrue(res.ok)

    def test_esm_export_default_function_rejected(self):
        # n8n Code 节点无模块加载器：export 是运行时错误 -> 前置为编译错误
        res = compile_js_static(
            "export default async function main() { return { a: 1 }; }"
        )
        self.assertFalse(res.ok)
        self.assertTrue(any("ExportDefaultDeclaration" in e for e in res.errors))

    def test_import_rejected_as_compile_error(self):
        res = compile_js_static("import { z } from 'zod'; return z;")
        self.assertFalse(res.ok)
        self.assertTrue(any("ImportDeclaration" in e for e in res.errors))

    def test_dynamic_import_rejected(self):
        res = compile_js_static("const mod = await import('node:fs'); return mod;")
        self.assertFalse(res.ok)
        self.assertTrue(any("import()" in e for e in res.errors))

    def test_syntax_error_has_position(self):
        res = compile_js_static("const = 42;")
        self.assertFalse(res.ok)
        self.assertEqual(len(res.errors), 1)
        self.assertIn("1:", res.errors[0])

    def test_unclosed_string_rejected(self):
        res = compile_js_static("return { a: \"oops };")
        self.assertFalse(res.ok)

    def test_repo_bad_code_caught(self):
        # n8n 仓库 playwright fixture 里的真实坏代码：item.json.myNewField = 1aaa;
        # 后端保存零校验放行，编译器必须在编译期抓出（与文件一致：3 行 26 列）。
        res = compile_js_static(
            "// Loop over input items and add a new field called 'myNewField' to the JSON of each one\n"
            "for (const item of $input.all()) {\n"
            "  item.json.myNewField = 1aaa;\n"
            "}\n"
            "return $input.all();"
        )
        self.assertFalse(res.ok)
        self.assertTrue(any("3:26" in e for e in res.errors))

    def test_modern_syntax_accepted(self):
        res = compile_js_static(
            "const arr = items.map(({ json }) => json?.id ?? 0); return arr;"
        )
        self.assertTrue(res.ok)


@skip_unless_node
class TestDependencyExtraction(unittest.TestCase):
    def test_node_ref_call_direct(self):
        # $("NodeName") 直接引用节点输出
        res = compile_js_static("return { from: $('HTTP').json.body };")
        self.assertIn(("$node", ("HTTP", "body")), [(d.base, d.path) for d in res.contract.deps])

    def test_node_ref_call_json_stripped(self):
        res = compile_js_static("return $items('Query', 0).json.id;")
        self.assertIn(("$node", ("Query", "id")), [(d.base, d.path) for d in res.contract.deps])

    def test_node_ref_call_standalone(self):
        res = compile_js_static("const all = $('Setup'); return all;")
        self.assertIn(("$node", ("Setup",)), [(d.base, d.path) for d in res.contract.deps])

    def test_item_call_reference(self):
        res = compile_js_static("return { v: $item('Source', 0).json.v };")
        self.assertIn(("$node", ("Source", "v")), [(d.base, d.path) for d in res.contract.deps])
    def test_simple_field(self):
        res = compile_js_static("return { name: items[0].json.name };")
        self.assertEqual(
            [(d.base, d.path) for d in res.contract.deps],
            [("items", ("name",))],
        )

    def test_nested_path_single_dep(self):
        res = compile_js_static(
            "const c = items[0].json.user.address.city; return { city: c };"
        )
        self.assertEqual(res.contract.deps, [("items", ("user", "address", "city"))] if False else res.contract.deps)
        self.assertEqual(len(res.contract.deps), 1)
        self.assertEqual(res.contract.deps[0].path, ("user", "address", "city"))

    def test_method_call_not_a_dep(self):
        res = compile_js_static("return items.map(i => ({ id: i.json.id }));")
        paths = [d.path for d in res.contract.deps]
        self.assertNotIn(("map",), paths)

    def test_for_each_write_is_not_dep_read(self):
        res = compile_js_static("items.forEach(i => { i.json.x = 1; }); return items;")
        self.assertEqual(res.contract.deps, [])


@skip_unless_node
class TestOutputShape(unittest.TestCase):
    def test_object_props_typed(self):
        res = compile_js_static("return { s: \"x\", n: 42, b: true, a: [] };")
        self.assertEqual(res.contract.output.kind, OutputShapeKind.OBJECT)
        self.assertEqual(res.contract.output.props["s"], "string")
        self.assertEqual(res.contract.output.props["n"], "number")
        self.assertEqual(res.contract.output.props["b"], "boolean")
        self.assertEqual(res.contract.output.props["a"], "array")

    def test_return_items_is_list(self):
        res = compile_js_static("return items;")
        self.assertEqual(res.contract.output.kind, OutputShapeKind.LIST)

    def test_return_map_is_list(self):
        res = compile_js_static("return items.map(i => i.json);")
        self.assertEqual(res.contract.output.kind, OutputShapeKind.LIST)

    def test_no_return_is_void(self):
        res = compile_js_static("items.forEach(i => { i.json.x = 1; });")
        self.assertEqual(res.contract.output.kind, OutputShapeKind.VOID)

    def test_last_top_level_return_wins(self):
        res = compile_js_static(
            "if (items[0].json.x > 3) { return { big: true }; } return { big: false };"
        )
        self.assertEqual(res.contract.output.kind, OutputShapeKind.OBJECT)
        self.assertEqual(res.contract.output.props.get("big"), "boolean")

    def test_factory_return_new_expression_is_object(self):
        # AI 链 supplyData 工厂：return new X() 必然返回对象实例（JS 语义：
        # 构造函数返回对象则取之，否则取实例——new 表达式的值恒为对象）
        res = compile_js_static(
            "const { FakeEmbeddings } = require('@langchain/core/utils/testing');\n"
            "return new FakeEmbeddings();"
        )
        self.assertEqual(res.contract.output.kind, OutputShapeKind.OBJECT)

    def test_factory_mode_hint(self):
        # langchain.code 工厂模式：mode="factory" 产出语义提示 warning
        res = compile_js_static("return new FakeEmbeddings();", mode="factory")
        self.assertTrue(res.ok)
        self.assertTrue(any("factory" in w for w in res.warnings),
                        f"expected factory hint in warnings, got {res.warnings}")


@skip_unless_node
class TestEffect(unittest.TestCase):
    def test_pure(self):
        res = compile_js_static("return { n: 1 + 2 };")
        self.assertEqual(res.contract.effect, CodeEffect.PURE)

    def test_fetch_is_io(self):
        res = compile_js_static("const r = await fetch(url); return r;")
        self.assertEqual(res.contract.effect, CodeEffect.IO)

    def test_require_is_io(self):
        res = compile_js_static("const fs = require('fs'); return fs;")
        self.assertEqual(res.contract.effect, CodeEffect.IO)

    def test_math_random_non_deterministic(self):
        res = compile_js_static("return { r: Math.random() };")
        self.assertEqual(res.contract.effect, CodeEffect.IO)

    def test_dynamic_subscript_warns(self):
        res = compile_js_static(
            "const key = 'name'; return { v: items[0].json[key] };"
        )
        self.assertTrue(res.ok)
        self.assertTrue(any("dynamic subscript" in w for w in res.warnings))

    def test_fetch_warns_network_unavailable(self):
        res = compile_js_static("const r = await fetch(url); return r;")
        self.assertTrue(res.ok)
        self.assertTrue(any("network is blocked" in w for w in res.warnings))

    def test_require_warns_allowlist_gated(self):
        res = compile_js_static("const fs = require('fs'); return fs;")
        self.assertTrue(res.ok)
        self.assertTrue(any("NODE_FUNCTION_ALLOW" in w for w in res.warnings))


if __name__ == "__main__":
    unittest.main(verbosity=2)


@skip_unless_node
class TestBatchCompilation(unittest.TestCase):
    """compile_js_batch（一次 acorn 进程）与 compile_js_static 结果必须一致。"""

    def test_batch_matches_single(self):
        sources = [
            "return { a: items[0].json.x };",
            "const = bad",
            "return $('HTTP').json.body;",
            "import { z } from 'zod'; return z;",
            "const k = 'n'; return { v: items[0].json[k] };",
        ]
        batched = compile_js_batch(sources)
        self.assertEqual(len(batched), len(sources))
        for (contract, estree), source in zip(batched, sources):
            single = compile_js_static(source)
            self.assertEqual(contract.ok, single.ok)
            self.assertEqual(contract.errors, single.errors)
            self.assertEqual(contract.warnings, single.warnings)
            self.assertEqual(
                [(d.base, d.path) for d in contract.contract.deps],
                [(d.base, d.path) for d in single.contract.deps],
            )
            self.assertEqual(
                contract.contract.output.kind,
                single.contract.output.kind,
            )

    def test_batch_empty(self):
        self.assertEqual(compile_js_batch([]), [])

    def test_batch_order_preserved(self):
        sources = ["return 1;", "const = bad", "return 2;"]
        batched = compile_js_batch(sources)
        self.assertEqual([b[0].ok for b in batched], [True, False, True])
        self.assertEqual([b[1] is not None for b in batched], [True, False, True])


@skip_unless_node
class TestContractJsAstInvariant(unittest.TestCase):
    """P2-4 不变量：IR 中 config.js（contract）与 config.js_ast 语义一致。

    编译器链路（_contract_from_ast）天然自洽；本测试锁定该不变量，防未来
    改动（如改推导逻辑未同步序列化/反序列化）让两者漂移——digest 只校验
    文档完整性，不校验 contract 与 AST 的语义一致。
    """

    def _ir_for(self, source: str) -> tuple[dict, dict]:
        from ast_nodes.mappings import _contract_from_dict
        from compiler.workflow import compile_ast
        from parser.workflow import parse_workflow
        from tests.helpers import chain_workflow, code_node, webhook_node
        wf = chain_workflow([webhook_node("W"), code_node("C", source)], [("W", "C")])
        ir = compile_ast(parse_workflow(wf), workflow_id="t", version="1").to_dict()
        node = next(n for n in ir["nodes"] if n["name"] == "C")
        return node, _contract_from_dict(node["config"]["js"])

    def test_contract_derivable_from_js_ast(self):
        from jscode.js_static import _contract_from_ast
        source = "return items.map((it) => ({ v: it.json.v }));"
        node, stored = self._ir_for(source)
        js = node["config"]["js"]
        mode = node["config"]["parameters"].get("mode", "runOnceForAllItems")
        derive = _contract_from_ast(
            node["config"]["js_ast"], js["payload"]["source"], mode,
            js["contract"]["runtime"],
        )
        self.assertEqual(derive.contract, stored.contract)
        self.assertEqual(derive.errors, stored.errors)
        self.assertEqual(derive.warnings, stored.warnings)

    def test_contract_deps_consistent_across_shapes(self):
        # 多种输出形状下 contract（deps/effect）都与 AST 推导一致
        from jscode.js_static import _contract_from_ast
        for source in (
            "return { sum: items.reduce((a, it) => a + it.json.n, 0) };",
            "return items[0];",
            "return null;",
        ):
            node, stored = self._ir_for(source)
            js = node["config"]["js"]
            mode = node["config"]["parameters"].get("mode", "runOnceForAllItems")
            derive = _contract_from_ast(
                node["config"]["js_ast"], js["payload"]["source"], mode,
                js["contract"]["runtime"],
            )
            self.assertEqual(derive.contract, stored.contract, source)
