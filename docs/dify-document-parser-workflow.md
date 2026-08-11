# Dify 文档语义解析辅助 Workflow

## 定位

该 Workflow 仅处理本地工具已经提取出的文本，用于补充章节结构和关键字段。
它不负责打开文件、PDF 渲染、OCR 或表格物理结构提取。

## 节点

```text
用户输入(document_text)
-> LLM(语义解析)
-> 代码(JSON规范化)
-> 输出(result)
```

## LLM SYSTEM 提示词

```text
你是招投标文档语义解析助手。

你的任务是根据已经提取的文档文本，补充识别文档章节和关键字段。你不负责合规
判断，不得判断条款是否合法，不得引用或编造法律法规。

规则：
1. 只能提取文档中明确存在的信息，不得推测、补写或创造内容。
2. sections 按原文顺序输出；title 必须来自原文标题。
3. content 保留该章节的主要原文，不得改写事实。
4. key_fields 仅输出能够在原文中定位的字段。
5. raw_text 必须逐字引用包含字段值的原文。
6. source_location 无法确认页码时，可以填写章节标题或“文本位置待确认”。
7. confidence 范围为 0 到 1；证据不明确时必须降低置信度并标记人工复核。
8. 找不到的字段不要输出空对象，应写入 warnings。
9. 只输出合法 JSON，不得输出 Markdown、代码块或解释文字。

重点字段包括：
- project_name：项目名称；
- budget：项目预算；
- price_limit：最高投标限价；
- tenderer：招标人或采购人；
- deadline：投标或响应文件提交截止时间。

输出格式：
{
  "sections": [
    {
      "title": "原文章节标题",
      "level": 1,
      "content": "章节原文",
      "page": null,
      "line_start": null
    }
  ],
  "key_fields": {
    "project_name": {
      "value": "字段值",
      "raw_text": "包含字段值的原文",
      "source_location": "章节或位置",
      "confidence": 0.9,
      "requires_human_review": false
    }
  },
  "warnings": []
}
```

## LLM USER 提示词

```text
请解析以下文档文本：

【文档文本】
[插入 用户输入/document_text 变量]
```

## 代码节点

输入变量：`llm_text`，绑定 `语义解析/text`。

```python
import json
import re


def main(llm_text: str) -> dict:
    text = llm_text.strip()
    text = re.sub(r"^```(?:json)?\\s*", "", text, flags=re.I)
    text = re.sub(r"\\s*```$", "", text)
    data = json.loads(text)

    sections = []
    for item in data.get("sections", []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip()
        if not title:
            continue
        sections.append({
            "title": title[:200],
            "level": max(1, min(int(item.get("level", 1)), 6)),
            "content": str(item.get("content", "")).strip(),
            "page": item.get("page"),
            "line_start": item.get("line_start"),
        })

    key_fields = {}
    for name, item in data.get("key_fields", {}).items():
        if isinstance(item, str):
            item = {"value": item}
        if not isinstance(item, dict):
            continue
        value = str(item.get("value", "")).strip()
        if not value:
            continue
        confidence = float(item.get("confidence", 0.7))
        key_fields[str(name)] = {
            "value": value[:500],
            "raw_text": str(item.get("raw_text", "")).strip()[:1000],
            "source_location": str(item.get("source_location", "")).strip()[:200],
            "confidence": min(max(confidence, 0.0), 1.0),
            "requires_human_review": bool(
                item.get("requires_human_review", confidence < 0.75)
            ),
        }

    result = {
        "sections": sections,
        "key_fields": key_fields,
        "warnings": [
            str(item).strip()
            for item in data.get("warnings", [])
            if str(item).strip()
        ],
    }
    return {"result": json.dumps(result, ensure_ascii=False)}
```

代码节点输出变量：`result`，类型 `String`。

## 输出节点

输出变量名：`result`，绑定 `JSON规范化/result`。

## API 配置

发布后创建 API Key，并写入：

```text
DIFY_DOCUMENT_PARSER_API_KEY=app-xxxxxxxx
```
