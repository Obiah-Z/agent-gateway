from __future__ import annotations

from agent_gateway.ai.news.models import NewsItem


def build_digest_prompt(
    items: list[NewsItem],
    *,
    lookback_hours: int,
    max_output_items: int,
    errors: list[str] | None = None,
) -> str:
    lines = [
        "请基于下面由程序预先采集并去重的候选来源，生成一份适合飞书推送的中文 AI Agent 简报。",
        "",
        f"时间范围：过去 {lookback_hours} 小时。",
        f"最多输出 {max_output_items} 条。",
        "",
        "硬性要求：",
        "- 只能使用候选来源中的信息，不要自行编造额外新闻、产品名、政策或数据。",
        "- 如果候选条目不足或证据弱，宁可少写；没有高质量条目时输出“今日未发现值得推送的高质量 AI Agent 动态。”。",
        "- 每条必须包含标题、来源平台、发布时间或发布日期、2 到 3 句中文摘要、为什么值得关注、原文链接。",
        "- 优先官方公告、官方博客、GitHub Release、arXiv；媒体报道只能作为补充。",
        "- 末尾补充“趋势观察”，用 2 到 4 条短句总结对 Agent Gateway、工具调用、记忆、调度或多通道系统的启发。",
        "",
        "候选来源：",
    ]
    if not items:
        lines.append("- 无候选条目。")
    for index, item in enumerate(items, start=1):
        lines.extend(
            [
                f"{index}. {item.title}",
                f"   - source_id: {item.source_id}",
                f"   - source_type: {item.source_type}",
                f"   - tags: {', '.join(item.tags) or '-'}",
                f"   - published_at: {item.published_at or 'unknown'}",
                f"   - url: {item.url}",
                f"   - summary: {_single_line(item.summary) or '-'}",
            ]
        )
    if errors:
        lines.extend(["", "采集警告："])
        for error in errors[:8]:
            lines.append(f"- {error}")
    return "\n".join(lines)


def _single_line(value: str) -> str:
    return " ".join(value.split())[:800]
