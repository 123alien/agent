# 现有系统接入说明

智能体服务作为独立 HTTP 服务运行。业务系统提交核验任务后保存 `task_id`，可以轮询任务状态，也可以提供 `callback_url` 接收完成通知。

## 推荐调用流程

1. 业务系统生成招标文件、投标文件或评标报告的可访问 URL。
2. 调用 `POST /api/agent/tasks/from-urls` 创建任务。
3. 接口立即返回任务编号和 `pending` 状态。
4. 智能体服务在后台依次执行文档解析、合规审查、数据核验、异常分析和报告生成。
5. 业务系统通过回调或 `GET /api/agent/tasks/{task_id}` 获取最终结果。
6. 用户人工确认后，调用 `POST /api/agent/tasks/{task_id}/review` 回传复核结果。

## 通过文件 URL 创建任务

请求：

```http
POST /api/agent/tasks/from-urls
Content-Type: application/json
```

```json
{
  "project_id": "P20260806001",
  "project_name": "某设备采购项目",
  "check_type": "full",
  "callback_url": "http://business-system/api/ai-review/callback",
  "files": [
    {
      "url": "http://file-system/files/tender.docx",
      "filename": "招标文件.docx",
      "file_type": "招标文件"
    },
    {
      "url": "http://file-system/files/bid-a.pdf",
      "filename": "A公司投标文件.pdf",
      "file_type": "投标文件"
    }
  ]
}
```

响应中的关键字段：

```json
{
  "task_id": "T123456789abc",
  "status": "pending",
  "callback_status": "pending",
  "result": null
}
```

`status` 可能为 `pending`、`running`、`completed` 或 `failed`。任务完成后，`result.issues` 是结构化问题清单，`result.report_url` 是报告下载地址。

## 完成回调

填写 `callback_url` 后，服务会在任务完成或失败时向该地址发送 `POST` 请求，请求体为完整任务对象。业务系统应返回任意 `2xx` 状态码，并根据 `task_id` 做幂等处理。

回调失败不会改变核验任务状态。可查询 `callback_status` 和 `callback_error` 定位问题。本版本只尝试回调一次，正式部署时建议通过消息队列增加重试机制。

## 人工复核回传

```http
POST /api/agent/tasks/T123456789abc/review
Content-Type: application/json
```

```json
{
  "reviewer": "张老师",
  "items": [
    {
      "issue_id": "issue-1",
      "decision": "正确",
      "comment": "确认属于限制性条款"
    }
  ]
}
```

`decision` 可取 `正确`、`误判`、`漏判`、`需修改`。

## 部署配置

- `REMOTE_FILE_ALLOWED_HOSTS`：允许下载文件的主机名，多个值用英文逗号分隔。
- `REMOTE_FILE_MAX_BYTES`：单个远程文件最大字节数，默认 50 MB。
- `CALLBACK_ALLOWED_HOSTS`：允许回调的业务系统主机名。
- `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`：大模型访问配置。

正式部署建议设置两个主机白名单，并由网关增加 HTTPS、身份认证、限流和访问日志。
