from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from rich.console import RenderableType

from odoo_instance_sdk.commands import output as output_commands
from odoo_instance_sdk.commands import ps as ps_commands
from odoo_instance_sdk.commands.cli_parts.callbacks import _print_doctor
from odoo_instance_sdk.commands.env import checkout as env_checkout
from odoo_instance_sdk.commands.env import display as env_display
from odoo_instance_sdk.commands.multi_target import _rich_multi_target
from odoo_instance_sdk.commands.output import (
    JsonObject,
    OutputMode,
    emit,
    render_rich_text,
    success_document,
)
from odoo_instance_sdk.commands.pg import _cluster_rich, _rows_table
from odoo_instance_sdk.internal.doctor import CheckResult, DoctorReport
from odoo_instance_sdk.models import (
    CheckoutClusterSummary,
    CheckoutGitFacts,
    CheckoutInventory,
    CheckoutRow,
    ClusterEndpoint,
    ClusterSnapshot,
    EnvironmentState,
    PostgresClusterState,
)
from odoo_instance_sdk.models._literals import ClusterUnavailabilityReason


def _cluster(
    state: PostgresClusterState,
    reason: str | None,
) -> ClusterSnapshot:
    return ClusterSnapshot(
        mode="compose",
        owned=True,
        state=state,
        endpoint=ClusterEndpoint(host="127.0.0.1", port=5432),
        container=None,
        metrics=None,
        unavailability_reason=cast("ClusterUnavailabilityReason | None", reason),
        sampled_at=datetime(2026, 9, 24, tzinfo=UTC),
    )


def _cluster_payload(cluster: ClusterSnapshot) -> JsonObject:
    return output_commands.model_to_dict(cluster)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("state", "reason"),
    [
        (PostgresClusterState.HEALTHY, "stats_failed"),
        (PostgresClusterState.STOPPED, None),
    ],
)
def test_frozen_postgres_state_is_consistent_across_human_views(
    state: PostgresClusterState,
    reason: str | None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One frozen cluster state keeps lifecycle and availability on separate axes."""
    cluster = _cluster(state, reason)
    payload = _cluster_payload(cluster)
    env_list_cluster = CheckoutClusterSummary(
        project_id="project-1",
        mode="compose",
        state=state,
        unavailability_reason=cast("ClusterUnavailabilityReason | None", reason),
    )
    document = success_document(
        command="env.show",
        result={"environment": {}, "project": {}, "cluster": payload},
    )

    views = {
        "ps": " | ".join(ps_commands._cluster_row(cluster)),
        "env list": env_display._checkout_cluster_summary_line(env_list_cluster),
        "env show": env_checkout._rich_env_show(document),
        "postgres status": _cluster_rich(
            success_document(command="postgres.status", result=payload)
        ),
    }
    _print_doctor(
        DoctorReport(
            checks=[
                CheckResult(
                    "postgres.cluster",
                    "ok" if state is PostgresClusterState.HEALTHY else "info",
                    f"mode=compose owned=True state={state.value}",
                    facts={"availability": reason or "available"},
                )
            ]
        )
    )
    views["doctor"] = capsys.readouterr().out

    for name, rendered in views.items():
        assert state.value in rendered, name
        if reason is not None:
            assert reason in rendered, name
        opposite = (
            PostgresClusterState.STOPPED.value
            if state is PostgresClusterState.HEALTHY
            else PostgresClusterState.HEALTHY.value
        )
        assert f"state={opposite}" not in rendered, name


def _long_checkout_inventory() -> CheckoutInventory:
    return CheckoutInventory(
        schema_version=1,
        generated_at=datetime(2026, 9, 24, tzinfo=UTC),
        sample_time=datetime(2026, 9, 24, tzinfo=UTC),
        project_id="project-1",
        rows=(
            CheckoutRow(
                kind="environment",
                project_id="project-1",
                name="environment-with-a-long-but-stable-name",
                worktree_path="/Users/example/projects/a-very-long-worktree-path",
                odoo_status="running",
                db_mode="shared",
                database="database-with-a-long-name",
                git=CheckoutGitFacts(
                    branch="feature/with-a-long-but-stable-branch-name",
                    ahead=2,
                    behind=1,
                    added_lines=10,
                    deleted_lines=3,
                ),
                lifecycle_state=EnvironmentState.READY,
            ),
        ),
        clusters=(
            CheckoutClusterSummary(
                project_id="project-1",
                mode="compose",
                state=PostgresClusterState.HEALTHY,
                unavailability_reason="stats_failed",
            ),
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize("width", [80, 120, 180])
def test_representative_audited_views_remain_bounded_at_supported_widths(width: int) -> None:
    from tests.unit.test_ps_presentation import _inventory

    views: tuple[tuple[str, object], ...] = (
        (
            "env list",
            env_checkout._render_env_list_rich(_long_checkout_inventory(), width=width),
        ),
        ("ps", ps_commands._render_ps_rich(_inventory(), width=width)),
        (
            "postgres diagnostics",
            _rows_table(
                "Locks",
                [
                    {
                        "blocked_pid": 42,
                        "query": "long diagnostic value " * 8,
                        "state": "active",
                    }
                ],
                width=width,
            ),
        ),
        ("empty diagnostics", _rows_table("Empty", [], width=width)),
        (
            "multi-target result",
            _rich_multi_target(
                success_document(
                    command="backup.delete",
                    result={
                        "targets": [{"target": "backup-with-a-long-name", "ok": True, "result": {}}]
                    },
                )
            ),
        ),
    )

    for name, renderable in views:
        rendered = render_rich_text(cast("RenderableType", renderable), width=width)
        assert rendered.strip(), name
        assert all(len(line) <= width for line in rendered.splitlines()), name
        assert "┌" in rendered and "└" in rendered, name
    assert "stats_failed" in render_rich_text(cast("RenderableType", views[0][1]), width=width)
    assert "unavailable" in render_rich_text(cast("RenderableType", views[1][1]), width=width)
    assert "(none)" in render_rich_text(cast("RenderableType", views[3][1]), width=width)


@pytest.mark.unit
def test_machine_envelopes_have_json_toon_parity_and_one_emission(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result: JsonObject = {"status": "ok", "operation_count": 1, "warnings": []}
    documents: list[object] = []
    for mode in (OutputMode.JSON, OutputMode.TOON):
        output_commands.emit_json_envelope(
            ok=True,
            command="compatibility",
            result=result,
            mode=mode,
        )
        captured = capsys.readouterr()
        assert captured.err == ""
        assert captured.out.count("schema_version") == 1
        if mode is OutputMode.JSON:
            import json

            documents.append(json.loads(captured.out))
        else:
            from toon import DecodeOptions, decode

            documents.append(decode(captured.out, DecodeOptions(indent=2, strict=True)))
    assert documents[0] == documents[1]

    calls = 0

    def projection(_document: object) -> str:
        nonlocal calls
        calls += 1
        return "one human result"

    assert (
        emit(
            success_document(command="compatibility", result=result),
            OutputMode.RICH,
            rich=projection,
        )
        == 0
    )
    assert calls == 1
    assert capsys.readouterr().out == "one human result\n"
