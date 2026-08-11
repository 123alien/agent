# 招投标智能核验项目交接说明

## 1. 项目背景

这是老师布置的“智能体构建”任务，面向已有的招投标业务系统。现有系统已经存在，本项目不负责重做完整业务前端，主要负责构建可以被现有系统调用的智能核验能力。

任务中需要建设五类专项智能体：

1. 文档解析智能体
2. 合规审查智能体
3. 数据核验智能体
4. 异常分析智能体
5. 报告生成智能体

每类智能体都需要体现任务拆解、工具调度、结果验证优化和人工核验。项目最终还要支持接入已有系统，而不是只做一个孤立演示页面。

原始需求文件保存在仓库根目录：`requirements_20260803.xlsx`。

## 2. 已确认的架构方向

多智能体不等于每个智能体使用一个不同的大模型。多个智能体可以共享 DeepSeek，主要通过不同提示词、工具、规则、知识库和输出结构形成专业分工。

推荐最终采用“FastAPI 接入层 + Dify 编排层”的混合架构：

```text
现有业务系统
      |
      | HTTP：创建任务、查询结果、人工复核
      v
FastAPI 智能体接入服务
  - 文件接收和远程文件下载
  - 任务状态与回调
  - 报告下载
  - 人工复核结果保存
      |
      | Dify Workflow API
      v
Dify 总控工作流
  - 文档解析
  - 合规审查
  - 数据核验
  - 异常分析
  - 报告生成
      |
      v
DeepSeek + 规则引擎 + RAG 知识库 + OCR/文档工具
```

Dify 应负责可视化工作流、提示词、模型、知识库、RAG 和节点执行日志。FastAPI 应继续负责和已有业务系统对接。

## 3. 当前实现状态

当前仓库已经实现可独立运行的 Python 多智能体服务，并已接入五个可分别配置的 Dify
Workflow。Dify 不可用时，专项智能体会按能力回退到本地规则或兼容模型调用。

核心代码：

- `app/agents/supervisor.py`：总控调度智能体
- `app/agents/document_parser.py`：文档解析智能体
- `app/agents/compliance_checker.py`：合规审查智能体
- `app/agents/data_validator.py`：数据核验智能体
- `app/agents/anomaly_analyzer.py`：异常分析智能体
- `app/agents/report_generator.py`：报告生成智能体
- `app/api/tasks.py`：业务系统接入 API
- `app/services/llm_client.py`：OpenAI 兼容的大模型调用客户端

当前 `SupervisorAgent` 已使用 LangGraph `StateGraph` 管理共享状态，并根据
`check_type` 动态路由合规审查、数据核验和异常分析节点，最后统一生成报告。
合规审查优先调用已发布的 Dify Workflow 和 DeepSeek，调用失败时回退到本地规则；
专项结果进入结果复核智能体，校验证据、人工复核标记并去重，不合格时最多退回
原专项智能体一次。其余部分目前主要是启发式规则和确定性程序，仍需继续增强。

高风险问题会通过 LangGraph `interrupt()` 暂停，任务进入 `waiting_review`。
图状态保存在 `data/langgraph_checkpoints.sqlite`；人工复核接口使用相同任务编号和
`Command(resume=...)` 恢复执行，因此服务重启后仍可继续生成最终报告。

## 4. 已有接口

服务默认运行在 `http://127.0.0.1:8000`，Swagger 文档地址为 `http://127.0.0.1:8000/docs`。

- `POST /api/agent/tasks`：上传文件并创建核验任务
- `POST /api/agent/tasks/from-urls`：通过文件 URL 创建任务
- `GET /api/agent/tasks/{task_id}`：查询状态和结构化结果
- `GET /api/agent/tasks/{task_id}/report`：下载 Markdown 报告
- `POST /api/agent/tasks/{task_id}/review`：回传人工复核结果
- `GET /health`：服务健康检查

URL 提交、任务回调和人工复核的字段示例见 `docs/integration.md`。

## 5. 模型规划

建议的模型能力组合：

- 主模型：`deepseek-v4-flash`，用于常规解析、审查和报告
- 复杂推理：`deepseek-v4-pro`，用于疑难条款和复杂异常复核
- Embedding：`text-embedding-v4`，用于法律法规和历史项目 RAG
- Reranker：`qwen3-rerank`，用于知识检索重排
- OCR/版面解析：PaddleOCR `PP-StructureV3`

第一阶段只接通 `deepseek-v4-flash` 即可，后续再加入 Embedding、Reranker 和 OCR。

仓库中的 `.env.example` 已包含 DeepSeek 和网络配置字段。真实 `.env` 被 `.gitignore` 排除，不会上传。

## 6. 已完成验证

以下验证已经通过：

- `python -m compileall app scripts`
- `python scripts/smoke_test.py`
- `python scripts/integration_smoke_test.py`
- 文件上传任务可以完成五个智能体编排
- URL 文件任务可以完成并生成报告
- DeepSeek 密钥曾使用无业务文档的一行 JSON 请求验证成功
- FastAPI 服务健康检查与 OpenAPI 路由验证成功

没有将 `samples/demo_tender.txt` 发送给外部 DeepSeek 服务，因为传输文档内容需要明确授权。因此，真实模型参与完整文档核验的端到端测试仍待执行。

## 7. 新电脑启动代码服务

```bash
git clone git@github.com:123alien/agent.git
cd agent
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

在 `.env` 中填写新的模型密钥。聊天中曾出现过一枚真实密钥，应视为已经暴露，不要继续使用或提交到 GitHub。

## 8. Dify 部署与接入下一步

另一台电脑已经具备 WSL 2 和 Docker Desktop，应从这里继续：

1. 在本项目目录之外克隆官方 Dify 仓库。
2. 进入 Dify 的 `docker` 目录，将 `.env.example` 复制为 `.env`。
3. 执行 `docker compose up -d`。
4. 打开 `http://localhost/install` 完成管理员初始化。
5. 在 Dify 模型供应商中配置新的 DeepSeek 密钥。
6. 创建“招投标智能核验”Workflow。
7. 建立文档解析、合规审查、数据核验、异常分析和报告生成节点。
8. 发布 Workflow 并获取 Dify 应用 API Key。
9. 在本项目中增加 `DIFY_BASE_URL`、`DIFY_COMPLIANCE_API_KEY` 和
   `DIFY_DOCUMENT_PARSER_API_KEY` 等配置。
10. 新增 Dify 客户端，让 `SupervisorAgent` 调用 Dify Workflow API。

建议先创建一个总工作流，不要一开始就拆成五个完全独立的 Dify 应用。需要单独测试和复用时，再把专项能力拆为子工作流或工具。

## 9. 推荐的 Dify 工作流节点

```text
开始
  -> 文件/文本输入
  -> 文档提取或 OCR 工具
  -> 参数提取：项目、主体、报价、评分、关键条款
  -> 知识检索：法规库、制度库、历史项目库
  -> 合规审查 LLM 节点
  -> 数据核验代码/规则节点
  -> 异常分析 LLM + 统计工具节点
  -> 结果校验与 JSON 规范化
  -> 报告生成节点
  -> 输出
```

所有专项节点最终应返回统一问题结构：风险等级、问题类型、来源文件、来源位置、问题描述、依据、修改建议和证据。

## 10. 仍需完成的工作

- 部署并初始化 Dify
- 建立 Dify Workflow 并连接 DeepSeek
- FastAPI 增加 Dify API 客户端
- 决定文件传给 Dify 的方式：Dify 文件上传或 FastAPI 解析后传文本
- 建设法律法规、内部制度和历史项目知识库
- 引入 Embedding 与 Reranker
- 对扫描 PDF 和表格接入 OCR/版面解析
- 将本地 JSON 任务存储替换为数据库
- 将 FastAPI BackgroundTasks 替换为可靠消息队列
- 增加身份认证、限流、审计日志和回调重试
- 使用人工标注样本评估准确率、误判率和漏判率
- 将当前后台任务执行升级为独立消息队列与 Worker
- 将本地 JSON 任务存储升级为生产数据库与对象存储
- 使用人工标注样本持续评估准确率、误判率和漏判率

## 11. 交接原则

当前项目是可运行、可联调的 MVP，不应描述为已经达到生产级准确率。下一阶段重点是 Dify 可视化编排、规则库/RAG 建设和人工复核闭环，而不是重新开发一套业务系统前端。

