# 招投标智能核验项目交接与复现说明

本文只回答三件事：项目已经完成了什么、代码分别在哪里、接手人员如何复现。
原始任务需求保存在仓库根目录 `requirements_20260803.xlsx`。

## 1. 最终交付是什么

本仓库实现的是可接入现有采购业务系统的招投标智能核验服务，不是单独重做一套采购系统。
系统以 FastAPI 提供接口，由 LangGraph 编排五个专项智能体，并可调用 Dify Workflow、
法规 RAG 和模型进行语义增强。Dify 或模型不可用时，确定性解析和规则仍可继续执行。

五个智能体及职责如下：

1. 文档解析智能体：解析 PDF、DOCX、TXT、XLSX，执行 OCR、版面与表格处理、字段抽取、
   显式解析规划、质量门重试、证据切片和视觉核验。
2. 合规审查智能体：审查采购条款、章节完整性、基础信息一致性和废标依据，结合法规知识库
   形成问题、依据、原文证据和整改建议。
3. 数据核验智能体：复算报价、评分、权重和排名，核对报告、开标记录和评分表中的字段一致性。
4. 异常分析智能体：分析专家评分偏离、跨标段差异、文本雷同、联系人、设备、网络、文件元数据
   和报价规律等组合线索；只输出风险线索，不直接认定串通投标事实。
5. 报告生成智能体：汇总最终三态结论和证据链，生成结构化交付物、Word 与 PDF 报告。

最终状态统一为：

- `confirmed_issue`：人工或确定性规则已经确认的问题；
- `human_review`：证据不足、低置信度或视觉检测未完成，等待人工复核；
- `passed`：人工确认或规则确认不构成问题。

## 2. 已经完成的功能

- FastAPI 文件上传、URL 文件接入、任务查询、回调、人工复核和报告下载接口；
- LangGraph 动态路由、五智能体协作、质量复核、失败退回、人工中断与断点恢复；
- PDF、DOCX、TXT、Markdown、CSV、JSON、XLSX 解析及扫描 PDF OCR；
- 文档显式 `parse_plan`、质量检查与自动重试、标准 `evidence_chunks`、项目级临时证据索引；
- Dify 五工作流的独立 API Key 接入和超时降级；法规长期知识库与项目临时证据索引分离；
- 合规、数据、异常的确定性规则和语义增强结果合并；
- `AI发现 → 待人工复核 → 确认问题/确认无问题` 的三态审计闭环；
- 前端展示当前智能体、目标、调用工具、当前发现、下一步决策、复核原因和完整执行轨迹；
- 真实内容的 Excel 清单、完整 JSON 数据包、Word《评标智能核验报告》和 PDF；
- 企业级演示数据集及自动评测，覆盖多供应商、设备/网络关联、文本雷同、报价规律、缺章、
  错章和签名模型未配置等场景；
- 冻结统一 Agent 请求、响应、错误、证据和三态协议，提供五个独立智能体接口和一个总任务入口。

最近一次完整验收任务使用 6 份企业级 Demo 文件运行，自动评测的 7 个预期检查全部命中，
证据覆盖率和人工复核覆盖率均为 100%；人工复核后成功生成 Word、PDF、四类 Excel 和完整数据包。
该结果用于工程回归，不代表在所有真实采购项目上的业务准确率为 100%。

## 3. 代码在哪里

### 3.1 总控、五个智能体和质量复核

| 能力 | 代码位置 |
|---|---|
| LangGraph 总控、路由、执行事件、人工中断恢复 | `app/agents/supervisor.py` |
| 文档解析智能体 | `app/agents/document_parser.py` |
| 合规审查智能体 | `app/agents/compliance_checker.py` |
| 数据核验智能体 | `app/agents/data_validator.py` |
| 异常分析智能体 | `app/agents/anomaly_analyzer.py` |
| 报告生成智能体 | `app/agents/report_generator.py` |
| 结果证据校验、去重及退回 | `app/agents/quality_reviewer.py` |
| 自动选择核验路径 | `app/agents/routing_agent.py` |

### 3.2 文档、证据、Dify 和报告服务

| 能力 | 代码位置 |
|---|---|
| 文件类型解析与表格提取 | `app/services/file_parser.py` |
| OCR | `app/services/ocr_service.py` |
| 视觉检测与状态映射 | `app/services/document_visual_service.py` |
| 文档语义增强 | `app/services/document_semantic_enhancer.py` |
| Dify Workflow HTTP 客户端 | `app/services/dify_client.py` |
| 项目级证据索引与检索 | `app/services/project_index.py` |
| 证据定位 | `app/services/evidence_locator.py` |
| 资料清单与角色识别 | `app/services/material_inventory.py` |
| Word/PDF 报告 | `app/services/report_service.py` |
| Excel/JSON 等真实交付物 | `app/services/deliverable_service.py` |
| 任务持久化 | `app/services/task_store.py` |

### 3.3 API、协议和前端

| 能力 | 代码位置 |
|---|---|
| 总任务 API | `app/api/tasks.py` |
| 五个独立智能体 API | `app/api/agents.py` |
| 冻结 Agent 协议 | `app/schemas/agent_protocol.py` |
| 任务和业务结果模型 | `app/schemas/task.py` |
| 服务配置 | `app/core/config.py`、`.env.example` |
| 可视化控制台 | `app/static/index.html`、`app/static/app.js`、`app/static/styles.css` |

### 3.4 Dify 资产

- 报告生成工作流 DSL：根目录 `招投标报告生成智能体-2.0.yml`；
- 合规批量分类、提示词和结构化输出：`dify/`；
- 文档解析配置说明：`docs/dify-document-parser-workflow.md`；
- 合规批量版配置说明：`docs/dify-compliance-batch-v2.md`。

注意：Dify 中已经发布的五个应用及知识库属于 Dify 实例的外部状态，不会随 Git 自动迁移。
当前仓库只有上述 DSL、提示词、Schema 和配置说明。换 Dify 服务器时需要导入 DSL、按文档重建或导出其余工作流，
重新上传法规知识库文档，并把新应用 Key 写入目标机器的 `.env`。

## 4. 如何在另一台电脑复现

### 4.1 获取代码和 Python 依赖

```powershell
git clone https://github.com/123alien/agent.git
cd agent
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

如果目标机器使用其他受支持的 Python 3 版本，可将 `py -3.12` 换成实际命令。

### 4.2 配置 `.env`

最小本地运行只需设置数据目录；生产接入还必须设置访问令牌和跨域来源：

```dotenv
DATA_DIR=./data
AGENT_API_TOKEN=至少32位随机字符串
CORS_ALLOWED_ORIGINS=http://业务系统地址
```

需要复现 Dify 增强时填写五个已发布工作流的 Key：

```dotenv
DIFY_BASE_URL=http://Dify服务器地址/v1
DIFY_DOCUMENT_PARSER_API_KEY=app-文档解析Key
DIFY_COMPLIANCE_API_KEY=app-合规审查Key
DIFY_DATA_VALIDATOR_API_KEY=app-数据核验Key
DIFY_ANOMALY_ANALYZER_API_KEY=app-异常分析Key
DIFY_REPORT_GENERATOR_API_KEY=app-报告生成Key
```

后端兼容模型可通过 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` 替换，不需要改 Python 代码。
真实 `.env` 不得提交 Git。

### 4.3 启动服务

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开：

- 前端控制台：`http://127.0.0.1:8000/`
- Swagger：`http://127.0.0.1:8000/docs`
- 健康检查：`http://127.0.0.1:8000/health`
- 能力与协议版本：`http://127.0.0.1:8000/api/agent/capabilities`

### 4.4 复现自动测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts\smoke_test.py
```

当前基线为 133 个测试通过。依赖或代码升级后应先重新执行，不应只依据本说明判断通过。

### 4.5 复现企业级五智能体 Demo

演示资料位于 `test_data/enterprise_demo/`，详细文件顺序、预期线索和边界说明见该目录的 `README.md`。
在前端新建任务，选择 `full`，上传采购文件、四家供应商响应文件和电子交易元数据后运行。

任务进入 `waiting_review` 后逐项选择“确认问题”“确认无问题”或“需修改”，提交后工作流恢复并生成正式报告。
完成后复制页面或查询接口中的 `task_id`，执行：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate_enterprise_demo.py --task-id 任务编号
```

验收时至少检查：

- B/C 的设备、网络、联系人、文件元数据和文本雷同是否形成组合证据链；
- 报价规律是否只表述为异常线索，而不是直接认定串标；
- D 公司的 `not_detected`、`mismatch/low_confidence`、`not_checked` 是否进入人工复核；
- 合规问题、数据复算、风险数量、人工复核状态和最终报告口径是否一致；
- Word、PDF、四类 Excel 和完整 JSON 数据包是否有真实明细，而不是只有标题。

## 5. 业务系统如何接入

推荐调用总入口创建任务，由 LangGraph 调度五个智能体：

- `POST /api/agent/tasks`：直接上传文件；
- `POST /api/agent/tasks/from-urls`：由服务下载业务系统文件；
- `GET /api/agent/tasks/{task_id}`：查询状态、执行轨迹和结果；
- `POST /api/agent/tasks/{task_id}/review`：提交人工复核；
- 报告和交付物下载接口以 Swagger 中当前定义为准。

调用方在启用访问令牌后必须发送 `X-API-Key`。完整请求、回调、文件角色和复核数据示例见
`docs/integration.md`；统一接口和证据协议见 `docs/agent-contract-v1.md`。

如果业务系统只想单独复用某项能力，可以调用 `app/api/agents.py` 暴露的五个 `/api/v1/agents/*` 接口。

## 6. 运行数据、Git 与迁移边界

- GitHub 保存代码、测试、Demo、文档、Dify DSL/提示词/Schema；
- `.env`、用户上传文件、任务 JSON、报告、复核记录、SQLite 检查点和本地验收产物不提交 Git；
- 迁移已有任务时需单独复制 `DATA_DIR`，只拉 Git 不会带走历史任务；
- 迁移 Dify 时需迁移工作流、模型供应商配置和知识库，Git 中的后端代码不能替代这些外部状态；
- 模型、Prompt、规则和知识库升级后，应更新版本配置并重新执行测试集和人工样本验收。

## 7. 当前边界与后续优化

当前版本已适合系统联调、演示和验收，但不能宣称对任意真实采购项目达到生产级准确率。
后续进入业务系统后优先积累人工标准答案，评估准确率、召回率、误报率和证据定位准确率；
再根据真实负载引入数据库、对象存储、消息队列、权限、脱敏、限流和集中日志。

不要把单一 IP、MAC、报价规律或文本相似度直接写成违法事实。系统定位始终是：
发现线索、给出证据、评估风险、进入人工复核并保留审计记录。
