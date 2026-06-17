from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_gateway.channels.base import ChannelAccount
from agent_gateway.channels.manager import ChannelManager
from agent_gateway.models import InboundMessage
from agent_gateway.runtime.channel_runtime import ChannelRuntime


DEFAULT_FEISHU_EVENT_KEY = "im.message.receive_v1"
DEFAULT_FEISHU_EVENT_KEYS = ("im.message.receive_v1",)


class FeishuEventInterceptor(Protocol):
    async def try_consume_event(self, event: dict[str, Any], account_id: str) -> bool:
        ...


@dataclass(slots=True)
class FeishuLongConnectionConsumer:
    account: ChannelAccount
    event_key: str = DEFAULT_FEISHU_EVENT_KEY
    identity: str = "bot"
    command: str = "lark-cli"
    jq: str = ""
    restart_delay_seconds: float = 3.0
    last_ready_at: float | None = None
    last_event_at: float | None = None
    last_error: str = ""
    event_count: int = 0
    restart_count: int = 0
    process: subprocess.Popen[str] | None = field(default=None, repr=False)
    thread: threading.Thread | None = field(default=None, repr=False)
    stop_event: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def account_id(self) -> str:
        return self.account.account_id

    def status(self) -> dict[str, Any]:
        running = self.process is not None and self.process.poll() is None
        return {
            "account_id": self.account_id,
            "event_key": self.event_key,
            "identity": self.identity,
            "running": running,
            "last_ready_at": self.last_ready_at,
            "last_event_at": self.last_event_at,
            "last_error": self.last_error,
            "event_count": self.event_count,
            "restart_count": self.restart_count,
        }


class FeishuLongConnectionRuntime:
    def __init__(
        self,
        *,
        channels: ChannelManager,
        channel_runtime: ChannelRuntime,
        event_interceptors: list[FeishuEventInterceptor] | None = None,
    ) -> None:
        self.channels = channels
        self.channel_runtime = channel_runtime
        self.event_interceptors = list(event_interceptors or [])
        self._loop: asyncio.AbstractEventLoop | None = None
        self._consumers: list[FeishuLongConnectionConsumer] = []
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._loop = asyncio.get_running_loop()
        self._consumers = self._build_consumers()
        for consumer in self._consumers:
            self._start_consumer(consumer)

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for consumer in self._consumers:
            consumer.stop_event.set()
            self._terminate_process(consumer.process)
        for consumer in self._consumers:
            thread = consumer.thread
            if thread is not None and thread.is_alive():
                await asyncio.to_thread(thread.join, 3.0)
        self._consumers = []

    async def restart(self, channels: ChannelManager) -> None:
        was_running = self._running
        if was_running:
            await self.stop()
        self.channels = channels
        if was_running:
            await self.start()

    def status(self) -> list[dict[str, Any]]:
        return [consumer.status() for consumer in self._consumers]

    def _build_consumers(self) -> list[FeishuLongConnectionConsumer]:
        consumers: list[FeishuLongConnectionConsumer] = []
        for account, _channel in self.channels.iter_channels():
            if account.channel != "feishu":
                continue
            if str(account.config.get("connection_mode", "")).strip().lower() not in {
                "long_connection",
                "long-connection",
                "websocket",
            }:
                continue
            for event_key in self._normalize_event_keys(account.config):
                consumers.append(
                    FeishuLongConnectionConsumer(
                        account=account,
                        event_key=event_key,
                        identity=str(account.config.get("event_identity", "bot") or "bot"),
                        command=str(account.config.get("event_command", "lark-cli") or "lark-cli"),
                        jq=str(account.config.get("event_jq", "") or ""),
                        restart_delay_seconds=max(
                            0.5,
                            float(account.config.get("event_restart_delay_seconds", 3.0) or 3.0),
                        ),
                    )
                )
        return consumers

    def _start_consumer(self, consumer: FeishuLongConnectionConsumer) -> None:
        if shutil.which(consumer.command) is None:
            consumer.last_error = (
                f"{consumer.command} not found; install/configure lark-cli "
                "or switch Feishu connection_mode back to webhook"
            )
            print(f"[feishu-long] {consumer.last_error} account={consumer.account_id}")
            return
        thread = threading.Thread(
            target=self._consumer_loop,
            args=(consumer,),
            daemon=True,
            name=f"feishu-long-{consumer.account_id}",
        )
        consumer.thread = thread
        thread.start()

    def _consumer_loop(self, consumer: FeishuLongConnectionConsumer) -> None:
        while self._running and not consumer.stop_event.is_set():
            consumer.restart_count += 1
            try:
                self._run_consumer_once(consumer)
            except Exception as exc:
                consumer.last_error = f"{type(exc).__name__}: {exc}"
                print(
                    "[feishu-long] consumer failed:"
                    f" account={consumer.account_id}"
                    f" event_key={consumer.event_key}"
                    f" error={consumer.last_error}"
                )
            finally:
                self._terminate_process(consumer.process)
                consumer.process = None
            if self._running and not consumer.stop_event.is_set():
                consumer.stop_event.wait(consumer.restart_delay_seconds)

    def _run_consumer_once(self, consumer: FeishuLongConnectionConsumer) -> None:
        argv = [
            consumer.command,
            "event",
            "consume",
            consumer.event_key,
            "--as",
            consumer.identity,
        ]
        if consumer.jq:
            argv.extend(["--jq", consumer.jq])
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=self._process_env(consumer),
        )
        consumer.process = process
        print(
            "[feishu-long] consumer starting:"
            f" account={consumer.account_id}"
            f" event_key={consumer.event_key}"
            f" identity={consumer.identity}"
        )
        stderr_thread = threading.Thread(
            target=self._stderr_loop,
            args=(consumer, process),
            daemon=True,
            name=f"feishu-long-stderr-{consumer.account_id}",
        )
        stderr_thread.start()
        try:
            self._stdout_loop(consumer, process)
        finally:
            self._terminate_process(process)
            stderr_thread.join(timeout=1.0)

    def _stdout_loop(
        self,
        consumer: FeishuLongConnectionConsumer,
        process: subprocess.Popen[str],
    ) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            if consumer.stop_event.is_set() or not self._running:
                break
            payload = self._parse_event_line(line)
            if payload is None:
                continue
            inbound = self.event_to_inbound(payload, consumer.account_id)
            if inbound is None:
                self._submit_event(payload, consumer.account_id)
                continue
            consumer.event_count += 1
            consumer.last_event_at = time.time()
            self._submit_inbound(inbound)
        return_code = process.wait()
        if return_code not in {0, None} and not consumer.stop_event.is_set():
            consumer.last_error = f"lark-cli event consume exited with code {return_code}"

    def _stderr_loop(
        self,
        consumer: FeishuLongConnectionConsumer,
        process: subprocess.Popen[str],
    ) -> None:
        assert process.stderr is not None
        for line in process.stderr:
            text = line.strip()
            if not text:
                continue
            if text.startswith("[event] ready"):
                consumer.last_ready_at = time.time()
                consumer.last_error = ""
                print(
                    "[feishu-long] consumer ready:"
                    f" account={consumer.account_id}"
                    f" event_key={consumer.event_key}"
                )
            elif text.startswith("[event] exited"):
                print(f"[feishu-long] {text} account={consumer.account_id}")
            else:
                consumer.last_error = text
                print(f"[feishu-long] {text} account={consumer.account_id}")

    def _submit_inbound(self, inbound: InboundMessage) -> None:
        if self._loop is None:
            return
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is self._loop:
            self._loop.create_task(self.channel_runtime.ingest_external(inbound))
            return
        future = asyncio.run_coroutine_threadsafe(
            self.channel_runtime.ingest_external(inbound),
            self._loop,
        )
        try:
            future.result(timeout=10)
        except Exception as exc:
            print(
                "[feishu-long] failed to submit inbound:"
                f" account={inbound.account_id}"
                f" sender={inbound.sender_id}"
                f" peer={inbound.peer_id}"
                f" error={exc}"
            )

    def _submit_event(self, event: dict[str, Any], account_id: str) -> None:
        if self._loop is None or not self.event_interceptors:
            return

        async def _run() -> None:
            for interceptor in self.event_interceptors:
                if await interceptor.try_consume_event(event, account_id):
                    return

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is self._loop:
            self._loop.create_task(_run())
            return
        future = asyncio.run_coroutine_threadsafe(_run(), self._loop)
        try:
            future.result(timeout=10)
        except Exception as exc:
            print(
                "[feishu-long] failed to submit event:"
                f" account={account_id}"
                f" event_type={self.event_type(event)}"
                f" error={exc}"
            )

    @staticmethod
    def event_to_inbound(payload: dict[str, Any], account_id: str) -> InboundMessage | None:
        text = str(payload.get("content", "") or "").strip()
        if not text:
            return None
        sender_id = str(payload.get("sender_id", "") or "").strip()
        chat_id = str(payload.get("chat_id", "") or "").strip()
        chat_type = str(payload.get("chat_type", "") or "").strip().lower()
        message_type = str(payload.get("message_type", "") or "").strip()
        message_id = str(payload.get("message_id") or payload.get("id") or "").strip()
        event_id = str(payload.get("event_id", "") or "").strip()
        is_group = chat_type == "group"
        peer_id = chat_id if is_group else sender_id
        if not sender_id or not peer_id:
            return None
        receive_id_type = "chat_id" if is_group else "open_id"
        metadata = {
            "receive_id_type": receive_id_type,
            "connection_mode": "long_connection",
            "feishu_event_type": str(payload.get("type", DEFAULT_FEISHU_EVENT_KEY) or ""),
            "feishu_event_id": event_id,
            "feishu_message_id": message_id,
            "feishu_message_type": message_type,
            "feishu_chat_id": chat_id,
            "feishu_chat_type": chat_type,
        }
        return InboundMessage(
            text=text,
            sender_id=sender_id,
            channel="feishu",
            account_id=account_id,
            peer_id=peer_id,
            is_group=is_group,
            raw=payload,
            metadata=metadata,
        )

    @staticmethod
    def event_type(payload: dict[str, Any]) -> str:
        header = payload.get("header", {})
        if isinstance(header, dict) and header.get("event_type"):
            return str(header.get("event_type", ""))
        return str(payload.get("type") or payload.get("event_type") or "")

    @staticmethod
    def _parse_event_line(line: str) -> dict[str, Any] | None:
        text = line.strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            print(f"[feishu-long] ignore non-json event line: {text[:120]}")
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _process_env(consumer: FeishuLongConnectionConsumer) -> dict[str, str]:
        env = dict(os.environ)
        for raw_key, raw_value in consumer.account.config.items():
            key = str(raw_key)
            if not key.startswith("event_env_"):
                continue
            env[key.removeprefix("event_env_")] = str(raw_value)
        return env

    @staticmethod
    def _normalize_event_keys(config: dict[str, Any]) -> tuple[str, ...]:
        raw_keys: list[str] = []
        raw_value = config.get("event_keys")
        if isinstance(raw_value, str):
            raw_keys.extend(key.strip() for key in raw_value.split(","))
        elif isinstance(raw_value, list):
            raw_keys.extend(str(item).strip() for item in raw_value)
        event_key = str(config.get("event_key", DEFAULT_FEISHU_EVENT_KEY) or "").strip()
        if event_key:
            raw_keys.append(event_key)
        normalized = tuple(dict.fromkeys(key for key in raw_keys if key))
        return normalized or DEFAULT_FEISHU_EVENT_KEYS

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str] | None) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
        except Exception:
            pass
        try:
            process.wait(timeout=3)
            return
        except Exception:
            pass
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
