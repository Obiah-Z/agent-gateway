from __future__ import annotations

from dataclasses import dataclass


RUNTIME_ROLE_ALL = "all"
RUNTIME_ROLE_API = "api"
RUNTIME_ROLE_WORKER = "worker"
RUNTIME_ROLE_SCHEDULER = "scheduler"
RUNTIME_ROLE_DELIVERY = "delivery"
RUNTIME_ROLE_DASHBOARD = "dashboard"
RUNTIME_ROLE_CONTROL = "control"
RUNTIME_ROLE_OBSERVABILITY = "observability"

VALID_RUNTIME_ROLES = {
    RUNTIME_ROLE_ALL,
    RUNTIME_ROLE_API,
    RUNTIME_ROLE_WORKER,
    RUNTIME_ROLE_SCHEDULER,
    RUNTIME_ROLE_DELIVERY,
    RUNTIME_ROLE_DASHBOARD,
    RUNTIME_ROLE_CONTROL,
    RUNTIME_ROLE_OBSERVABILITY,
}


@dataclass(frozen=True, slots=True)
class RuntimeRolePlan:
    """运行角色展开后的启动计划。

    Phase 20.1 只拆启动边界，不改变默认单进程行为；`worker` 会在后续任务队列阶段
    接管后台任务消费，因此当前只作为显式角色保留。
    """

    roles: tuple[str, ...]
    control: bool
    inbound: bool
    scheduler: bool
    delivery: bool
    dashboard: bool
    observability: bool
    worker: bool

    @property
    def role_label(self) -> str:
        """返回便于日志展示的角色列表。"""

        return ",".join(self.roles)


def parse_runtime_roles(raw_value: str | None) -> tuple[str, ...]:
    """解析运行角色配置，支持逗号分隔和 `all` 默认值。"""

    roles = tuple(
        item.strip().lower()
        for item in (raw_value or RUNTIME_ROLE_ALL).split(",")
        if item.strip()
    )
    if not roles:
        return (RUNTIME_ROLE_ALL,)
    invalid = sorted(set(roles) - VALID_RUNTIME_ROLES)
    if invalid:
        raise ValueError(
            "Invalid GATEWAY_RUNTIME_ROLES: "
            f"{', '.join(invalid)}. "
            f"Valid roles: {', '.join(sorted(VALID_RUNTIME_ROLES))}"
        )
    if RUNTIME_ROLE_ALL in roles and len(roles) > 1:
        return (RUNTIME_ROLE_ALL,)
    return roles


def build_runtime_role_plan(roles: tuple[str, ...]) -> RuntimeRolePlan:
    """把角色列表展开为具体 runtime 启动开关。"""

    role_set = set(roles)
    all_enabled = RUNTIME_ROLE_ALL in role_set
    dashboard = all_enabled or RUNTIME_ROLE_DASHBOARD in role_set
    control = all_enabled or RUNTIME_ROLE_CONTROL in role_set or dashboard
    inbound = all_enabled or RUNTIME_ROLE_API in role_set
    scheduler = all_enabled or RUNTIME_ROLE_SCHEDULER in role_set
    delivery = all_enabled or RUNTIME_ROLE_DELIVERY in role_set
    observability = all_enabled or RUNTIME_ROLE_OBSERVABILITY in role_set or dashboard
    worker = all_enabled or RUNTIME_ROLE_WORKER in role_set
    return RuntimeRolePlan(
        roles=roles,
        control=control,
        inbound=inbound,
        scheduler=scheduler,
        delivery=delivery,
        dashboard=dashboard,
        observability=observability,
        worker=worker,
    )
