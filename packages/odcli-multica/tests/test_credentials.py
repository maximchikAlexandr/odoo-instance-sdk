from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from multica_py import Issue
from odcli_multica.client import MulticaOdooClient, read_gitlab_credential_mappings
from odcli_multica.models import (
    ContextVerificationError,
    MulticaCompatibility,
    VerifiedTaskContext,
)


class _Issues:
    def __init__(self, issues: tuple[Issue, ...]) -> None:
        self._issues = {issue.id: issue for issue in issues}

    def get(self, issue_id: str) -> Issue:
        try:
            return self._issues[issue_id]
        except KeyError as exc:
            raise RuntimeError("missing issue") from exc


def _context(issue_id: str = "child") -> VerifiedTaskContext:
    return VerifiedTaskContext(
        checkout_path="/task/checkout",
        task_root="/task",
        repository_url="https://gitlab.example/team/repo",
        workspace_id="workspace",
        multica_project_id="project",
        issue_id=issue_id,
        run_id="run",
        runtime_id="runtime",
        daemon_id="daemon",
        observed_at=datetime.now(UTC),
    )


def _client(*issues: Issue) -> MulticaOdooClient:
    client = object.__new__(MulticaOdooClient)
    client.multica = SimpleNamespace(issues=_Issues(issues))
    client._compatibility = MulticaCompatibility(
        package_version="0.1.0",
        package_revision="c1842ae2dfcd0cc5e739b7785d3209d5e72d01ed",
        native_cli_version="0.5.3",
        typed_checkout=True,
        typed_daemon_status=True,
        observed=True,
    )
    return client


def test_root_creator_traversal_is_finite_and_workspace_bound() -> None:
    root = Issue(
        "root",
        "Root",
        "in_progress",
        project_id="project",
        creator_id="human-root",
        creator_type="member",
    )
    child = Issue("child", "Child", "in_progress", project_id="project", parent_id="root")

    result = _client(child, root).root_creator(_context())

    assert result.root_issue_id == "root"
    assert result.root_creator_id == "human-root"
    assert result.workspace_id == "workspace"


@pytest.mark.parametrize(
    ("issues", "message"),
    [
        ((Issue("child", "Child", "todo", project_id="project", parent_id="missing"),), "parent"),
        (
            (
                Issue("child", "Child", "todo", project_id="project", parent_id="root"),
                Issue(
                    "root",
                    "Root",
                    "todo",
                    project_id="other",
                    creator_id="u",
                    creator_type="member",
                ),
            ),
            "workspace",
        ),
        (
            (
                Issue("child", "Child", "todo", project_id="project", parent_id="root"),
                Issue(
                    "root",
                    "Root",
                    "todo",
                    project_id="project",
                    parent_id="child",
                    creator_id="u",
                    creator_type="member",
                ),
            ),
            "cyclic",
        ),
        (
            (
                Issue(
                    "root",
                    "Root",
                    "todo",
                    project_id="project",
                    creator_id="agent",
                    creator_type="agent",
                ),
            ),
            "human",
        ),
    ],
)
def test_root_creator_rejects_invalid_lineage(issues: tuple[Issue, ...], message: str) -> None:
    with pytest.raises(ContextVerificationError, match=message):
        issue_id = "root" if issues[0].id == "root" else "child"
        _client(*issues).root_creator(_context(issue_id))


def test_credential_mapping_uses_process_precedence_and_redacts_token(tmp_path: Path) -> None:
    root = Issue(
        "root",
        "Root",
        "todo",
        project_id="project",
        creator_id="human-root",
        creator_type="member",
    )
    project = tmp_path / "project"
    dotenv = project / ".odcli" / ".env"
    dotenv.parent.mkdir(parents=True)
    dotenv.write_text("ODCLI_GITLAB_TOKEN_ROOT=file-token\n", encoding="utf-8")
    dotenv.chmod(0o600)
    mapping = project / ".odcli" / "gitlab-credentials.toml"
    mapping.write_text(
        '[[mappings]]\nuser_id = "human-root"\nhost = "https://GitLab.Example/"\n'
        'login = "root-login"\ntoken_key = "ODCLI_GITLAB_TOKEN_ROOT"\n',
        encoding="utf-8",
    )
    mapping.chmod(0o600)
    client = _client(root)

    credential = client._resolve_credentials(
        _context("root"),
        host="gitlab.example",
        project_root=project,
        mapping_path=mapping,
        process_environment={"ODCLI_GITLAB_TOKEN_ROOT": "process-token"},
    )

    assert credential.identity.user_id == "human-root"
    assert credential.identity.host == "gitlab.example"
    assert credential.child_environment()["GITLAB_TOKEN"] == "process-token"
    assert "process-token" not in repr(credential)
    assert "process-token" not in repr(credential.identity)


def test_mapping_reader_rejects_duplicate_host_and_bad_token_key(tmp_path: Path) -> None:
    mapping = tmp_path / "mapping.toml"
    mapping.write_text(
        '[[mappings]]\nuser_id = "u"\nhost = "gitlab.example"\nlogin = "one"\n'
        'token_key = "ODCLI_GITLAB_TOKEN_ONE"\n\n'
        '[[mappings]]\nuser_id = "u"\nhost = "https://GITLAB.EXAMPLE"\nlogin = "two"\n'
        'token_key = "ODCLI_GITLAB_TOKEN_TWO"\n',
        encoding="utf-8",
    )
    mapping.chmod(0o600)

    with pytest.raises(ContextVerificationError, match="ambiguous"):
        read_gitlab_credential_mappings(mapping)


def test_credentials_fail_closed_for_an_unmapped_host(tmp_path: Path) -> None:
    mapping = tmp_path / "mapping.toml"
    mapping.write_text(
        '[[mappings]]\nuser_id = "human-root"\nhost = "gitlab.example"\n'
        'login = "root-login"\ntoken_key = "ODCLI_GITLAB_TOKEN_ROOT"\n',
        encoding="utf-8",
    )
    mapping.chmod(0o600)
    root = Issue(
        "root",
        "Root",
        "todo",
        project_id="project",
        creator_id="human-root",
        creator_type="member",
    )

    with pytest.raises(ContextVerificationError, match="mapping is unavailable"):
        _client(root)._resolve_credentials(
            _context("root"),
            host="other.example",
            project_root=tmp_path,
            mapping_path=mapping,
            process_environment={"ODCLI_GITLAB_TOKEN_ROOT": "secret"},
        )


def test_two_root_creators_keep_private_tokens_isolated(tmp_path: Path) -> None:
    mapping = tmp_path / "mapping.toml"
    mapping.write_text(
        '[[mappings]]\nuser_id = "user-a"\nhost = "gitlab.example"\nlogin = "a"\n'
        'token_key = "ODCLI_GITLAB_TOKEN_A"\n\n'
        '[[mappings]]\nuser_id = "user-b"\nhost = "gitlab.example"\nlogin = "b"\n'
        'token_key = "ODCLI_GITLAB_TOKEN_B"\n',
        encoding="utf-8",
    )
    mapping.chmod(0o600)
    root_a = Issue(
        "root-a",
        "Root A",
        "todo",
        project_id="project",
        creator_id="user-a",
        creator_type="member",
    )
    root_b = Issue(
        "root-b",
        "Root B",
        "todo",
        project_id="project",
        creator_id="user-b",
        creator_type="member",
    )
    client = _client(root_a, root_b)

    credential_a = client._resolve_credentials(
        _context("root-a"),
        host="gitlab.example",
        project_root=tmp_path,
        mapping_path=mapping,
        process_environment={
            "ODCLI_GITLAB_TOKEN_A": "token-a",
            "ODCLI_GITLAB_TOKEN_B": "token-b",
        },
    )
    credential_b = client._resolve_credentials(
        _context("root-b"),
        host="gitlab.example",
        project_root=tmp_path,
        mapping_path=mapping,
        process_environment={
            "ODCLI_GITLAB_TOKEN_A": "token-a",
            "ODCLI_GITLAB_TOKEN_B": "token-b",
        },
    )

    assert credential_a.child_environment()["GITLAB_TOKEN"] == "token-a"
    assert credential_b.child_environment()["GITLAB_TOKEN"] == "token-b"
    assert credential_a.identity.login != credential_b.identity.login
