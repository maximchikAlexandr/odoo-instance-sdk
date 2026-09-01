"""Contract tests for the baseline being migrated by MYL-68.

These tests intentionally assert equality, rather than merely checking that a
known item exists.  A new launch, output write, imprecise annotation, or local
subprocess patch therefore fails with a file/line location until its owning
migration updates the checked baseline.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

from tests.fixtures.architecture_inventory import (
    DIRECT_OUTPUT_WRITES,
    DIRECT_SUBPROCESS_LAUNCHES,
    EXPLICIT_IMPRECISE_ANNOTATIONS,
    MODULE_LOCAL_SUBPROCESS_PATCHES,
    OUTPUT_WRITE_REASONS,
    PUBLIC_PROCESS_METHODS,
)

_REPO_ROOT = Path(__file__).parents[2]
_SOURCE_ROOT = _REPO_ROOT / "src" / "odoo_instance_sdk"
_TEST_ROOT = _REPO_ROOT / "tests"
_PATCH_LINE = re.compile(
    r"(?:monkeypatch\.setattr|patch\()[^\n]*(?:subprocess(?:\.(?:run|Popen)|,\s*['\"](?:run|Popen))|SubprocessComposeRunner[^\n]*['\"]run)",
)


def _location(path: Path, line: int) -> tuple[str, int]:
    return path.relative_to(_REPO_ROOT).as_posix(), line


def _format_locations(locations: set[tuple[str, int]]) -> str:
    return ", ".join(f"{path}:{line}" for path, line in sorted(locations))


def _source_modules() -> list[tuple[Path, ast.Module]]:
    return [
        (path, ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(_SOURCE_ROOT.rglob("*.py"))
    ]


def _discover_subprocess_launches() -> set[tuple[str, int]]:
    locations: set[tuple[str, int]] = set()
    for path, module in _source_modules():
        if path.is_relative_to(_SOURCE_ROOT / "internal" / "proc"):
            continue
        for node in ast.walk(module):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute):
                continue
            if not isinstance(node.func.value, ast.Name) or node.func.value.id != "subprocess":
                continue
            if node.func.attr in {"run", "Popen"}:
                locations.add(_location(path, node.lineno))
    return locations


def _output_call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name) and func.id == "print":
        return "print"
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "click"
        and func.attr in {"echo", "secho"}
    ):
        return f"click.{func.attr}"
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Attribute)
        and isinstance(func.value.value, ast.Name)
        and func.value.value.id == "sys"
        and func.value.attr in {"stdout", "stderr"}
        and func.attr in {"write", "flush"}
    ):
        return f"sys.{func.value.attr}.{func.attr}"
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "print"
        and isinstance(func.value, ast.Call)
        and isinstance(func.value.func, ast.Name)
        and func.value.func.id == "Console"
    ):
        return "Console().print"
    return None


def _discover_output_writes() -> set[tuple[str, int]]:
    locations: set[tuple[str, int]] = set()
    for path, module in _source_modules():
        for node in ast.walk(module):
            if isinstance(node, ast.Call) and _output_call_name(node) is not None:
                locations.add(_location(path, node.lineno))
    return locations


def _annotation_contains_imprecise_type(annotation: ast.expr) -> bool:
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            parsed = ast.parse(annotation.value, mode="eval").body
        except SyntaxError:
            return annotation.value.strip() in {"Any", "object", "typing.Any"}
        return _annotation_contains_imprecise_type(parsed)
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name) and node.id in {"Any", "object"}:
            return True
        if isinstance(node, ast.Attribute) and node.attr in {"Any", "object"}:
            return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.strip() in {"Any", "object", "typing.Any"}
        ):
            return True
    return False


def _annotations(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, ast.arg) and node.annotation is not None:
        return [node.annotation]
    if isinstance(node, ast.AnnAssign):
        return [node.annotation]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        arguments = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        annotations = [argument.annotation for argument in arguments if argument.annotation]
        if node.args.vararg is not None and node.args.vararg.annotation is not None:
            annotations.append(node.args.vararg.annotation)
        if node.args.kwarg is not None and node.args.kwarg.annotation is not None:
            annotations.append(node.args.kwarg.annotation)
        if node.returns is not None:
            annotations.append(node.returns)
        return annotations
    return []


def _discover_imprecise_annotations() -> dict[str, frozenset[int]]:
    locations: dict[str, set[int]] = {}
    for path, module in _source_modules():
        for node in ast.walk(module):
            if any(
                _annotation_contains_imprecise_type(annotation) for annotation in _annotations(node)
            ):
                locations.setdefault(path.relative_to(_REPO_ROOT).as_posix(), set()).add(
                    getattr(node, "lineno", 0)
                )
    return {path: frozenset(lines) for path, lines in locations.items()}


def _is_protocol_base(base: ast.expr) -> bool:
    return (isinstance(base, ast.Name) and base.id == "Protocol") or (
        isinstance(base, ast.Attribute) and base.attr == "Protocol"
    )


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _contains_callable_ellipsis(annotation: ast.expr) -> bool:
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        try:
            return _contains_callable_ellipsis(ast.parse(annotation.value, mode="eval").body)
        except SyntaxError:
            return False
    for node in ast.walk(annotation):
        if not isinstance(node, ast.Subscript):
            continue
        value = node.value
        is_callable = (isinstance(value, ast.Name) and value.id == "Callable") or (
            isinstance(value, ast.Attribute) and value.attr == "Callable"
        )
        if is_callable and any(
            isinstance(child, ast.Constant) and child.value is Ellipsis
            for child in ast.walk(node.slice)
        ):
            return True
    return False


def _is_empty_protocol(node: ast.ClassDef) -> bool:
    if not any(_is_protocol_base(base) for base in node.bases):
        return False
    members = [member for member in node.body if not _is_docstring(member)]
    return not members or all(isinstance(member, ast.Pass) for member in members)


def _is_opaque_alias(node: ast.AST) -> bool:
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    elif isinstance(node, ast.TypeAlias):
        targets = [node.name]
    else:
        return False
    return any(
        isinstance(target, ast.Name) and "opaque" in target.id.casefold() for target in targets
    )


def _discover_type_escape_hatches() -> set[tuple[str, int]]:
    """Reject renamed top types and marker protocols in production annotations."""

    locations: set[tuple[str, int]] = set()
    for path, module in _source_modules():
        for node in ast.walk(module):
            if isinstance(node, ast.ClassDef) and _is_empty_protocol(node):
                locations.add(_location(path, node.lineno))

            if _is_opaque_alias(node):
                locations.add(_location(path, getattr(node, "lineno", 0)))

            for annotation in _annotations(node):
                if _contains_callable_ellipsis(annotation):
                    locations.add(_location(path, getattr(node, "lineno", 0)))
    return locations


def _discover_local_subprocess_patches() -> set[tuple[str, int]]:
    locations: set[tuple[str, int]] = set()
    for path in sorted(_TEST_ROOT.rglob("*.py")):
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _PATCH_LINE.search(line):
                locations.add(_location(path, line_number))
    return locations


def _discover_public_process_methods() -> dict[str, int]:  # noqa: C901
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    calls: dict[str, set[str]] = {}
    direct: set[str] = set()

    for path, module in _source_modules():
        if path.is_relative_to(_SOURCE_ROOT / "internal" / "proc"):
            continue

        def visit(body: list[ast.stmt], prefix: str = "") -> None:
            for node in body:
                if isinstance(node, ast.ClassDef):
                    visit(node.body, f"{prefix}{node.name}.")
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qualified = f"{path.relative_to(_REPO_ROOT).as_posix()}:{prefix}{node.name}"
                    functions[qualified] = node
                    calls[qualified] = {
                        call.func.id
                        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        else call.func.attr
                        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        else ""
                        for call in ast.walk(node)
                    }
                    calls[qualified].discard("")
                    if any(
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and isinstance(call.func.value, ast.Name)
                        and call.func.value.id == "subprocess"
                        and call.func.attr in {"run", "Popen"}
                        for call in ast.walk(node)
                    ):
                        direct.add(qualified)

        visit(module.body)

    changed = True
    while changed:
        changed = False
        direct_names = {qualified.rsplit(":", 1)[1].split(".")[-1] for qualified in direct}
        for qualified, called_names in calls.items():
            if qualified not in direct and called_names & direct_names:
                direct.add(qualified)
                changed = True

    return {
        f"{qualified.rsplit(':', 1)[0]}:{qualified.rsplit(':', 1)[1]}": node.lineno
        for qualified, node in functions.items()
        if qualified in direct
        and "." in qualified.rsplit(":", 1)[1]
        and not qualified.rsplit(":", 1)[1].split(".")[-1].startswith("_")
    }


def test_direct_subprocess_launch_inventory_is_exact() -> None:
    discovered = _discover_subprocess_launches()
    assert discovered == DIRECT_SUBPROCESS_LAUNCHES, (
        "subprocess launch inventory changed at "
        + _format_locations(discovered ^ DIRECT_SUBPROCESS_LAUNCHES)
    )


def test_direct_output_inventory_is_exact() -> None:
    assert _discover_output_writes() == DIRECT_OUTPUT_WRITES
    assert set(OUTPUT_WRITE_REASONS) == DIRECT_OUTPUT_WRITES
    assert all(reason.strip() for reason in OUTPUT_WRITE_REASONS.values())


def test_production_imprecise_annotation_inventory_is_exact() -> None:
    discovered = _discover_imprecise_annotations()
    assert discovered == EXPLICIT_IMPRECISE_ANNOTATIONS, (
        "imprecise production annotations at "
        + _format_locations({(path, line) for path, lines in discovered.items() for line in lines})
    )
    escape_hatches = _discover_type_escape_hatches()
    assert not escape_hatches, "universal production type escape hatches at " + _format_locations(
        escape_hatches
    )


def test_module_local_subprocess_patch_inventory_is_exact() -> None:
    assert _discover_local_subprocess_patches() == MODULE_LOCAL_SUBPROCESS_PATCHES


def test_public_process_method_inventory_is_exact() -> None:
    expected = {
        f"src/odoo_instance_sdk/{name}" if not name.startswith("src/") else name: line
        for name, line in PUBLIC_PROCESS_METHODS.items()
    }
    assert _discover_public_process_methods() == expected


def test_inventory_entries_are_line_specific_and_exist() -> None:
    for relative_path, line in DIRECT_SUBPROCESS_LAUNCHES | DIRECT_OUTPUT_WRITES:
        source_lines = (_REPO_ROOT / relative_path).read_text(encoding="utf-8").splitlines()
        assert 0 < line <= len(source_lines), f"invalid inventory location: {relative_path}:{line}"


def test_repository_rules_enforce_the_post_35_gate() -> None:
    rules = (_REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8").lower()
    for required in (
        "github #45",
        "immutable inspectable command snapshot",
        "<operation>_command()",
        "internal/proc/",
        "publicleafcase`/`public_leaf_cases",
        "expression only for pure",
        "explicit `any` or bare `object`",
        "checkout planning branch count",
        "expression adapter/unwrap count",
        "post-#35 vertical-slice recheck",
        "positive checkout result",
        "cannot waive",
        "mandatory post-#35",
    ):
        assert required in rules, f"AGENTS.md is missing the enforced rule: {required}"

    positive = rules.index("positive checkout result")
    waiver = rules.index("cannot waive", positive)
    mandatory = rules.index("mandatory post-#35", positive)
    assert positive < waiver < mandatory


def test_expression_dependency_is_bounded_and_locked() -> None:
    project = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    assert "expression>=5,<6" in dependencies

    lock = (_REPO_ROOT / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "expression"' in lock
    assert '{ name = "expression", specifier = ">=5,<6" }' in lock

    source_imports = "\n".join(
        path.read_text(encoding="utf-8") for path in _SOURCE_ROOT.rglob("*.py")
    )
    assert "import expression" not in source_imports
    assert "from expression" not in source_imports


def test_expression_assessment_records_both_checkpoints() -> None:
    record = (
        (_REPO_ROOT / "docs/adr/0002-bounded-expression-checkout-assessment.md")
        .read_text(encoding="utf-8")
        .lower()
    )
    for required in (
        "planning branches",
        "expression adapters/unwraps",
        "checkout before the expression slice",
        "bounded checkout pipeline after the preliminary slice",
        "positive checkout result",
        "mandatory post-#35 recheck",
    ):
        assert required in record

    before = re.search(r"\| checkout before the expression slice \| (\d+) \| (\d+) \|", record)
    after = re.search(
        r"\| bounded checkout pipeline after the preliminary slice \| (\d+) \| (\d+) \|",
        record,
    )
    assert before is not None and after is not None
    before_branches, before_adapters = (int(value) for value in before.groups())
    after_branches, after_adapters = (int(value) for value in after.groups())
    assert (before_branches, before_adapters) == (12, 0)
    assert (after_branches, after_adapters) == (5, 6)
    assert after_adapters <= before_branches - after_branches
