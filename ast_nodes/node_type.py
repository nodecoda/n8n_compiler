"""n8n 节点类型注册表与端口语义 — 对齐 coze_compiler.ast_nodes.node_type。

每个 n8n 节点 type 声明：
  kind          语义分类（对应 NodeDecl.KIND）
  input_ports   main 输入端口数（0 = trigger）
  output_ports  main 输出端口数
  shape         输出形状变换（checker/compiler 用）

未知 n8n type 落到 GENERIC（1 入 1 出，形状 ANY）——n8n 有 1500+ 开放节点集，
注册表只覆盖 ncoda 语言面 + 常用节点，长尾以白名单泛型通过（不深查参数）。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

ENTRY_NODE_KEY = "__entry__"
EXIT_NODE_KEY = "__exit__"
ROOT_SCOPE = "__root__"


class NodeKind(str, Enum):
    TRIGGER = "trigger"
    HTTP = "http"
    IF = "if"
    FILTER = "filter"
    LIMIT = "limit"
    SET = "set"
    MERGE = "merge"
    SPLIT_OUT = "split_out"
    CODE = "code"
    ERROR_TRIGGER = "error_trigger"
    RESPOND = "respond"
    LLM = "llm"
    MODEL = "model"
    OUTPUT_PARSER = "output_parser"
    TOOL = "tool"
    VECTOR_STORE = "vector_store"
    RETRIEVER = "retriever"
    MEMORY = "memory"
    GENERIC = "generic"


class ShapeKind(str, Enum):
    TRIGGER = "trigger"    # 输出 = 工作流输入（any）
    IDENTITY = "identity"  # 输出形状 = 输入形状
    OBJECT = "object"      # 输出 object（如 Set）
    ANY = "any"            # 输出形状不可静态推导（HTTP/LLM/Code…）
    BRANCH = "branch"      # 多输出端口，各端口 = 输入形状（IF）
    MERGE = "merge"        # 多输入合并，输出 any
    SINK = "sink"          # 无输出（respondToWebhook）


@dataclass(frozen=True)
class NodeSpec:
    kind: NodeKind
    input_ports: int
    output_ports: int
    shape: ShapeKind
    description: str = ""


REGISTRY: dict[str, NodeSpec] = {
    # triggers
    "n8n-nodes-base.manualTrigger": NodeSpec(NodeKind.TRIGGER, 0, 1, ShapeKind.TRIGGER, "Manual Trigger"),
    "n8n-nodes-base.webhook": NodeSpec(NodeKind.TRIGGER, 0, 1, ShapeKind.TRIGGER, "Webhook"),
    "n8n-nodes-base.scheduleTrigger": NodeSpec(NodeKind.TRIGGER, 0, 1, ShapeKind.TRIGGER, "Schedule Trigger"),
    "n8n-nodes-base.errorTrigger": NodeSpec(NodeKind.ERROR_TRIGGER, 0, 1, ShapeKind.TRIGGER, "Error Trigger"),
    "@n8n/n8n-nodes-langchain.chatTrigger": NodeSpec(NodeKind.TRIGGER, 0, 1, ShapeKind.TRIGGER, "Chat Trigger"),
    # P2-1（v4）：未注册 trigger 曾落 GENERIC -> IR entry_keys 恒空（入口语义失真）。
    # 三类常用 trigger 补注册；仍未知的 trigger 类型由 _entry_keys 零入边回退兜底。
    "@n8n/n8n-nodes-langchain.mcpTrigger": NodeSpec(NodeKind.TRIGGER, 0, 1, ShapeKind.TRIGGER, "MCP Server Trigger"),
    "n8n-nodes-base.executeWorkflowTrigger": NodeSpec(NodeKind.TRIGGER, 0, 1, ShapeKind.TRIGGER, "Execute Workflow Trigger"),
    "n8n-nodes-base.formTrigger": NodeSpec(NodeKind.TRIGGER, 0, 1, ShapeKind.TRIGGER, "Form Trigger"),
    # data flow
    "n8n-nodes-base.httpRequest": NodeSpec(NodeKind.HTTP, 1, 1, ShapeKind.ANY, "HTTP Request"),
    "n8n-nodes-base.if": NodeSpec(NodeKind.IF, 1, 2, ShapeKind.BRANCH, "IF"),
    "n8n-nodes-base.switch": NodeSpec(NodeKind.GENERIC, 1, 4, ShapeKind.BRANCH, "Switch (multi-route)"),
    "n8n-nodes-base.splitInBatches": NodeSpec(NodeKind.GENERIC, 1, 2, ShapeKind.BRANCH, "Split in Batches (loop/done)"),
    "n8n-nodes-base.filter": NodeSpec(NodeKind.FILTER, 1, 1, ShapeKind.IDENTITY, "Filter"),
    "n8n-nodes-base.limit": NodeSpec(NodeKind.LIMIT, 1, 1, ShapeKind.IDENTITY, "Limit"),
    "n8n-nodes-base.set": NodeSpec(NodeKind.SET, 1, 1, ShapeKind.OBJECT, "Set"),
    "n8n-nodes-base.merge": NodeSpec(NodeKind.MERGE, 2, 1, ShapeKind.MERGE, "Merge"),
    "n8n-nodes-base.splitOut": NodeSpec(NodeKind.SPLIT_OUT, 1, 1, ShapeKind.IDENTITY, "Split Out"),
    "n8n-nodes-base.code": NodeSpec(NodeKind.CODE, 1, 1, ShapeKind.ANY, "Code"),
    "n8n-nodes-base.respondToWebhook": NodeSpec(NodeKind.RESPOND, 1, 0, ShapeKind.SINK, "Respond to Webhook"),
    "n8n-nodes-base.extractFromFile": NodeSpec(NodeKind.GENERIC, 1, 1, ShapeKind.ANY, "Extract from File"),
    "n8n-nodes-base.executeWorkflow": NodeSpec(NodeKind.GENERIC, 1, 1, ShapeKind.ANY, "Execute Workflow"),
    # langchain
    "@n8n/n8n-nodes-langchain.agent": NodeSpec(NodeKind.LLM, 1, 1, ShapeKind.ANY, "AI Agent"),
    "@n8n/n8n-nodes-langchain.chainLlm": NodeSpec(NodeKind.LLM, 1, 1, ShapeKind.ANY, "Basic LLM Chain"),
    # AI 链内联 Code：与 n8n-nodes-base.code 同走一等公民 JS 静态通道（acorn
    # 语法 + 契约 + ESTree 进 IR）。源码在 parameters.code.supplyData.code
    # （supplyData 工厂：return 组件实例，无 items 输入）。P1-2（v4）注册。
    "@n8n/n8n-nodes-langchain.code": NodeSpec(NodeKind.CODE, 1, 1, ShapeKind.ANY, "Code (AI chain)"),
    # P2-4（v5）：Code Tool——纯 AI 子节点（0 main 入/出，输出仅 ai_tool），
    # 顶层 jsCode 取源（非 supplyData 嵌套），同走一等公民 JS 静态通道。
    "@n8n/n8n-nodes-langchain.toolCode": NodeSpec(NodeKind.CODE, 0, 0, ShapeKind.ANY, "Code Tool"),
    "@n8n/n8n-nodes-langchain.lmChatOpenAi": NodeSpec(NodeKind.MODEL, 1, 1, ShapeKind.ANY, "OpenAI Chat Model"),
    "@n8n/n8n-nodes-langchain.lmChatGoogleGemini": NodeSpec(NodeKind.MODEL, 1, 1, ShapeKind.ANY, "Gemini Chat Model"),
    "@n8n/n8n-nodes-langchain.embeddingsGoogleGemini": NodeSpec(NodeKind.MODEL, 1, 1, ShapeKind.ANY, "Gemini Embeddings"),
    "@n8n/n8n-nodes-langchain.embeddingsOpenAi": NodeSpec(NodeKind.MODEL, 1, 1, ShapeKind.ANY, "OpenAI Embeddings"),
    "@n8n/n8n-nodes-langchain.vectorStoreInMemory": NodeSpec(NodeKind.VECTOR_STORE, 1, 1, ShapeKind.ANY, "In-Memory Vector Store"),
    "@n8n/n8n-nodes-langchain.outputParserStructured": NodeSpec(NodeKind.OUTPUT_PARSER, 1, 1, ShapeKind.OBJECT, "Structured Output Parser"),
    "@n8n/n8n-nodes-langchain.toolWorkflow": NodeSpec(NodeKind.TOOL, 1, 1, ShapeKind.ANY, "Workflow Tool"),
    "@n8n/n8n-nodes-langchain.vectorStorePinecone": NodeSpec(NodeKind.VECTOR_STORE, 1, 1, ShapeKind.ANY, "Vector Store (Pinecone)"),
    "@n8n/n8n-nodes-langchain.vectorStoreQdrant": NodeSpec(NodeKind.VECTOR_STORE, 1, 1, ShapeKind.ANY, "Vector Store (Qdrant)"),
    "@n8n/n8n-nodes-langchain.vectorStorePGVector": NodeSpec(NodeKind.VECTOR_STORE, 1, 1, ShapeKind.ANY, "Vector Store (PGVector)"),
    "@n8n/n8n-nodes-langchain.retrieverVectorStore": NodeSpec(NodeKind.RETRIEVER, 1, 1, ShapeKind.ANY, "Vector Store Retriever"),
    "@n8n/n8n-nodes-langchain.memoryBufferWindow": NodeSpec(NodeKind.MEMORY, 1, 1, ShapeKind.ANY, "Chat Memory (Buffer Window)"),
    "@n8n/n8n-nodes-langchain.documentDefaultDataLoader": NodeSpec(NodeKind.GENERIC, 1, 1, ShapeKind.ANY, "Default Data Loader"),
}

GENERIC_SPEC = NodeSpec(NodeKind.GENERIC, 1, 1, ShapeKind.ANY, "Generic node (unregistered type)")


def spec_for(n8n_type: str) -> NodeSpec:
    return REGISTRY.get(n8n_type, GENERIC_SPEC)
