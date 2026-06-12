from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from agent_gateway.channels.feishu_state import FeishuCardState
from agent_gateway.models import OutboundMessage


_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
_CODE_FENCE_RE = re.compile(r"^```([^\n`]*)\n([\s\S]*?)\n```$", re.MULTILINE)
_FENCED_BLOCK_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
_STRUCTURED_PLACEHOLDER_RE = re.compile(r"^\[\[STRUCTURED:(\d+)]]$")
_MARKDOWN_PATTERNS = (
    re.compile(r"(?m)^#{1,6}\s+\S"),
    re.compile(r"(?m)^\s*[-*+]\s+\S"),
    re.compile(r"(?m)^\s*\d+\.\s+\S"),
    re.compile(r"\[[^\]\n]+\]\((https?://[^\s)]+)\)"),
    re.compile(r"```[\s\S]+?```"),
    re.compile(r"`[^`\n]+`"),
    re.compile(r"\*\*[^*\n]+\*\*"),
    re.compile(r"(?m)^>\s+\S"),
)
_CARD_METADATA_KEYS = {
    "feishu_card_title",
    "feishu_card_summary",
    "feishu_card_link",
    "feishu_card_actions",
    "feishu_card_template",
}
_ALLOWED_CARD_TEMPLATES = {
    "blue",
    "wathet",
    "turquoise",
    "green",
    "yellow",
    "orange",
    "red",
    "carmine",
    "violet",
    "purple",
    "indigo",
    "grey",
}
_ALLOWED_BUTTON_TYPES = {
    "default",
    "primary",
    "danger",
    "text",
    "primary_text",
    "danger_text",
    "primary_filled",
    "danger_filled",
    "laser",
}


@dataclass(slots=True)
class FeishuSendPayload:
    payload: dict[str, Any]
    fallback_text: str
    card_state: FeishuCardState | None = None


class FeishuCardRenderer:
    def __init__(
        self,
        *,
        card_page_max_bytes: int = 6000,
        text_page_max_bytes: int = 12000,
        interactive_page_size: int = 4,
        enable_stateful_cards: bool = False,
    ) -> None:
        self.card_page_max_bytes = max(128, int(card_page_max_bytes))
        self.text_page_max_bytes = max(256, int(text_page_max_bytes))
        self.interactive_page_size = max(1, int(interactive_page_size))
        self.enable_stateful_cards = bool(enable_stateful_cards)

    def render(self, outbound: OutboundMessage, *, mode: str) -> list[FeishuSendPayload]:
        if mode == "text":
            return self._build_text_pages(outbound.text)
        if mode == "interactive":
            return self._build_card_pages(outbound)
        if self.should_use_card(outbound):
            return self._build_card_pages(outbound)
        return self._build_text_pages(outbound.text)

    def should_use_card(self, outbound: OutboundMessage) -> bool:
        if any(key in outbound.metadata for key in _CARD_METADATA_KEYS):
            return True
        return self.looks_like_markdown(outbound.text)

    def looks_like_markdown(self, text: str) -> bool:
        if not text or ("\n" not in text and len(text) < 12):
            return False
        return any(pattern.search(text) for pattern in _MARKDOWN_PATTERNS)

    def _build_text_pages(self, text: str) -> list[FeishuSendPayload]:
        chunks = self._split_text_chunks(text.strip() or "-", self.text_page_max_bytes)
        return [
            FeishuSendPayload(
                payload={
                    "msg_type": "text",
                    "content": json.dumps({"text": chunk}, ensure_ascii=False),
                },
                fallback_text=chunk,
            )
            for chunk in chunks
        ]

    def _build_card_pages(self, outbound: OutboundMessage) -> list[FeishuSendPayload]:
        metadata = outbound.metadata
        blocks = self._split_markdown_blocks(outbound.text.strip())
        title = self._normalize_text(metadata.get("feishu_card_title", ""))
        if not title and blocks:
            maybe_title = self._extract_heading_title(blocks[0])
            if maybe_title:
                title = maybe_title
                blocks = blocks[1:]
        summary = self._normalize_text(metadata.get("feishu_card_summary", ""))
        card_link = self._normalize_text(metadata.get("feishu_card_link", ""))
        template = self._normalize_template(metadata.get("feishu_card_template", "blue"))
        actions = self._parse_actions(metadata.get("feishu_card_actions", []))
        blocks, structured_blocks = self._extract_structured_blocks(blocks)
        blocks = self._normalize_render_blocks(blocks)
        if not blocks and summary:
            blocks = [summary]
            summary = ""
        if not blocks:
            blocks = ["-"]
        if self._should_use_interactive_stateful_card(blocks, structured_blocks):
            state = FeishuCardState(
                card_id=uuid.uuid4().hex[:12],
                owner_channel=outbound.channel,
                owner_account_id=str(outbound.metadata.get("account_id", "")),
                peer_id=outbound.to,
                message_id="",
                title=title,
                summary=summary,
                template=template,
                card_link=card_link,
                blocks=blocks,
                structured_blocks=structured_blocks,
                actions=actions,
                page_size=self.interactive_page_size,
                page_index=0,
                expanded=False,
            )
            payload, fallback_text = self.render_stateful_card(state)
            return [
                FeishuSendPayload(
                    payload={
                        "msg_type": "interactive",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                    fallback_text=fallback_text,
                    card_state=state,
                )
            ]
        pages = self._paginate_blocks(
            blocks,
            title=title,
            summary=summary,
            template=template,
            card_link=card_link,
        )
        if actions:
            pages = self._rebalance_last_page_for_actions(
                pages,
                title=title,
                summary=summary,
                template=template,
                card_link=card_link,
                actions=actions,
            )
        total_pages = len(pages)
        results: list[FeishuSendPayload] = []
        for index, page_blocks in enumerate(pages):
            elements: list[dict[str, Any]] = []
            if summary and index == 0:
                elements.append(self._markdown_element(summary))
                if page_blocks:
                    elements.append({"tag": "markdown", "content": "<hr>"})
            elements.extend(self._render_content_blocks(page_blocks, structured_blocks))
            if actions and index == total_pages - 1:
                if elements:
                    elements.append({"tag": "markdown", "content": "<hr>"})
                elements.extend(actions)
            page_title = self._page_title(title, index, total_pages)
            card: dict[str, Any] = {
                "schema": "2.0",
                "config": {
                    "enable_forward": True,
                    "update_multi": True,
                    "width_mode": "fill",
                },
                "body": {
                    "direction": "vertical",
                    "padding": "12px 12px 12px 12px",
                    "vertical_spacing": "8px",
                    "elements": elements,
                },
            }
            if summary:
                card["config"]["summary"] = {"content": summary}
            if page_title:
                card["header"] = {
                    "template": template,
                    "title": {
                        "tag": "plain_text",
                        "content": page_title,
                    },
                }
            if card_link:
                card["card_link"] = {"url": card_link}
            results.append(
                FeishuSendPayload(
                    payload={
                        "msg_type": "interactive",
                        "content": json.dumps(card, ensure_ascii=False),
                    },
                    fallback_text=self._build_fallback_text(
                        page_title=page_title,
                        summary=summary if index == 0 else "",
                        page_blocks=self._expand_fallback_blocks(page_blocks, structured_blocks),
                        actions=actions if index == total_pages - 1 else [],
                    ),
                )
            )
        return results

    def render_stateful_card(self, state: FeishuCardState) -> tuple[dict[str, Any], str]:
        visible_blocks = self._select_visible_blocks(state)
        total_pages = self._state_total_pages(state)
        header_title = state.title or "Reply"
        if total_pages > 1:
            header_title = f"{header_title} ({state.page_index + 1}/{total_pages})"
        elements: list[dict[str, Any]] = []
        if state.summary:
            elements.append(self._markdown_element(state.summary))
        if state.summary and visible_blocks:
            elements.append({"tag": "markdown", "content": "<hr>"})
        elements.extend(self._render_content_blocks(visible_blocks, state.structured_blocks))
        control_buttons = self._build_state_controls(state)
        if control_buttons:
            if elements:
                elements.append({"tag": "markdown", "content": "<hr>"})
            elements.extend(control_buttons)
        if state.actions:
            if elements:
                elements.append({"tag": "markdown", "content": "<hr>"})
            elements.extend(state.actions)
        card: dict[str, Any] = {
            "schema": "2.0",
            "config": {
                "enable_forward": True,
                "update_multi": True,
                "width_mode": "fill",
            },
            "body": {
                "direction": "vertical",
                "padding": "12px 12px 12px 12px",
                "vertical_spacing": "8px",
                "elements": elements,
            },
            "header": {
                "template": state.template or "blue",
                "title": {
                    "tag": "plain_text",
                    "content": header_title,
                },
            },
        }
        if state.summary:
            card["config"]["summary"] = {"content": state.summary}
        if state.card_link:
            card["card_link"] = {"url": state.card_link}
        fallback = self._build_fallback_text(
            page_title=header_title,
            summary=state.summary,
            page_blocks=self._expand_fallback_blocks(visible_blocks, state.structured_blocks),
            actions=state.actions,
        )
        return card, fallback

    def _page_title(self, title: str, index: int, total_pages: int) -> str:
        if total_pages <= 1:
            return title
        base = title or "Reply"
        return f"{base} ({index + 1}/{total_pages})"

    def _extract_heading_title(self, block: str) -> str:
        match = _HEADING_RE.match(block.strip())
        if not match:
            return ""
        return self._strip_inline_markdown(match.group(1))

    def _strip_inline_markdown(self, text: str) -> str:
        cleaned = re.sub(r"[`*_~]+", "", text)
        cleaned = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", cleaned)
        return cleaned.strip()

    def _normalize_text(self, value: object) -> str:
        return str(value or "").strip()

    def _normalize_template(self, value: object) -> str:
        template = str(value or "blue").strip().lower()
        if template in _ALLOWED_CARD_TEMPLATES:
            return template
        return "blue"

    def _should_use_interactive_stateful_card(
        self,
        blocks: list[str],
        structured_blocks: list[dict[str, Any]],
    ) -> bool:
        if not self.enable_stateful_cards:
            return False
        return len(blocks) > self.interactive_page_size or bool(structured_blocks)

    def _normalize_render_blocks(self, blocks: list[str]) -> list[str]:
        normalized: list[str] = []
        for block in blocks:
            if self._parse_structured_placeholder(block) is not None:
                normalized.append(block)
                continue
            normalized.extend(self._split_block_if_needed(block))
        return normalized

    def _parse_actions(self, value: object) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        actions: list[dict[str, Any]] = []
        for item in value[:3]:
            if not isinstance(item, dict):
                continue
            label = self._normalize_text(item.get("text") or item.get("label"))
            if not label:
                continue
            url = self._normalize_text(item.get("url"))
            multi_url = item.get("multi_url")
            callback_value = item.get("value")
            callback_enabled = bool(item.get("callback", False)) or callback_value is not None
            if not url and not isinstance(multi_url, dict):
                if not callback_enabled:
                    continue
            button_type = str(item.get("type", "default") or "default").strip().lower()
            if button_type not in _ALLOWED_BUTTON_TYPES:
                button_type = "default"
            action: dict[str, Any] = {
                "tag": "button",
                "text": {
                    "tag": "plain_text",
                    "content": label[:40],
                },
                "type": button_type,
                "size": "small",
            }
            behaviors: list[dict[str, Any]] = []
            if callback_enabled:
                behaviors.append(
                    {
                        "type": "callback",
                        "value": self._normalize_callback_value(callback_value, item, label),
                    }
                )
            if isinstance(multi_url, dict):
                normalized_multi = {
                    key: str(item_value).strip()
                    for key, item_value in multi_url.items()
                    if key in {"url", "pc_url", "ios_url", "android_url"}
                    and str(item_value).strip()
                }
                if normalized_multi:
                    behaviors.append(
                        {
                            "type": "open_url",
                            "default_url": normalized_multi.get("url", ""),
                            "pc_url": normalized_multi.get("pc_url", ""),
                            "ios_url": normalized_multi.get("ios_url", ""),
                            "android_url": normalized_multi.get("android_url", ""),
                        }
                    )
            elif url:
                behaviors.append(
                    {
                        "type": "open_url",
                        "default_url": url,
                    }
                )
            if behaviors:
                action["behaviors"] = [behavior for behavior in behaviors if any(behavior.values())]
            actions.append(action)
        return actions

    def _normalize_callback_value(
        self,
        callback_value: object,
        raw: dict[str, Any],
        label: str,
    ) -> dict[str, Any]:
        if isinstance(callback_value, dict):
            payload = dict(callback_value)
        elif callback_value is None:
            payload = {}
        else:
            payload = {"value": callback_value}
        payload.setdefault("action_label", label)
        action_name = self._normalize_text(raw.get("action") or raw.get("name"))
        if action_name:
            payload.setdefault("action", action_name)
        payload.setdefault("source", "gateway")
        return payload

    def _split_markdown_blocks(self, text: str) -> list[str]:
        if not text:
            return []
        blocks: list[str] = []
        cursor = 0
        for match in _FENCED_BLOCK_RE.finditer(text):
            if match.start() > cursor:
                blocks.extend(self._split_non_code_segment(text[cursor : match.start()]))
            blocks.append(match.group().strip())
            cursor = match.end()
        if cursor < len(text):
            blocks.extend(self._split_non_code_segment(text[cursor:]))
        return [block for block in blocks if block.strip()]

    def _split_non_code_segment(self, text: str) -> list[str]:
        segment = text.strip()
        if not segment:
            return []
        return [part.strip() for part in re.split(r"\n\s*\n+", segment) if part.strip()]

    def _paginate_blocks(
        self,
        blocks: list[str],
        *,
        title: str,
        summary: str,
        template: str,
        card_link: str,
    ) -> list[list[str]]:
        pages: list[list[str]] = []
        current: list[str] = []
        for block in blocks:
            for piece in self._split_block_if_needed(block):
                candidate = current + [piece]
                if current and self._estimate_card_payload_bytes(
                    title=title,
                    summary=summary if not pages else "",
                    blocks=candidate,
                    template=template,
                    card_link=card_link,
                    actions=[],
                ) > self.card_page_max_bytes:
                    pages.append(current)
                    current = []
                    candidate = [piece]
                current = candidate
        if current:
            pages.append(current)
        return pages or [["-"]]

    def _rebalance_last_page_for_actions(
        self,
        pages: list[list[str]],
        *,
        title: str,
        summary: str,
        template: str,
        card_link: str,
        actions: list[dict[str, Any]],
    ) -> list[list[str]]:
        if not pages:
            return [["-"]]
        while len(pages[-1]) > 1 and self._estimate_card_payload_bytes(
            title=title,
            summary=summary if len(pages) == 1 else "",
            blocks=pages[-1],
            template=template,
            card_link=card_link,
            actions=actions,
        ) > self.card_page_max_bytes:
            overflow = pages[-1].pop()
            if len(pages) == 1:
                pages.append([overflow])
                continue
            pages.insert(len(pages), [overflow])
        return pages

    def _split_block_if_needed(self, block: str) -> list[str]:
        if self._parse_structured_placeholder(block) is not None:
            return [block]
        block_size = len(block.encode("utf-8"))
        if block_size <= self.card_page_max_bytes:
            return [block]
        fenced = self._split_fenced_code_block(block)
        if fenced:
            return fenced
        return self._split_text_chunks(block, self.card_page_max_bytes)

    def _extract_structured_blocks(
        self,
        blocks: list[str],
    ) -> tuple[list[str], list[dict[str, Any]]]:
        render_blocks: list[str] = []
        structured_blocks: list[dict[str, Any]] = []
        for block in blocks:
            extracted = self._parse_structured_block(block)
            if not extracted:
                render_blocks.append(block)
                continue
            for item in extracted:
                structured_blocks.append(item)
                render_blocks.append(self._structured_placeholder(len(structured_blocks) - 1))
        return render_blocks, structured_blocks

    def _parse_structured_block(self, block: str) -> list[dict[str, Any]]:
        match = _CODE_FENCE_RE.match(block.strip())
        if match is None:
            return []
        language = match.group(1).strip().lower()
        if language not in {"", "json", "jsonc"}:
            return []
        body = match.group(2).strip()
        if not body:
            return []
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return []
        normalized = self._normalize_structured_payload(payload)
        return normalized

    def _normalize_structured_payload(self, payload: object) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            result: list[dict[str, Any]] = []
            for item in payload:
                block = self._normalize_structured_block(item)
                if block is not None:
                    result.append(block)
            return result
        if not isinstance(payload, dict):
            return []
        direct = self._normalize_structured_block(payload)
        if direct is not None:
            return [direct]
        result: list[dict[str, Any]] = []
        for kind in ("status", "kv", "table"):
            if kind not in payload:
                continue
            block = self._normalize_structured_block(payload.get(kind), default_type=kind)
            if block is not None:
                result.append(block)
        return result

    def _normalize_structured_block(
        self,
        payload: object,
        *,
        default_type: str = "",
    ) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        block_type = str(payload.get("type") or default_type).strip().lower()
        if block_type == "status":
            return self._normalize_status_block(payload)
        if block_type == "kv":
            return self._normalize_kv_block(payload)
        if block_type == "table":
            return self._normalize_table_block(payload)
        return None

    def _normalize_status_block(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        status = str(payload.get("status") or payload.get("level") or "info").strip().lower()
        if status not in {"success", "info", "warning", "error"}:
            status = "info"
        title = self._normalize_text(payload.get("title") or payload.get("summary"))
        message = self._normalize_text(payload.get("message") or payload.get("detail") or payload.get("text"))
        if not title and not message:
            return None
        return {
            "type": "status",
            "status": status,
            "title": title,
            "message": message,
        }

    def _normalize_kv_block(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        title = self._normalize_text(payload.get("title"))
        raw_items = payload.get("items", payload.get("metrics", payload.get("fields", [])))
        items: list[dict[str, str]] = []
        if isinstance(raw_items, dict):
            for label, value in raw_items.items():
                label_text = self._normalize_text(label)
                value_text = self._stringify_structured_value(value)
                if label_text and value_text:
                    items.append({"label": label_text, "value": value_text})
        elif isinstance(raw_items, list):
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                label_text = self._normalize_text(item.get("label") or item.get("key") or item.get("name"))
                value_text = self._stringify_structured_value(
                    item.get("value", item.get("text", item.get("content", "")))
                )
                if label_text and value_text:
                    items.append({"label": label_text, "value": value_text})
        if not items:
            return None
        return {
            "type": "kv",
            "title": title,
            "items": items,
        }

    def _normalize_table_block(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        raw_columns = payload.get("columns", payload.get("headers", payload.get("fields", [])))
        raw_rows = payload.get("rows", [])
        if not isinstance(raw_columns, list) or not isinstance(raw_rows, list):
            return None
        columns: list[dict[str, Any]] = []
        column_specs: list[dict[str, Any]] = []
        row_height_locked = bool(self._normalize_text(payload.get("row_height")))
        row_max_height_locked = bool(self._normalize_text(payload.get("row_max_height")))
        for index, item in enumerate(raw_columns):
            if isinstance(item, dict):
                explicit_width = bool(self._normalize_text(item.get("width")))
                name = (
                    self._normalize_text(item.get("name"))
                    or self._normalize_text(item.get("key"))
                    or self._normalize_text(item.get("field"))
                    or f"col_{index + 1}"
                )
                display_name = self._normalize_text(
                    item.get("display_name") or item.get("title") or item.get("label") or name
                )
                data_type = self._normalize_table_data_type(
                    item.get("data_type") or item.get("type") or "text"
                )
                column: dict[str, Any] = {
                    "name": name,
                    "display_name": display_name,
                    "data_type": data_type,
                    "width": self._normalize_table_width(item.get("width")),
                }
                if item.get("horizontal_align"):
                    column["horizontal_align"] = str(item["horizontal_align"])
                if item.get("vertical_align"):
                    column["vertical_align"] = str(item["vertical_align"])
                if data_type == "number" and isinstance(item.get("format"), dict):
                    column["format"] = item["format"]
                if data_type == "date" and item.get("date_format"):
                    column["date_format"] = str(item["date_format"])
                columns.append(column)
                column_specs.append(
                    {
                        "name": name,
                        "display_name": display_name,
                        "data_type": data_type,
                        "lookup_keys": self._table_lookup_keys(name, display_name, item),
                        "explicit_width": explicit_width,
                    }
                )
                continue
            display_name = self._normalize_text(item)
            if not display_name:
                continue
            name = f"col_{index + 1}"
            columns.append(
                {
                    "name": name,
                    "display_name": display_name,
                    "data_type": "text",
                    "width": "auto",
                }
            )
            column_specs.append(
                {
                    "name": name,
                    "display_name": display_name,
                    "data_type": "text",
                    "lookup_keys": [name, display_name],
                    "explicit_width": False,
                }
            )
        if not columns:
            return None
        rows: list[dict[str, Any]] = []
        for row in raw_rows:
            if isinstance(row, dict):
                source_row = self._unwrap_table_row_source(row)
                rendered_row = {
                    spec["name"]: self._table_cell_value(
                        self._find_table_row_value(source_row, spec["lookup_keys"]),
                        data_type=spec["data_type"],
                    )
                    for spec in column_specs
                }
                rows.append(rendered_row)
                continue
            if isinstance(row, list):
                rendered_row = {
                    spec["name"]: self._table_cell_value(
                        row[index] if index < len(row) else "",
                        data_type=spec["data_type"],
                    )
                    for index, spec in enumerate(column_specs)
                }
                rows.append(rendered_row)
        if not rows:
            return None
        column_stats = self._collect_table_column_stats(column_specs, rows)
        self._apply_table_column_widths(columns, column_specs, column_stats)
        page_size = payload.get("page_size", min(5, len(rows)))
        try:
            normalized_page_size = max(1, min(10, int(page_size)))
        except (TypeError, ValueError):
            normalized_page_size = min(5, len(rows))
        row_height = self._normalize_table_row_height(
            payload.get("row_height"),
            stats=column_stats,
            locked=row_height_locked,
        )
        row_max_height = self._normalize_table_row_max_height(
            payload.get("row_max_height"),
            stats=column_stats,
            locked=row_max_height_locked,
        )
        return {
            "type": "table",
            "title": self._normalize_text(payload.get("title")),
            "page_size": normalized_page_size,
            "row_height": row_height,
            "row_max_height": row_max_height,
            "freeze_first_column": bool(payload.get("freeze_first_column", len(columns) >= 4)),
            "columns": columns,
            "rows": rows,
        }

    def _split_fenced_code_block(self, block: str) -> list[str]:
        match = _CODE_FENCE_RE.match(block.strip())
        if match is None:
            return []
        language = match.group(1).strip()
        body = match.group(2)
        chunks = self._split_text_chunks(body, max(1000, self.card_page_max_bytes - 32))
        prefix = f"```{language}\n" if language else "```\n"
        return [f"{prefix}{chunk}\n```" for chunk in chunks]

    def _split_text_chunks(self, text: str, max_bytes: int) -> list[str]:
        lines = text.splitlines()
        if not lines:
            return [text[:max_bytes]]
        chunks: list[str] = []
        current_lines: list[str] = []
        for line in lines:
            candidate_lines = current_lines + [line]
            candidate = "\n".join(candidate_lines).strip("\n")
            if current_lines and len(candidate.encode("utf-8")) > max_bytes:
                chunks.append("\n".join(current_lines).strip("\n"))
                current_lines = []
            if len(line.encode("utf-8")) <= max_bytes:
                current_lines.append(line)
                continue
            chunks.extend(self._split_oversized_line(line, max_bytes))
        if current_lines:
            chunks.append("\n".join(current_lines).strip("\n"))
        return [chunk for chunk in chunks if chunk]

    def _split_oversized_line(self, line: str, max_bytes: int) -> list[str]:
        pieces: list[str] = []
        current = ""
        for char in line:
            candidate = current + char
            if current and len(candidate.encode("utf-8")) > max_bytes:
                pieces.append(current)
                current = char
                continue
            current = candidate
        if current:
            pieces.append(current)
        return pieces

    def _render_content_blocks(
        self,
        blocks: list[str],
        structured_blocks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        elements: list[dict[str, Any]] = []
        for block in blocks:
            structured_index = self._parse_structured_placeholder(block)
            if structured_index is None:
                elements.append(self._markdown_element(block))
                continue
            if structured_index >= len(structured_blocks):
                continue
            elements.extend(self._render_structured_block(structured_blocks[structured_index]))
        return elements

    def _render_structured_block(self, block: dict[str, Any]) -> list[dict[str, Any]]:
        block_type = str(block.get("type", "")).strip().lower()
        if block_type == "status":
            return self._render_status_block(block)
        if block_type == "kv":
            return self._render_kv_block(block)
        if block_type == "table":
            return self._render_table_block(block)
        return []

    def _render_status_block(self, block: dict[str, Any]) -> list[dict[str, Any]]:
        status = str(block.get("status", "info")).strip().lower()
        title = self._normalize_text(block.get("title")) or {
            "success": "Success",
            "warning": "Warning",
            "error": "Error",
            "info": "Info",
        }.get(status, "Info")
        message = self._normalize_text(block.get("message"))
        icon = {
            "success": "OK",
            "warning": "WARN",
            "error": "ERR",
            "info": "INFO",
        }.get(status, "INFO")
        content = f"**{title}**"
        if message:
            content = f"{content}\n{message}"
        return [
            {
                "tag": "column_set",
                "flex_mode": "none",
                "columns": [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "vertical_align": "top",
                        "elements": [
                            {
                                "tag": "plain_text",
                                "content": icon,
                            }
                        ],
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 4,
                        "vertical_align": "top",
                        "elements": [self._markdown_element(content)],
                    },
                ],
            }
        ]

    def _render_kv_block(self, block: dict[str, Any]) -> list[dict[str, Any]]:
        items = block.get("items", [])
        if not isinstance(items, list) or not items:
            return []
        elements: list[dict[str, Any]] = []
        title = self._normalize_text(block.get("title"))
        if title:
            elements.append(self._markdown_element(f"**{title}**"))
        for item in items:
            if not isinstance(item, dict):
                continue
            label = self._normalize_text(item.get("label"))
            value = self._normalize_text(item.get("value"))
            if not label or not value:
                continue
            elements.append(
                {
                    "tag": "column_set",
                    "flex_mode": "none",
                    "columns": [
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 2,
                            "vertical_align": "top",
                            "elements": [
                                self._markdown_element(f"<font color='grey'>{label}</font>")
                            ],
                        },
                        {
                            "tag": "column",
                            "width": "weighted",
                            "weight": 3,
                            "vertical_align": "top",
                            "elements": [self._markdown_element(value)],
                        },
                    ],
                }
            )
        return elements

    def _render_table_block(self, block: dict[str, Any]) -> list[dict[str, Any]]:
        columns = block.get("columns", [])
        rows = block.get("rows", [])
        if not isinstance(columns, list) or not isinstance(rows, list) or not columns or not rows:
            return []
        elements: list[dict[str, Any]] = []
        title = self._normalize_text(block.get("title"))
        if title:
            elements.append(self._markdown_element(f"**{title}**"))
        elements.append(
            {
                "tag": "table",
                "page_size": max(1, min(10, int(block.get("page_size", 5) or 5))),
                "row_height": self._normalize_table_row_height(block.get("row_height")),
                "freeze_first_column": bool(block.get("freeze_first_column", len(columns) >= 4)),
                "header_style": {
                    "text_align": "left",
                    "text_size": "normal",
                    "background_style": "none",
                    "text_color": "grey",
                    "bold": True,
                    "lines": 1,
                },
                "columns": columns,
                "rows": rows,
            }
        )
        if elements[-1]["row_height"] == "auto":
            elements[-1]["row_max_height"] = self._normalize_table_row_max_height(
                block.get("row_max_height")
            )
        return elements

    def _expand_fallback_blocks(
        self,
        blocks: list[str],
        structured_blocks: list[dict[str, Any]],
    ) -> list[str]:
        expanded: list[str] = []
        for block in blocks:
            structured_index = self._parse_structured_placeholder(block)
            if structured_index is None:
                expanded.append(block)
                continue
            if structured_index >= len(structured_blocks):
                continue
            fallback = self._structured_block_fallback(structured_blocks[structured_index])
            if fallback:
                expanded.append(fallback)
        return expanded

    def _structured_block_fallback(self, block: dict[str, Any]) -> str:
        block_type = str(block.get("type", "")).strip().lower()
        if block_type == "status":
            title = self._normalize_text(block.get("title"))
            message = self._normalize_text(block.get("message"))
            status = self._normalize_text(block.get("status")).upper() or "INFO"
            parts = [f"[{status}] {title}".strip()]
            if message:
                parts.append(message)
            return "\n".join(part for part in parts if part.strip())
        if block_type == "kv":
            title = self._normalize_text(block.get("title"))
            items = block.get("items", [])
            lines = [title] if title else []
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    label = self._normalize_text(item.get("label"))
                    value = self._normalize_text(item.get("value"))
                    if label and value:
                        lines.append(f"{label}: {value}")
            return "\n".join(line for line in lines if line.strip())
        if block_type == "table":
            title = self._normalize_text(block.get("title"))
            columns = block.get("columns", [])
            rows = block.get("rows", [])
            if not isinstance(columns, list) or not isinstance(rows, list):
                return title
            headers = [self._normalize_text(item.get("display_name") or item.get("name")) for item in columns if isinstance(item, dict)]
            names = [self._normalize_text(item.get("name")) for item in columns if isinstance(item, dict)]
            lines = [title] if title else []
            if headers:
                lines.append(" | ".join(headers))
            for row in rows:
                if not isinstance(row, dict):
                    continue
                values = [self._stringify_structured_value(row.get(name, "")) for name in names]
                lines.append(" | ".join(values))
            return "\n".join(line for line in lines if line.strip())
        return ""

    def _structured_placeholder(self, index: int) -> str:
        return f"[[STRUCTURED:{index}]]"

    def _parse_structured_placeholder(self, value: str) -> int | None:
        match = _STRUCTURED_PLACEHOLDER_RE.match(value.strip())
        if match is None:
            return None
        try:
            return int(match.group(1))
        except ValueError:
            return None

    def _stringify_structured_value(self, value: object) -> str:
        if isinstance(value, str):
            return value.strip()
        if value is None:
            return ""
        if isinstance(value, (int, float, bool)):
            return str(value)
        if isinstance(value, list):
            return ", ".join(self._stringify_structured_value(item) for item in value if self._stringify_structured_value(item))
        if isinstance(value, dict):
            if {"text", "content"} & set(value.keys()):
                return self._normalize_text(value.get("text") or value.get("content"))
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return str(value).strip()

    def _table_cell_value(self, value: object, *, data_type: str = "text") -> Any:
        normalized_type = self._normalize_table_data_type(data_type)
        if normalized_type == "number":
            return self._normalize_table_number_value(value)
        if normalized_type == "options":
            return self._normalize_table_options_value(value)
        if normalized_type == "persons":
            return self._normalize_table_persons_value(value)
        if normalized_type == "date":
            return self._normalize_table_date_value(value)
        if isinstance(value, (int, float, bool)):
            return value
        return self._stringify_structured_value(value)

    def _table_value_preview(self, value: object) -> str:
        if isinstance(value, list):
            return "\n".join(self._table_value_preview(item) for item in value)
        if isinstance(value, dict):
            if {"text", "content"} & set(value.keys()):
                return self._normalize_text(value.get("text") or value.get("content"))
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return self._stringify_structured_value(value)

    def _normalize_table_data_type(self, value: object) -> str:
        data_type = self._normalize_text(value).lower() or "text"
        aliases = {
            "md": "lark_md",
            "markdown_text": "lark_md",
            "rich_text": "lark_md",
        }
        data_type = aliases.get(data_type, data_type)
        if data_type not in {"text", "lark_md", "number", "markdown", "options", "persons", "date"}:
            return "text"
        return data_type

    def _normalize_table_width(self, value: object) -> str:
        width = self._normalize_text(value)
        if not width:
            return "auto"
        if width == "auto":
            return width
        if re.match(r"^\d+px$", width):
            return width
        if re.match(r"^\d+%$", width):
            return width
        return "auto"

    def _normalize_table_row_height(
        self,
        value: object,
        *,
        stats: list[dict[str, Any]] | None = None,
        locked: bool = False,
    ) -> str:
        height = self._normalize_text(value)
        if height in {"low", "middle", "high", "auto"}:
            return height
        if re.match(r"^\d+px$", height):
            return height
        if not locked and stats and self._table_has_long_cells(stats):
            return "auto"
        return "middle"

    def _normalize_table_row_max_height(
        self,
        value: object,
        *,
        stats: list[dict[str, Any]] | None = None,
        locked: bool = False,
    ) -> str:
        max_height = self._normalize_text(value)
        if re.match(r"^\d+px$", max_height):
            return max_height
        if not locked and stats and self._table_has_long_cells(stats):
            return "480px"
        return "160px"

    def _table_lookup_keys(
        self,
        name: str,
        display_name: str,
        raw: dict[str, Any],
    ) -> list[str]:
        keys: list[str] = [name, display_name]
        for extra in (raw.get("key"), raw.get("field"), raw.get("title"), raw.get("label")):
            text = self._normalize_text(extra)
            if text:
                keys.append(text)
        result: list[str] = []
        seen: set[str] = set()
        for item in keys:
            normalized = item.strip()
            if not normalized:
                continue
            lowered = normalized.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            result.append(normalized)
        return result

    def _unwrap_table_row_source(self, row: dict[str, Any]) -> dict[str, Any]:
        cells = row.get("cells")
        if isinstance(cells, dict):
            merged = dict(row)
            merged.update(cells)
            return merged
        return row

    def _find_table_row_value(self, row: dict[str, Any], keys: list[str]) -> object:
        for key in keys:
            if key in row:
                return row[key]
        lowered = {str(key).lower(): value for key, value in row.items()}
        for key in keys:
            if key.lower() in lowered:
                return lowered[key.lower()]
        return ""

    def _collect_table_column_stats(
        self,
        column_specs: list[dict[str, Any]],
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        stats: list[dict[str, Any]] = []
        for spec in column_specs:
            name = str(spec.get("name", ""))
            max_chars = len(str(spec.get("display_name", "")))
            has_multiline = False
            for row in rows:
                preview = self._table_value_preview(row.get(name, ""))
                max_chars = max(max_chars, len(preview))
                if "\n" in preview:
                    has_multiline = True
            stats.append(
                {
                    "name": name,
                    "data_type": str(spec.get("data_type", "text")),
                    "max_chars": max_chars,
                    "has_multiline": has_multiline,
                }
            )
        return stats

    def _apply_table_column_widths(
        self,
        columns: list[dict[str, Any]],
        column_specs: list[dict[str, Any]],
        column_stats: list[dict[str, Any]],
    ) -> None:
        for index, column in enumerate(columns):
            if index >= len(column_specs) or index >= len(column_stats):
                continue
            if bool(column_specs[index].get("explicit_width", False)):
                continue
            column["width"] = self._suggest_table_column_width(
                column_stats[index],
                total_columns=len(columns),
            )

    def _suggest_table_column_width(
        self,
        stat: dict[str, Any],
        *,
        total_columns: int,
    ) -> str:
        data_type = str(stat.get("data_type", "text"))
        max_chars = int(stat.get("max_chars", 0) or 0)
        has_multiline = bool(stat.get("has_multiline", False))
        if data_type in {"number", "date"}:
            return "120px"
        if data_type == "persons":
            return "160px"
        if data_type == "options":
            return "180px"
        if has_multiline or max_chars >= 120:
            return "420px" if total_columns >= 4 else "55%"
        if max_chars >= 72:
            return "320px" if total_columns >= 4 else "45%"
        if max_chars >= 36:
            return "240px" if total_columns >= 4 else "35%"
        if max_chars <= 10:
            return "120px"
        if max_chars <= 18:
            return "160px"
        return "220px"

    def _table_has_long_cells(self, stats: list[dict[str, Any]]) -> bool:
        for stat in stats:
            if bool(stat.get("has_multiline", False)):
                return True
            if int(stat.get("max_chars", 0) or 0) >= 48:
                return True
        return False

    def _normalize_table_number_value(self, value: object) -> Any:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return value
        text = self._normalize_text(value)
        if not text:
            return ""
        if re.match(r"^-?\d+$", text):
            try:
                return int(text)
            except ValueError:
                return text
        if re.match(r"^-?\d+\.\d+$", text):
            try:
                return float(text)
            except ValueError:
                return text
        return text

    def _normalize_table_options_value(self, value: object) -> Any:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            normalized: list[Any] = []
            for item in value:
                if isinstance(item, dict):
                    normalized.append(item)
                    continue
                text = self._stringify_structured_value(item)
                if text:
                    normalized.append({"text": text})
            return normalized
        if isinstance(value, dict):
            return [value]
        return self._stringify_structured_value(value)

    def _normalize_table_persons_value(self, value: object) -> Any:
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if isinstance(value, list):
            return [item for item in value if self._stringify_structured_value(item)]
        return self._stringify_structured_value(value)

    def _normalize_table_date_value(self, value: object) -> Any:
        if isinstance(value, (int, float)):
            return int(value)
        text = self._normalize_text(value)
        if re.match(r"^\d+$", text):
            try:
                return int(text)
            except ValueError:
                return text
        return text

    def _build_fallback_text(
        self,
        *,
        page_title: str,
        summary: str,
        page_blocks: list[str],
        actions: list[dict[str, Any]],
    ) -> str:
        parts: list[str] = []
        if page_title:
            parts.append(page_title)
        if summary:
            parts.append(summary)
        parts.extend(page_blocks)
        if actions:
            action_lines = []
            for action in actions:
                label = str(action.get("text", {}).get("content", "")).strip()
                url = ""
                behaviors = action.get("behaviors", [])
                if isinstance(behaviors, list):
                    for behavior in behaviors:
                        if not isinstance(behavior, dict) or behavior.get("type") != "open_url":
                            continue
                        url = str(
                            behavior.get("default_url")
                            or behavior.get("pc_url")
                            or behavior.get("url")
                            or ""
                        ).strip()
                        if url:
                            break
                if label and url:
                    action_lines.append(f"[{label}] {url}")
            if action_lines:
                parts.append("\n".join(action_lines))
        return "\n\n".join(part for part in parts if part.strip())

    def _state_total_pages(self, state: FeishuCardState) -> int:
        if state.expanded:
            return 1
        return max(1, (len(state.blocks) + state.page_size - 1) // state.page_size)

    def _select_visible_blocks(self, state: FeishuCardState) -> list[str]:
        if state.expanded:
            return state.blocks
        start = state.page_index * state.page_size
        end = start + state.page_size
        return state.blocks[start:end]

    def _build_state_controls(self, state: FeishuCardState) -> list[dict[str, Any]]:
        buttons: list[dict[str, Any]] = []
        total_pages = self._state_total_pages(state)
        if state.expanded:
            buttons.append(
                self._control_button("收起", "collapse", state.card_id, button_type="text")
            )
        else:
            if total_pages > 1 and state.page_index > 0:
                buttons.append(
                    self._control_button("上一页", "prev_page", state.card_id, button_type="text")
                )
            if len(state.blocks) > state.page_size:
                buttons.append(
                    self._control_button("展开全文", "expand", state.card_id, button_type="primary")
                )
            if total_pages > 1 and state.page_index + 1 < total_pages:
                buttons.append(
                    self._control_button("下一页", "next_page", state.card_id, button_type="text")
                )
        return buttons

    def _control_button(
        self,
        label: str,
        action: str,
        card_id: str,
        *,
        button_type: str,
    ) -> dict[str, Any]:
        return {
            "tag": "button",
            "text": {
                "tag": "plain_text",
                "content": label,
            },
            "type": button_type,
            "size": "small",
            "behaviors": [
                {
                    "type": "callback",
                    "value": {
                        "source": "gateway_card_control",
                        "action": action,
                        "card_id": card_id,
                    },
                }
            ],
        }

    def _estimate_card_payload_bytes(
        self,
        *,
        title: str,
        summary: str,
        blocks: list[str],
        template: str,
        card_link: str,
        actions: list[dict[str, Any]],
    ) -> int:
        elements: list[dict[str, Any]] = []
        if summary:
            elements.append(self._markdown_element(summary))
            if blocks:
                elements.append({"tag": "markdown", "content": "<hr>"})
        for block in blocks:
            elements.append(self._markdown_element(block))
        if actions:
            if elements:
                elements.append({"tag": "markdown", "content": "<hr>"})
            elements.extend(actions)
        card: dict[str, Any] = {
            "schema": "2.0",
            "config": {
                "enable_forward": True,
                "update_multi": True,
                "width_mode": "fill",
            },
            "body": {
                "direction": "vertical",
                "padding": "12px 12px 12px 12px",
                "vertical_spacing": "8px",
                "elements": elements,
            },
        }
        if summary:
            card["config"]["summary"] = {"content": summary}
        if title:
            card["header"] = {
                "template": template,
                "title": {
                    "tag": "plain_text",
                    "content": title,
                },
            }
        if card_link:
            card["card_link"] = {"url": card_link}
        content = json.dumps(card, ensure_ascii=False)
        return len(content.encode("utf-8"))

    def _markdown_element(self, content: str) -> dict[str, Any]:
        return {
            "tag": "markdown",
            "content": content,
            "text_align": "left",
            "text_size": "normal",
        }
