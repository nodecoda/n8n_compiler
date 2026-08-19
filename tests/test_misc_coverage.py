"""杂项模块定向补测：序列化 round-trip、scope/symbol_table、js 桥失败路径。

覆盖 values.reference / ast_nodes.configs / ast_nodes.nodes / scope.* /
parser.node_adaptors 的未执行分支；js_parser 的 JSInfraError 失败路径用
monkeypatch 构造（不依赖真实 Node 状态），js_static 走真实 acorn 桥。
"""
import json
import unittest
from unittest import mock

from ast_nodes.configs import ExceptionConfig, N8NErrorPolicy
from ast_nodes.connection import Connection
from ast_nodes.node_decls import CodeNode, SetNode, TriggerNode
from ast_nodes.nodes import WorkflowAST, node_to_config_dict
from code import JSInfraError  # noqa: E402
from code.js_parser import JSSyntaxError, _run_bridge, parse_js_batch
from parser.node_adaptors import _code_source, adapt_node
from scope.scope import DuplicateSymbolError, Scope, ScopeLevel
from scope.symbol import Symbol, SymbolKind
from scope.symbol_table import SymbolTable
from type_system.datatype import DataType
from type_system.typeinfo import TypeInfo
from values.reference import FieldInfo, Reference, Source
from values.variable import GlobalVarType, RefSourceType


class TestReferenceSerialization(unittest.TestCase):
    def test_reference_round_trip(self):
        ref = Reference(from_node_key="A", from_path=["body", "id"])
        d = ref.to_dict()
        self.assertEqual(d, {"from_node_key": "A", "from_path": ["body", "id"]})
        self.assertEqual(Reference.from_dict(d), ref)
        self.assertFalse(ref.is_global_variable())

    def test_reference_with_variable_type(self):
        ref = Reference(from_node_key="", from_path=["DB"], variable_type=GlobalVarType.ENV)
        d = ref.to_dict()
        self.assertEqual(d["variable_type"], "env")
        restored = Reference.from_dict(d)
        self.assertTrue(restored.is_global_variable())
        self.assertEqual(restored.variable_type, GlobalVarType.ENV)
        # 无 variable_type 字段 -> None
        self.assertIsNone(Reference.from_dict({"from_node_key": "A", "from_path": []}).variable_type)

    def test_source_literal_and_ref(self):
        lit = Source(literal=42)
        self.assertTrue(lit.is_literal())
        self.assertEqual(lit.to_dict(), {"literal": 42})
        self.assertEqual(Source.from_dict({"literal": "x"}).literal, "x")
        ref = Source(ref=Reference(from_node_key="A", from_path=["a"]))
        self.assertFalse(ref.is_literal())
        self.assertIn("ref", ref.to_dict())
        self.assertEqual(Source.from_dict(ref.to_dict()).ref, ref.ref)

    def test_field_info_round_trip(self):
        fi = FieldInfo(path=["a", "b"], source=Source(literal=1))
        self.assertEqual(FieldInfo.from_dict(fi.to_dict()), fi)


class TestConfigsSerialization(unittest.TestCase):
    def test_exception_config_to_dict(self):
        cfg = ExceptionConfig(timeout_ms=500, max_retry=2, data_on_err="stop", process_type=1)
        self.assertEqual(cfg.to_dict(),
                         {"timeout_ms": 500, "max_retry": 2, "data_on_err": "stop", "process_type": 1})
        self.assertEqual(ExceptionConfig().to_dict(), {})

    def test_error_policy_to_dict_full(self):
        pol = N8NErrorPolicy(on_error="continueErrorOutput", retry_on_fail=True,
                             max_tries=3, wait_between_tries=10)
        self.assertEqual(pol.to_dict(), {"on_error": "continueErrorOutput", "retry_on_fail": True,
                                         "max_tries": 3, "wait_between_tries": 10})
        self.assertEqual(N8NErrorPolicy().to_dict(), {"on_error": "stopWorkflow"})

    def test_error_policy_from_node(self):
        raw = {"onError": "continueRegularOutput", "retryOnFail": True, "maxTries": 5,
               "waitBetweenTries": 30}
        pol = N8NErrorPolicy.from_node(raw)
        self.assertEqual(pol.on_error, "continueRegularOutput")
        self.assertTrue(pol.retry_on_fail)
        self.assertEqual(pol.max_tries, 5)
        self.assertEqual(pol.wait_between_tries, 30)
        # 非法 onError -> 回退 stopWorkflow
        self.assertEqual(N8NErrorPolicy.from_node({"onError": "nope"}).on_error, "stopWorkflow")
        self.assertFalse(N8NErrorPolicy.from_node({}).retry_on_fail)


class TestScopeAndSymbolTable(unittest.TestCase):
    def test_scope_define_duplicate(self):
        s = Scope(name="wf", level=ScopeLevel.WORKFLOW)
        sym = Symbol(name="X", type=TypeInfo.any(), kind=SymbolKind.OUTPUT)
        s.define(sym)
        with self.assertRaises(DuplicateSymbolError):
            s.define(sym)
        # replace=True 允许覆盖
        s.define(sym, replace=True)
        self.assertEqual(s.resolve_local("X"), sym)
        self.assertIsNone(s.resolve_local("Missing"))

    def test_scope_resolve_parent_chain(self):
        child = Scope(name="n1", level=ScopeLevel.NODE,
                      parent=Scope(name="wf", level=ScopeLevel.WORKFLOW))
        child.parent.define(Symbol(name="Global", type=TypeInfo.any(), kind=SymbolKind.OUTPUT))
        self.assertEqual(child.resolve("Global").name, "Global")
        self.assertIsNone(child.resolve("Absent"))

    def test_symbol_table(self):
        wf_scope = Scope(name="wf", level=ScopeLevel.WORKFLOW)
        st = SymbolTable([wf_scope])
        st.define("wf", Symbol(name="S", type=TypeInfo.any(), kind=SymbolKind.OUTPUT))
        self.assertEqual(st.resolve("wf", "S").name, "S")
        self.assertIsNone(st.resolve("wf", "Nope"))
        self.assertIsNone(st.resolve("unknown_scope", "S"))
        # register_scope 路径
        other = Scope(name="wf2", level=ScopeLevel.NODE)
        st.register_scope(other)
        self.assertIn("wf2", st.scopes)


class TestNodeDeclAndAst(unittest.TestCase):
    def test_input_type_at_path(self):
        # P2-6 回归：input_type_at 与 output_type_at 对称（validator 已改用
        # 方法，删除重复的 _type_at_path）
        node = SetNode(
            key="S", n8n_type="n8n-nodes-base.set", name="S",
            input_types={"main": TypeInfo.object(properties={"a": TypeInfo.object(properties={"b": TypeInfo.number()})})})
        self.assertEqual(node.input_type_at(["a"]).type, DataType.OBJECT)
        self.assertEqual(node.input_type_at(["a", "b"]).type, DataType.NUMBER)
        # 端口名优先
        node.input_types["port"] = TypeInfo.object(properties={"p": TypeInfo.string()})
        self.assertEqual(node.input_type_at(["port"]).type, DataType.OBJECT)
        self.assertEqual(node.input_type_at(["port", "p"]).type, DataType.STRING)
        # 空路径 / 缺字段 -> None
        self.assertIsNone(node.input_type_at([]))
        self.assertIsNone(node.input_type_at(["nope"]))

    def test_output_type_at_path(self):
        node = SetNode(
            key="S", n8n_type="n8n-nodes-base.set", name="S",
            output_types={"main": TypeInfo.object(properties={"a": TypeInfo.object(properties={"b": TypeInfo.number()})})})
        self.assertEqual(node.output_type_at(["a"]).type, DataType.OBJECT)
        self.assertEqual(node.output_type_at(["a", "b"]).type, DataType.NUMBER)
        # 端口名优先
        node.output_types["port"] = TypeInfo.object(properties={"p": TypeInfo.string()})
        self.assertEqual(node.output_type_at(["port"]).type, DataType.OBJECT)
        self.assertEqual(node.output_type_at(["port", "p"]).type, DataType.STRING)
        # 空路径 / 缺字段 -> None
        self.assertIsNone(node.output_type_at([]))
        self.assertIsNone(node.output_type_at(["a", "missing"]))
        self.assertIsNone(node.output_type_at(["nope"]))

    def test_to_config_dict_and_node_to_config_dict(self):
        node = SetNode(key="S", n8n_type="n8n-nodes-base.set", name="S",
                       type_version=3, position=(1.0, 2.0), parameters={"v": 1})
        cfg = node.to_config_dict()
        self.assertEqual(cfg["n8n_type"], "n8n-nodes-base.set")
        self.assertEqual(cfg["type_version"], 3)
        self.assertEqual(cfg["position"], [1.0, 2.0])
        d = node_to_config_dict(node)
        self.assertEqual(d["key"], "S")
        self.assertEqual(d["type"], node.node_type.value)
        self.assertEqual(d["config"]["parameters"], {"v": 1})

    def test_workflow_ast_helpers(self):
        trig = TriggerNode(key="T", n8n_type="n8n-nodes-base.manualTrigger", name="T")
        setn = SetNode(key="S", n8n_type="n8n-nodes-base.set", name="S")
        ast = WorkflowAST(
            nodes={"T": trig, "S": setn},
            connections=[Connection(from_node="T", from_port="main", to_node="S")],
            hierarchy={"T": "parent"},
        )
        self.assertEqual(ast.entry_keys, ["T"])
        self.assertEqual(ast.predecessors("S"), {"T"})
        self.assertEqual(ast.upstream_of(), {"T": set(), "S": {"T"}})
        d = ast.to_dict()
        self.assertEqual(set(d["nodes"]), {"T", "S"})
        self.assertEqual(d["connections"][0]["to_node"], "S")
        self.assertEqual(d["hierarchy"], {"T": "parent"})


class TestNodeAdaptors(unittest.TestCase):
    def test_code_source_python_mode_rejected(self):
        # 源不受支持 -> ValueError（与 JSInfraError 桥不可用区分，P1-3）
        with self.assertRaises(ValueError):
            _code_source({"language": "python", "pythonCode": "print(1)"})
        # 非 python 无源码 -> ("", mode)
        self.assertEqual(_code_source({}), ("", "runOnceForAllItems"))

    def test_langchain_code_source_factory_routing(self):
        # P1-2（v4）：langchain.code 取源分流——supplyData 固定集合、旧版
        # 顶层字符串兜底、空源三态；mode 恒 "factory"
        params = {"code": {"supplyData": {"code": "return new F();"}}}
        self.assertEqual(
            _code_source(params, node_type="@n8n/n8n-nodes-langchain.code"),
            ("return new F();", "factory"))
        # 旧版 n8n 导出：parameters.code 直接是字符串
        self.assertEqual(
            _code_source({"code": "return new F();"},
                         node_type="@n8n/n8n-nodes-langchain.code"),
            ("return new F();", "factory"))
        # 空源（无 code / 空串）-> ("", factory)，adapt_node 走错误契约路径
        self.assertEqual(
            _code_source({"code": {"supplyData": {}}},
                         node_type="@n8n/n8n-nodes-langchain.code"),
            ("", "factory"))
        # 普通 Code 节点不受分流影响
        self.assertEqual(_code_source({"jsCode": "return 1;"}, node_type="n8n-nodes-base.code"),
                         ("return 1;", "runOnceForAllItems"))

    def test_code_node_no_source_gets_error_contract(self):
        node = adapt_node({"name": "C", "type": "n8n-nodes-base.code",
                           "typeVersion": 2, "position": [0, 0],
                           "parameters": {"language": "javaScript"}})
        self.assertIsInstance(node, CodeNode)
        self.assertIsNotNone(node.js_contract)
        self.assertIn("no JS source", node.js_contract.errors[0])
        self.assertIsNone(node.js_ast)

    def test_code_node_with_js_cache(self):
        contract = mock.Mock()
        node = adapt_node({"name": "C", "type": "n8n-nodes-base.code",
                           "typeVersion": 2, "position": [0, 0],
                           "parameters": {"jsCode": "return 1;"}},
                          js_cache={"C": (contract, {"type": "Program"})})
        self.assertIs(node.js_contract, contract)
        self.assertEqual(node.js_ast, {"type": "Program"})

    def test_credentials_copied(self):
        node = adapt_node({"name": "N", "type": "n8n-nodes-base.httpRequest",
                           "typeVersion": 4, "position": [0, 0],
                           "parameters": {}, "credentials": {"httpHeaderAuth": {"name": "h"}}})
        self.assertEqual(node.credentials, {"httpHeaderAuth": {"name": "h"}})


class TestJsBridgeInfraFailures(unittest.TestCase):
    """JSInfraError 基础设施失败路径：不静默降级，明确失败。"""

    def test_find_node_env_override(self):
        with mock.patch.dict("os.environ", {"NODE": "/opt/node/bin/node"}, clear=False):
            from code.js_parser import find_node
            self.assertEqual(find_node(), "/opt/node/bin/node")

    def test_bridge_script_missing(self):
        with mock.patch("code.js_parser._SCRIPT", mock.Mock(exists=lambda: False)):
            with self.assertRaises(JSInfraError) as ctx:
                _run_bridge(b"{}", timeout=5)
            self.assertIn("bridge script missing", str(ctx.exception))

    def test_bridge_timeout(self):
        with mock.patch("code.js_parser._SCRIPT", mock.Mock(exists=lambda: True)), \
             mock.patch("code.js_parser.subprocess.run",
                        side_effect=__import__("subprocess").TimeoutExpired("node", 5)):
            with self.assertRaises(JSInfraError) as ctx:
                _run_bridge(b"{}", timeout=5)
            self.assertIn("timed out", str(ctx.exception))

    def test_bridge_nonzero_exit(self):
        proc = mock.Mock(returncode=1, stderr=b"boom")
        with mock.patch("code.js_parser._SCRIPT", mock.Mock(exists=lambda: True)), \
             mock.patch("code.js_parser.subprocess.run", return_value=proc):
            with self.assertRaises(JSInfraError) as ctx:
                _run_bridge(b"{}", timeout=5)
            self.assertIn("exited 1", str(ctx.exception))

    def test_bridge_invalid_json(self):
        proc = mock.Mock(returncode=0, stdout=b"not json")
        with mock.patch("code.js_parser._SCRIPT", mock.Mock(exists=lambda: True)), \
             mock.patch("code.js_parser.subprocess.run", return_value=proc):
            with self.assertRaises(JSInfraError) as ctx:
                _run_bridge(b"{}", timeout=5)
            self.assertIn("invalid JSON", str(ctx.exception))

    def test_parse_js_batch_empty_and_mismatch(self):
        self.assertEqual(parse_js_batch([]), [])
        proc = mock.Mock(returncode=0, stdout=json.dumps({"results": [{"ok": True}]}).encode())
        with mock.patch("code.js_parser._SCRIPT", mock.Mock(exists=lambda: True)), \
             mock.patch("code.js_parser.subprocess.run", return_value=proc):
            # 结果数与脚本数不匹配 -> 明确失败
            with self.assertRaises(JSInfraError) as ctx:
                parse_js_batch(["a", "b"])
            self.assertIn("results", str(ctx.exception))

    def test_error_to_dict(self):
        err = JSSyntaxError(line=2, col=5, message="oops")
        self.assertEqual(err.to_dict(), {"line": 2, "col": 5, "message": "oops"})


class TestJsStatic(unittest.TestCase):
    """真实 acorn 桥（Node 可用时）验证 js_static 高层入口。"""

    def test_compile_js_static_warning_path(self):
        res = __import__("code").compile_js_static("return $json;")
        self.assertTrue(res.ok)
        # runOnceForAllItems 有 mode hint warning
        self.assertTrue(any("runOnceForAllItems" in w for w in res.warnings))

    def test_scan_js_source_legacy_compat(self):
        from code.js_static import scan_js_source
        tokens, errors, warnings = scan_js_source("return 1;")
        self.assertEqual(tokens, [])
        self.assertEqual(errors, [])
        self.assertIsInstance(warnings, list)
        tokens, errors, warnings = scan_js_source("const = ;")
        self.assertEqual(tokens, [])
        self.assertTrue(errors)
        self.assertEqual(warnings, [])

    def test_compile_js_batch_mixed(self):
        from code import compile_js_batch
        results = compile_js_batch(["return 1;", "const = ;"])
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0][0].ok)
        self.assertFalse(results[1][0].ok)
        self.assertTrue(results[1][0].errors)
        self.assertIsNotNone(results[0][1])  # 成功脚本保留 ESTree


if __name__ == "__main__":
    unittest.main()


class TestPublicErrorContracts(unittest.TestCase):
    """P3-5/P3-10：公开错误/拒绝 API 的契约固化。"""

    def test_reject_non_finite_public_and_raises(self):
        # P3-5：reject_non_finite 公开导出（替代私有 _reject_non_finite），
        # 作为 json parse_constant 使用；NaN/Infinity 都显式拒绝
        from json import loads
        from typed_ir import reject_non_finite
        for const in ("NaN", "Infinity", "-Infinity"):
            with self.assertRaises(ValueError) as ctx:
                loads('{"x": %s}' % const, parse_constant=reject_non_finite)
            self.assertIn("non-finite", str(ctx.exception))
        # 私有名不再可导入（防旧引用复活）
        import typed_ir
        self.assertFalse(hasattr(typed_ir, "_reject_non_finite"))

    def test_unsupported_source_error_is_value_error_subclass(self):
        # P3-10：UnsupportedSourceError 是 ValueError 子类（既有 assertRaises
        # 兼容），且消息含 "not supported"（CLI 据此前缀分类）
        from parser.node_adaptors import UnsupportedSourceError, _code_source
        self.assertTrue(issubclass(UnsupportedSourceError, ValueError))
        with self.assertRaises(UnsupportedSourceError) as ctx:
            _code_source({"language": "python", "pythonCode": "print(1)"})
        self.assertIn("not supported", str(ctx.exception))


class TestRefSourceType(unittest.TestCase):
    def test_from_str(self):
        self.assertEqual(RefSourceType.from_str("node_output"), RefSourceType.NODE_OUTPUT)
        self.assertEqual(RefSourceType.from_str("global_variable"), RefSourceType.GLOBAL_VARIABLE)
        # 非法值回退 NODE_OUTPUT
        self.assertEqual(RefSourceType.from_str("bogus"), RefSourceType.NODE_OUTPUT)
