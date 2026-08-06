# 招投标智能核验智能体服务

这是一个可独立运行、后续可接入现有系统的多智能体服务原型。现有系统后续只需要通过 HTTP API 调用本服务，即可完成文件核验、结果查询和报告下载。

## 功能

- 创建智能核验任务并上传招标/投标/评标文件
- 总控调度智能体编排五个专项智能体
- 文档解析、合规审查、数据核验、异常分析、报告生成
- 返回结构化 JSON 问题清单
- 生成 Markdown 核验报告
- 支持人工复核结果回传接口

## 启动

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

打开接口文档：

```text
http://localhost:8000/docs
```

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

## 后续系统接入

现有系统可以调用：

- `POST /api/agent/tasks` 创建核验任务
- `POST /api/agent/tasks/from-urls` 通过业务系统文件 URL 创建核验任务
- `GET /api/agent/tasks/{task_id}` 查询任务状态和结果
- `GET /api/agent/tasks/{task_id}/report` 下载报告
- `POST /api/agent/tasks/{task_id}/review` 提交人工复核结果

URL 提交、任务完成回调和字段示例见 [现有系统接入说明](docs/integration.md)。

第一版为了方便本地演示，任务执行使用后台线程和本地文件存储。正式接入时可以替换为数据库、对象存储和消息队列。
