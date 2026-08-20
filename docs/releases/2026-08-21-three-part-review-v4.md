# 2026-08-21 三部分评标核验规则升级说明

## 版本定位

- Git 分支：`main`
- 规则版本：`evaluation-review-2026.08.20-v4`
- 协议方向：五智能体 + LangGraph 编排 + Dify/RAG 法规检索 + 三态人工复核
- 测试基线：146 个自动化测试通过

本版本重点不是增加新的智能体，而是把三部分业务规则真正接入文档解析、结果复核、前端展示和报告交付链路，减少“资料明明存在却显示资料不足”的情况。

## 本次完成的优化

### 1. 三部分、41 条评审规则落地

规则统一分为：

1. 项目基本信息、评标结果与推荐、报告签章与附件；
2. 招标公告与发售情况、开标情况、评标委员会组成；
3. 资格审查、符合性审查、详细评审与评审结果汇总。

系统输出 `passed`、`confirmed_issue`、`human_review`、`insufficient_data`、`not_applicable` 和 `disabled`。其中 `passed` 必须有执行证据，`insufficient_data` 必须明确列出缺失资料，不能把未执行显示成通过。

### 2. 正式评标报告解析增强

- 增强跨页表格和横向专家评分表解析；
- 支持从专家横向列恢复逐项评分明细；
- 补充商务分、技术分、价格分和总分汇总字段；
- 加强项目编号、时间、开标报价、评审结果和候选人排序的跨文件比对；
- 在部分材料足够时执行可执行子项，不再因单个外部字段缺失让整条规则失效。

### 3. 规则执行与报告交付

- 任务结果保存 `three_part_rule_execution` 完整执行矩阵；
- 前端任务详情展示三部分规则状态、证据、计算口径和缺失输入；
- 新增独立三部分规则 Word 报告生成能力；
- 综合 Word/PDF 报告同步纳入规则执行结果；
- 增加 `scripts/generate_three_part_rule_report.py`，可从已有任务数据重建规则报告。

### 4. 完整参考和测试文件

- `docs/reference/evaluation_rules/评审规则8.12.xlsx`
- `docs/reference/evaluation_rules/评标报告智能核验场景用例流程V2.0.xlsx`
- `test_data/real_public_project_2026_changyuan/`：6 份官方公开项目资料；
- `test_data/official/招标文件_道路脱空检测项目_ZJWZ2025-025.pdf`

## 验证结果

自动化测试命令：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

当前结果：146 项通过。

使用长垣市公开资料包执行 `full` 端到端核验时，最终规则矩阵为：

| 指标 | 数量 |
|---|---:|
| 规则总数 | 41 |
| 已执行 | 35 |
| 通过 | 25 |
| 待人工复核 | 10 |
| 资料不足未执行 | 1 |
| 已停用合并 | 2 |
| 本次不适用 | 3 |

剩余 1 条“资料不足未执行”为专家回避关系核验，需要外部专家关系数据或声明材料，属于真实输入边界，不是基本信息解析失败。

## 别人拉取后的更新步骤

```powershell
git checkout main
git pull origin main

# 已有虚拟环境时更新依赖
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 验证代码与规则
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 启动后端
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开：

- 前端：`http://127.0.0.1:8000/`
- Swagger：`http://127.0.0.1:8000/docs`
- 规则目录：`GET /api/agent/review-rules`

本次没有数据库迁移脚本。`.env` 不随 Git 分发，接手人应从 `.env.example` 复制并填写自己的模型、Dify 与服务配置。Dify 工作流和知识库属于外部运行状态；代码更新不会自动覆盖对方机器上的 Dify 配置。

## 复现三部分规则报告

先在前端上传 `test_data/real_public_project_2026_changyuan/` 的 6 份资料并执行 `full` 核验，得到任务编号后运行：

```powershell
.\.venv\Scripts\python.exe scripts\generate_three_part_rule_report.py 任务编号
```

输出位于 `data/reports/`。运行任务、上传文件、报告和临时矩阵均属于本机数据，已从 Git 提交中排除。

## 已知边界

- 专家回避关系、供应商关联关系、IP/MAC/机器码等依赖业务平台外部数据；
- 未公开的专家逐项原始评分表、供应商响应文件不能由模型虚构；
- 系统定位是发现问题和线索、形成证据并进入人工复核，不直接替代法定评审结论。
