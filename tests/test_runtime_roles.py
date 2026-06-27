import pytest

from agent_gateway.runtime.execution.roles import (
    build_runtime_role_plan,
    parse_runtime_roles,
)


def test_parse_runtime_roles_defaults_to_all() -> None:
    assert parse_runtime_roles("") == ("all",)
    assert parse_runtime_roles(None) == ("all",)


def test_parse_runtime_roles_normalizes_comma_separated_roles() -> None:
    assert parse_runtime_roles(" API, Delivery ,dashboard ") == (
        "api",
        "delivery",
        "dashboard",
    )


def test_parse_runtime_roles_collapses_all_with_other_roles() -> None:
    assert parse_runtime_roles("all,api,delivery") == ("all",)


def test_parse_runtime_roles_rejects_unknown_roles() -> None:
    with pytest.raises(ValueError, match="Invalid GATEWAY_RUNTIME_ROLES"):
        parse_runtime_roles("api,unknown")


def test_runtime_role_plan_for_all_starts_every_runtime_boundary() -> None:
    plan = build_runtime_role_plan(("all",))

    assert plan.control is True
    assert plan.inbound is True
    assert plan.scheduler is True
    assert plan.delivery is True
    assert plan.dashboard is True
    assert plan.observability is True
    assert plan.worker is True


def test_runtime_role_plan_for_dashboard_includes_control_and_observability() -> None:
    plan = build_runtime_role_plan(("dashboard",))

    assert plan.dashboard is True
    assert plan.control is True
    assert plan.observability is True
    assert plan.inbound is False
    assert plan.scheduler is False
    assert plan.delivery is False


def test_runtime_role_plan_for_api_only_starts_inbound_boundary() -> None:
    plan = build_runtime_role_plan(("api",))

    assert plan.inbound is True
    assert plan.control is False
    assert plan.scheduler is False
    assert plan.delivery is False
    assert plan.dashboard is False
    assert plan.observability is False
