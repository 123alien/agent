# 新电脑快速接入清单

本文是交付给另一台电脑或业务系统开发人员的最短操作路径。详细说明见
[`deployment-guide.md`](deployment-guide.md)、[`integration.md`](integration.md) 和
[`agent-contract-v1.md`](agent-contract-v1.md)。

## 1. 拉取与启动

Windows PowerShell：

```powershell
git clone https://github.com/123alien/agent.git
cd agent
Copy-Item .env.example .env
.\scripts\setup_windows.ps1
.\scripts\start_windows.ps1
```

Docker：

```bash
git clone https://github.com/123alien/agent.git
cd agent
cp .env.example .env
docker compose up -d --build
```

验证：

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/docs
```

## 2. 必填配置

生产环境至少修改 `.env`：

```dotenv
AGENT_API_TOKEN=至少32位随机字符串
ALLOWED_ORIGINS=https://你的业务系统域名
DATA_DIR=./data
```

业务请求增加请求头：`X-API-Key: 与 AGENT_API_TOKEN 相同的值`。

## 3. Dify 配置

五个工作流均可独立替换；未配置时后端使用确定性能力继续运行：

```dotenv
DIFY_BASE_URL=http://Dify服务器地址/v1
DIFY_DOCUMENT_PARSER_API_KEY=app-xxx
DIFY_COMPLIANCE_API_KEY=app-xxx
DIFY_DATA_VALIDATOR_API_KEY=app-xxx
DIFY_ANOMALY_ANALYZER_API_KEY=app-xxx
DIFY_REPORT_GENERATOR_API_KEY=app-xxx
```

工作流重新发布后只替换对应 Key。Dify 中替换 DeepSeek、通义千问或其他模型，不需要修改后端接口。

## 4. 后端模型替换

后端使用 OpenAI 兼容接口：

```dotenv
LLM_API_KEY=你的模型Key
LLM_BASE_URL=https://模型服务/v1
LLM_MODEL=模型名称
```

Ollama 与 Docker 组合时通常使用：

```dotenv
LLM_API_KEY=ollama
LLM_BASE_URL=http://host.docker.internal:11434/v1
LLM_MODEL=qwen2.5:14b
```

## 5. 两种业务接入方式

推荐生产方式：调用 `POST /api/agent/tasks/from-urls`，由现有 LangGraph 总流程调度五个智能体、人工复核与回调。

独立服务方式：依次调用：

1. `POST /api/v1/agents/document-parser`
2. `POST /api/v1/agents/compliance-review`
3. `POST /api/v1/agents/data-verification`
4. `POST /api/v1/agents/anomaly-analysis`
5. `POST /api/v1/agents/report-generator`

仓库提供可直接运行的五接口串联客户端：

```powershell
.\.venv\Scripts\python.exe scripts\standalone_pipeline.py `
  test_data\enterprise_demo\00_采购文件_XX市信息化平台升级建设项目.pdf `
  test_data\enterprise_demo\A_华诚科技有限公司_投标响应文件.pdf `
  --base-url http://127.0.0.1:8000 `
  --api-key 你的AGENT_API_TOKEN `
  --project-id XXCG-2026-0811 `
  --project-name XX市信息化平台升级建设项目
```

需要调用 Dify 时增加 `--enable-dify`。外部关系数据通过
`--relationship-data relationship.json` 传入；没有时脚本自动传 `{}`。

## 6. 上线验收

```powershell
.\.venv\Scripts\python.exe scripts\verify_config.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe scripts\smoke_test.py
```

确认健康检查、Swagger、五接口、人工复核、Word/PDF下载和业务回调全部通过后再接生产数据。

## 7. 数据目录

运行数据位于 `DATA_DIR`，默认是 `./data`。上传文件、任务状态、报告和复核记录不会提交到 GitHub。迁移服务器时应单独备份该目录，并由业务系统保存 `task_id`。
