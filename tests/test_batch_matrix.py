"""批量回归：n8n 仓库自带工作流矩阵固化（143 文件）。

分类：
  PASS: 130 — 编译全链路通过（parse/check/compile/IR 校验/digest）
  CYCLIC: 10 — 故意环（编辑器容忍保存，运行时必败；检出 = 正确行为）
  OTHER: 精确断言（Python 模式 / 畸形输入 / n8n 仓库真实坏代码）
"""
import json
import unittest
from collections import Counter
from typing import ClassVar

from checker.validator import validate_workflow
from compiler.workflow import compile_ast
from parser.workflow import parse_workflow
from tests.helpers import (
    COMMITTED_DIR_REL,
    PLAYWRIGHT_DIR_REL,
    TEMPLATES_DIR_REL,
    n8n_repo,
)
from typed_ir import validate_typed_ir, verify_typed_ir_digest

REPO = n8n_repo()
GROUP_DIRS = {
    "workflow-sdk": REPO / COMMITTED_DIR_REL,
    "playwright": REPO / PLAYWRIGHT_DIR_REL,
    "editor-templates": REPO / TEMPLATES_DIR_REL,
}


def _run_all():
    results = {}
    seen = set()
    for d in GROUP_DIRS.values():
        if not d.exists():
            continue
        for p in sorted(d.rglob('*.json')):
            key = str(p)
            if key in seen:
                continue
            seen.add(key)
            try:
                data = json.loads(p.read_text(encoding='utf-8'))
                ast = parse_workflow(data.get('workflow', data))
            except Exception:
                results[str(p.relative_to(REPO))] = 'parse_error'
                continue
            issues = validate_workflow(ast)
            if issues:
                codes = Counter(i.code for i in issues)
                results[str(p.relative_to(REPO))] = 'check_' + ','.join(sorted(codes))
                continue
            try:
                compiled = compile_ast(ast, workflow_id='x', version='1')
                validate_typed_ir(compiled.document)
                verify_typed_ir_digest(compiled.document)
                # P1-1c（v4）：IR v2 完整携带 ai_* 子连接（conn_type），
                # 结构性丢弃不存在——含 AI 链的工作流与普通工作流同判 PASS。
                results[str(p.relative_to(REPO))] = 'PASS'
            except Exception:
                results[str(p.relative_to(REPO))] = 'compile_error'
    return results


class TestRepoBatchMatrix(unittest.TestCase):
    """n8n 仓库自带工作流批量矩阵（固化于 2026-08-18，P1-1c 更新于 2026-08-19）。

    分类：
      PASS: 130 — 编译全链路通过且结构无损（IR v2 完整携带 main + ai_* 子
        连接；原 PASS_AI_DROPPED 18 个 AI 工作流随 P1-1c 撤回 PASS）
      CYCLIC: 10 / OTHER: 精确断言
    """

    PASS: ClassVar[set[str]] = {
        'packages/@n8n/workflow-sdk/test-fixtures/committed-workflows/10.json',
        'packages/@n8n/workflow-sdk/test-fixtures/committed-workflows/11.json',
        'packages/@n8n/workflow-sdk/test-fixtures/committed-workflows/12.json',
        'packages/@n8n/workflow-sdk/test-fixtures/committed-workflows/7.json',
        'packages/@n8n/workflow-sdk/test-fixtures/committed-workflows/8.json',
        'packages/@n8n/workflow-sdk/test-fixtures/committed-workflows/9.json',
        'packages/@n8n/workflow-sdk/test-fixtures/committed-workflows/manifest.json',
        'packages/frontend/editor-ui/src/features/workflows/templates/utils/samples/tutorial/api_fundamentals.json',
        'packages/frontend/editor-ui/src/features/workflows/templates/utils/samples/tutorial/expressions_tutorial.json',
        'packages/frontend/editor-ui/src/features/workflows/templates/utils/samples/tutorial/json_basics.json',
        'packages/frontend/editor-ui/src/features/workflows/templates/utils/samples/tutorial/workflow_logic.json',
        'packages/testing/playwright/workflows/Canvas-node-groups-fixture.json',
        'packages/testing/playwright/workflows/Canvas-node-groups-if-fixture.json',
        'packages/testing/playwright/workflows/Canvas-node-groups-persisted-fixture.json',
        'packages/testing/playwright/workflows/Canvas-node-groups-sticky-fixture.json',
        'packages/testing/playwright/workflows/Check_manual_node_run_for_pinned_and_rundata.json',
        'packages/testing/playwright/workflows/Custom_credential.json',
        'packages/testing/playwright/workflows/Custom_node.json',
        'packages/testing/playwright/workflows/Custom_node_custom_credential.json',
        'packages/testing/playwright/workflows/Custom_node_n8n_credential.json',
        'packages/testing/playwright/workflows/Ecommerce_starter_pack_template_collection.json',
        'packages/testing/playwright/workflows/Lots_of_nodes.json',
        'packages/testing/playwright/workflows/Manual_wait_set.json',
        'packages/testing/playwright/workflows/Multiple_trigger_node_rerun.json',
        'packages/testing/playwright/workflows/NDV-debug-generate-data.json',
        'packages/testing/playwright/workflows/NDV-test-select-input.json',
        'packages/testing/playwright/workflows/Node_IO_filter.json',
        'packages/testing/playwright/workflows/Onboarding_workflow.json',
        'packages/testing/playwright/workflows/Pinned_webhook_node.json',
        'packages/testing/playwright/workflows/Simple_chain_4nodes.json',
        'packages/testing/playwright/workflows/Simple_workflow_with_http_node.json',
        'packages/testing/playwright/workflows/Subworkflow-debugging-execute-workflow.json',
        'packages/testing/playwright/workflows/Subworkflow-extraction-disconnected.json',
        'packages/testing/playwright/workflows/Subworkflow-extraction-workflow.json',
        'packages/testing/playwright/workflows/Switch_node_with_null_connection.json',
        'packages/testing/playwright/workflows/Test-workflow-with-long-parameters.json',
        'packages/testing/playwright/workflows/Test_9999_SUG_38.json',
        'packages/testing/playwright/workflows/Test_Subworkflow_Get_Weather.json',
        'packages/testing/playwright/workflows/Test_Subworkflow_Search_DB.json',
        'packages/testing/playwright/workflows/Test_Template_1.json',
        'packages/testing/playwright/workflows/Test_Template_2.json',
        'packages/testing/playwright/workflows/Test_Workflow_pairedItem_incomplete_manual_bug.json',
        'packages/testing/playwright/workflows/Test_chat_partial_execution.json',
        'packages/testing/playwright/workflows/Test_ndv_search.json',
        'packages/testing/playwright/workflows/Test_ndv_two_branches_of_same_parent_false_populated.json',
        'packages/testing/playwright/workflows/Test_workflow_4_executions_view.json',
        'packages/testing/playwright/workflows/Test_workflow_chat_partial_execution.json',
        'packages/testing/playwright/workflows/Test_workflow_filter.json',
        'packages/testing/playwright/workflows/Test_workflow_form_switch.json',
        'packages/testing/playwright/workflows/Test_workflow_multiple_outputs.json',
        'packages/testing/playwright/workflows/Test_workflow_ndv_errors.json',
        'packages/testing/playwright/workflows/Test_workflow_ndv_paired_item_single_output.json',
        'packages/testing/playwright/workflows/Test_workflow_ndv_run_error.json',
        'packages/testing/playwright/workflows/Test_workflow_ndv_version.json',
        'packages/testing/playwright/workflows/Test_workflow_partial_execution_v2.json',
        'packages/testing/playwright/workflows/Test_workflow_partial_execution_with_missing_credentials.json',
        'packages/testing/playwright/workflows/Test_workflow_schema_test.json',
        'packages/testing/playwright/workflows/Test_workflow_schema_test_pinned_data.json',
        'packages/testing/playwright/workflows/Test_workflow_webhook_with_pin_data.json',
        'packages/testing/playwright/workflows/Test_workflow_xml_output.json',
        'packages/testing/playwright/workflows/Two_schedule_triggers.json',
        'packages/testing/playwright/workflows/Webhook_set_pinned.json',
        'packages/testing/playwright/workflows/Webhook_wait_set.json',
        'packages/testing/playwright/workflows/Workflow_if.json',
        'packages/testing/playwright/workflows/Workflow_template_write_http_query.json',
        'packages/testing/playwright/workflows/Workflow_wait_for_webhook.json',
        'packages/testing/playwright/workflows/all_templates_search_response.json',
        'packages/testing/playwright/workflows/cat-1801-child.json',
        'packages/testing/playwright/workflows/cat-1801-parent.json',
        'packages/testing/playwright/workflows/cat-1854-wait-execution-history.json',
        'packages/testing/playwright/workflows/cat-1929-child-two-waits.json',
        'packages/testing/playwright/workflows/cat-1929-parent.json',
        'packages/testing/playwright/workflows/cat-2662-child.json',
        'packages/testing/playwright/workflows/cat-2662-parent.json',
        'packages/testing/playwright/workflows/cat-3263-child.json',
        'packages/testing/playwright/workflows/chat-send-and-wait.json',
        'packages/testing/playwright/workflows/evaluations_loop.json',
        'packages/testing/playwright/workflows/execute-previous-nodes.json',
        'packages/testing/playwright/workflows/expression_with_paired_item_in_multi_input_node.json',
        'packages/testing/playwright/workflows/large.json',
        'packages/testing/playwright/workflows/manual-partial-execution.json',
        'packages/testing/playwright/workflows/manual-trigger-with-code.json',
        'packages/testing/playwright/workflows/manual.json',
        'packages/testing/playwright/workflows/mcp-service/mcp-available-basic.json',
        'packages/testing/playwright/workflows/mcp-service/mcp-available-webhook.json',
        'packages/testing/playwright/workflows/mcp-service/mcp-unavailable.json',
        'packages/testing/playwright/workflows/merge_node_inputs_paired_items.json',
        'packages/testing/playwright/workflows/multi-branch-data-transform.json',
        'packages/testing/playwright/workflows/open_node_creator_for_connection.json',
        'packages/testing/playwright/workflows/partial-execution-disabled-pinned-parent.json',
        'packages/testing/playwright/workflows/sales_templates_search_response.json',
        'packages/testing/playwright/workflows/schedule-trigger-with-set-nodes.json',
        'packages/testing/playwright/workflows/send-and-wait-approval.json',
        'packages/testing/playwright/workflows/send-and-wait-form.json',
        'packages/testing/playwright/workflows/simple-webhook-test.json',
        'packages/testing/playwright/workflows/subworkflow-noop-child.json',
        'packages/testing/playwright/workflows/subworkflow-parent-no-wait.json',
        'packages/testing/playwright/workflows/subworkflow-version-child.json',
        'packages/testing/playwright/workflows/subworkflow-version-parent.json',
        'packages/testing/playwright/workflows/subworkflow-wait-child.json',
        'packages/testing/playwright/workflows/subworkflow-waiting-parent-no-child-wait.json',
        'packages/testing/playwright/workflows/test_pdf_workflow.json',
        'packages/testing/playwright/workflows/wait-form-resume.json',
        'packages/testing/playwright/workflows/wait-webhook-resume.json',
        'packages/testing/playwright/workflows/webhook-isolate-skip-expression.json',
        'packages/testing/playwright/workflows/webhook-isolate-skip-static.json',
        'packages/testing/playwright/workflows/webhook-misconfiguration-test.json',
        'packages/testing/playwright/workflows/webhook-origin-isolation.json',
        'packages/testing/playwright/workflows/webhook-publish-local-conflict.json',
        'packages/testing/playwright/workflows/webhook-publish-no-conflicts.json',
        'packages/testing/playwright/workflows/webhook-publish-with-wait-node.json',
        'packages/testing/playwright/workflows/workflow-with-unknown-credentials.json',
    
        'packages/@n8n/workflow-sdk/test-fixtures/committed-workflows/0.json',
        'packages/@n8n/workflow-sdk/test-fixtures/committed-workflows/5.json',
        'packages/@n8n/workflow-sdk/test-fixtures/committed-workflows/6.json',
        'packages/frontend/editor-ui/src/features/workflows/templates/utils/samples/easy_ai_starter.json',
        'packages/frontend/editor-ui/src/features/workflows/templates/utils/samples/rag_starter.json',
        'packages/frontend/editor-ui/src/features/workflows/templates/utils/samples/tutorial/build_your_first_ai_agent.json',
        'packages/testing/playwright/workflows/AI-2505_pdf_embed_fake_embeddings.json',
        'packages/testing/playwright/workflows/Floating_Nodes.json',
        'packages/testing/playwright/workflows/In_memory_vector_store_fake_embeddings.json',
        'packages/testing/playwright/workflows/Test_ai_1401.json',
        'packages/testing/playwright/workflows/Workflow_ai_agent.json',
        'packages/testing/playwright/workflows/chat-hub-workflow-agent.json',
        'packages/testing/playwright/workflows/hitl-wrapped-tool.json',
        'packages/testing/playwright/workflows/mcp-trigger/mcp-trigger-basic.json',
        'packages/testing/playwright/workflows/mcp-trigger/mcp-trigger-bearer-auth.json',
        'packages/testing/playwright/workflows/mcp-trigger/mcp-trigger-header-auth.json',
        'packages/testing/playwright/workflows/mcp-trigger/mcp-trigger-multi-tool.json',
        'packages/testing/playwright/workflows/mcp-trigger/mcp-trigger-n8n-oauth2-private-cred.json',
}

    CYCLIC: ClassVar[set[str]] = {
        'packages/@n8n/workflow-sdk/test-fixtures/committed-workflows/1.json',
        'packages/@n8n/workflow-sdk/test-fixtures/committed-workflows/2.json',
        'packages/@n8n/workflow-sdk/test-fixtures/committed-workflows/3.json',
        'packages/@n8n/workflow-sdk/test-fixtures/committed-workflows/4.json',
        'packages/testing/playwright/workflows/Bug_node_insertions_between_stickies.json',
        'packages/testing/playwright/workflows/Bug_node_insertions_sticky.json',
        'packages/testing/playwright/workflows/Cyclic_workflow_for_insertion_test.json',
        'packages/testing/playwright/workflows/Test_ado_1338.json',
        'packages/testing/playwright/workflows/Test_workflow-actions_paste-data.json',
        'packages/testing/playwright/workflows/Workflow_loop.json',
    
}

    OTHER: ClassVar[dict[str, str]] = {
        'packages/@n8n/workflow-sdk/test-fixtures/committed-workflows/13.json': 'parse_error',
        'packages/testing/playwright/workflows/Test_workflow-actions_import_nodes_empty_name.json': 'parse_error',
        'packages/testing/playwright/workflows/ai_assistant_test_workflow.json': 'check_code_syntax_error',
    }

    def test_matrix(self):
        if not REPO.exists():
            self.skipTest('n8n repo not present')
        results = _run_all()
        pass_set = {r for r, s in results.items() if s == 'PASS'}
        cyclic = {r for r, s in results.items() if s.startswith('check_cycle')}
        other = {r: s for r, s in results.items()
                 if s != 'PASS' and not s.startswith('check_cycle')}
        self.assertEqual(pass_set, self.PASS, 'PASS 集合漂移')
        self.assertEqual(cyclic, self.CYCLIC, 'CYCLIC 集合漂移')
        self.assertEqual(other, self.OTHER, 'OTHER 分类漂移')


if __name__ == "__main__":
    unittest.main()
