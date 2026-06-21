from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.runtime.domain.models import ProactiveTarget
from agent_gateway.runtime.execution.dispatcher import GatewayDispatcher
from agent_gateway.runtime.observability.alerts import AlertRule, AlertState, AlertStore
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.runtime.observability.metrics import MetricsStore


RuleEvaluator = Callable[[dict[str, Any] | None, RuntimeEventStore | None], tuple[float, bool, str, dict[str, Any]]]


class AlertsRuntime:
    def __init__(
        self,
        *,
        metrics_store: MetricsStore,
        alert_store: AlertStore,
        event_store: RuntimeEventStore | None = None,
        dispatcher: GatewayDispatcher | None = None,
        channels: ChannelManager | None = None,
        target: ProactiveTarget | None = None,
        interval_seconds: float = 60.0,
        rules: list[tuple[AlertRule, RuleEvaluator]] | None = None,
    ) -> None:
        self.metrics_store = metrics_store
        self.alert_store = alert_store
        self.event_store = event_store
        self.dispatcher = dispatcher
        self.channels = channels
        self.target = target
        self.interval_seconds = max(5.0, float(interval_seconds))
        self.rules = rules or self._default_rules()
        self.states = {rule.id: AlertState(rule_id=rule.id, threshold=rule.threshold) for rule, _ in self.rules}
        self._task: asyncio.Task[None] | None = None
        self._stopped = False

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopped = False
        self._task = asyncio.create_task(self._loop(), name="alerts-runtime")

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def evaluate_once(self) -> list[dict[str, Any]]:
        latest = self.metrics_store.latest()
        now = time.time()
        emitted: list[dict[str, Any]] = []
        for rule, evaluator in self.rules:
            state = self.states[rule.id]
            state.last_evaluated_at = now
            state.threshold = rule.threshold
            value, matched, message, metadata = evaluator(latest, self.event_store)
            state.current_value = value
            state.last_message = message
            state.metadata = metadata
            if matched:
                state.consecutive_hits += 1
                state.consecutive_misses = 0
                if state.active_since <= 0:
                    state.active_since = now
                if state.consecutive_hits >= rule.sustain_intervals:
                    if state.status != "active":
                        state.status = "active"
                        state.last_triggered_at = now
                        emitted.append(
                            self.alert_store.append(
                                rule=rule,
                                state=state,
                                event="triggered",
                                message=message,
                                value=value,
                                metadata=metadata,
                                timestamp=now,
                            )
                        )
                    elif now - state.last_triggered_at >= rule.cooldown_seconds:
                        state.last_triggered_at = now
                        emitted.append(
                            self.alert_store.append(
                                rule=rule,
                                state=state,
                                event="reminded",
                                message=message,
                                value=value,
                                metadata=metadata,
                                timestamp=now,
                            )
                        )
            else:
                state.consecutive_hits = 0
                state.consecutive_misses += 1
                if state.status == "active":
                    state.status = "recovered"
                    state.last_recovered_at = now
                    emitted.append(
                        self.alert_store.append(
                            rule=rule,
                            state=state,
                            event="recovered",
                            message=message or f"{rule.title} 已恢复",
                            value=value,
                            metadata=metadata,
                            timestamp=now,
                        )
                    )
                elif state.status == "recovered":
                    state.status = "inactive"
                state.active_since = 0.0
        return emitted

    def active_alerts(self) -> list[dict[str, Any]]:
        rows = []
        for rule, _ in self.rules:
            state = self.states[rule.id]
            if state.status == "active":
                rows.append(
                    {
                        "rule_id": rule.id,
                        "title": rule.title,
                        "severity": rule.severity,
                        "description": rule.description,
                        **state.to_dict(),
                    }
                )
        rows.sort(key=lambda row: (row["severity"], -(row.get("active_since") or 0.0)))
        return rows

    def recent_history(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self.alert_store.tail(limit=limit)

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                emitted = await asyncio.to_thread(self.evaluate_once)
                if emitted:
                    await self._deliver_notifications(emitted)
            except Exception:
                pass
            await asyncio.sleep(self.interval_seconds)

    async def _deliver_notifications(self, rows: list[dict[str, Any]]) -> None:
        if self.dispatcher is None or self.channels is None or self.target is None or not self.target.peer_id:
            return
        for row in rows:
            rule = row.get("rule", {})
            state_row = row.get("state", {})
            rule_id = str(rule.get("id", "")).strip()
            state = self.states.get(rule_id)
            try:
                await self.dispatcher.deliver_text(
                    self.channels,
                    self.target,
                    self._render_alert_message(row),
                    metadata={
                        "kind": "alert",
                        "alert_rule_id": rule_id,
                        "alert_event": row.get("event", ""),
                    },
                )
                if state is not None:
                    state.last_notified_at = time.time()
                    state.last_notification_error = ""
            except Exception as exc:
                if state is not None:
                    state.last_notification_error = str(exc)

    def _render_alert_message(self, row: dict[str, Any]) -> str:
        rule = row.get("rule", {})
        state = row.get("state", {})
        event = str(row.get("event", "") or "")
        value = row.get("value", 0)
        title = str(rule.get("title", "告警"))
        severity = str(rule.get("severity", "warning"))
        threshold = rule.get("threshold")
        duration = self._duration_label(state.get("active_since"))
        suggestion = self._suggestion_for_rule(str(rule.get("id", "")))
        headline = {
            "triggered": "告警触发",
            "reminded": "告警持续",
            "recovered": "告警恢复",
        }.get(event, "告警通知")
        lines = [
            f"## {headline}：{title}",
            "",
            f"- 级别：{severity}",
            f"- 当前值：{value}",
            f"- 阈值：{threshold}",
        ]
        if event != "recovered":
            lines.append(f"- 持续时间：{duration}")
        if state.get("last_message"):
            lines.append(f"- 说明：{state['last_message']}")
        lines.extend(
            [
                f"- 建议动作：{suggestion}",
                "",
                "技术线索：",
                "```json",
                _safe_json(
                    {
                        "rule_id": rule.get("id"),
                        "event": event,
                        "active_since": state.get("active_since_time"),
                        "last_triggered": state.get("last_triggered_time"),
                        "last_recovered": state.get("last_recovered_time"),
                        "metadata": row.get("metadata", {}),
                    }
                ),
                "```",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _duration_label(active_since: Any) -> str:
        if not active_since:
            return "--"
        seconds = max(0, int(time.time() - float(active_since)))
        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours}小时{minutes}分"
        if minutes > 0:
            return f"{minutes}分{sec}秒"
        return f"{sec}秒"

    @staticmethod
    def _suggestion_for_rule(rule_id: str) -> str:
        suggestions = {
            "delivery_pending_backlog": "检查投递队列、目标通道和下游可达性，必要时手动 flush。",
            "delivery_failed_persisting": "检查失败消息的错误详情，确认目标 ID、凭证或权限是否失效。",
            "cron_failures_present": "检查对应 Cron 提示词、投递目标和最近错误事件。",
            "profiles_unavailable": "检查模型 API key、base_url、限流与 profile 冷却状态。",
            "feishu_signature_rejected_spike": "检查飞书验签配置、回调地址和是否有异常探测请求。",
            "lane_backlog_high": "检查是否有慢模型、慢工具或长时间阻塞的会话车道。",
        }
        return suggestions.get(rule_id, "查看最近事件、错误和指标趋势，定位上游异常点。")

    @staticmethod
    def _default_rules() -> list[tuple[AlertRule, RuleEvaluator]]:
        return [
            (
                AlertRule(
                    id="delivery_pending_backlog",
                    title="投递队列堆积",
                    severity="warning",
                    description="待投递消息持续超过阈值。",
                    threshold=20,
                    sustain_intervals=3,
                    cooldown_seconds=900,
                ),
                lambda latest, events: _metric_rule(
                    latest,
                    value_path=("delivery", "pending"),
                    threshold=20,
                    title="投递队列堆积",
                    comparison="ge",
                ),
            ),
            (
                AlertRule(
                    id="delivery_failed_persisting",
                    title="投递失败持续存在",
                    severity="critical",
                    description="失败投递持续不为 0。",
                    threshold=1,
                    sustain_intervals=2,
                    cooldown_seconds=900,
                ),
                lambda latest, events: _metric_rule(
                    latest,
                    value_path=("delivery", "failed"),
                    threshold=1,
                    title="投递失败持续存在",
                    comparison="ge",
                ),
            ),
            (
                AlertRule(
                    id="cron_failures_present",
                    title="Cron 存在连续失败",
                    severity="warning",
                    description="有 Cron 任务进入错误态。",
                    threshold=1,
                    sustain_intervals=2,
                    cooldown_seconds=1200,
                ),
                lambda latest, events: _metric_rule(
                    latest,
                    value_path=("cron", "errored"),
                    threshold=1,
                    title="Cron 存在连续失败",
                    comparison="ge",
                ),
            ),
            (
                AlertRule(
                    id="profiles_unavailable",
                    title="没有可用模型 Profile",
                    severity="critical",
                    description="所有模型 Profile 当前均不可用。",
                    threshold=0,
                    sustain_intervals=2,
                    cooldown_seconds=600,
                ),
                lambda latest, events: _metric_rule(
                    latest,
                    value_path=("profiles", "available"),
                    threshold=0,
                    title="没有可用模型 Profile",
                    comparison="le",
                ),
            ),
            (
                AlertRule(
                    id="feishu_signature_rejected_spike",
                    title="飞书验签拒绝过多",
                    severity="warning",
                    description="短时间内出现过多飞书验签拒绝。",
                    threshold=3,
                    sustain_intervals=1,
                    cooldown_seconds=1800,
                ),
                _feishu_signature_rule,
            ),
            (
                AlertRule(
                    id="lane_backlog_high",
                    title="并发车道排队过高",
                    severity="warning",
                    description="并发车道排队持续偏高，可能影响响应延迟。",
                    threshold=10,
                    sustain_intervals=2,
                    cooldown_seconds=900,
                ),
                lambda latest, events: _metric_rule(
                    latest,
                    value_path=("lanes", "queued"),
                    threshold=10,
                    title="并发车道排队过高",
                    comparison="ge",
                ),
            ),
        ]


def _metric_rule(
    latest: dict[str, Any] | None,
    *,
    value_path: tuple[str, str],
    threshold: float,
    title: str,
    comparison: str,
) -> tuple[float, bool, str, dict[str, Any]]:
    if latest is None:
        return 0.0, False, "暂无指标快照", {}
    section = latest.get(value_path[0], {})
    raw_value = section.get(value_path[1], 0) if isinstance(section, dict) else 0
    value = float(raw_value or 0.0)
    matched = value >= threshold if comparison == "ge" else value <= threshold
    operator = ">=" if comparison == "ge" else "<="
    return (
        value,
        matched,
        f"{title}：当前值 {value:g}，阈值 {operator} {threshold:g}",
        {"section": value_path[0], "field": value_path[1]},
    )


def _feishu_signature_rule(
    latest: dict[str, Any] | None,
    event_store: RuntimeEventStore | None,
) -> tuple[float, bool, str, dict[str, Any]]:
    if event_store is None:
        return 0.0, False, "未配置事件存储", {}
    rows = event_store.tail(limit=200)
    recent = []
    now = time.time()
    for row in rows:
        timestamp = float(row.get("timestamp", 0.0) or 0.0)
        if now - timestamp > 300:
            continue
        if row.get("type") != "feishu.event.rejected":
            continue
        error = str(row.get("error", "") or row.get("message", "")).lower()
        if "signature" not in error:
            continue
        recent.append(row)
    value = float(len(recent))
    return (
        value,
        value >= 3,
        f"最近 5 分钟飞书验签拒绝 {int(value)} 次",
        {"sample_count": len(recent)},
    )


def _safe_json(value: dict[str, Any]) -> str:
    try:
        return __import__("json").dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception:
        return "{}"
