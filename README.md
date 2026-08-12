# 招投标智能核验智能体服务

这是一个可独立运行、后续可接入现有系统的多智能体服务原型。现有系统后续只需要通过 HTTP API 调用本服务，即可完成文件核验、结果查询和报告下载。

项目背景、架构决策、当前进度及 Dify 后续接入步骤见 [项目交接说明](docs/project-handoff.md)。

新电脑部署或交付业务系统时，优先阅读 [新电脑快速接入清单](docs/quickstart-handoff.md)。
五个独立智能体的冻结协议见 [Agent Contract v1](docs/agent-contract-v1.md)。

跨电脑部署、Dify配置、模型替换、业务系统调用和上线验收请直接查看
[跨电脑部署与系统接入指南](docs/deployment-guide.md)。

## 功能

- 创建智能核验任务并上传招标/投标/评标文件
- 总控调度智能体编排五个专项智能体
- 文档解析、合规审查、数据核验、异常分析、报告生成
- 返回结构化 JSON 问题清单
- 生成 Markdown 核验报告
- 支持人工复核结果回传接口

## 文档解析智能体

文档解析智能体目前支持文本型 PDF、DOCX、TXT、Markdown、CSV 和 JSON。
它会自动按文件扩展名选择解析器，并输出：

- 清洗后的正文及页数
- 章节标题、层级、正文和来源页码/行号
- 表格行列数据及所在页
- 项目名称、预算、最高限价、采购人和截止时间等关键字段
- 字段原文、来源位置、置信度和人工复核标记
- 扫描件、空文本、乱码和关键字段缺失等质量检查结果

疑似扫描型 PDF 会自动调用本地 RapidOCR，OCR 失败或平均置信度偏低时进入
人工复核；旧版 `.doc` 需要先转换为 `.docx`。完整数据结构和验收方法见
[文档解析智能体说明](docs/document-parser.md)。

## 启动

推荐新环境直接使用Docker：

```bash
cp .env.example .env
docker compose up -d --build
```

Windows也可以执行：

```powershell
.\scripts\setup_windows.ps1
.\scripts\start_windows.ps1
```

手工启动方式：

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

打开接口文档：

```text
http://localhost:8000/docs
```

系统接入前可读取服务能力和冻结的接口版本：

```text
GET http://localhost:8000/api/agent/capabilities
```

打开可视化测试控制台：

```text
http://localhost:8000/
```

控制台支持多文件上传、任务进度轮询、解析指标、关键字段、问题清单、
智能体执行结果、原始 JSON 查看，以及 Markdown、Word、PDF 报告下载。

## Dify Workflow 接入

在 `.env` 中配置已发布的 Dify Workflow：

```text
DIFY_BASE_URL=http://localhost:8080/v1
DIFY_COMPLIANCE_API_KEY=app-xxxxxxxx
DIFY_DOCUMENT_PARSER_API_KEY=app-yyyyyyyy
DIFY_TIMEOUT_SECONDS=120
```

旧配置名 `DIFY_API_KEY` 仍兼容，但新配置建议使用语义明确的
`DIFY_COMPLIANCE_API_KEY`。配置后，合规审查智能体优先调用 Dify Workflow，输入变量名必须为
`document_text`，结束节点输出变量名必须为 `result`。Dify 不可用或返回异常时，
服务会回退到本地规则与 OpenAI 兼容模型调用。

## LangGraph 多智能体编排

`SupervisorAgent` 使用 LangGraph `StateGraph` 管理共享任务状态和动态路由：

```text
文档解析 -> Supervisor -> 合规审查 / 数据核验 / 异常分析
                                  -> 结果复核 -> 报告生成
                                       |
                                       `-> 不合格时退回一次
```

`check_type` 支持以下路由：

- `auto`（默认）：根据文件类型、解析字段和原文特征自动选择
- `full`：执行合规审查、数据核验和异常分析
- `compliance`：只执行合规审查
- `data`：只执行数据核验
- `anomaly`：只执行异常分析

结果复核智能体会校验证据、高风险人工复核标记和重复问题；不合格结果最多
退回对应专项智能体一次，重试后仍不合格则剔除。报告生成智能体的
`data.execution_trace` 会记录实际执行的图节点。

高风险问题会触发 LangGraph 人工中断，任务状态变为 `waiting_review`。
调用 `POST /api/agent/tasks/{task_id}/review` 提交人工决定后，工作流使用同一个
`task_id` 从 SQLite Checkpointer 恢复。支持“正确”“误判”和“需修改”：误判会
剔除问题，需修改可通过 `corrected_text` 修正问题描述。

## 创建任务

```bash
curl -X POST "http://localhost:8000/api/agent/tasks" ^
  -F "project_id=P20260806001" ^
  -F "project_name=测试招标项目" ^
  -F "check_type=full" ^
  -F "files=@sample.txt"
```

## 查询结果

```bash
curl "http://localhost:8000/api/agent/tasks/{task_id}"
```

## 下载报告

```bash
curl -O "http://localhost:8000/api/agent/tasks/{task_id}/report"
```

## 本地冒烟测试

```bash
python scripts/smoke_test.py
```

测试会使用 `samples/demo_tender.txt` 创建一条核验任务，并输出任务状态、问题数量和报告路径。

## 企业级多文件 Demo 数据集

仓库提供一套可重复生成的“1 个采购项目 + 4 家供应商”测试数据，覆盖正常对照、
设备与网络特征重合、文件雷同、报价规律、缺章、印章主体不一致和签名能力未配置等场景：

```text
test_data/enterprise_demo/
```

数据包同时包含 PDF、DOCX、电子交易与专家评分 Excel 以及机器可读预期结果。
具体上传顺序、风险口径和验收标准见
[Demo 数据集说明](test_data/enterprise_demo/README.md)。重新生成命令：

```powershell
.\.venv\Scripts\python.exe .\scripts\build_enterprise_demo.py
```

该数据集只用于发现风险线索和验证人工复核闭环，任何单一 IP、MAC、报价规律或
文本相似信号都不得被直接表述为串通投标事实。

## 后续系统接入

现有系统可以调用：

- `POST /api/agent/tasks` 创建核验任务
- `POST /api/agent/tasks/from-urls` 通过业务系统文件 URL 创建核验任务
- `GET /api/agent/tasks/{task_id}` 查询任务状态和结果
- `GET /api/agent/tasks/{task_id}/report` 下载报告
- `POST /api/agent/tasks/{task_id}/review` 提交人工复核结果

URL 提交、任务完成回调和字段示例见 [现有系统接入说明](docs/integration.md)。

第一版为了方便本地演示，任务执行使用后台线程和本地文件存储。正式接入时可以替换为数据库、对象存储和消息队列。
