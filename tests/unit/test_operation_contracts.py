from __future__ import annotations

import ast
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import msgspec
import pytest

from odoo_instance_sdk.execution import ActionStep
from odoo_instance_sdk.models import DepsMissingImport
from odoo_instance_sdk.operations import (
    ClickCommand,
    OperationBinding,
    OperationDescriptor,
    OperationErrorDetails,
    OperationRequest,
    OperationResult,
    OperationTransport,
    WireCreateValue,
    WireDeleteValue,
    WireNestedValue,
    WireOperationDocument,
    assert_compatible_bundle,
    build_registry,
    click_leaf_paths,
    contract_bundle,
    contract_bytes,
    provider_bindings,
    wire_projection,
    wire_schema,
)


def _function_metrics(source_path: Path) -> dict[str, tuple[int, int]]:
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    return {
        node.name: (
            node.end_lineno - node.lineno + 1,
            sum(
                isinstance(child, (ast.If, ast.For, ast.While, ast.Try, ast.Match))
                for child in ast.walk(node)
            ),
        )
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.end_lineno is not None
    }


def test_builtin_inventory_is_complete_and_aliases_share_identity() -> None:
    registry = build_registry()

    assert len(registry.bindings) == 65
    assert registry.for_path(("env", "create")) is registry.for_path(("env", "checkout"))
    assert registry.for_path(("env", "create")).operation_id == "odcli.env.create"


def test_builtin_factory_complexity_stays_in_typed_adapter_table() -> None:
    source_path = Path(__file__).parents[2] / "src/odoo_instance_sdk/operations/contracts.py"
    module = ast.parse(source_path.read_text(encoding="utf-8"))
    factory = next(
        node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef) and node.name == "_builtin_factory"
    )

    assert factory.end_lineno is not None
    assert factory.end_lineno - factory.lineno <= 45

    from odoo_instance_sdk.operations.adapters import BUILTIN_ADAPTERS
    from odoo_instance_sdk.operations.contracts import PUBLIC_LEAF_CASES

    references = {
        case.sdk_primitive for case in PUBLIC_LEAF_CASES if case.sdk_primitive is not None
    }
    assert references == set(BUILTIN_ADAPTERS)

    metrics = _function_metrics(
        Path(__file__).parents[2] / "src/odoo_instance_sdk/operations/adapters.py"
    )
    for adapter in set(BUILTIN_ADAPTERS.values()):
        lines, branches = metrics[adapter.__name__]
        assert lines <= 45
        assert branches <= 4


def test_init_adapter_preserves_explicit_project_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import odoo_instance_sdk.project_init as project_init
    from odoo_instance_sdk.operations.adapters import BUILTIN_ADAPTERS
    from odoo_instance_sdk.operations.contracts import PUBLIC_LEAF_CASES

    captured: dict[str, object] = {}

    def fake_init(project: Path, config: Any, **_: Any) -> object:
        captured["project"] = project
        captured["config"] = config
        return object()

    monkeypatch.setattr(project_init, "init_project_command", fake_init)
    case = next(case for case in PUBLIC_LEAF_CASES if case.path == ("init",))
    context = cast("Any", SimpleNamespace(cwd=Path("/captured-cwd"), project_selector=None))

    BUILTIN_ADAPTERS["init_project_command"](case, {"project_path": "/explicit-target"}, context)

    assert captured["project"] == Path("/explicit-target")
    assert cast("Any", captured["config"]).repository_root == Path("/explicit-target")


def test_environment_checkout_adapter_converts_path_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    import odoo_instance_sdk.resources.environment as environment
    from odoo_instance_sdk.operations.adapters import BUILTIN_ADAPTERS
    from odoo_instance_sdk.operations.contracts import PUBLIC_LEAF_CASES

    captured: dict[str, object] = {}

    class FakeEnvironmentResource:
        def __init__(self, *, _client: object) -> None:
            del _client

        def checkout_command(self, project: Path, base_ref: str, *, options: Any) -> object:
            captured["project"] = project
            captured["base_ref"] = base_ref
            captured["options"] = options
            return object()

    monkeypatch.setattr(environment, "EnvironmentResource", FakeEnvironmentResource)
    case = next(case for case in PUBLIC_LEAF_CASES if case.path == ("env", "create"))
    instance = SimpleNamespace(_client=object())
    context = cast(
        "Any",
        SimpleNamespace(
            cwd=Path("/captured-cwd"),
            project_selector=None,
            resolved=SimpleNamespace(instance=instance),
        ),
    )

    BUILTIN_ADAPTERS["EnvironmentResource.checkout_command"](
        case,
        {"config_path": "/explicit/config", "odoo_bin": "/explicit/odoo-bin"},
        context,
    )

    options = cast("Any", captured["options"])
    assert options.config_path == Path("/explicit/config")
    assert options.odoo_bin == Path("/explicit/odoo-bin")


def test_init_preview_binds_target_and_manifest_inputs_without_secrets() -> None:
    from odoo_instance_sdk.project import ProjectConfig
    from odoo_instance_sdk.project_init import init_project_command

    first = init_project_command(
        Path("/target-a"),
        ProjectConfig(
            repository_root=Path("/target-a"), default_run_args=("--password", "secret-token-a")
        ),
        postgres_allocated=False,
        postgres_image="postgres:16",
        dry_run=True,
    )
    secret_only = init_project_command(
        Path("/target-a"),
        ProjectConfig(
            repository_root=Path("/target-a"), default_run_args=("--password", "secret-token-b")
        ),
        postgres_allocated=False,
        postgres_image="postgres:16",
        dry_run=True,
    )
    non_secret = init_project_command(
        Path("/target-a"),
        ProjectConfig(
            repository_root=Path("/target-a"),
            default_base_ref="develop",
            default_run_args=("--password", "secret-token-a"),
        ),
        postgres_allocated=False,
        postgres_image="postgres:16",
        dry_run=True,
    )
    different_target = init_project_command(
        Path("/target-b"),
        ProjectConfig(
            repository_root=Path("/target-b"), default_run_args=("--password", "secret-token-a")
        ),
        postgres_allocated=False,
        postgres_image="postgres:16",
        dry_run=True,
    )

    first_action = first.plan.steps[0]
    secret_action = secret_only.plan.steps[0]
    non_secret_action = non_secret.plan.steps[0]
    different_target_action = different_target.plan.steps[0]
    assert isinstance(first_action, ActionStep)
    assert isinstance(secret_action, ActionStep)
    assert isinstance(non_secret_action, ActionStep)
    assert isinstance(different_target_action, ActionStep)
    first_details = cast("dict[str, Any]", first_action.details)
    secret_details = cast("dict[str, Any]", secret_action.details)
    non_secret_details = cast("dict[str, Any]", non_secret_action.details)
    different_target_details = cast("dict[str, Any]", different_target_action.details)
    assert first_details == secret_details
    assert first.plan.fingerprint == secret_only.plan.fingerprint
    assert first_details != non_secret_details
    assert first.plan.fingerprint != non_secret.plan.fingerprint
    assert first_details != different_target_details
    assert first.plan.fingerprint != different_target.plan.fingerprint
    assert first_details == {
        "project_path": "/target-a",
        "manifest_input_digest": first_details["manifest_input_digest"],
    }
    assert secret_details == {
        "project_path": "/target-a",
        "manifest_input_digest": secret_details["manifest_input_digest"],
    }
    assert non_secret_details == {
        "project_path": "/target-a",
        "manifest_input_digest": non_secret_details["manifest_input_digest"],
    }
    assert different_target_details == {
        "project_path": "/target-b",
        "manifest_input_digest": different_target_details["manifest_input_digest"],
    }
    assert "secret-token" not in json.dumps(first_details)
    assert "secret-token" not in json.dumps(secret_details)
    assert "secret-token" not in json.dumps(non_secret_details)
    assert "secret-token" not in json.dumps(different_target_details)

    safe_workers_2 = init_project_command(
        Path("/target-a"),
        ProjectConfig(repository_root=Path("/target-a"), default_run_args=("--workers=2",)),
        postgres_allocated=False,
        postgres_image="postgres:16",
        dry_run=True,
    )
    safe_workers_4 = init_project_command(
        Path("/target-a"),
        ProjectConfig(repository_root=Path("/target-a"), default_run_args=("--workers=4",)),
        postgres_allocated=False,
        postgres_image="postgres:16",
        dry_run=True,
    )
    safe_workers_2_action = safe_workers_2.plan.steps[0]
    safe_workers_4_action = safe_workers_4.plan.steps[0]
    assert isinstance(safe_workers_2_action, ActionStep)
    assert isinstance(safe_workers_4_action, ActionStep)
    assert safe_workers_2_action.details != safe_workers_4_action.details
    assert safe_workers_2.plan.fingerprint != safe_workers_4.plan.fingerprint


def test_schema_and_runtime_projection_use_wire_aliases() -> None:
    schema = cast("Any", wire_schema(DepsMissingImport))["$defs"]["DepsMissingImport"]

    assert "import" in schema["properties"]
    assert "import_name" not in schema["properties"]
    assert wire_projection(DepsMissingImport(module="base", import_name="odoo")) == {
        "module": "base",
        "import": "odoo",
    }


def test_contract_export_is_byte_stable_and_includes_schema_policy() -> None:
    first = contract_bytes()
    second = contract_bytes()
    bundle = json.loads(first)

    assert first == second
    assert bundle["contract_version"] == 1
    assert bundle["envelope"] == {
        "name": "OutputDocument",
        "schema_version": 1,
        "wire_fixture": "WireOperationDocument",
    }
    assert bundle["operations"][0]["transport"] == OperationTransport.DOCUMENT
    assert bundle["operations"][0]["implementation"]["kind"] == "sdk-primitive"


def test_builtin_bindings_have_concrete_finite_types_and_parameters() -> None:
    bindings = build_registry().bindings
    finite = [binding for binding in bindings if binding.descriptor.request_type is not None]
    unbounded = [binding for binding in bindings if binding.descriptor.request_type is None]

    assert len({binding.descriptor.request_type for binding in finite}) == len(finite)
    assert all(binding.descriptor.request_type is not OperationRequest for binding in finite)
    assert all(binding.descriptor.result_type is not OperationResult for binding in finite)
    assert all(binding.descriptor.error_types != (OperationErrorDetails,) for binding in finite)
    assert sum(bool(binding.descriptor.parameters) for binding in finite) >= len(finite) - 4
    assert all(
        binding.descriptor.result_type is None and not binding.descriptor.error_types
        for binding in unbounded
    )
    assert all(
        parameter.default is not True
        for binding in bindings
        for parameter in binding.descriptor.parameters
        if parameter.name == "dry_run"
    )
    assert any(
        parameter.name not in {"project", "environment"}
        for binding in bindings
        for parameter in binding.descriptor.parameters
    )
    env_create = build_registry().for_path(("env", "create")).descriptor
    deps_verify = build_registry().for_path(("deps", "verify")).descriptor
    assert env_create.parameters[0].name == "ticket"
    assert env_create.parameters[0].required is True
    assert env_create.result_type is not None
    assert env_create.result_type.__name__ == "EnvironmentCheckoutResult"
    assert deps_verify.result_type is not None
    assert deps_verify.result_type.__name__ == "DepsVerifyResult"
    assert all(
        binding.sdk_primitive is not None or binding.click_path == binding.canonical_path
        for binding in bindings
    )
    assert all("cli." not in (binding.sdk_primitive or "") for binding in bindings)


def test_composed_click_tree_checks_canonical_paths_and_aliases() -> None:
    import odoo_instance_sdk.commands.cli_parts.callbacks  # noqa: F401
    from odoo_instance_sdk.cli import cli

    registry = build_registry()
    actual_paths = set(click_leaf_paths(cast("ClickCommand", cli)))
    expected_paths = {binding.canonical_path for binding in registry.bindings}

    assert actual_paths == expected_paths
    assert len(actual_paths) == len(registry.bindings) == 65
    registry.validate_click_tree(cast("ClickCommand", cli))


def test_composed_click_tree_rejects_unknown_leaves_and_alias_drift() -> None:
    import rich_click as click

    registry = build_registry()
    unknown = click.Group(
        name="root",
        commands={"unexpected": click.Command("unexpected", callback=lambda: None)},
    )
    with pytest.raises(ValueError, match="unknown Click leaf"):
        registry.validate_click_tree(cast("ClickCommand", unknown))

    leaf = click.Command("check", callback=lambda: None)
    leaf._odcli_aliases = ("wrong",)  # type: ignore[attr-defined]
    drifted = click.Group(
        name="root",
        commands={"git": click.Group(commands={"check": leaf})},
    )
    with pytest.raises(ValueError, match="alias inventory drift"):
        registry.validate_click_tree(cast("ClickCommand", drifted))


def test_nested_tagged_default_null_and_alias_wire_fixture_is_real_json() -> None:
    value = WireOperationDocument(
        nested=WireNestedValue(label="nested", enabled=None),
        action=WireCreateValue(name="new", count=0),
        alias="list",
    )

    payload = msgspec.json.encode(value)
    assert json.loads(payload) == {
        "nested": {"label": "nested", "enabled": None},
        "action": {"name": "new", "count": 0, "type": "create"},
        "alias": "list",
    }
    decoded = msgspec.json.decode(payload, type=WireOperationDocument)
    assert decoded == value
    schema = cast("Any", wire_schema(WireOperationDocument))
    assert "anyOf" in schema["$defs"]["WireOperationDocument"]["properties"]["action"]
    assert schema["$defs"]["WireNestedValue"]["properties"]["enabled"]["anyOf"][-1] == {
        "type": "null"
    }
    assert WireDeleteValue(name="old")


def test_registry_rejects_duplicate_ids_paths_and_incomplete_provider_bindings() -> None:
    descriptor = OperationDescriptor(
        operation_id="fixture.read",
        canonical_path=("fixture", "read"),
        request_type=OperationRequest,
        result_type=OperationResult,
    )
    complete = OperationBinding(descriptor, factory=lambda request, _context: request)

    with pytest.raises(ValueError, match="duplicate operation id"):
        build_registry((complete, complete))

    with pytest.raises(ValueError, match="incomplete binding"):
        provider_bindings(
            type(
                "Provider",
                (),
                {"contract_version": 1, "provide": lambda self: (OperationBinding(descriptor),)},
            )()
        )


def test_same_contract_version_rejects_removed_operations() -> None:
    bundle = cast("Any", contract_bundle())
    reduced = {**bundle, "operations": bundle["operations"][1:]}

    with pytest.raises(ValueError, match="removed operations"):
        assert_compatible_bundle(bundle, reduced)


@pytest.mark.parametrize(
    ("surface", "expected"),
    (("parameters", "parameters"), ("envelope", "bundle: envelope"), ("schemas", "schemas")),
)
def test_same_contract_version_rejects_every_public_surface_drift(
    surface: str, expected: str
) -> None:
    bundle = cast("Any", contract_bundle())
    changed = deepcopy(bundle)
    if surface == "parameters":
        changed["operations"][0]["parameters"][0]["default"] = "changed"
    elif surface == "envelope":
        changed["envelope"]["schema_version"] = 2
    else:
        changed["schemas"]["OperationRequest"]["title"] = "Changed"

    with pytest.raises(ValueError, match=expected):
        assert_compatible_bundle(bundle, changed)


def test_provider_conflict_and_incomplete_binding_fail_before_factory_execution() -> None:
    core = build_registry().bindings[0]
    called = False

    def factory(_: object, _context: object) -> object:
        nonlocal called
        called = True
        return None

    conflicting = OperationBinding(
        replace(core.descriptor, provider="fixture"), factory=cast("Any", factory)
    )
    provider = type(
        "Provider",
        (),
        {"contract_version": 1, "provide": lambda self: (conflicting,)},
    )()

    with pytest.raises(ValueError, match="duplicate operation id"):
        build_registry(providers=(provider,))
    assert called is False

    incomplete = type(
        "Provider",
        (),
        {"contract_version": 1, "provide": lambda self: (OperationBinding(core.descriptor),)},
    )()
    with pytest.raises(ValueError, match="incomplete binding"):
        provider_bindings(incomplete)


def test_request_dto_forbids_unknown_fields() -> None:
    with pytest.raises(msgspec.ValidationError):
        msgspec.json.decode(b'{"unexpected": true}', type=OperationRequest)
