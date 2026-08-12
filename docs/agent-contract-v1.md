# 招投标智能体接口规范 v1.0.0

状态：冻结（Frozen）  
适用范围：文档解析、合规审查、数据核验、异常分析、报告生成、人工复核  
原则：文档只解析一次；所有智能体复用统一文档对象；确定性计算优先于大模型。

## 1. 总体数据流

```text
原始文件
  -> 文档解析智能体
  -> DocumentContext[]
  -> 合规审查 / 数据核验 / 异常分析
  -> AgentResult[]
  -> 人工复核
  -> ReportResult
```

任何专业智能体不得重新解析 PDF、DOCX 或 OCR；只能消费 `DocumentContext`。

## 2. 版本规则

- 所有跨智能体请求必须携带 `contract_version: "1.0.0"`。
- 增加可选字段：升级次版本，例如 `1.1.0`。
- 删除字段、改名或改变字段类型：升级主版本，例如 `2.0.0`。
- 已发布的 Dify 工作流不得自行改变字段类型。
- Dify 的字符串 JSON 由后端适配器解析，内部对象不得保存转义后的 JSON 字符串。
- 对方系统可调用 `GET /api/agent/capabilities` 获取当前 API、智能体契约及支持能力。
- 所有任务响应包含 `api_version`；v1 系列只能增加向后兼容的可选字段。

### 2.1 独立文档解析接口

`POST /api/v1/agents/document-parser` 使用 `multipart/form-data`：

- `request`：序列化后的 `AgentRequest` v1 JSON，必须提供 `input.project_name`。
- `files`：一个或多个 PDF、DOCX、TXT、Markdown 或 XLSX 文件。
- `X-API-Key`：配置 `AGENT_API_TOKEN` 后必须提供。

`request` 示例：

```json
{
  "contract_version": "1.0.0",
  "request_id": "REQ-DOC-001",
  "project_id": "P-001",
  "task_id": "",
  "input": {"project_name": "某市信息化平台项目"},
  "options": {
    "enable_dify": true,
    "enable_human_review": true,
    "trace_enabled": true
  }
}
```

接口返回统一 `AgentResponse`。结构化解析结果位于 `result.documents`，
共享事实对象位于 `result.document_contexts`，可追溯问题位于 `findings`。
`enable_dify=false` 时仅执行确定性解析、OCR、
版面与视觉规则，不调用 Dify 文档语义增强工作流。

### 2.2 独立合规审查接口

`POST /api/v1/agents/compliance-review` 使用 `application/json`，请求体为
`AgentRequest` v1。其中 `input` 必须包含：

- `documents`：文档解析接口返回的 `result.documents`。
- `document_contexts`：文档解析接口返回的 `result.document_contexts`。
- `system_record`：可选的业务系统基准数据对象；未提供时按空对象处理。

接口校验两组文档标识完全一致，随后执行评标报告章节完整性、基础信息一致性、
废标依据回查、风险条款审查与证据定位。它不会重新读取或解析原始文件。
`enable_dify=false` 时仅执行确定性合规规则；启用时复用已配置的 Dify 合规工作流与 RAG。

## 3. DocumentContext

`DocumentContext` 是所有智能体共享的唯一文档事实来源。

必填字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| contract_version | string | 固定为 `1.0.0` |
| document_id | string | 文档稳定标识 |
| file_name | string | 原始文件名 |
| file_hash | string | SHA-256，用于缓存和幂等 |
| document_type | string | 招标文件、投标文件、合同、法规等 |
| raw_text | string | 解析后的全文；仅存储一次 |
| sections | array | 章节及来源位置 |
| tables | array | 表格及来源位置 |
| key_fields | object | 项目名称、预算、限价、期限等 |
| clause_groups | object | 资格、技术、评分、程序合同条款 |
| entities | object | 企业、人员、电话、邮箱、账户等 |
| file_metadata | object | 作者、创建工具、创建时间等 |
| quality | object | 解析状态、置信度、警告和人工复核标识 |

数值字段同时保留：

- `value`：标准化值，例如金额统一为人民币元；
- `raw_text`：原文；
- `source`：页码、章节和原文证据；
- `confidence`：0 到 1；
- `requires_human_review`：Boolean。

## 4. 统一请求与响应外壳

所有独立智能体服务使用 `AgentRequest` 和 `AgentResponse`，JSON 中未知字段将被拒绝。

`AgentRequest` 固定包含：`contract_version`、`request_id`、`project_id`、`task_id`、`input`、`options`。

`AgentResponse` 固定包含：`contract_version`、`request_id`、`agent`、`status`、`summary`、`result`、`findings`、`warnings`、`errors`、`execution`。

## 5. 统一问题 AgentFinding

所有审查智能体必须输出同一种问题结构：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| finding_id | string | 是 | 稳定问题编号 |
| agent | enum | 是 | compliance、data、anomaly、human_review |
| risk_level | enum | 是 | 高、中、低 |
| issue_type | string | 是 | 问题类型 |
| description | string | 是 | 事实性描述 |
| evidence | array[AgentEvidence] | 是 | 可定位证据；passed 检查项可为空 |
| basis | string | 是 | 判断依据；不得编造 |
| suggestion | string | 是 | 修改或复核建议 |
| final_status | enum | 是 | confirmed_issue、human_review、passed |
| confidence | number | 是 | 0 到 1 |
| requires_human_review | boolean | 是 | 是否进入人工复核 |
| rule_id | string | 否 | 命中的确定性规则 |

三态含义：

- `confirmed_issue`：规则已确认或人工确认存在问题；
- `human_review`：仅形成机器线索，等待人工确认；
- `passed`：确认未发现问题或人工判定为误报。

`not_detected`、`not_checked`、`low_confidence`、`mismatch`、`uncertain` 必须映射为 `human_review`。只有人工确认存在问题后才能转为 `confirmed_issue`。

`AgentEvidence`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| document_id | string | 来源文档 |
| quote | string | 必须逐字存在于原文 |
| page | integer/null | 页码 |
| section | string | 章节 |
| source_type | enum | text、table、metadata、visual、derived |
| derived_from | array | 派生证据引用；非派生证据为空 |

## 6. 统一错误 AgentError

错误字段固定为：`code`、`message`、`retryable`、`stage`、`details`、`trace_id`。

冻结错误码：`INVALID_REQUEST`、`FILE_PARSE_FAILED`、`OCR_FAILED`、`MODEL_UNAVAILABLE`、`KNOWLEDGE_RETRIEVAL_FAILED`、`AGENT_WORKFLOW_TIMEOUT`、`EVIDENCE_NOT_FOUND`、`OUTPUT_VALIDATION_FAILED`、`INTERNAL_ERROR`。

外部响应与正式报告不得暴露密钥、堆栈和供应商内部错误；真实异常仅进入服务日志。

## 7. 智能体职责与输入输出

### 5.1 文档解析智能体

输入：文件及文件元数据。  
输出：`DocumentContext[]`。  
禁止：法律判断、风险定级、编造缺失字段。

### 5.2 合规审查智能体

输入：`DocumentContext`、法规检索上下文。  
消费字段：`key_fields`、`clause_groups`、相关原文。  
输出：`AgentResult<AgentIssue[]>`。  
禁止：重新解析文件、执行金额计算、把未勾选模板项判为问题。

### 5.3 数据核验智能体

输入：`DocumentContext`。  
输出：金额、比例、日期、期限、合计及字段冲突类 `AgentIssue[]`。  
要求：计算规则由代码执行，LLM 只解释复杂语义关系。

### 5.4 异常分析智能体

输入：`DocumentContext[]`、合规结果、数据结果、`relationship_data`。  
输出：跨文件、跨主体、多信号异常 `AgentIssue[]`。  
要求：不得把单一弱信号直接认定为串通投标。

### 5.5 报告生成智能体

输入：全部 `AgentResult[]` 和人工复核结果。  
输出：`ReportResult`。  
禁止：新增问题、修改证据原文、重新进行法律判断。

## 8. Dify 边界

Dify 负责：候选语义提取、法规检索、批量合规判断、语言摘要。  
后端负责：文件解析、OCR、字段标准化、计算、缓存、去重、证据校验、调度、人工复核和报告持久化。

Dify 输入使用以下固定名称：

- 文档解析：`document_text`
- 合规审查：`document_context`
- 数据核验：`document_context`
- 异常分析：`parsed_documents`、`compliance_results`、`validation_results`、`relationship_data`
- 报告生成：`agent_results`、`human_review_results`

## 9. 不可破坏的校验规则

1. `requires_human_review`、`is_issue` 必须是 Boolean。
2. `evidence.quote` 必须能在对应原文或结构化来源中定位。
3. 纯计算问题必须包含计算表达式和输入来源。
4. 未勾选模板项不得产生合规问题。
5. 报告只能包含专业智能体输出或人工补录的问题。
6. 同一 `file_hash + workflow_version + ruleset_version` 必须可命中缓存。
7. 所有运行记录必须保存工作流版本、模型、提示词版本和耗时。

## 10. 当前迁移顺序

1. 冻结本规范和 JSON Schema。
2. 现有解析结果适配为 `DocumentContext`。
3. 合规 Dify 输入由全文改为 `document_context`。
4. 数据核验复用同一 `document_context`。
5. 异常分析复用多个文档对象和前序结果。
6. 全链路通过真实文件评测后，再迁移 PostgreSQL、缓存和任务队列。

