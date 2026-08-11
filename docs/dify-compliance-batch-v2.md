# Dify 合规批量检索工作流 v2

目标：把 N 个候选条款的 N 次知识检索和 N 次单项 LLM 审查，改为最多 4 次知识检索和 1 次批量 LLM 审查。

## 节点结构

```text
用户输入(document_text)
  -> 问题提取（结构化 candidates）
  -> 候选分类（代码）
       ├─ 资格法规检索
       ├─ 技术法规检索
       ├─ 评分法规检索
       └─ 程序法规检索
  -> 批量合规审查（LLM）
  -> 输出(structured_output)
```

删除旧的“迭代 → 逐条法规检索 → 单项审查 → 结果汇总”链路。先保留旧节点但断开连线，v2 测试通过后再删除。

## 候选分类代码节点

- 输入变量：`candidates`，选择 `问题提取 / structured_output / candidates`
- 代码：使用 `dify/compliance_batch_classifier.py`
- 输出变量：
  - `qualification_candidates`: String
  - `technical_candidates`: String
  - `scoring_candidates`: String
  - `procedure_candidates`: String
  - `qualification_query`: String
  - `technical_query`: String
  - `scoring_query`: String
  - `procedure_query`: String
  - `candidate_count`: Number

## 四个知识检索节点

四个节点均选择“招投标法规知识库”，设置：

- 检索方式：混合检索
- Top K：5
- Score 阈值：开启，`0.35`
- Rerank：当前没有可用模型时关闭

查询文本分别绑定四个 `*_query`。当对应查询为空时，检索结果应视为空；如果当前 Dify 版本仍执行空查询，可在每个检索节点前增加条件分支判断字符串非空。

## 批量合规审查节点

- 模型：`deepseek-chat`
- Temperature：`0.1`
- 最大输出 Token：`6000`
- 系统提示词：`dify/compliance_batch_review_system.txt`
- 用户提示词：`dify/compliance_batch_review_user.txt`
- 开启结构化输出
- Schema：导入 `dify/compliance_batch_output.schema.json`

## 输出节点

- 输出名：`result`
- 绑定：`批量合规审查 / structured_output`

如果 API 返回 Object，后端可直接解析；不要在提示词中输出 Schema 本身。

## 验收

使用同一份回归测试标书连续运行两次：

1. 候选数量一致；
2. 明确地域、品牌、差别评分和不当权限不得漏掉；
3. 正常条款不得新增误判；
4. 知识检索节点最多执行4次；
5. `requires_human_review` 必须为 Boolean；
6. 发布后将后端 `COMPLIANCE_WORKFLOW_VERSION` 改为 `2.0.0`，使旧缓存自动失效。
