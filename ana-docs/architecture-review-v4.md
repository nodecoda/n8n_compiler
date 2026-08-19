# n8n_compiler 架构审核报告（第四轮）

审核对象：`/home/dev/n8n_compiler`（ncoda→n8n 工作流编译器，Python 纯标准库；
源码 8,396 行：核心链 ~3,500 + typed_ir/manifest/cli ~590 + runtime ~240 + scripts ~620 + tests ~2,900）
对照基准：`/home/dev/n8n` 运行时源码（本轮独立重读：public-api schema、workflow-creation
service、import 命令、execution-engine、workflow-sdk lint）+ v3 审核结论
审核角色：编译器架构师（只读；未修改任何编译器业务代码，仅产出本报告）
审核日期：2026-08-19　性质：第四轮完整架构审核（重点：新代码 decompile/deploy/execute_deploy
+ REST 部署链契约 + 覆盖盲区）

> 纪律：所有基线数字为本轮**独立复跑**；所有关键断言附 file:line 证据（编译器侧 +
> n8n 侧）；v3 修复项逐条复核落地；真实实例数字为**记录转引**（本轮无远端访问权，
> 已在第八节逐项标注）。

---

## 一、复跑基线（本轮实测）

| 项 | 结果 | 说明 |
|---|---|---|
| `python3 -m unittest discover -s tests` | **253 tests OK（5.205s）** | 独立复跑，非转引 |
| `python3 tests/coverage.py --quiet` | **COVERAGE 92.8% (2546/2745)** ran=253 fail=0 err=0 skip=0 | 同上 |
| `test_batch_matrix`（143 文件重扫） | **130 PASS / 10 CYCLIC / 2 parse_error / 1 code_syntax_error** | 本轮独立重跑 `_run_all()` 实测分类 |
| `_scenarios()` 场景数 | 7：set_assignments / if_true_branch / switch_routes / switch_fallback / merge_combine / expr_interpolation / code_chain | 与文档一致 |
| **AI 子连接盲区（新增实测）** | 130 PASS 工作流中 **18 个携带 ai_\* 边（共 48 条）**；RAG fixture `committed-workflows/6.json` round-trip 丢 3 条 ai 边（main=12 保持不变） | 本轮独立测量，详见 P1-1 |
| **未注册 trigger 类型（新增实测）** | `mcp-trigger-basic.json` / `Test_Subworkflow_Get_Weather.json` → IR `entry_keys=[]`（chatTrigger 注册后正常） | 本轮独立测量，详见 P2-1 |
| **注册表覆盖（新增实测）** | 矩阵 75 种节点类型中 **27 注册 / 48 未注册（64% 落 GENERIC）** | 本轮独立测量，详见 P3-8 |
| 覆盖率明细关键行 | runtime/decompile **91.7% (77/84)**；runtime/deploy **105.5% (58/55)**；typed_ir 83.5%；manifest 90.7%；cli 91.7% | trace 口径已纳 runtime（P2-N3 落地复核 ✓） |

---

## 二、总体结论

**无 P0。新增 1 个 P1（AI 子连接静默丢弃——与 v1/v2/v3 已修缺陷不同根但更广的覆盖盲区）、
5 个 P2、8 个 P3。v3 全部修复项逐一复核落地，settings/id 两项新修复经 n8n schema
行级核对**（`workflowCreate.yml` 强制 settings、id readOnly；`workflow-execute.ts:200`
缺失 executionOrder 按 v2 处理——envelope 恒 v1 使两条部署路径行为确定且一致）。

本轮审核重心（新代码 + 部署链契约 + 覆盖盲区）结论：

- **runtime/decompile.py + deploy.py**：digest 入口强制、HTTP/网络/非 JSON 显式失败、
  篡改 IR 请求前拒绝——全部有测试固化且与 n8n 行为一致（本轮逐行核对）。settings/id
  收口位置正确（deploy 剥 id、decompile 恒带 settings）。**遗留缺口集中在调用侧
  语义：非幂等（P2-2）、凭据引用无映射（P2-3）、2xx 响应不校验 id（P3-3）**。
- **scripts/execute_deploy.py**：隧道 finally 清理正确（正常/异常路径均执行），但
  pkill 模式匹配可能误杀无关隧道（P2-4）、陈旧结果文件可造成假 PASS（P2-5）、
  server id 内插远端 shell 命令无校验（P3-5）、API key 命令行透传（P3-4）。
- **settings/id 一致性**：两条路径契约差异已收口（schema 行级证据见第五节 c），
  但 decompile 注释对「CLI import 填充缺省」的机制描述与 n8n 源码不符（P3-2），
  且恒 v1 使 P3-8「executionOrder 不还原」边界从"缺失"变为"硬编码覆盖"。
- **覆盖盲区**：48/75 矩阵节点类型未注册（设计内）；未注册 trigger → IR entry_keys
  恒空（P2-1）；`@n8n/n8n-nodes-langchain.code` 不走 CodeNode 静态分析（与 P1-1 同根）。

分级汇总：**P0=0，P1=1（新），P2=5（新），P3=8（新）**；v3 遗留全部关闭
（P2-8 / checker 类型信号 / P3-8 为文档化决策，仍活且有意，见第六节）。

---

## 三、与 v3 的 Delta

### 3.1 v3 修复项：全部复核落地 ✅

| v3 项 | 状态 | 本轮证据 |
|---|---|---|
| P1-N1 Switch expression 模式端口收口 | ✅ 落地 | `parser/workflow.py:63-101`（`mode=='expression'` → numberOutputs 取整；fallback 'extra' +1）；全量 253 tests 含其回归 |
| P2-N2 node 层 skip 守卫 | ✅ 落地 | `tests/helpers.py:66-89`（`skip_unless_node` 类级装饰，env 注入回归）；`NODE=/nonexistent` 行为测试固化 |
| P2-N3 runtime/ 入覆盖率口径 | ✅ 落地 | `tests/coverage.py:26`（PACKAGES 含 "runtime"）；`_is_project_module` 用 `rel.stem`（coverage.py:54）；本轮回放口径正确（runtime 两模块进报表） |
| P2-N4 Set 替换语义断言 | ✅ 落地 | `scripts/execute_matrix.py` set_assignments 场景（has_x 区分替换/合并）；断言 `{"y":5,"has_x":False}` |
| P3-5..P3-12 全清 | ✅ 落地 | `typed_ir.py:33` reject_non_finite 公开；`runtime/__init__.py` 存在；`cli.py:24-26` UnsupportedSourceError 映射退出码 1；README/TESTING 基线 253/92.8% 与实测一致；`execute_matrix.py` 无 `_link` 残留 |
| A2-REST 三项 | ✅ 落地且正确 | 见第五节 c（schema 行级核对） |

### 3.2 新增 / 加剧项

| 项 | 类别 | 说明 |
|---|---|---|
| **P1-1 AI 子连接静默丢弃** | 新发现（v3 未列） | v3 聚焦 Switch/Set 等节点形状契约；本轮首次把「非 main 连接整类丢弃」与 PASS 认证、deploy 链组合评估——18/130 PASS 工作流 round-trip 结构性损坏 |
| **P2-1..P2-5** | 新 | 部署链健壮性/验证脚本治理类 |
| **P3-1..P3-8** | 新 | 节点字段丢失清单、注释机制、注册表覆盖、死路径等 |
| **P3-8 边界强化** | 加剧 | v3：decompile 不还原 settings（缺失→运行时按 v2）。v4：envelope 恒 v1（decompile.py:131）→ 从"缺失"变"硬编码覆盖"，任何 v2 源工作流被强制降级 v1 |

---

## 四、缺口分级表（P0–P3）

### P0
无。

### P1

**P1-1　AI 子连接（ai_languageModel/ai_tool/ai_embedding/ai_memory/ai_vectorStore…）被静默丢弃，编译仍认证 PASS —— 缺陷 + 验证缺口**
- 证据（编译器侧）：
  - `parser/workflow.py:163` —— `if conn_type != _MAIN: continue`（非 main 连接静默跳过，无 warning）；
  - `parser/workflow.py:195,203` —— ai_referenced 节点排除终端收口（AI 子节点不接 exit，也不影响主链）；
  - `runtime/decompile.py:107-121` —— 只从 IR connections 重建 main 边，IR 里根本没有 ai 边可还原；
  - `tests/test_batch_matrix.py` PASS 认证不含 round-trip 边数断言。
- 证据（本轮独立测量）：
  - 143 矩阵文件中 **12 个含非 main 连接（48 条 ai_* 边）**，其中 **18 个 PASS 工作流携带 ai_* 边**（多 agent `committed-workflows/0.json` 16 条、`In_memory_vector_store_fake_embeddings.json` 8 条、RAG 6.json 3 条、rag_starter 4 条等）；
  - RAG fixture 6.json 实测：source main=12 / ai=3 → decompiled main=12 / ai=0；链 LLM 节点（chainLlm）在产物中成为无模型孤儿（实测节点保留、边丢失）。
- 性质：缺陷（静默语义丢失）+ 验证缺口（PASS 认证误导）。
- 影响：任何含 AI 子连接的 ncoda/n8n 工作流「编译通过 → deploy 成功 → 运行时失败」
  （agent/chainLlm 无模型、工具/向量库脱钩）。新 deploy 链使此缺口**生产可见**。
- 修复路径（分级）：
  1. 最小（推荐先做，低工作量）：parser 对丢弃的非 main 连接计数，编译期产出
     `manifest.ai_connections_dropped`（或 checker informational issue + warning）；
     `test_batch_matrix` 增加断言：含 ai_* 边的工作流不得无标记 PASS（改判 WARN 类）。
  2. 正解（IR v2，中工作量）：IR connections 白名单增加 `ai_*` 类型字段
     （from_type/to_type/index），decompile 按源类型还原；`typed_ir.py` 连接校验同步扩。
  3. 文档：README/架构文档明示「v1 不支持 AI 子连接 round-trip」为**编译警告**而非 PASS。

### P2

**P2-1　未注册 trigger 类型 → IR entry_keys 恒空 —— 缺陷（元数据）**
- 证据：`compiler/workflow.py:23-31`（entry 仅按 `NodeKind.TRIGGER/ERROR_TRIGGER` 判定；
  GENERIC 落 `NodeKind.GENERIC`）；`parser/node_adaptors.py:50-64`（未注册类型 → GenericNode，
  input_types 恒 {main:any} → `WorkflowAST.entry_keys` 的零输入回退也不触发）。
- 本轮实测：`mcp-trigger-basic.json`（@n8n/n8n-nodes-langchain.mcpTrigger）、
  `Test_Subworkflow_Get_Weather.json`（n8n-nodes-base.executeWorkflowTrigger）→ `entry_keys=[]`；
  chatTrigger（已注册）→ 正常。矩阵中此类未注册 trigger 约 17 文件。
- 性质：缺陷（IR 语义不实）+ 验证缺口（矩阵 PASS 不含 entry_keys 断言）。
- 影响：IR 消费方（deploy adapter、trigger 清单、编排）误判工作流入口；entry_keys 是
  v1 文档的顶层语义字段，恒空使「入口=trigger」契约失真。
- 修复路径：`_entry_keys` 增加「零 main 入边节点」回退（`main_incoming` 为空者，
  排除 exit/装饰/AI 子节点），或注册 mcpTrigger/executeWorkflowTrigger/formTrigger 三类。

**P2-2　deploy_to_n8n 非幂等（POST 恒新建，无 upsert）—— 验证缺口/设计**
- 证据：`runtime/deploy.py:50-63`（POST /api/v1/workflows 单发）；n8n 侧幂等素材齐备：
  GET /workflows 支持 `name` 查询（`public-api/v1/handlers/workflows/spec/paths/getWorkflows.generated.yml:27`）、
  PATCH /workflows/{id} 存在。
- 影响：同一 IR 重复部署产生重复工作流（同名可共存，无冲突信号）；生产「部署即更新」
  语义缺失。
- 修复路径：deploy adapter 增加 upsert 模式（GET ?name=X → 命中则 PATCH，否则 POST），
  或 manifest 记录 server_id 支持显式 update；单元测试 mock 两条路径。

**P2-3　凭据引用跨实例透传无映射 —— 验证缺口**
- 证据：`runtime/deploy.py:83`（decompile 原样还原 `node.credentials` 引用）；
  n8n 侧 create 流程 `workflow-creation.service.ts:130`（`replaceInvalidCredentials` 静默
  置空未知引用）、`:149-163`（凭据共享校验仅在 license 开启，未共享 → BadRequest）。
- 影响：跨实例部署时，源实例凭据 id/name 在目标实例不存在 → create 成功但凭据被置空 →
  execute 时认证失败（静默、无创建期信号）。编译器 manifest 已提取凭据名
  （`manifest.py:86-103`），映射步骤可做。
- 修复路径：deploy 前凭据 name→id 解析（GET /credentials → 替换引用）；对未知凭据
  显式失败或列出缺失清单；测试固化。

**P2-4　execute_deploy.py 隧道生命周期：pkill 模式匹配 + 批次无 per-scenario 容错 —— 验证脚本缺陷**
- 证据：`scripts/execute_deploy.py:96`（`pkill -f "ssh -f -N -L {port}:..."` substring
  匹配，可误杀同端口映射的无关隧道）；`:77-90`（`command_deploy` 循环内任何
  deploy_to_n8n 异常直接向上抛 → 批次中止、ids.json 不落盘，仅 finally 收隧道）。
- 性质：验证脚本健壮性缺陷（非产品代码，但影响验证可信度与并行环境安全）。
- 修复路径：`subprocess.Popen(["ssh","-N","-L",...])` 持柄 + finally `terminate()/kill()`
  （或 ControlMaster socket + `ssh -O exit`）替代 pkill；循环内 per-scenario
  try/except 记录失败继续，末尾汇总退出码。

**P2-5　execute_deploy.py 陈旧结果文件可造成假 PASS —— 验证缺口**
- 证据：`scripts/execute_deploy.py:130`（`command_execute` 仅成功时写 `${name}.json`，
  从不清理 out_dir）→ 场景失败时上一轮成功文件残留 → `execute_matrix.py assert` 读陈旧
  数据报 PASS。
- 修复路径：执行前清空 out_dir（或失败写哨兵文件）；assert 前校验结果文件 mtime 新鲜度。

### P3

| # | 性质 | 证据 | 修复路径 |
|---|---|---|---|
| **P3-1** 节点级字段 round-trip 丢失清单不完整（webhookId/disabled/notes/executeOnce/alwaysOutputData 全部丢弃；源 disabled 节点 round-trip 后变 enabled） | 文档问题 + 潜在缺陷 | `parser/node_adaptors.py:50-88`（只读 type/name/typeVersion/position/parameters/credentials/onError）；n8n REST create 补 webhookId（`workflow-creation.service.ts:132`）而 CLI import 不补（`import/workflow.ts` 全文无 resolveNodeWebhookIds）→ 两路径 webhook 激活行为可能不一致 | 先补文档化丢失清单 + round-trip 断言（disabled/executeOnce）；IR v2 再考虑携带 |
| **P3-2** decompile settings 注释机制描述不准确 | 文档问题 | `runtime/decompile.py:128-131` 声称「与 CLI import 填充的缺省一致」——n8n import 不填充 settings（`import/workflow.ts` 直存实体），运行时把缺失 executionOrder 当 v2（`execution-engine/workflow-execute.ts:200`）；行为选择（恒 v1）正确，机制引用错了 | 修正注释；并把「CLI 路径由隐式 v2 → 显式 v1」的语义影响并入 P3-8 文档 |
| **P3-3** deploy_to_n8n 2xx 非 workflow JSON（无 id）静默返回 | 验证缺口 | `runtime/deploy.py:68-74`（直接 `json.loads` 返回，不校验 `created.id`）；检查点在调用侧 `scripts/execute_deploy.py:81-84` | 库内校验 `created.get("id")` 或文档化调用方职责 |
| **P3-4** `--api-key` 命令行透传 | 卫生问题 | `scripts/execute_deploy.py:64`（argparse required）→ `ps` 可见 | 改 `N8N_API_KEY` 环境变量（AGENTS 秘密纪律同源） |
| **P3-5** server id / container 内插远端 shell 命令无校验 | 防御性缺陷 | `scripts/execute_deploy.py:114-116`（`docker exec {container} ... --id={sid}`） | sid 校验 `^[A-Za-z0-9_-]+$`；container 白名单或引号转义 |
| **P3-6** `static_values` 序列化死路径 | 文档问题 | `compiler/dependency.py:83`（仅 `source.ref is None` 填充），而 parser 只为 ref 建 input_sources（`parser/workflow.py:221-237`）→ static_values 恒空 | 标注 coze 对齐残留或删除 |
| **P3-7** 未注册多输出终端节点 exit 收口仅 main 端口 | 文档问题 | `parser/workflow.py:100-112`（GENERIC 按注册表 1 端口收口） | 行为与 n8n「未连端口即丢弃」等价、无害；注册表覆盖文档补一句 |
| **P3-8** 注册表覆盖盲区未成文（48/75 未注册） | 文档问题 | 本轮测量 75 种类型 48 未注册（64%）；关键漏项：`@n8n/n8n-nodes-langchain.code`（AI 链 Code 节点不走 CodeNode 静态分析，与 P1-1 同根）、executeWorkflowTrigger/formTrigger/mcpTrigger（P2-1）、wait/noOp（透传无害） | README/架构文档给出「注册表 vs 泛型透传」覆盖矩阵与风险说明 |

---

## 五、重点检查结论（任务要求 a–d）

### a) REST 部署链健壮性（错误处理 / 幂等 / 凭据注入边界）

| 维度 | 结论 | 证据 |
|---|---|---|
| 错误处理 | ✅ 显式：HTTP 非 2xx / 网络失败 / 非 JSON 均 ValueError（含状态码+响应摘要）；篡改 IR 请求前 digest 拒绝 | `runtime/deploy.py:60-74`；`test_deploy.py::test_tampered_ir_rejected`（urlopen 未调用断言） |
| 幂等 | ❌ **P2-2**：POST 恒新建，重复部署重复工作流 | `runtime/deploy.py:50-63` |
| 凭据注入边界 | ⚠️ **P2-3**：引用原样透传；n8n create 静默置空未知引用；共享校验仅 license 开启 | `runtime/deploy.py:56-58`；`workflow-creation.service.ts:130,149-163` |
| 响应校验 | ⚠️ **P3-3**：2xx 无 id 不校验 | `runtime/deploy.py:68-74` |

### b) execute_deploy.py 隧道生命周期

| 维度 | 结论 | 证据 |
|---|---|---|
| 正常/异常路径清理 | ✅ finally 必收隧道（含 healthz 超时、部署中途异常） | `scripts/execute_deploy.py:94-96` |
| pkill 安全性 | ❌ **P2-4**：substring 匹配可误杀同端口无关隧道；SIGKILL 时泄漏（无自愈） | `scripts/execute_deploy.py:96` |
| 失败清理/批次容错 | ⚠️ **P2-4**：单场景异常中止整批、ids.json 不落盘（幂等重试缺失） | `scripts/execute_deploy.py:77-90` |
| 结果可信度 | ❌ **P2-5**：陈旧结果文件假 PASS | `scripts/execute_deploy.py:130` |
| 命令注入面 | ⚠️ **P3-5**：sid/container 内插远端 shell | `scripts/execute_deploy.py:114-116` |

### c) settings/id 修复的一致性核查（CLI import vs REST 双路径）

- **schema 行级核对（本轮独立）**：`workflowCreate.yml` `required: [name, nodes, connections, settings]` +
  `id.readOnly: true` → decompile 恒带 settings（`runtime/decompile.py:131`）、deploy 剥 id
  （`runtime/deploy.py:48`）——**收口正确**。
- 节点级 schema（`node.yml` `additionalProperties: false`）：decompile 节点 dict 的键
  （name/type/typeVersion/position/parameters/credentials/onError/retryOnFail/maxTries/
  waitBetweenTries）全部在属性表内 —— 无 400 风险。
- 连接形状：IR main_ports 结构与 REST connections 形状一致 —— 无 400 风险。
- **未收口处**：
  1. decompile API 本身产出「带 id 的 CLI-import 形状」，REST 消费者必须记得剥 id
     （目前只在 deploy.py 收口，`runtime/deploy.py:44-48`）——文档已注明，属 API 形状
     warts（P3 级）；
  2. webhookId 双路径不对称（REST create 服务端补、CLI import 不补）→ P3-1；
  3. settings 恒 v1 与源工作流 v2 语义冲突 → 仍活项 P3-8（v4 强化）。

### d) 编译器对真实 n8n 节点的覆盖盲区（注册表 vs 实际）

- 量化（本轮）：矩阵 75 种类型 / 27 注册 / 48 未注册（64% 落 GENERIC 1入1出 ANY）。
- 三个真实盲区：
  1. **AI 子连接整类丢弃**（P1-1）——最险；
  2. **未注册 trigger → entry_keys 恒空**（P2-1）；
  3. **langchain Code 节点**（`@n8n/n8n-nodes-langchain.code`）不走 CodeNode 静态分析
     （`parser/node_adaptors.py:67` 只认 `n8n-nodes-base.code`）——与 P1-1 同根，AI 链
     代码依赖无法提取。
- 设计内盲区（可接受）：HTTP/gmail/slack 等长尾节点按 GENERIC 透传（n8n 1500+ 开放节点集）。

---

## 六、仍活项（v3 确认 / 本轮复核）

| 项 | 状态 | 本轮证据 |
|---|---|---|
| **P2-8** acorn script→module 回退 vs 编译器 module-only strict（文档化决策） | 仍活且有意 | `scripts/js_parse.mjs:23-26`（module-only + allowReturn/allowAwait）；n8n lint 为 script→module 回退（`packages/@n8n/workflow-sdk/src/lint/code-node/js.ts:44-47`）——偏差真实，决策记录成立 |
| **checker 类型矩阵信号≈0**（文档化） | 仍活且有意 | `checker/validator.py:152-173`（_is_assignable 宽松 + 多数输出 ANY）→ type_mismatch 仅双静态形状触发 |
| **P3-8** settings.executionOrder 不还原（v1→v2 语义边界） | 仍活；v4 强化 | `runtime/decompile.py:131` 恒 v1 硬编码；修复路径：IR v2 携带 settings 原值（或 deploy 侧可配） |

---

## 七、优先建议排序（风险 × 工作量）

| 优先 | 建议 | 风险降幅 | 工作量 | 依赖 |
|---|---|---|---|---|
| 1 | **P1-1 最小修复**：编译期 ai_* 边丢弃计数（manifest/checker warning）+ 矩阵「含 ai 边不得无标记 PASS」断言 + 文档降级「AI 支持 = WARNING」 | 消除「PASS 即正确」误导，部署前暴露 | 低（≤1d） | 无 |
| 2 | **P2-1 entry_keys 零入边回退** + 注册 mcpTrigger/executeWorkflowTrigger/formTrigger | IR 语义正确 | 低 | 无 |
| 3 | **P2-2 deploy upsert（name→PATCH/POST）** | 生产部署可重复执行 | 中（2-3d） | n8n GET/PATCH 契约（已核实存在） |
| 4 | **P2-4+P2-5 execute_deploy 治理**：Popen 隧道 + per-scenario 容错 + out_dir 清理 | 验证可信度与并行安全 | 低-中 | 无 |
| 5 | **P2-3 凭据 name→id 映射** | 跨实例部署不再静默失效 | 中 | manifest.credentials 已备 |
| 6 | **P3 批**：注释修正（P3-2）、节点字段丢失文档（P3-1）、api-key 环境变量（P3-4）、sid 校验（P3-5）、覆盖矩阵文档（P3-8） | 可维护性与审计性 | 低 | 无 |
| 7 | **P1-1 正解（IR v2 携带 ai_* 边）** | AI 工作流完整 round-trip | 中-高 | IR v2 通道（typed_ir `_ACCEPTED_VERSIONS` 已预留） |

---

## 八、置信度与证据边界

**独立复现（本轮实测，可复跑）**：
- 253 tests OK / 92.8% (2546/2745) / 143 矩阵分类（130/10/3）；
- AI 边盲区：18/130 PASS 携带 ai_* 边、RAG 6.json 丢 3 条（main 12 不变）、
  multi-agent 0.json 丢 16 条；
- entry_keys 空：mcpTrigger / executeWorkflowTrigger 两例实测；
- 注册表覆盖：75 类型 / 27 注册 / 48 未注册；
- settings/id 的 n8n schema 行级核对（workflowCreate.yml / workflow-execute.ts:200 /
  workflow-creation.service.ts:130,132,149-163 / workflow-helpers.ts:362-392 /
  import/workflow.ts 无 webhookId 解析 / getWorkflows.generated.yml:27 / js.ts:44-47）；
- decompile/deploy 单测行为（test_deploy.py 10 例逐项复核通过）。

**记录转引（未独立复跑，来自 v3 记录）**：
- nodecoda-production 真实实例 **7/7 PASS 双路径**（CLI import + REST deploy）——本轮无远端访问权；
- set 替换语义（has_x=False）的真实 n8n 执行实证；
- N8N_RUNNERS_BROKER_PORT=15679 端口冲突行为。

**无法在本环境验证**：真实实例 HTTP 时序、webhook 激活行为（双路径 webhookId 差异的实际影响）、
凭据替换（replaceInvalidCredentials）在目标实例的端到端表现。

---

## 九、修复记录

v4 审核后按「先回归红→再修绿」实施，2026-08-19 落地：

| # | 修复 | 状态 | 证据 |
|---|---|---|---|
| P1-1a | parser 统计非 main 连接（类型→边数）进 `WorkflowAST.non_main_connections`；manifest 透出 `ai_connections_dropped`（typed_ir 白名单放行，负数/非 int 拒绝） | ✅ | `parser/workflow.py`、`manifest.py`、`typed_ir.py:300-309`；回归 `test_manifest_ai_dropped_counted` |
| P1-1b | 矩阵分类拆出 `PASS_AI_DROPPED`（18 个），PASS（112）与 AI_DROPPED **互斥断言**；README 矩阵数字 130→112/18 | ✅ | `tests/test_batch_matrix.py`；独立测量 18 文件 / 61 条 ai_* 边（9 类） |
| P1-1c | IR v2 携带 ai_* 连接并还原：parser 非 main 边进 `WorkflowAST.ai_connections`（不再丢弃）；IR connections 每条带 `conn_type`（v2，v1 缺省 main 兼容装载）；decompile 按 conn_type 分组还原；AI 子节点排除出 execution_order（typed_ir 按 main 拓扑节点全排列断言）；矩阵 18 个 PASS_AI_DROPPED 撤回 PASS（130）；manifest `ai_connections_dropped` 语义改为携带数 | ✅ | `parser/workflow.py`、`compiler/workflow.py`、`typed_ir.py`、`runtime/decompile.py`；回归 `test_ai_edges_preserved_round_trip`、`test_v2_ai_connections_carry_conn_type`、`test_execution_order_excludes_ai_subnodes`、`test_v1_document_without_conn_type_loads`、`test_ai_conn_to_port_accepts_conn_type`、`test_main_conn_to_port_strict`；真实实例（nodecoda-production）：`In_memory_vector_store_fake_embeddings.json` round-trip 部署，8 条 ai 边（6 类）服务端 GET 守恒，`n8n execute` exit=0 全链执行通过 |
| P2-1 | REGISTRY 注册 mcpTrigger/executeWorkflowTrigger/formTrigger + `_entry_keys`「类型名含 trigger」启发式兜底（零入边回退会误伤无 trigger 工作流，弃用） | ✅ | `ast_nodes/node_type.py:69-73`、`compiler/workflow.py:26-48`；回归 `test_unregistered_trigger_entry_key_fallback`（注册 + 启发式两路径）；mcp-trigger 实测 entry_keys=['MCP Server Trigger'] |
| P2-2 | deploy upsert：GET ?name → **PUT** 更新 / POST 新建（n8n update 路由是 PUT，PATCH→405）；key 需 `workflow:create + workflow:list + workflow:update`（list ≠ read，真实实例抓出） | ✅ | `runtime/deploy.py:31-83`；回归 `TestDeployUpsert` 4 例；真实实例 upsert 7/7（db 不新增） |
| P2-3 | 凭据 name→id 部署前映射：节点引用按 name 解析目标实例 id（GET /credentials cursor 分页）；未知凭据部署前显式失败列缺失清单；`credential_map` 显式映射可跳过 GET；无凭据引用零请求 | ✅ | `runtime/deploy.py:69-157`；回归 `TestCredentialsResolution` 4 例；真实实例：建 MyHeaderAuth 凭据 + 部署引用工作流（src-1→KaMqWKQeCFB3guaT 替换）+ 缺失 NopeCred 显式失败（真实抓出：offset 分页 400 → cursor 分页） |
| P2-4 | Popen 持柄隧道（finally terminate/kill，替代 pkill substring）+ per-scenario 容错（失败记录继续，ids.json 始终落盘） | ✅ | `scripts/execute_deploy.py:86-125`；真实实例回归 7/7 |
| P2-5 | execute 前重建 out_dir（防陈旧结果假 PASS） | ✅ | `scripts/execute_deploy.py:139-141` |
| P3-3 | 2xx 但响应无 id → 显式 ValueError（不静默返回） | ✅ | `runtime/deploy.py:112-118`；回归 `test_response_without_id_rejected` |
| P3-4 | `--api-key` 降级为应急，主通道 `N8N_API_KEY` 环境变量 | ✅ | `scripts/execute_deploy.py:65-71` |
| P3-5 | sid / container 名 shell 内插前正则校验 | ✅ | `scripts/execute_deploy.py:41-43,142,149` |
| P3-1 | round-trip 丢失清单成文（README「编译边界」表：notes/webhookId/disabled/executeOnce/alwaysOutputData）+ 边界回归 `test_node_aux_fields_dropped_documented`（锁定现状防静默改变） | ✅ | `README.md:64-76`、`tests/test_decompile.py` |
| P3-2 | decompile settings 注释机制修正（n8n CLI import 不填充 settings；缺失 executionOrder 运行时按 v2——注释改为准确机制描述） | ✅ | `runtime/decompile.py:134-140` |
| P3-6 | `static_values` 死路径标注（coze 对齐残留，勿扩展） | ✅ | `compiler/dependency.py:80-85` |
| P3-7 | 未注册多输出终端 exit 收口仅 main 端口——文档说明与 n8n 行为等价 | ✅ | `README.md`「注册表覆盖」节 |
| P3-8 | 注册表覆盖矩阵成文（75 类型 27 注册 / 48 未注册 + 关键漏项风险）；**settings.executionOrder 仍活项关闭**：IR v2 携带源 settings 原值（`_serialize_ir` workflow.settings），decompile 还原（缺失回退 v1），typed_ir 校验 settings 为对象 | ✅ | `README.md:78-91`、`compiler/workflow.py:125-128`、`typed_ir.py:35-37,155-160`、`runtime/decompile.py:134-141`；回归 `test_settings_round_trip_restored`、`test_settings_default_v1_when_absent`、`test_settings_carried_in_ir_v2`、`test_workflow_settings_must_be_object`；真实实例（nodecoda-production）：v2-settings 工作流部署后服务端 GET 守恒（未降级 v1），`n8n execute` exit=0 |
| P1-2（v4 后补） | P3-8 记录的「AI 链 Code 盲区」关闭：`@n8n/n8n-nodes-langchain.code` 注册为 CODE kind，与普通 Code 节点同走一等公民静态通道——acorn 语法检查 + 契约 + ESTree 进 IR；取源分流 `parameters.code.supplyData.code`（旧版字符串兜底）；新增工厂模式语义（return 组件实例 → shape OBJECT、mode hint）；`extract_output_shape` NewExpression → OBJECT（new 表达式值恒为对象） | ✅ | `ast_nodes/node_type.py`、`parser/node_adaptors.py`、`parser/workflow.py`、`code/js_ast.py`、`code/js_static.py`；回归 `test_langchain_code_statically_compiled`、`test_langchain_code_bad_syntax_caught`、`test_langchain_code_factory_source_extracted`、`test_langchain_code_source_factory_routing`、`test_factory_return_new_expression_is_object`、`test_factory_mode_hint`；真实实例：fake-embeddings（4 个 langchain.code，require 判 IO）部署 + `n8n execute` exit=0 |

**复跑基线（v4 + P1-1c 后）**：`python3 -m unittest discover -s tests` → **268 tests OK**；
`python3 tests/coverage.py --quiet` → 见 TESTING.md 基线（P1-1c 新增 6 条回归）。
真实实例（nodecoda-production，n8nio/n8n:latest）：create 7/7 + upsert 7/7（PUT 更新、
db 不新增）+ execute 7/7 断言全部 PASS；凭据解析命中/缺失双路径实测通过（P2-3）。

---

## 附：n8n 侧关键证据行（本轮独立核对）

- `packages/cli/src/public-api/v1/handlers/workflows/spec/schemas/workflowCreate.yml`：required name/nodes/connections/settings；id readOnly
- `packages/cli/src/public-api/v1/handlers/workflows/spec/schemas/node.yml`：节点属性白名单（additionalProperties:false）
- `packages/core/src/execution-engine/workflow-execute.ts:200,441,964`：`executionOrder !== 'v1'` → v2 语义
- `packages/cli/src/workflows/workflow-creation.service.ts:130,132,149-163`：replaceInvalidCredentials / resolveNodeWebhookIds / 凭据共享校验（license 门控）
- `packages/cli/src/workflow-helpers.ts:362-392`：replaceInvalidCredentials（未知引用静默置空）
- `packages/cli/src/commands/import/workflow.ts`：实体直存，无 settings 填充、无 webhookId 解析
- `packages/cli/src/public-api/v1/handlers/workflows/spec/paths/getWorkflows.generated.yml:27`：GET /workflows 支持 name 查询
- `packages/@n8n/config/src/configs/endpoints.config.ts:222`：`/healthz` 默认开启（liveness，不依赖 DB）
- `packages/@n8n/workflow-sdk/src/lint/code-node/js.ts:44-47`：n8n lint script→module 回退（P2-8 偏差依据）
- `packages/cli/src/abstract-server.ts:139-157`：healthz / healthz/readiness 语义

---

## 附：可用性结论（供决策）

> 本节为 v4 审核**时点**结论；P1-1c + P1-2 落地后状态已升级（2026-08-19 修订）。

**v4 时点**：非 AI 工作流 253 tests / 92.8%；AI 工作流编译 PASS 但 round-trip 结构性
损坏（P1-1），建议明示 WARNING 级能力。

**当前（P1-1c + P1-2 后）**：AI 工作流已结构无损——IR v2 完整携带 ai_* 子连接
（round-trip 守恒，真实实例 8/8 边 + execute exit=0）；AI 链内联 Code（langchain.code）
与普通 Code 节点同走一等公民静态通道（acorn 语法 + 契约 + ESTree 进 IR）。
当前基线 281 tests / 92.8%（2721/2932）。仍活且有意（非缺陷）：P2-8 编译器
module-only 严格、checker 类型信号≈0。
