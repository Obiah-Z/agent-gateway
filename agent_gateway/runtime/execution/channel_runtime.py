"""通道运行时。

负责把 CLI / Telegram / Feishu 等通道采集到的入站消息统一放进异步队列，再交给
dispatcher 顺序处理。CLI 的 completion_event 设计用于避免提示符抢跑。

这个模块是“协议通道”和“Agent 执行链路”之间的缓冲层：

1. 各通道可以用自己的阻塞式 receive/poll 逻辑采集消息。
2. ChannelRuntime 把这些消息统一桥接到 asyncio 主循环。
3. Dispatcher 再负责路由、Agent Loop 和出站投递。

因此这里重点处理的是并发边界、生命周期和故障兜底，而不是具体业务语义。
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any, Protocol

from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.gateways.messaging.telegram import TelegramChannel
from agent_gateway.runtime.domain.models import InboundMessage, ProactiveTarget
from agent_gateway.runtime.domain.router import build_preroute_lane_key
from agent_gateway.runtime.execution.delivery_runtime import DeliveryRuntime
from agent_gateway.runtime.execution.dispatcher import GatewayDispatcher
from agent_gateway.runtime.tasks.handlers import inbound_to_task_payload
from agent_gateway.runtime.tasks.queue import LocalTaskQueue


@dataclass(slots=True)
class PendingInbound:
    """从具体通道进入网关主队列的待处理消息。

    `completion_event` 只用于需要同步交互节奏的通道。目前主要是 CLI：终端输入线程
    必须等本轮回复投递完成后再继续读取下一次输入，否则提示符会抢在回复前出现。
    """

    message: InboundMessage  # 标准化后的入站消息。
    completion_event: threading.Event | None = None  # 通知采集线程“本条消息已处理完成”。
    enqueued_at: float = 0.0  # 入站队列入队时间，用于统计等待时长。

    @property
    def preroute_lane_key(self) -> str:
        """返回路由前 lane key，供后续入站 lane dispatcher 使用。"""

        return build_preroute_lane_key(self.message)


class InboundInterceptor(Protocol):
    """入站拦截器协议。

    例如飞书 onboarding 会在正式进入 Agent 执行前消费部分激活消息。
    """

    async def try_consume_activation(self, inbound: InboundMessage) -> bool:
        """尝试在进入普通 Agent 路由前消费入站消息。"""
        ...


class InboundBackpressureError(RuntimeError):
    """入站背压触发时抛出的拒绝异常。"""


class ChannelRuntime:
    """协调多通道采集、顺序消费和错误回退。

    设计上每个通道一个采集线程，所有线程把消息投递到同一个 asyncio 队列。
    队列消费者只有一个，因此同一进程内的入站处理是顺序的，避免会话记录和投递队列
    在多线程下被并发写入。
    """

    def __init__(
        self,
        dispatcher: GatewayDispatcher,
        channels: ChannelManager,
        delivery_runtime: DeliveryRuntime | None = None,
        inbound_interceptors: list[InboundInterceptor] | None = None,
        shutdown_timeout_seconds: float = 5.0,
        max_concurrent_lanes: int = 4,
        max_queue_size: int = 200,
        max_lane_queue_size: int = 20,
        long_task_notice_seconds: float = 15.0,
        task_queue: LocalTaskQueue | None = None,
        inbound_task_queue_enabled: bool = False,
        background_inbound_commands: tuple[str, ...] = ("/github-repo-analyzer", "/space-advisor"),
    ) -> None:
        self.dispatcher = dispatcher  # 负责路由、执行 Agent 回合并生成出站投递。
        self.channels = channels  # 当前启用的通道集合，可被控制面热替换。
        self.delivery_runtime = delivery_runtime  # 出站投递运行时，CLI 场景需要同步 flush。
        self.inbound_interceptors = list(inbound_interceptors or [])  # 入站前置拦截链。
        self.shutdown_timeout_seconds = shutdown_timeout_seconds  # stop/restart 等待线程和队列 drain 的上限。
        self.max_concurrent_lanes = max(1, int(max_concurrent_lanes))  # 全局入站 lane 并发上限。
        self.max_queue_size = max(1, int(max_queue_size))  # 全局入口队列最大积压。
        self.max_lane_queue_size = max(1, int(max_lane_queue_size))  # 单条 lane 最大积压。
        self.long_task_notice_seconds = max(0.0, float(long_task_notice_seconds))
        self.task_queue = task_queue  # 可承载明确长任务命令，也可承载可配置的普通入站任务。
        self.inbound_task_queue_enabled = inbound_task_queue_enabled
        self.background_inbound_commands = tuple(
            command.strip()
            for command in background_inbound_commands
            if command.strip().startswith("/")
        )
        self._lane_semaphore = asyncio.Semaphore(self.max_concurrent_lanes)
        self._queue: asyncio.Queue[PendingInbound | None] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None  # 主 asyncio 事件循环，供采集线程回投消息。
        self._stop_event = threading.Event()  # 跨线程停止信号。
        self._threads: list[threading.Thread] = []  # 每个通道对应一个后台采集线程。
        self._consumer_task: asyncio.Task[None] | None = None  # 统一入口队列分发任务。
        self._lane_queues: dict[str, asyncio.Queue[PendingInbound | None]] = {}
        self._lane_tasks: dict[str, asyncio.Task[None]] = {}
        self._lane_active: dict[str, int] = {}
        self._running = False  # 防止重复 start/stop。

    async def start(self) -> None:
        """启动消费队列和每个已配置通道的采集线程。

        通道 receive_batch 可能是阻塞/轮询式实现，因此不能直接跑在 asyncio 主循环中。
        这里为每个通道开一个 daemon 线程，再用 `run_coroutine_threadsafe` 把消息投回
        `_queue`。
        """

        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._loop = asyncio.get_running_loop()
        self._consumer_task = asyncio.create_task(self._consume())
        for account, channel in self.channels.iter_channels():
            # 线程名包含通道和账号，便于排查多账号飞书/Telegram 运行问题。
            thread = threading.Thread(
                target=self._worker_loop,
                args=(account.channel, account.account_id, channel),
                daemon=True,
                name=f"channel-{account.channel}-{account.account_id}",
            )
            thread.start()
            self._threads.append(thread)

    async def stop(self) -> None:
        """优雅停止所有通道线程并结束消费循环。

        停止顺序很重要：先阻止新采集并关闭通道，再等待旧线程退出，然后 drain 已经
        入队的消息，最后投递退出哨兵。这样控制面 reload 通道配置时，旧队列里的消息
        不会因为 consumer 提前退出而丢失。
        """

        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        self.channels.close_all()
        await asyncio.to_thread(self._join_threads, self.shutdown_timeout_seconds)
        await self._drain_queue(timeout=self.shutdown_timeout_seconds)
        await self._queue.put(None)
        if self._consumer_task is not None:
            await self._consumer_task
        self._threads = []

    async def wait_closed(self) -> None:
        """等待消费任务结束。"""

        if self._consumer_task is not None:
            await self._consumer_task

    async def ingest_external(self, inbound: InboundMessage) -> None:
        """接收外部已经解析好的入站消息。

        飞书 HTTP Webhook 和长连接都已经在各自模块里完成协议解析，因此这里直接把
        `InboundMessage` 放入统一队列，而不再经过通道线程。
        """

        if not self._running:
            raise RuntimeError("channel runtime is not running")
        await self._enqueue_pending(PendingInbound(message=inbound))

    async def restart(self, channels: ChannelManager) -> None:
        """在控制面 reload 通道配置后热重启通道运行时。

        控制面更新通道配置时不能只替换 `self.channels`，还要同步更新
        `DeliveryRuntime.channels`，否则入站用新通道，出站仍可能投递到旧通道实例。
        """

        was_running = self._running
        if was_running:
            await self.stop()
        self.channels = channels
        if self.delivery_runtime is not None:
            self.delivery_runtime.channels = channels
        self._threads = []
        if was_running:
            await self.start()

    def _join_threads(self, timeout: float) -> None:
        """等待旧通道采集线程退出，避免 reload 后仍向旧队列投递。"""

        deadline = time.monotonic() + max(0.0, timeout)
        for thread in list(self._threads):
            if not thread.is_alive():
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)

    async def _drain_queue(self, timeout: float) -> None:
        """等待全局队列和 lane 队列清空，超时后释放 CLI 等待者。"""

        try:
            await asyncio.wait_for(self._queue.join(), timeout=max(0.0, timeout))
            await asyncio.wait_for(self._drain_lane_queues(), timeout=max(0.0, timeout))
        except asyncio.TimeoutError:
            self._release_pending_completion_events()

    def _release_pending_completion_events(self) -> None:
        """兜底释放还停留在队列里的 CLI completion_event。"""

        for item in list(self._queue._queue):  # noqa: SLF001 - shutdown 兜底只能检查底层 deque。
            if isinstance(item, PendingInbound) and item.completion_event is not None:
                item.completion_event.set()
        for queue in self._lane_queues.values():
            for item in list(queue._queue):  # noqa: SLF001 - shutdown 兜底只能检查底层 deque。
                if isinstance(item, PendingInbound) and item.completion_event is not None:
                    item.completion_event.set()

    def _worker_loop(self, channel_name: str, account_id: str, channel: Any) -> None:
        """在独立线程里轮询具体通道，避免阻塞主事件循环。"""

        while not self._stop_event.is_set():
            try:
                batch = channel.receive_batch()
            except Exception:
                # 通道采集失败不能杀死整个进程。这里做短退避，等待下一轮轮询自恢复。
                time.sleep(1.0)
                continue

            if not batch:
                # 空轮询保持轻量退避，避免无消息时 CPU 空转。
                time.sleep(0.1)
                continue

            for inbound in batch:
                if self._loop is None or self._stop_event.is_set():
                    return
                # CLI 是同步终端体验：必须等回复真正刷出后再允许下一次 input()。
                completion_event = threading.Event() if channel_name == "cli" else None
                try:
                    # 从普通线程安全地投递到 asyncio 队列；`.result()` 确保投递完成。
                    asyncio.run_coroutine_threadsafe(
                        self._enqueue_pending(
                            PendingInbound(
                                message=inbound,
                                completion_event=completion_event,
                            )
                        ),
                        self._loop,
                    ).result()
                except InboundBackpressureError:
                    if completion_event is not None:
                        completion_event.set()
                    continue
                except Exception:
                    # 主循环已关闭或队列不可用时退出当前通道线程，避免后台异常刷屏。
                    return
                if completion_event is not None:
                    # CLI 输入线程在这里等待消费者 finally 中 set()，从而避免提示符抢跑。
                    while not self._stop_event.is_set():
                        if completion_event.wait(timeout=0.1):
                            break

    async def _consume(self) -> None:
        """消费统一入站队列，并按 lane key 分发到 lane worker。

        全局队列只负责统一接收和粗分发；每个 lane 内部由独立 worker 串行处理，避免
        同一 peer/session 的消息乱序，同时允许不同 lane 并发执行。
        """

        while True:
            pending = await self._queue.get()
            if pending is None:
                # None 是 stop() 放入的退出哨兵，不代表真实消息。
                self._queue.task_done()
                await self._drain_lane_queues()
                await self._stop_lane_workers()
                break

            lane_key = pending.preroute_lane_key
            lane_queue = self._ensure_lane_queue(lane_key)
            try:
                self._ensure_lane_capacity(lane_key, lane_queue)
            except InboundBackpressureError as exc:
                await self._reject_pending(pending, exc)
                self._queue.task_done()
                continue
            lane_queue.put_nowait(pending)
            self._queue.task_done()

    async def _enqueue_pending(self, pending: PendingInbound) -> None:
        """把消息放入全局入口队列，超过容量时触发背压拒绝。"""

        if pending.enqueued_at <= 0:
            pending.enqueued_at = time.time()
        if self._queue.qsize() >= self.max_queue_size:
            await self._reject_pending(
                pending,
                InboundBackpressureError("global inbound queue is full"),
            )
            raise InboundBackpressureError("global inbound queue is full")
        self._queue.put_nowait(pending)

    def _ensure_lane_capacity(
        self,
        lane_key: str,
        queue: asyncio.Queue[PendingInbound | None],
    ) -> None:
        """检查单 lane 积压，超过阈值时拒绝新消息。"""

        if queue.qsize() >= self.max_lane_queue_size:
            raise InboundBackpressureError(f"inbound lane is full: {lane_key}")

    async def _reject_pending(self, pending: PendingInbound, exc: Exception) -> None:
        """拒绝入站消息并尽量给用户返回明确反馈。"""

        try:
            await self._deliver_backpressure_reply(pending.message, exc)
        finally:
            if pending.completion_event is not None:
                pending.completion_event.set()

    def _ensure_lane_queue(self, lane_key: str) -> asyncio.Queue[PendingInbound | None]:
        """获取或创建入站 lane 队列和对应 worker。"""

        queue = self._lane_queues.get(lane_key)
        if queue is None:
            queue = asyncio.Queue()
            self._lane_queues[lane_key] = queue
            self._lane_tasks[lane_key] = asyncio.create_task(self._lane_worker(lane_key, queue))
        return queue

    async def _lane_worker(
        self,
        lane_key: str,
        queue: asyncio.Queue[PendingInbound | None],
    ) -> None:
        """串行处理单个入站 lane 内的消息。"""

        while True:
            pending = await queue.get()
            if pending is None:
                queue.task_done()
                break
            async with self._lane_semaphore:
                self._lane_active[lane_key] = self._lane_active.get(lane_key, 0) + 1
                try:
                    await self._process_pending(pending)
                finally:
                    self._lane_active[lane_key] = max(0, self._lane_active.get(lane_key, 0) - 1)
            queue.task_done()
        self._lane_queues.pop(lane_key, None)
        self._lane_tasks.pop(lane_key, None)
        self._lane_active.pop(lane_key, None)

    async def _process_pending(self, pending: PendingInbound) -> None:
        """处理一条 lane 内消息，并把异常转换成用户可见错误。"""

        inbound = pending.message
        notice_task = self._start_long_task_notice(inbound)
        try:
            await self._handle_inbound(inbound)
        except Exception as exc:
            # print + traceback 是最低限度兜底；结构化事件由下游 dispatcher/runner 记录。
            print(
                "[channel_runtime] inbound processing failed:"
                f" channel={inbound.channel}"
                f" account={inbound.account_id}"
                f" sender={inbound.sender_id}"
                f" peer={inbound.peer_id}"
                f" error={exc}"
            )
            traceback.print_exc()
            await self._deliver_error_reply(inbound, exc)
        finally:
            if notice_task is not None:
                notice_task.cancel()
                with suppress(asyncio.CancelledError):
                    await notice_task
            if pending.completion_event is not None:
                # 无论成功还是失败，都必须释放 CLI 输入线程。
                pending.completion_event.set()

    def _start_long_task_notice(self, inbound: InboundMessage) -> asyncio.Task[None] | None:
        """为长任务启动一次延迟提示。"""

        if self.long_task_notice_seconds <= 0:
            return None
        return asyncio.create_task(self._deliver_long_task_notice_after_delay(inbound))

    async def _deliver_long_task_notice_after_delay(self, inbound: InboundMessage) -> None:
        """超过阈值后发送一次“继续处理中”提示。"""

        await asyncio.sleep(self.long_task_notice_seconds)
        await self._deliver_long_task_notice(inbound)

    async def _drain_lane_queues(self) -> None:
        """等待所有入站 lane 队列完成当前积压。"""

        for queue in list(self._lane_queues.values()):
            await queue.join()

    async def _stop_lane_workers(self) -> None:
        """停止所有空闲 lane worker。"""

        tasks = list(self._lane_tasks.values())
        for queue in list(self._lane_queues.values()):
            await queue.put(None)
        if tasks:
            await asyncio.gather(*tasks)

    def stats(self) -> dict[str, Any]:
        """返回入站队列和 lane 运行状态。"""

        now = time.time()
        lanes: list[dict[str, Any]] = []
        oldest_wait_seconds = 0.0
        queued_total = self._queue.qsize()
        for lane_key, queue in self._lane_queues.items():
            pending_items = [
                item for item in list(queue._queue) if isinstance(item, PendingInbound)  # noqa: SLF001
            ]
            lane_oldest = min((item.enqueued_at for item in pending_items if item.enqueued_at), default=0.0)
            lane_wait = max(0.0, now - lane_oldest) if lane_oldest else 0.0
            oldest_wait_seconds = max(oldest_wait_seconds, lane_wait)
            queued_total += len(pending_items)
            lanes.append(
                {
                    "key": lane_key,
                    "queued": len(pending_items),
                    "active": self._lane_active.get(lane_key, 0),
                    "oldest_wait_seconds": lane_wait,
                }
            )
        lanes.sort(key=lambda row: (int(row["active"]) <= 0, -int(row["queued"]), row["key"]))
        return {
            "running": self._running,
            "global_queue_depth": self._queue.qsize(),
            "global_queue_limit": self.max_queue_size,
            "lane_queue_limit": self.max_lane_queue_size,
            "max_concurrent_lanes": self.max_concurrent_lanes,
            "active_lanes": sum(1 for count in self._lane_active.values() if count > 0),
            "running_tasks": sum(self._lane_active.values()),
            "lane_count": len(self._lane_queues),
            "queued_messages": queued_total,
            "oldest_wait_seconds": oldest_wait_seconds,
            "lanes": lanes[:50],
        }

    async def _handle_inbound(self, inbound: InboundMessage) -> None:
        """处理单条入站消息：typing -> onboarding interceptor -> dispatch -> delivery。"""

        print(
            "[channel_runtime] inbound dequeued:"
            f" channel={inbound.channel}"
            f" account={inbound.account_id}"
            f" sender={inbound.sender_id}"
            f" peer={inbound.peer_id}"
        )
        await self._send_typing_if_supported(inbound)
        for interceptor in self.inbound_interceptors:
            # onboarding 等激活消息如果被拦截器消费，就不应进入普通 Agent 会话。
            if await interceptor.try_consume_activation(inbound):
                await self._flush_cli_delivery_if_needed(inbound)
                return
        if await self._maybe_enqueue_background_inbound(inbound):
            await self._flush_cli_delivery_if_needed(inbound)
            return
        if await self._maybe_enqueue_persistent_inbound(inbound):
            await self._flush_cli_delivery_if_needed(inbound)
            return
        # dispatcher 内部会完成路由解析、Agent Loop 执行和回复对象构造。
        result = await self.dispatcher.dispatch_inbound(inbound)
        # 普通回复仍先入可靠投递队列，再由 DeliveryRuntime 发送。
        await self.dispatcher.deliver_reply(self.channels, result)
        await self._flush_cli_delivery_if_needed(inbound)

    async def _maybe_enqueue_background_inbound(self, inbound: InboundMessage) -> bool:
        """把明确的长任务命令转入后台任务队列。"""

        if self.task_queue is None or not self._is_background_inbound_command(inbound.text):
            return False
        task = await asyncio.to_thread(
            self.task_queue.enqueue,
            task_type="agent_inbound",
            source=inbound.channel or "inbound",
            agent_id=str(inbound.metadata.get("agent_id", "")),
            session_key=build_preroute_lane_key(inbound),
            priority=120,
            payload=inbound_to_task_payload(inbound),
            metadata={
                "channel": inbound.channel,
                "account_id": inbound.account_id,
                "peer_id": inbound.peer_id,
                "sender_id": inbound.sender_id,
                "command": self._background_command_name(inbound.text),
            },
        )
        await self._deliver_background_task_accepted(inbound, task.id)
        return True

    async def _maybe_enqueue_persistent_inbound(self, inbound: InboundMessage) -> bool:
        """按配置把普通非 CLI 入站转入持久化任务队列。

        CLI 需要同步等待回复，仍保留直接 dispatch；飞书/Telegram/Webhook 等外部入站可
        先落任务队列，再由 `agent_inbound` worker 消费，降低进程重启和瞬时高峰导致的
        入站丢失风险。
        """

        if self.task_queue is None or not self.inbound_task_queue_enabled:
            return False
        if inbound.channel == "cli":
            return False
        idempotency_key = self._build_inbound_task_idempotency_key(inbound)
        task = await asyncio.to_thread(
            self.task_queue.enqueue,
            task_type="agent_inbound",
            source=inbound.channel or "inbound",
            agent_id=str(inbound.metadata.get("agent_id", "")),
            session_key=build_preroute_lane_key(inbound),
            priority=100,
            idempotency_key=idempotency_key,
            payload=inbound_to_task_payload(inbound),
            metadata={
                "channel": inbound.channel,
                "account_id": inbound.account_id,
                "peer_id": inbound.peer_id,
                "sender_id": inbound.sender_id,
                "mode": "persistent_inbound",
                "idempotency_key": idempotency_key,
            },
        )
        print(
            "[channel_runtime] inbound persisted as task:"
            f" task_id={task.id}"
            f" channel={inbound.channel}"
            f" account={inbound.account_id}"
            f" peer={inbound.peer_id}"
        )
        return True

    def _build_inbound_task_idempotency_key(self, inbound: InboundMessage) -> str:
        """基于平台事件 ID 构造入站任务幂等键。

        不使用纯文本内容做兜底，避免用户连续发送两条相同内容时被误判为重复消息。
        """

        metadata = inbound.metadata or {}
        for key in (
            "idempotency_key",
            "feishu_event_id",
            "feishu_message_id",
            "message_id",
            "event_id",
        ):
            value = str(metadata.get(key, ""))
            if value:
                return f"inbound:{inbound.channel}:{inbound.account_id}:{value}"
        correlation_id = str(metadata.get("correlation_id", ""))
        if correlation_id.startswith("feishu_"):
            return f"inbound:{inbound.channel}:{inbound.account_id}:{correlation_id}"
        raw_id = self._extract_stable_raw_event_id(inbound.raw)
        if raw_id:
            return f"inbound:{inbound.channel}:{inbound.account_id}:{raw_id}"
        return ""

    def _extract_stable_raw_event_id(self, value: Any) -> str:
        """从原始事件结构中递归提取常见稳定事件 ID。"""

        if not isinstance(value, dict):
            return ""
        for key in (
            "event_id",
            "message_id",
            "open_message_id",
            "msg_id",
            "uuid",
        ):
            item = value.get(key)
            if item:
                return str(item)
        for key in ("header", "event", "message"):
            nested = value.get(key)
            found = self._extract_stable_raw_event_id(nested)
            if found:
                return found
        return ""

    def _is_background_inbound_command(self, text: str) -> bool:
        """判断用户消息是否是明确的后台长任务命令。"""

        normalized = text.lstrip()
        return any(
            normalized == command or normalized.startswith(f"{command} ")
            for command in self.background_inbound_commands
        )

    def _background_command_name(self, text: str) -> str:
        """提取后台命令名，便于任务元数据和排障展示。"""

        normalized = text.lstrip()
        for command in self.background_inbound_commands:
            if normalized == command or normalized.startswith(f"{command} "):
                return command
        return ""

    async def _deliver_error_reply(self, inbound: InboundMessage, exc: Exception) -> None:
        """把处理异常转换成一条出站错误提示，避免用户无反馈。"""

        try:
            # 复用原入站 metadata，保留 receive_id_type 等通道投递需要的上下文。
            metadata = dict(inbound.metadata)
            metadata.update(
                {
                    "kind": "error",
                    "sender_id": inbound.sender_id,
                    "error_type": type(exc).__name__,
                }
            )
            await self.dispatcher.deliver_text(
                self.channels,
                ProactiveTarget(
                    channel=inbound.channel,
                    account_id=inbound.account_id,
                    peer_id=inbound.peer_id,
                    agent_id=str(inbound.metadata.get("agent_id", "main")),
                ),
                "本轮消息处理失败，网关已记录错误。请稍后重试，或检查模型/API 配置。",
                metadata=metadata,
            )
            await self._flush_cli_delivery_if_needed(inbound)
        except Exception as delivery_exc:
            # 连错误提示都投递失败时只能记录日志，不能再次抛出，否则消费者会被终止。
            print(
                "[channel_runtime] failed to enqueue error reply:"
                f" channel={inbound.channel}"
                f" account={inbound.account_id}"
                f" peer={inbound.peer_id}"
                f" error={delivery_exc}"
            )
            traceback.print_exc()

    async def _deliver_backpressure_reply(self, inbound: InboundMessage, exc: Exception) -> None:
        """把背压拒绝转换成用户可见提示。"""

        try:
            metadata = dict(inbound.metadata)
            metadata.update(
                {
                    "kind": "backpressure",
                    "sender_id": inbound.sender_id,
                    "error_type": type(exc).__name__,
                }
            )
            await self.dispatcher.deliver_text(
                self.channels,
                ProactiveTarget(
                    channel=inbound.channel,
                    account_id=inbound.account_id,
                    peer_id=inbound.peer_id,
                    agent_id=str(inbound.metadata.get("agent_id", "main")),
                ),
                "当前网关入站消息积压较多，本条消息已被拒绝。请稍后重试。",
                metadata=metadata,
            )
            await self._flush_cli_delivery_if_needed(inbound)
        except Exception as delivery_exc:
            print(
                "[channel_runtime] failed to enqueue backpressure reply:"
                f" channel={inbound.channel}"
                f" account={inbound.account_id}"
                f" peer={inbound.peer_id}"
                f" error={delivery_exc}"
            )
            traceback.print_exc()

    async def _deliver_long_task_notice(self, inbound: InboundMessage) -> None:
        """向用户提示长任务仍在继续处理，最终结果会后续投递。"""

        try:
            metadata = dict(inbound.metadata)
            metadata.update(
                {
                    "kind": "long_task_notice",
                    "sender_id": inbound.sender_id,
                    "threshold_seconds": self.long_task_notice_seconds,
                }
            )
            await self.dispatcher.deliver_text(
                self.channels,
                ProactiveTarget(
                    channel=inbound.channel,
                    account_id=inbound.account_id,
                    peer_id=inbound.peer_id,
                    agent_id=str(inbound.metadata.get("agent_id", "main")),
                ),
                "本轮任务处理时间较长，网关已进入后台继续处理，完成后会继续推送结果。",
                metadata=metadata,
            )
            await self._flush_cli_delivery_if_needed(inbound)
        except Exception as delivery_exc:
            print(
                "[channel_runtime] failed to enqueue long task notice:"
                f" channel={inbound.channel}"
                f" account={inbound.account_id}"
                f" peer={inbound.peer_id}"
                f" error={delivery_exc}"
            )
            traceback.print_exc()

    async def _deliver_background_task_accepted(
        self,
        inbound: InboundMessage,
        task_id: str,
    ) -> None:
        """告知用户长任务已进入后台队列。"""

        try:
            metadata = dict(inbound.metadata)
            metadata.update(
                {
                    "kind": "background_task_accepted",
                    "sender_id": inbound.sender_id,
                    "task_id": task_id,
                }
            )
            await self.dispatcher.deliver_text(
                self.channels,
                ProactiveTarget(
                    channel=inbound.channel,
                    account_id=inbound.account_id,
                    peer_id=inbound.peer_id,
                    agent_id=str(inbound.metadata.get("agent_id", "main")),
                ),
                f"已接收后台任务，任务 ID：{task_id}。执行完成后会继续推送结果。",
                metadata=metadata,
            )
        except Exception as delivery_exc:
            print(
                "[channel_runtime] failed to enqueue background task accepted reply:"
                f" channel={inbound.channel}"
                f" account={inbound.account_id}"
                f" peer={inbound.peer_id}"
                f" error={delivery_exc}"
            )
            traceback.print_exc()

    async def _flush_cli_delivery_if_needed(self, inbound: InboundMessage) -> None:
        """CLI 通道需要在打印提示后立即刷新投递，避免交互体验错位。

        飞书/Telegram 可以让 DeliveryRuntime 后台慢慢发送；CLI 用户正在当前进程里等待
        输出，所以这里主动 flush 一次。
        """

        if inbound.channel == "cli" and self.delivery_runtime is not None:
            await self.delivery_runtime.flush_once()

    async def _send_typing_if_supported(self, inbound: InboundMessage) -> None:
        """仅对支持 typing 的通道发送打字状态。

        目前只有 Telegram 通道在这里处理 typing。飞书卡片和消息回调不走这个提示机制。
        """

        if inbound.channel != "telegram":
            return
        channel = self.channels.get("telegram", inbound.account_id)
        if not isinstance(channel, TelegramChannel):
            return
        # 带 topic 的群消息 peer_id 形如 chat_id:topic:thread_id，typing 只需要 chat_id。
        chat_id = inbound.peer_id.split(":topic:")[0]
        await asyncio.to_thread(channel.send_typing, chat_id)
