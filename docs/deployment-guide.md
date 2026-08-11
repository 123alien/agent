# 跨电脑部署与系统接入指南

本文面向第一次接手项目的开发、运维和业务系统团队。目标是在一台新电脑或服务器上完成：代码拉取、服务启动、Dify连接、模型替换、业务系统接入和验收。

## 1. 交付架构

```text
业务系统 / 浏览器
        |
        | HTTP + X-API-Key
        v
招投标智能核验服务（本仓库，端口8000）
        |-- 本地确定性解析、规则、OCR、视觉检测、报告生成
        |-- LangGraph流程编排与人工复核
        |-- Dify五个工作流（可选增强）
        `-- OpenAI兼容模型接口（可选兜底）
```

Dify和模型服务可以部署在同一台电脑，也可以部署在局域网或云服务器。智能核验服务只要求能够访问其HTTP地址。

## 2. 推荐部署方式：Docker

新电脑需要安装 Git、Docker Desktop（Windows）或 Docker Engine + Compose（Linux）。

```bash
git clone https://github.com/123alien/agent.git
cd agent
cp .env.example .env
```

Windows PowerShell复制配置：

```powershell
Copy-Item .env.example .env
```

编辑 `.env` 后启动：

```bash
docker compose up -d --build
docker compose ps
```

验证：

```text
http://服务器IP:8000/health
http://服务器IP:8000/docs
http://服务器IP:8000/
```

升级：

```bash
git pull
docker compose up -d --build
```

运行数据保存在宿主机 `data/`，重建容器不会删除任务和报告。

## 3. Windows原生部署

要求 Python 3.12（64位）。在项目目录执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup_windows.ps1
notepad .env
.\scripts\start_windows.ps1
```

配置检查：

```powershell
.\.venv\Scripts\python.exe scripts\verify_config.py
```

## 4. 最小配置

服务完全使用本地规则时，可以不配置Dify和大模型：

```dotenv
DATA_DIR=./data
AGENT_API_TOKEN=请生成至少32位随机字符串
CORS_ALLOWED_ORIGINS=http://业务系统地址
```

生产环境必须设置 `AGENT_API_TOKEN`。业务系统调用 `/api/agent/*` 时添加：

```http
X-API-Key: 与AGENT_API_TOKEN相同的值
```

## 5. Dify连接配置

先在Dify中发布五个Workflow，再分别复制各Workflow的API Key。不同工作流不能共用同一个应用Key。

```dotenv
DIFY_BASE_URL=http://DIFY服务器IP/v1
DIFY_DOCUMENT_PARSER_API_KEY=app-文档解析Key
DIFY_COMPLIANCE_API_KEY=app-合规审查Key
DIFY_DATA_VALIDATOR_API_KEY=app-数据核验Key
DIFY_ANOMALY_ANALYZER_API_KEY=app-异常分析Key
DIFY_REPORT_GENERATOR_API_KEY=app-报告生成Key

COMPLIANCE_WORKFLOW_VERSION=2.2.0
DATA_VALIDATOR_WORKFLOW_VERSION=2.0.0
ANOMALY_ANALYZER_WORKFLOW_VERSION=2.0.0
REPORT_GENERATOR_WORKFLOW_VERSION=2.0.0
DIFY_TIMEOUT_SECONDS=120
DIFY_DOCUMENT_PARSER_TIMEOUT_SECONDS=45
```

报告生成工作流DSL位于仓库根目录 `招投标报告生成智能体-2.0.yml`。其他工作流的变量、Schema和提示词规范见：

- `docs/dify-document-parser-workflow.md`
- `docs/dify-compliance-batch-v2.md`
- `docs/agent-contract-v1.md`
- `dify/`目录中的提示词、Schema和分类代码

Dify换IP后只需要修改 `DIFY_BASE_URL`；重新发布某个工作流后，只替换对应的 `DIFY_*_API_KEY`，不需要修改Python代码。

## 6. 模型替换

### 6.1 替换Dify工作流模型

在Dify的“设置 → 模型供应商”中配置新模型，然后逐个打开五个Workflow，将LLM节点切换到目标模型，测试结构化输出后重新发布。后端API Key不变时无需改 `.env`。

建议参数：

- temperature：0.0—0.2
- 结构化输出：开启
- 输出必须符合Workflow中定义的JSON Schema
- 合规场景优先选择长上下文、中文法规理解稳定的模型

### 6.2 替换后端OpenAI兼容模型

后端兜底模型通过三个变量切换：

```dotenv
LLM_API_KEY=你的Key
LLM_BASE_URL=https://模型服务/v1
LLM_MODEL=模型名称
```

DeepSeek示例：

```dotenv
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

阿里云百炼兼容接口示例：

```dotenv
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
```

本机Ollama需要提供OpenAI兼容接口；Docker部署时不能使用容器自身的 `localhost`：

```dotenv
LLM_API_KEY=ollama
LLM_BASE_URL=http://host.docker.internal:11434/v1
LLM_MODEL=qwen2.5:14b
```

Linux Docker访问宿主机Ollama时，应使用宿主机局域网IP，或在Compose中配置host-gateway。

## 7. 业务系统接入

推荐使用文件URL方式，业务系统无需把大文件二次上传：

```http
POST http://智能核验服务器:8000/api/agent/tasks/from-urls
Content-Type: application/json
X-API-Key: ******
```

```json
{
  "project_id": "P20260001",
  "project_name": "某市信息化运行维护项目",
  "check_type": "full",
  "callback_url": "http://业务系统/api/intelligent-review/callback",
  "system_record": {
    "project_name": "某市信息化运行维护项目",
    "budget": "1200000"
  },
  "output_type": "综合智能核验报告",
  "template_type": "详细审查报告",
  "files": [
    {
      "url": "http://文件服务/files/evaluation.pdf",
      "filename": "评标报告.pdf",
      "file_type": "评标报告"
    }
  ]
}
```

业务系统保存返回的 `task_id`，随后使用：

- `GET /api/agent/tasks/{task_id}`：查询状态和结构化结果
- `POST /api/agent/tasks/{task_id}/review`：提交人工复核
- `GET /api/agent/tasks/{task_id}/report.docx`：下载Word
- `GET /api/agent/tasks/{task_id}/report.pdf`：下载PDF

远程文件和回调必须设置白名单：

```dotenv
REMOTE_FILE_ALLOWED_HOSTS=files.example.com,10.0.0.20
CALLBACK_ALLOWED_HOSTS=business.example.com,10.0.0.10
CALLBACK_MAX_ATTEMPTS=3
CALLBACK_RETRY_BASE_SECONDS=1
CALLBACK_SECRET=请替换为独立随机密钥
```

更完整的请求和人工复核格式见 `docs/integration.md`。

## 8. 网络与防火墙

至少确认以下方向可访问：

| 来源 | 目标 | 端口 | 用途 |
|---|---|---:|---|
| 业务系统 | 智能核验服务 | 8000/443 | 创建任务、查询、下载 |
| 智能核验服务 | Dify | 80/443 | 调用工作流 |
| 智能核验服务 | 文件服务器 | 80/443 | 下载业务文件 |
| 智能核验服务 | 业务系统 | 80/443 | 完成回调 |
| Dify/Ollama | 模型服务 | 按实际配置 | 模型推理 |

正式环境建议由Nginx/API网关提供HTTPS、IP白名单、限流和访问日志，不要把Dify管理端直接暴露到公网。

## 9. 上线验收

```bash
python scripts/verify_config.py
python -m unittest discover -s tests -v
python scripts/smoke_test.py
```

验收至少覆盖：PDF/DOCX解析、扫描PDF OCR、Dify五个工作流、人工复核恢复、Word/PDF下载、回调、模型不可用时本地降级。

## 10. 常见故障

- `401 Invalid or missing X-API-Key`：请求头未携带正确Token。
- Dify `401`：对应Workflow Key错误或应用未发布。
- Dify连接超时：检查 `DIFY_BASE_URL`、防火墙和Docker网络。
- Ollama `host.docker.internal`不可达：Linux改用宿主机IP或host-gateway。
- OCR首次运行较慢：首次会加载模型，建议上线前执行一次扫描PDF预热。
- 旧任务报告没有变化：报告是任务完成时生成的静态文件，需要重新创建任务。
