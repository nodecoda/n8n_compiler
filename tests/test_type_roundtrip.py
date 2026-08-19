"""类型系统 / IR 序列化 round-trip 定向测试。

覆盖 mappings.load_typed_node（IR -> NodeDecl 反序列化）、
DataType.from_str 别名矩阵、TypeInfo / Connection 的 dict round-trip。
"""
import unittest

from ast_nodes.connection import Connection
from ast_nodes.mappings import load_typed_node, node_class_for
from ast_nodes.node_decls import CodeNode, EntryNode, ExitNode, GenericNode, SetNode
from ast_nodes.nodes import node_to_config_dict
from jscode.contract import (
    CodeEffect,
    CodePayload,
    Contract,
    FieldDep,
    OutputShape,
    OutputShapeKind,
    StaticContract,
)
from type_system.datatype import DataType
from type_system.typeinfo import TypeInfo


class TestDataTypeAliases(unittest.TestCase):
    """from_str 别名矩阵：n8n/JSON 类型名 -> 统一 DataType。"""

    def test_alias_matrix(self):
        cases = {
            "array": DataType.ARRAY, "list": DataType.ARRAY,
            "int": DataType.NUMBER, "integer": DataType.NUMBER, "long": DataType.NUMBER,
            "float": DataType.NUMBER, "number": DataType.NUMBER, "double": DataType.NUMBER,
            "bool": DataType.BOOLEAN, "boolean": DataType.BOOLEAN,
            "file": DataType.BINARY, "files": DataType.BINARY,
            "object": DataType.OBJECT, "map": DataType.OBJECT, "dict": DataType.OBJECT,
            "any": DataType.ANY, "unknown": DataType.ANY, "": DataType.ANY,
            "string": DataType.STRING, "binary": DataType.BINARY,
        }
        for raw, expected in cases.items():
            self.assertEqual(DataType.from_str(raw), expected, f"from_str({raw!r})")

    def test_unknown_and_none_fall_back_to_any(self):
        self.assertEqual(DataType.from_str("bogus"), DataType.ANY)
        self.assertEqual(DataType.from_str(None), DataType.ANY)
        self.assertEqual(DataType.from_str("  INTEGER "), DataType.NUMBER)  # 大小写 + 空白


class TestTypeInfoRoundTrip(unittest.TestCase):
    def test_nested_round_trip(self):
        ti = TypeInfo.object(properties={
            "name": TypeInfo.string(required=True, desc="user name"),
            "tags": TypeInfo.array(TypeInfo.string(), required=True),
            "meta": TypeInfo.object(properties={"id": TypeInfo.number()}),
        }, required=True)
        restored = TypeInfo.from_dict(ti.to_dict())
        self.assertEqual(restored.type, DataType.OBJECT)
        self.assertTrue(restored.required)
        self.assertEqual(restored.properties["name"].desc, "user name")
        self.assertTrue(restored.properties["name"].required)
        self.assertEqual(restored.properties["tags"].elem_type_info.type, DataType.STRING)
        self.assertEqual(restored.properties["meta"].properties["id"].type, DataType.NUMBER)

    def test_from_dict_non_dict_is_any(self):
        for bad in (None, "x", 42, ["a"]):
            self.assertEqual(TypeInfo.from_dict(bad).type, DataType.ANY, f"from_dict({bad!r})")

    def test_from_dict_missing_keys_defaults(self):
        ti = TypeInfo.from_dict({"type": "list"})
        self.assertEqual(ti.type, DataType.ARRAY)
        self.assertFalse(ti.required)
        self.assertEqual(ti.desc, "")
        self.assertIsNone(ti.elem_type_info)
        self.assertEqual(ti.properties, {})

    def test_predicates(self):
        self.assertTrue(TypeInfo.string().is_simple())
        self.assertTrue(TypeInfo.number().is_simple())
        self.assertTrue(TypeInfo.boolean().is_simple())
        self.assertFalse(TypeInfo.array(TypeInfo.string()).is_simple())
        self.assertTrue(TypeInfo.array(TypeInfo.string()).is_array())
        self.assertTrue(TypeInfo.object({}).is_object())
        self.assertTrue(TypeInfo.any().is_any())
        self.assertTrue(TypeInfo.binary().type is DataType.BINARY)
        self.assertIn("TypeInfo(list", repr(TypeInfo.array(TypeInfo.string())))
        self.assertIn("TypeInfo(string", repr(TypeInfo.string()))


class TestConnectionRoundTrip(unittest.TestCase):
    def test_full_round_trip(self):
        conn = Connection(from_node="IF", from_port="main_0", to_node="B", to_port="main",
                          to_index=0)
        d = conn.to_dict()
        restored = Connection.from_dict(d)
        self.assertEqual(restored, conn)
        self.assertEqual(restored.identity, "IF|main_0|B|0")

    def test_merge_multi_input_index(self):
        # Merge 多输入：同一源连到目标输入端口 0/1，identity 必须区分
        c0 = Connection(from_node="A", from_port="main", to_node="Merge", to_index=0)
        c1 = Connection(from_node="A", from_port="main", to_node="Merge", to_index=1)
        self.assertNotEqual(c0.identity, c1.identity)
        self.assertEqual(c0.to_dict()["to_index"], 0)
        self.assertEqual(c1.to_dict()["to_index"], 1)

    def test_defaults(self):
        restored = Connection.from_dict({"from_node": "A", "to_node": "B"})
        self.assertEqual(restored.from_port, "0")
        self.assertEqual(restored.to_port, "main")
        self.assertEqual(restored.to_index, 0)
        # 显式端口保留
        self.assertEqual(Connection.from_dict(
            {"from_node": "A", "from_port": "main_2", "to_node": "B", "to_port": "x"}).to_port, "x")


class TestLoadTypedNode(unittest.TestCase):
    """IR node dict -> 强类型 NodeDecl（load_typed_node）round-trip。"""

    def test_simple_node_round_trip(self):
        d = {
            "key": "Set1", "n8n_type": "n8n-nodes-base.set", "name": "Set1",
            "parent_key": None,
            "config": {"type_version": 3, "position": [10, 20], "parameters": {"a": 1}},
            "input_types": {}, "output_types": {"main": TypeInfo.any().to_dict()},
        }
        node = load_typed_node(d)
        self.assertIsInstance(node, SetNode)
        self.assertEqual(node.position, (10.0, 20.0))
        self.assertEqual(node.type_version, 3)
        self.assertEqual(node.parameters, {"a": 1})
        self.assertEqual(node.output_types["main"].type, DataType.ANY)

    def test_code_node_contract_round_trip(self):
        contract = StaticContract(
            contract=Contract(
                deps=[],
                output=OutputShape(kind=OutputShapeKind.OBJECT, props={"ok": "boolean"}, elem=None),
                effect=CodeEffect.PURE,
                runtime="external",
            ),
            payload=CodePayload(language="js", source="return { ok: true };"),
            errors=(),
            warnings=("unused var",),
        )
        src = CodeNode(key="C", n8n_type="n8n-nodes-base.code", name="C",
                       js_contract=contract, js_ast={"type": "Program"})
        node_dict = {
            "key": src.key, "n8n_type": src.n8n_type, "name": src.name,
            "parent_key": None,
            "config": src.to_config_dict(),
            "input_types": {k: v.to_dict() for k, v in src.input_types.items()},
            "output_types": {k: v.to_dict() for k, v in src.output_types.items()},
        }
        node = load_typed_node(node_dict)
        self.assertIsInstance(node, CodeNode)
        self.assertIsNotNone(node.js_contract)
        self.assertEqual(node.js_contract.contract.output.kind, OutputShapeKind.OBJECT)
        self.assertEqual(node.js_contract.contract.output.props, {"ok": "boolean"})
        self.assertEqual(node.js_contract.contract.effect, CodeEffect.PURE)
        self.assertEqual(node.js_contract.payload.source, "return { ok: true };")
        self.assertEqual(node.js_contract.warnings, ("unused var",))
        self.assertEqual(node.js_ast, {"type": "Program"})

    def test_code_contract_deps_round_trip(self):
        # P2-3 回归：IR contract.deps 必须反序列化（曾硬编码 deps=[] 丢弃，
        # 序列化/反序列化不对称 -> 只写不读的漂移源）。
        contract = StaticContract(
            contract=Contract(
                deps=[FieldDep(base="items", path=("a",)), FieldDep(base="$json", path=("b",))],
                output=OutputShape(kind=OutputShapeKind.ANY),
                effect=CodeEffect.IO,
                runtime="external",
            ),
            payload=CodePayload(language="js", source="return $json.b;"),
        )
        src = CodeNode(key="C", n8n_type="n8n-nodes-base.code", name="C",
                       js_contract=contract, js_ast=None)
        node_dict = {
            "key": src.key, "n8n_type": src.n8n_type, "name": src.name,
            "parent_key": None,
            "config": src.to_config_dict(),
            "input_types": {}, "output_types": {},
        }
        node = load_typed_node(node_dict)
        self.assertEqual(node.js_contract.contract.deps,
                         [FieldDep(base="items", path=("a",)),
                          FieldDep(base="$json", path=("b",))])

    def test_input_output_sources_round_trip(self):
        # IR -> NodeDecl 必须回填依赖/引用（P1-1 回归：coze 加载器明确回填，
        # 运行时无需维护并行的 node dict 表示）。
        from values.reference import FieldInfo, Reference, Source
        from values.variable import GlobalVarType
        node = SetNode(key="B", n8n_type="n8n-nodes-base.set", name="B")
        node.input_sources = [
            FieldInfo(path=["a"], source=Source(ref=Reference(from_node_key="A", from_path=["x"]))),
            FieldInfo(path=["env"],
                      source=Source(ref=Reference(from_node_key="", from_path=["K"],
                                                  variable_type=GlobalVarType.ENV))),
            FieldInfo(path=["lit"], source=Source(literal=42)),
        ]
        node.output_sources = [FieldInfo(path=["o"], source=Source(literal=True))]
        d = node_to_config_dict(node)
        restored = load_typed_node(d)
        self.assertEqual([s.to_dict() for s in restored.input_sources],
                         [s.to_dict() for s in node.input_sources])
        self.assertEqual([s.to_dict() for s in restored.output_sources],
                         [s.to_dict() for s in node.output_sources])
        # 无 input_sources 字段的旧 IR -> 空列表（非 None）
        self.assertEqual(load_typed_node(
            {"key": "C", "n8n_type": "n8n-nodes-base.set", "name": "C",
             "parent_key": None, "config": {}, "input_types": {}, "output_types": {}}).input_sources,
            [])

    def test_unknown_type_falls_back_to_generic(self):
        node = load_typed_node({
            "key": "G", "n8n_type": "community.someThing", "name": "G",
            "parent_key": None, "config": {}, "input_types": {}, "output_types": {},
        })
        self.assertIsInstance(node, GenericNode)
        self.assertEqual(node.n8n_type, "community.someThing")

    def test_synthetic_entry_exit(self):
        entry = load_typed_node({"key": "E", "n8n_type": "synthetic.entry", "name": "E",
                                 "parent_key": None, "config": {}, "input_types": {},
                                 "output_types": {}})
        self.assertIsInstance(entry, EntryNode)
        exitn = load_typed_node({"key": "X", "n8n_type": "synthetic.exit", "name": "X",
                                 "parent_key": None, "config": {}, "input_types": {},
                                 "output_types": {}})
        self.assertIsInstance(exitn, ExitNode)

    def test_registry_kind_matches_node_class(self):
        # P2-2 双源防漂移：REGISTRY 的 NodeKind 与子类 KIND ClassVar 必须一致，
        # 否则类型会静默落 GenericNode
        from ast_nodes.node_type import REGISTRY
        for n8n_type, spec in REGISTRY.items():
            cls = node_class_for(n8n_type)
            self.assertEqual(cls.KIND, spec.kind.value,
                             f"{n8n_type}: spec.kind={spec.kind.value} "
                             f"vs {cls.__name__}.KIND={cls.KIND}")

    def test_node_class_for_mapping(self):
        self.assertEqual(node_class_for("synthetic.entry"), EntryNode)
        self.assertEqual(node_class_for("synthetic.exit"), ExitNode)
        self.assertEqual(node_class_for("n8n-nodes-base.if").__name__, "IfNode")
        self.assertEqual(node_class_for("n8n-nodes-base.set").__name__, "SetNode")
        self.assertEqual(node_class_for("n8n-nodes-base.code").__name__, "CodeNode")
        self.assertIs(node_class_for("no.such.node"), GenericNode)


if __name__ == "__main__":
    unittest.main()
