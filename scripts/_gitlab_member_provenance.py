"""Static provenance verification for the private GitLab cache workflow."""

from __future__ import annotations

import argparse
import base64
import configparser
import csv
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tomllib
from pathlib import Path

from scripts import e2e_gitlab_member_workflow as runner

_SHA = runner._SHA
_git = runner._git


def validate_candidate_repository(args: argparse.Namespace) -> tuple[Path, dict[str, str]]:
    project = args.core_project.expanduser().resolve(strict=True)
    root = Path(_git(args.git, project, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if project != root:
        raise RuntimeError("core project must be the exact SDK repository root")
    if not _SHA.fullmatch(args.sdk_sha):
        raise RuntimeError("core project does not match the exact candidate SDK SHA")
    status = _git(
        args.git,
        root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    if status:
        raise RuntimeError("candidate SDK repository must be clean before cache provenance checks")
    if _git(args.git, root, "rev-parse", "HEAD") != args.sdk_sha:
        raise RuntimeError("core project does not match the exact candidate SDK SHA")

    package_prefix = "src/odoo_instance_sdk/"
    tree = _git(args.git, root, "ls-tree", "-r", "-z", args.sdk_sha, "--", "src/odoo_instance_sdk")
    manifest: dict[str, str] = {}
    for record in tree.split("\0"):
        if not record:
            continue
        metadata, path = record.split("\t", 1)
        mode, object_type, object_id = metadata.split()
        if (
            object_type != "blob"
            or mode not in {"100644", "100755"}
            or not path.startswith(package_prefix)
        ):
            raise RuntimeError("candidate SDK Git package manifest contains an unsupported entry")
        manifest[path.removeprefix(package_prefix)] = object_id
    if not manifest:
        raise RuntimeError("candidate SDK Git package manifest is unavailable")
    return root, manifest


def pin_trusted_executables(args: argparse.Namespace) -> None:
    """Resolve runner tools without searching the candidate installation."""
    helper_dir = Path(args.helper_dir).expanduser().absolute()
    if helper_dir.is_symlink():
        raise RuntimeError("candidate SDK helper directory must not be a symlink")
    resolved_helper_dir = helper_dir.resolve(strict=True)
    install_root = resolved_helper_dir.parent.resolve(strict=True)
    inherited_path = os.environ.get("PATH", os.defpath)
    trusted_entries: list[str] = []
    for entry in inherited_path.split(os.pathsep):
        candidate = Path(entry or os.curdir).expanduser()
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved == install_root or resolved.is_relative_to(install_root):
            continue
        if resolved.is_dir():
            trusted_entries.append(str(resolved))
    search_path = os.pathsep.join(dict.fromkeys(trusted_entries))
    if not search_path:
        raise RuntimeError("trusted executable search path is unavailable")

    def resolve(name: str) -> str:
        requested = Path(name).expanduser()
        resolved_text = (
            str(requested.resolve(strict=True))
            if requested.is_absolute()
            else shutil.which(name, path=search_path)
        )
        if resolved_text is None:
            raise RuntimeError(
                f"trusted executable is unavailable outside candidate installation: {name}"
            )
        executable = Path(resolved_text).resolve(strict=True)
        if (
            not executable.is_file()
            or not os.access(executable, os.X_OK)
            or executable == install_root
            or executable.is_relative_to(install_root)
        ):
            raise RuntimeError(
                f"trusted executable is unavailable outside candidate installation: {name}"
            )
        return str(executable)

    args.git = resolve(args.git)
    args.multica = resolve(args.multica)
    args._trusted_search_path = search_path


def _candidate_helper_path(helper_dir: Path) -> tuple[Path, Path]:
    requested_dir = helper_dir.expanduser().absolute()
    if requested_dir.is_symlink():
        raise RuntimeError("candidate SDK helper directory must not be a symlink")
    candidate_dir = requested_dir.resolve(strict=True)
    helper = shutil.which("git-credential-odcli", path=str(candidate_dir))
    if helper is None:
        raise RuntimeError("candidate SDK credential helper is unavailable")
    helper_path = Path(helper).resolve(strict=True)
    if not helper_path.is_relative_to(candidate_dir):
        raise RuntimeError("candidate SDK credential helper escapes its owned installation")
    install_root = candidate_dir.parent
    if candidate_dir.name not in {"bin", "Scripts"} or not install_root.is_dir():
        raise RuntimeError("candidate SDK helper directory is not an owned installation bin")
    return helper_path, install_root


def _helper_interpreter(helper_path: Path, install_root: Path) -> Path:
    try:
        launcher_lines = helper_path.read_text(encoding="utf-8").splitlines()[:2]
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("candidate SDK credential helper launcher is invalid") from exc
    interpreter_match = next(
        (match for line in launcher_lines if (match := re.match(r"^'''exec' '([^']+)'", line))),
        None,
    )
    if interpreter_match is not None:
        interpreter_text = interpreter_match.group(1)
    else:
        shebang = next(
            (match for line in launcher_lines if (match := re.match(r"^#!\s*(\S+)", line))),
            None,
        )
        interpreter_text = None if shebang is None else shebang.group(1)
    if interpreter_text is None:
        raise RuntimeError("candidate SDK credential helper has no owned interpreter")
    interpreter = Path(interpreter_text).absolute()
    if not interpreter.parent.resolve().is_relative_to(install_root.resolve()):
        raise RuntimeError("candidate SDK credential helper uses a foreign interpreter")
    return interpreter


def _validate_trusted_interpreter(interpreter: Path, install_root: Path) -> None:
    base_text = getattr(sys, "_base_executable", None) or sys.executable
    try:
        trusted = Path(base_text).resolve(strict=True)
        selected = interpreter.resolve(strict=True)
        trusted_digest = hashlib.sha256(trusted.read_bytes()).digest()
        selected_digest = hashlib.sha256(selected.read_bytes()).digest()
    except OSError as exc:
        raise RuntimeError(
            "candidate SDK interpreter is not bound to the trusted base runtime"
        ) from exc
    if (
        not trusted.is_file()
        or not selected.is_file()
        or trusted.is_relative_to(install_root.resolve())
        or selected.is_relative_to(install_root.resolve())
    ):
        raise RuntimeError("candidate SDK interpreter is not bound to the trusted base runtime")
    if trusted_digest != selected_digest:
        raise RuntimeError("candidate SDK interpreter differs from the trusted base runtime")


def _git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def _site_packages(install_root: Path) -> tuple[Path, ...]:
    candidates = [
        *install_root.glob("lib/python*/site-packages"),
        install_root / "Lib" / "site-packages",
    ]
    selected = tuple(
        sorted(
            {
                path.resolve(strict=True)
                for path in candidates
                if path.is_dir()
                and path.resolve(strict=True).is_relative_to(install_root.resolve())
            }
        )
    )
    if len(selected) != 1:
        raise RuntimeError("candidate SDK virtual environment has ambiguous site-packages")
    return selected


def _dist_info(site_packages: Path) -> Path:
    candidates = tuple(site_packages.glob("odoo_instance_sdk-*.dist-info"))
    if len(candidates) != 1 or candidates[0].is_symlink():
        raise RuntimeError("candidate SDK distribution metadata is unavailable or ambiguous")
    return candidates[0].resolve(strict=True)


def _record_rows(record: Path) -> tuple[list[str], ...]:
    try:
        return tuple(csv.reader(io.StringIO(record.read_text(encoding="utf-8"))))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise RuntimeError("candidate SDK installation RECORD is invalid") from exc


def _validate_record_row(
    row: list[str], dist_info: Path, install_root: Path, package_root: Path
) -> Path:
    if len(row) != 3:
        raise RuntimeError("candidate SDK installation RECORD is invalid")
    relative, digest, size = row
    unresolved = dist_info.parent / relative
    if unresolved.is_symlink():
        raise RuntimeError("candidate SDK installation RECORD references a symlink")
    path = unresolved.resolve(strict=True)
    roots = (install_root.resolve(), package_root.resolve())
    if not any(path.is_relative_to(root) for root in roots) or not path.is_file():
        raise RuntimeError("candidate SDK installation RECORD references an invalid file")
    content = path.read_bytes()
    if size and len(content) != int(size):
        raise RuntimeError("candidate SDK installation RECORD size does not match")
    if digest:
        algorithm, separator, encoded = digest.partition("=")
        actual = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
        if not separator or algorithm != "sha256" or actual != encoded:
            raise RuntimeError("candidate SDK installation RECORD digest does not match")
    return path


def _validate_record(dist_info: Path, install_root: Path, package_root: Path) -> None:
    record = dist_info / "RECORD"
    seen = {
        _validate_record_row(row, dist_info, install_root, package_root)
        for row in _record_rows(record)
    }
    if record.resolve(strict=True) not in seen:
        raise RuntimeError("candidate SDK installation RECORD omits itself")


def _entry_points(path: Path) -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(path.read_text(encoding="utf-8"))
        return dict(parser.items("console_scripts"))
    except (OSError, UnicodeError, configparser.Error) as exc:
        raise RuntimeError("candidate SDK entry-points metadata is invalid") from exc


def _expected_launcher(interpreter: Path) -> bytes:
    return (
        f"#!{interpreter}\n"
        "# -*- coding: utf-8 -*-\n"
        "import sys\n"
        "from odoo_instance_sdk.internal.git_credential_helper import main\n"
        'if __name__ == "__main__":\n'
        '    if sys.argv[0].endswith("-script.pyw"):\n'
        "        sys.argv[0] = sys.argv[0][:-11]\n"
        '    elif sys.argv[0].endswith(".exe"):\n'
        "        sys.argv[0] = sys.argv[0][:-4]\n"
        "    sys.exit(main())\n"
    ).encode()


def _package_root(site_packages: Path, source_root: Path, dist_info: Path) -> Path:
    direct_url = dist_info / "direct_url.json"
    if direct_url.exists():
        try:
            metadata = json.loads(direct_url.read_text(encoding="utf-8"))
            editable = metadata.get("dir_info", {}).get("editable") is True
            url = metadata.get("url")
            project_root = source_root.parent.parent
            expected_url = project_root.as_uri()
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError) as exc:
            raise RuntimeError("candidate SDK direct-url metadata is invalid") from exc
        if url != expected_url:
            raise RuntimeError("candidate SDK installation points to a different project")
        if editable:
            pth_files = tuple(site_packages.glob("*.pth"))
            expected_line = str(source_root.parent)
            if len(pth_files) != 1 or pth_files[0].name != "odoo_instance_sdk.pth":
                raise RuntimeError("candidate SDK editable path configuration is invalid")
            pth_lines = pth_files[0].read_text(encoding="utf-8").splitlines()
            if pth_lines != [expected_line]:
                raise RuntimeError("candidate SDK editable path configuration is unsafe")
            return source_root
    return (site_packages / "odoo_instance_sdk").resolve(strict=True)


def _candidate_distribution_metadata(
    site_packages: Path, install_root: Path, interpreter: Path, repository: Path
) -> tuple[Path, dict[str, object], Path]:
    dist_info = _dist_info(site_packages)
    pyvenv = install_root / "pyvenv.cfg"
    if pyvenv.is_symlink() or not pyvenv.is_file():
        raise RuntimeError("candidate SDK virtual environment metadata is invalid")
    try:
        config = {
            key.strip(): value.strip()
            for key, value in (
                line.split("=", 1)
                for line in pyvenv.read_text(encoding="utf-8").splitlines()
                if "=" in line
            )
        }
        home = Path(config["home"]).resolve(strict=True)
        distribution = (dist_info / "METADATA").read_text(encoding="utf-8").splitlines()
        project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]
        version = next(
            line.partition(":")[2].strip() for line in distribution if line.startswith("Version:")
        )
        name = next(
            line.partition(":")[2].strip() for line in distribution if line.startswith("Name:")
        )
    except (OSError, UnicodeError, KeyError, StopIteration, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError("candidate SDK virtual environment metadata is invalid") from exc
    if not interpreter.is_file() or not interpreter.parent.resolve().is_relative_to(
        install_root.resolve()
    ):
        raise RuntimeError("candidate SDK interpreter is outside its owned virtual environment")
    resolved_interpreter = interpreter.resolve(strict=True)
    if not resolved_interpreter.is_relative_to(home) and not resolved_interpreter.is_relative_to(
        install_root.resolve()
    ):
        raise RuntimeError("candidate SDK interpreter does not match its virtual environment")
    if name.casefold() != str(project["name"]).casefold() or version != str(project["version"]):
        raise RuntimeError(
            "candidate SDK distribution metadata does not match the candidate project"
        )

    expected_entries = dict(project.get("scripts", {}))
    entries = _entry_points(dist_info / "entry_points.txt")
    if entries != expected_entries or entries.get("git-credential-odcli") != (
        "odoo_instance_sdk.internal.git_credential_helper:main"
    ):
        raise RuntimeError("candidate SDK entry-points do not match the candidate project")
    return dist_info, project, home


def _validate_package_bytes(package_root: Path, manifest: dict[str, str]) -> None:
    if package_root.is_symlink() or not package_root.is_dir():
        raise RuntimeError("candidate SDK package root is invalid")
    actual_files: dict[str, str] = {}
    for directory, names, files in os.walk(package_root, followlinks=False):
        base = Path(directory)
        if any((base / name).is_symlink() for name in names):
            raise RuntimeError("candidate SDK package contains a directory symlink")
        names[:] = [name for name in names if name != "__pycache__"]
        for filename in files:
            path = base / filename
            if path.is_symlink():
                raise RuntimeError("candidate SDK package contains a file symlink")
            if filename.endswith(".pyc"):
                continue
            relative = str(path.relative_to(package_root))
            actual_files[relative] = _git_blob_id(path.read_bytes())
    if actual_files != manifest:
        raise RuntimeError("candidate SDK package bytes do not match the exact Git candidate")


def _validate_startup_hooks(site_packages: Path, package_root: Path, source_root: Path) -> None:
    site_customizations = (site_packages / "sitecustomize.py", site_packages / "usercustomize.py")
    if any(path.exists() for path in site_customizations):
        raise RuntimeError("candidate SDK virtual environment contains a startup customization")
    allowed_pth = tuple(site_packages.glob("*.pth"))
    editable = package_root.resolve() == source_root.resolve()
    if editable and (len(allowed_pth) != 1 or allowed_pth[0].name != "odoo_instance_sdk.pth"):
        raise RuntimeError("candidate SDK editable path configuration is invalid")
    if not editable and allowed_pth:
        raise RuntimeError("candidate SDK virtual environment contains an unverified path hook")


def _validate_candidate_package(
    install_root: Path,
    interpreter: Path,
    helper_path: Path,
    repository: Path,
    manifest: dict[str, str],
) -> None:
    source_root = repository / "src" / "odoo_instance_sdk"
    (site_packages,) = _site_packages(install_root)
    dist_info, _project, _home = _candidate_distribution_metadata(
        site_packages, install_root, interpreter, repository
    )
    package_root = _package_root(site_packages, source_root, dist_info)
    if package_root != source_root.resolve() and not package_root.is_relative_to(
        install_root.resolve()
    ):
        raise RuntimeError("candidate SDK package escapes its owned roots")
    _validate_package_bytes(package_root, manifest)
    _validate_startup_hooks(site_packages, package_root, source_root)
    _validate_record(dist_info, install_root, package_root)
    if helper_path.read_bytes() != _expected_launcher(interpreter):
        raise RuntimeError("candidate SDK helper launcher is not the expected entry point")


def validate_installed_helper(args: argparse.Namespace) -> Path:
    helper_path, install_root = _candidate_helper_path(args.helper_dir)
    interpreter = _helper_interpreter(helper_path, install_root)
    _validate_trusted_interpreter(interpreter, install_root)
    repository, manifest = validate_candidate_repository(args)
    _validate_candidate_package(install_root, interpreter, helper_path, repository, manifest)
    return helper_path
