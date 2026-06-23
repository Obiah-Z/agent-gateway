from __future__ import annotations

from agent_gateway.ai.news.models import NewsItem


def build_digest_prompt(
    items: list[NewsItem],
    *,
    lookback_hours: int,
    max_output_items: int,
    errors: list[str] | None = None,
) -> str:
    """把普通新闻候选条目拼成给 Agent 的简报生成 prompt。"""

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


def build_github_skill_digest_prompt(
    items: list[NewsItem],
    *,
    lookback_hours: int,
    max_output_items: int,
    errors: list[str] | None = None,
) -> str:
    """把 GitHub Skill 候选仓库拼成给 Agent 的简报生成 prompt。"""

    lines = [
        "请基于下面由程序从 GitHub 采集并去重的候选仓库，生成一份适合飞书推送的中文“热门 Skill 发现”简报。",
        "",
        f"时间范围：重点关注最近 {lookback_hours} 小时内仍活跃的仓库。",
        f"最多输出 {max_output_items} 条。",
        "",
        "这里的 Skill 指可被 AI Agent、Codex、MCP、工作流自动化、工具调用或个人效率系统复用的能力模块、插件、模板、提示词包、自动化脚本或集成方案。",
        "",
        "硬性要求：",
        "- 只能使用候选仓库中的信息，不要自行编造 star、功能、作者或兼容平台。",
        "- 优先选择与 agent skill、MCP server、tool calling、workflow automation、prompt/tool pack、personal automation 明确相关的仓库。",
        "- 排除泛泛的框架、课程、纯示例、无明显复用价值或主题不清的仓库。",
        "- 每条必须包含仓库名、star/fork、主要语言或主题、为什么值得关注、可用于我这个 Gateway 的落地方向、原链接。",
        "- 最后给出“可尝试接入 Gateway 的优先级”：高 / 中 / 低，并说明理由。",
        "- 如果候选不足，输出“今日未发现值得推送的热门 Skill 仓库。”。",
        "",
        "候选仓库：",
    ]
    if not items:
        lines.append("- 无候选仓库。")
    for index, item in enumerate(items, start=1):
        metadata = item.metadata or {}
        lines.extend(
            [
                f"{index}. {item.title}",
                f"   - source_id: {item.source_id}",
                f"   - tags: {', '.join(item.tags) or '-'}",
                f"   - pushed_at: {item.published_at or metadata.get('pushed_at', 'unknown')}",
                f"   - stars: {metadata.get('stars', 'unknown')}",
                f"   - forks: {metadata.get('forks', 'unknown')}",
                f"   - language: {metadata.get('language', 'unknown') or 'unknown'}",
                f"   - topics: {', '.join(str(topic) for topic in metadata.get('topics', [])[:8]) or '-'}",
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
    """压成单行摘要，避免来源内容把 prompt 撑得过长。"""

    return " ".join(value.split())[:800]
