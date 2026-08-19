"""checker 定向补测：手工构造 AST 覆盖 validate_* 的缺失分支。

这些分支（unknown_source/target、type_mismatch、retry_policy_incomplete、
WorkflowValidationError 等）用真实工作流 dict 难以精确构造（类型形状由节点
解析派生），因此按 parse_workflow 的产物形状手工构建 WorkflowAST。
"""
import unittest

from ast_nodes.connection import Connection
from ast_nodes.configs import N8NErrorPolicy
from ast_nodes.node_decls import CodeNode, ExitNode, SetNode, TriggerNode
from ast_nodes.node_type import EXIT_NODE_KEY
from ast_nodes.nodes import WorkflowAST
from checker.validator import (
    ValidationIssue,
    WorkflowValidationError,
    validate_connections,
    validate_node_semantics,
    validate_references,
    validate_workflow,
)
from code.contract import CodeEffect, CodePayload, Contract, OutputShape, StaticContract
from scope.symbol_table import SymbolTable
from type_system.typeinfo import TypeInfo
from values.reference import FieldInfo, Reference, Source
from values.variable import GlobalVarType


def _ast(nodes: dict, connections: list[Connection]) -> WorkflowAST:
    return WorkflowAST(nodes=nodes, connections=connections, symbol_table=SymbolTable())


class TestConnectionBranches(unittest.TestCase):
    def test_unknown_source_node(self):
        ast = _ast({"B": SetNode(key="B", n8n_type="n8n-nodes-base.set", name="B")},
                   [Connection(from_node="Ghost", from_port="main", to_node="B")])
        issues = validate_connections(ast)
        self.assertEqual([i.code for i in issues], ["unknown_source_node"])

    def test_unknown_target_node(self):
        ast = _ast({"A": SetNode(key="A", n8n_type="n8n-nodes-base.set", name="A")},
                   [Connection(from_node="A", from_port="main", to_node="Ghost")])
        issues = validate_connections(ast)
        self.assertEqual([i.code for i in issues], ["unknown_target_node"])


class TestReferenceBranches(unittest.TestCase):
    """validate_references 缺失分支：缺块引用 / 缺节点 / 字段缺失 / 类型不匹配。"""

    @staticmethod
    def _typed_pair(source_type: TypeInfo, target_type: TypeInfo):
        a = SetNode(key="A", n8n_type="n8n-nodes-base.set", name="A",
                    output_types={"main": TypeInfo.object(properties={"a": source_type})})
        b = SetNode(key="B", n8n_type="n8n-nodes-base.set", name="B",
                    input_types={"main": TypeInfo.object(properties={"a": target_type})})
        b.input_sources = [FieldInfo(path=["a"],
                                     source=Source(ref=Reference(from_node_key="A", from_path=["a"])))]
        ast = _ast({"A": a, "B": b}, [Connection(from_node="A", from_port="main", to_node="B")])
        return ast

    def test_empty_ref_block_id(self):
        b = SetNode(key="B", n8n_type="n8n-nodes-base.set", name="B")
        b.input_sources = [FieldInfo(path=["x"], source=Source(ref=Reference(from_node_key="")))]
        ast = _ast({"B": b}, [])
        issues = validate_references(ast)
        self.assertEqual([i.code for i in issues], ["empty_ref_block_id"])

    def test_referenced_node_missing(self):
        b = SetNode(key="B", n8n_type="n8n-nodes-base.set", name="B")
        b.input_sources = [FieldInfo(path=["x"], source=Source(ref=Reference(from_node_key="Ghost")))]
        ast = _ast({"B": b}, [])
        issues = validate_references(ast)
        self.assertEqual([i.code for i in issues], ["referenced_node_missing"])

    def test_global_variable_path_empty(self):
        b = SetNode(key="B", n8n_type="n8n-nodes-base.set", name="B")
        b.input_sources = [FieldInfo(path=["x"],
                                     source=Source(ref=Reference(from_node_key="",
                                                                 variable_type=GlobalVarType.ENV)))]
        ast = _ast({"B": b}, [])
        issues = validate_references(ast)
        self.assertEqual([i.code for i in issues], ["global_variable_path_empty"])

    def test_source_field_missing(self):
        # 上游形状已知（number 字段 a）但引用不存在的字段 z -> source_field_missing
        ast = self._typed_pair(TypeInfo.number(), TypeInfo.string())
        ast.nodes["B"].input_sources = [
            FieldInfo(path=["z"], source=Source(ref=Reference(from_node_key="A", from_path=["z"])))
        ]
        issues = validate_references(ast)
        self.assertEqual([i.code for i in issues], ["source_field_missing"])

    def test_type_mismatch(self):
        # number -> list[number] 不可赋（非 string 可转场景）-> type_mismatch
        ast = self._typed_pair(TypeInfo.number(), TypeInfo.array(TypeInfo.number()))
        issues = validate_references(ast)
        codes = [i.code for i in issues]
        self.assertIn("type_mismatch", codes)
        self.assertTrue(any("cannot assign" in i.message for i in issues))

    def test_number_to_string_is_assignable(self):
        # number -> string 运行时自动转（n8n 语义），不得报 type_mismatch
        ast = self._typed_pair(TypeInfo.number(), TypeInfo.string())
        issues = validate_references(ast)
        self.assertEqual([i.code for i in issues], [])


class TestSemanticsBranches(unittest.TestCase):
    def test_retry_policy_incomplete(self):
        # 注意：retry 检查在 js_contract 非 None 的代码路径内，必须有 contract
        node = CodeNode(
            key="C", n8n_type="n8n-nodes-base.code", name="C",
            js_contract=StaticContract(
                contract=Contract(output=OutputShape(), effect=CodeEffect.UNKNOWN),
                payload=CodePayload(source="return $json;"),
            ),
        )
        node.error_policy = N8NErrorPolicy(retry_on_fail=True, max_tries=None)
        ast = _ast({"C": node}, [])
        issues = validate_node_semantics(ast)
        self.assertEqual([i.code for i in issues], ["retry_policy_incomplete"])

    def test_code_syntax_error_surfaces(self):
        node = CodeNode(
            key="C", n8n_type="n8n-nodes-base.code", name="C",
            js_contract=StaticContract(
                contract=Contract(output=OutputShape(), effect=CodeEffect.UNKNOWN),
                payload=CodePayload(source="return ;"),
                errors=("unexpected token",),
            ),
        )
        ast = _ast({"C": node}, [])
        issues = validate_node_semantics(ast)
        self.assertEqual([i.code for i in issues], ["code_syntax_error"])

    def test_entry_node_has_input_sources(self):
        trig = TriggerNode(key="T", n8n_type="n8n-nodes-base.manualTrigger", name="T")
        trig.input_sources = [FieldInfo(path=["x"], source=Source(ref=Reference(from_node_key="A")))]
        ast = _ast({"T": trig}, [])
        issues = validate_node_semantics(ast)
        self.assertEqual([i.code for i in issues], ["entry_node_has_input_sources"])

    def test_exit_node_has_output_sources(self):
        exitn = ExitNode(key=EXIT_NODE_KEY, name="__exit__")
        exitn.output_sources = [FieldInfo(path=["x"], source=Source(ref=Reference(from_node_key="A")))]
        ast = _ast({EXIT_NODE_KEY: exitn}, [])
        issues = validate_node_semantics(ast)
        self.assertEqual([i.code for i in issues], ["exit_node_has_output_sources"])


class TestRaiseOnError(unittest.TestCase):
    def test_workflow_validation_error_raises_with_issues(self):
        ast = _ast({"A": SetNode(key="A", n8n_type="n8n-nodes-base.set", name="A")},
                   [Connection(from_node="A", from_port="main", to_node="Ghost")])
        with self.assertRaises(WorkflowValidationError) as ctx:
            validate_workflow(ast, raise_on_error=True)
        self.assertIsInstance(ctx.exception.issues[0], ValidationIssue)
        self.assertEqual(ctx.exception.issues[0].code, "unknown_target_node")

    def test_no_raise_when_clean(self):
        ast = _ast({"A": SetNode(key="A", n8n_type="n8n-nodes-base.set", name="A")}, [])
        issues = validate_workflow(ast, raise_on_error=True)
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()


class TestAssignabilityMatrix(unittest.TestCase):
    """_is_assignable 分支矩阵：any 万能 / 同型 / 字符串强转 / 嵌套数组。"""

    @staticmethod
    def _assignable_ok(source_type: TypeInfo, target_type: TypeInfo) -> bool:
        from checker.validator import validate_references
        a = SetNode(key="A", n8n_type="n8n-nodes-base.set", name="A",
                    output_types={"main": TypeInfo.object(properties={"v": source_type})})
        b = SetNode(key="B", n8n_type="n8n-nodes-base.set", name="B",
                    input_types={"main": TypeInfo.object(properties={"v": target_type})})
        b.input_sources = [FieldInfo(path=["v"],
                                     source=Source(ref=Reference(from_node_key="A", from_path=["v"])))]
        ast = _ast({"A": a, "B": b}, [Connection(from_node="A", from_port="main", to_node="B")])
        return [i.code for i in validate_references(ast)] == []

    def test_any_is_assignable_to_anything(self):
        self.assertTrue(self._assignable_ok(TypeInfo.any(), TypeInfo.array(TypeInfo.number())))
        self.assertTrue(self._assignable_ok(TypeInfo.number(), TypeInfo.any()))

    def test_same_type_assignable(self):
        self.assertTrue(self._assignable_ok(TypeInfo.string(), TypeInfo.string()))

    def test_string_coercions(self):
        # n8n 运行时可转：string -> number / boolean / object
        self.assertTrue(self._assignable_ok(TypeInfo.string(), TypeInfo.number()))
        self.assertTrue(self._assignable_ok(TypeInfo.string(), TypeInfo.boolean()))
        self.assertTrue(self._assignable_ok(TypeInfo.string(), TypeInfo.object({})))

    def test_number_string_cross_assignable(self):
        self.assertTrue(self._assignable_ok(TypeInfo.number(), TypeInfo.string()))
        self.assertTrue(self._assignable_ok(TypeInfo.boolean(), TypeInfo.string()))

    def test_nested_array_element_assignable(self):
        # array(string) -> array(number)：元素 string->number 可转
        self.assertTrue(self._assignable_ok(
            TypeInfo.array(TypeInfo.string()), TypeInfo.array(TypeInfo.number())))
        # array(number) -> array(string)：元素可转
        self.assertTrue(self._assignable_ok(
            TypeInfo.array(TypeInfo.number()), TypeInfo.array(TypeInfo.string())))
        # array(array(string)) -> array(array(number))：递归判定
        self.assertTrue(self._assignable_ok(
            TypeInfo.array(TypeInfo.array(TypeInfo.string())),
            TypeInfo.array(TypeInfo.array(TypeInfo.number()))))

    def test_binary_not_assignable_to_number(self):
        self.assertFalse(self._assignable_ok(TypeInfo.binary(), TypeInfo.number()))


class TestReferenceTopology(unittest.TestCase):
    def test_self_reference_in_field_mapping(self):
        b = SetNode(key="B", n8n_type="n8n-nodes-base.set", name="B")
        b.input_sources = [FieldInfo(path=["x"], source=Source(ref=Reference(from_node_key="B", from_path=["x"])))]
        ast = _ast({"B": b}, [])
        issues = validate_references(ast)
        self.assertEqual([i.code for i in issues], ["self_reference_in_field_mapping"])

    def test_reference_not_reachable(self):
        # C 存在但不在 B 的上游闭包中（B 上游只有 A）-> reference_not_reachable
        b = SetNode(key="B", n8n_type="n8n-nodes-base.set", name="B")
        c = SetNode(key="C", n8n_type="n8n-nodes-base.set", name="C",
                    output_types={"main": TypeInfo.object(properties={"v": TypeInfo.number()})})
        b.input_sources = [FieldInfo(path=["v"], source=Source(ref=Reference(from_node_key="C", from_path=["v"])))]
        a = SetNode(key="A", n8n_type="n8n-nodes-base.set", name="A")
        ast = _ast({"A": a, "B": b, "C": c},
                   [Connection(from_node="A", from_port="main", to_node="B")])
        issues = validate_references(ast)
        self.assertEqual([i.code for i in issues], ["reference_not_reachable"])

    def test_node_semantics_self_reference(self):
        node = SetNode(key="S", n8n_type="n8n-nodes-base.set", name="S")
        node.input_sources = [FieldInfo(path=["x"], source=Source(ref=Reference(from_node_key="S", from_path=["x"])))]
        ast = _ast({"S": node}, [])
        issues = validate_node_semantics(ast)
        self.assertEqual([i.code for i in issues], ["self_reference_in_field_mapping"])
