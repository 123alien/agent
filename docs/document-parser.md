# 文档解析智能体说明

## 目标

文档解析智能体负责把业务文档转换为统一、可追溯的结构化数据，并在解析
结果不完整、存在冲突或置信度较低时触发人工复核。结构化结果供合规审查、
数据核验、异常分析和报告生成智能体继续使用。

## 执行流程

```text
文件接收
-> 文件存在性和格式检查
-> 按类型选择解析器
-> 正文与表格提取
-> 文本清洗
-> 章节识别
-> 关键字段提取
-> 完整性和质量核验
-> 统一结构化输出
-> 低置信度结果进入人工复核
```

## 工具调度

解析器通过工具注册表进行选择，当前注册了：

- 通用文本解析工具；
- PDF 文本与表格解析工具；
- DOCX 段落与表格解析工具。

每份文档的 `selected_tool` 和 `tool_trace` 会记录实际选择、执行和降级过程。
访问 `GET /api/agent/document-tools` 可以查看当前可用工具及其支持的扩展名。
后续 OCR、版面分析或语义解析工具可以注册到同一调度入口。

## Dify 语义增强

基础解析后，满足以下任一条件时可以调用独立的 Dify 文档解析 Workflow：

- 未识别出有效章节；
- 招标文件缺少项目名称、预算或最高限价；
- 关键字段置信度低于阈值或已经标记人工复核。

在 `.env` 配置：

```text
DIFY_DOCUMENT_PARSER_API_KEY=app-xxxxxxxx
```

Workflow 输入变量为 `document_text`，结束节点输出变量为 `result`，格式为：

```json
{
  "sections": [
    {
      "title": "章节标题",
      "level": 1,
      "content": "章节正文",
      "page": null,
      "line_start": null
    }
  ],
  "key_fields": {
    "project_name": {
      "value": "项目名称",
      "raw_text": "项目名称：……",
      "source_location": "第1页",
      "confidence": 0.8,
      "requires_human_review": false
    }
  },
  "warnings": []
}
```

Dify 只补充缺失或置信度更高的数据，不覆盖可靠的本地解析结果。调用失败时主
流程继续执行，并在 `warnings` 和 `tool_trace` 中记录降级信息。

## 支持范围

| 类型 | 当前能力 |
| --- | --- |
| PDF | 提取逐页文本和表格，识别低文本密度扫描件 |
| DOCX | 提取段落和表格 |
| TXT/Markdown | 支持 UTF-8、UTF-8 BOM 和 GB18030 |
| CSV/JSON | 作为文本读取并进入统一流程 |
| DOC | 暂不直接支持，需转换为 DOCX |
| 扫描 PDF | 自动调用 RapidOCR；低置信度或失败时进入人工复核 |

## 结构化结果

每个 `ParsedDocument` 除兼容原有项目、主体、报价、资质和评分字段外，还包含：

- `page_count`：页数；
- `is_scanned`：是否疑似扫描件；
- `ocr_applied` / `ocr_confidence`：OCR 是否执行及平均置信度；
- `parse_status`：`success`、`warning` 或 `failed`；
- `sections`：章节标题、层级、正文、页码和起始行；
- `tables`：表格页码和二维行列数据；
- `extracted_fields`：字段值、原文、位置、置信度和复核标记；
- `quality_checks`：文本、扫描件、关键字段和乱码检查；
- `warnings`：需要关注的解析告警。

## 人工复核规则

以下情况会产生“文档解析质量”问题，并设置 `requires_human_review=true`：

- 未提取到有效文本；
- PDF 疑似扫描件且 OCR 未成功或平均置信度低于阈值；
- 项目名称、预算或最高限价等关键字段缺失；
- 文本中存在明显乱码。

LangGraph 总控会将高风险问题以及所有明确标记为需要人工复核的问题统一送入
人工复核节点。

## 验证

在项目虚拟环境中运行：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_document_parser -v
```

测试覆盖 TXT 编码读取、DOCX 表格提取、章节识别、关键字段提取和质量核验。
