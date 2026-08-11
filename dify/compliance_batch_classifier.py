import json


GROUPS = {
    "qualification": {
        "title": "供应商资格与公平竞争",
        "keywords": [
            "资格", "注册", "成立", "资本", "业绩", "人员", "证书", "奖项",
            "本地", "本市", "本省", "外省", "联合体", "所有制", "行业协会",
        ],
    },
    "technical": {
        "title": "品牌、产品与技术参数",
        "keywords": [
            "品牌", "产品", "产地", "供应商", "专利", "技术路线", "型号",
            "参数", "兼容", "软件", "硬件", "系统", "工具",
        ],
    },
    "scoring": {
        "title": "评分标准与评标方法",
        "keywords": [
            "评分", "得分", "加分", "满分", "评审", "评标", "分值",
            "优良", "一般", "主观", "不得分",
        ],
    },
    "procedure": {
        "title": "招标程序、合同与不当权限",
        "keywords": [
            "无效", "废标", "否决", "中标", "候选人", "调整", "解释权",
            "无需说明", "招标人有权", "采购人有权", "委员会", "合同", "期限",
        ],
    },
}


def _parse_candidates(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return []
    if isinstance(value, dict):
        value = value.get("candidates", [])
    if not isinstance(value, list):
        return []
    result = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        evidence = str(item.get("evidence", "")).strip()
        if not evidence or evidence in seen:
            continue
        seen.add(evidence)
        result.append(
            {
                "evidence": evidence,
                "issue_type_hint": str(item.get("issue_type_hint", "")).strip(),
                "search_query": str(item.get("search_query", "")).strip(),
            }
        )
    return result[:20]


def _select_group(item):
    text = " ".join(
        [item["evidence"], item["issue_type_hint"], item["search_query"]]
    )
    if any(keyword in text for keyword in ["评分", "得分", "加分", "满分", "不得分", "分值"]):
        return "scoring"
    if any(keyword in text for keyword in ["品牌", "产地", "型号", "专利", "技术路线"]):
        return "technical"
    if any(keyword in text for keyword in ["中标候选人", "评标结果", "解释权", "无需说明", "招标人有权", "采购人有权"]):
        return "procedure"
    scores = {
        name: sum(1 for keyword in config["keywords"] if keyword in text)
        for name, config in GROUPS.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "procedure"


def _query(name, items):
    if not items:
        return ""
    topics = []
    for item in items:
        topic = item["search_query"] or item["issue_type_hint"]
        if topic and topic not in topics:
            topics.append(topic)
    prefix = "招标采购合规审查：" + GROUPS[name]["title"]
    return (prefix + "；" + "；".join(topics))[:800]


def main(candidates):
    parsed = _parse_candidates(candidates)
    grouped = {name: [] for name in GROUPS}
    for item in parsed:
        grouped[_select_group(item)].append(item)

    return {
        "qualification_candidates": json.dumps(
            grouped["qualification"], ensure_ascii=False
        ),
        "technical_candidates": json.dumps(
            grouped["technical"], ensure_ascii=False
        ),
        "scoring_candidates": json.dumps(grouped["scoring"], ensure_ascii=False),
        "procedure_candidates": json.dumps(
            grouped["procedure"], ensure_ascii=False
        ),
        "qualification_query": _query("qualification", grouped["qualification"]),
        "technical_query": _query("technical", grouped["technical"]),
        "scoring_query": _query("scoring", grouped["scoring"]),
        "procedure_query": _query("procedure", grouped["procedure"]),
        "candidate_count": len(parsed),
    }
