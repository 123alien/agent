import re


def unique_keep_order(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def find_lines(text: str, keywords: list[str], limit: int = 8) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    matches: list[str] = []
    for line in lines:
        if any(keyword in line for keyword in keywords):
            matches.append(line[:240])
        if len(matches) >= limit:
            break
    return unique_keep_order(matches)


def money_values(text: str, limit: int = 10) -> list[str]:
    patterns = [
        r"\d+(?:\.\d+)?\s*万元",
        r"\d+(?:\.\d+)?\s*元",
        r"人民币\s*\d+(?:\.\d+)?",
    ]
    values: list[str] = []
    for pattern in patterns:
        values.extend(re.findall(pattern, text))
    return unique_keep_order(values)[:limit]


def guess_project_name(text: str, fallback: str = "") -> str:
    patterns = [
        r"项目名称[:：]\s*([^\n\r]+)",
        r"招标项目名称[:：]\s*([^\n\r]+)",
        r"工程名称[:：]\s*([^\n\r]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()[:80]
    return fallback

