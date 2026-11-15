"""Splice translations into empty Odoo PO entries without rewriting the catalog."""

from __future__ import annotations

import re
from pathlib import Path

from odcli_codex.errors import CodexWorkflowError

_FIELD = re.compile(r"^(msgctxt|msgid|msgid_plural|msgstr(?:\[\d+\])?)(\s+)\"(.*)\"$")
_CONTINUED = re.compile(r"^\"(.*)\"$")
_LANGUAGE = re.compile(r"^[a-z]{2}(?:[_-][A-Za-z]{2})?$")
_TOKEN = re.compile(r"%(?:\([^)]+\))?[sdifr]|%%|\{[^{}\n]+\}|</?[A-Za-z][^>\s]*|\\[nNt\"\\]")
_HEADER_LANGUAGE = re.compile(r"Language:\s*([A-Za-z_]+)")


class CatalogEntry:
    """One PO block and the fields parsed from it."""

    def __init__(self, raw: str, fields: dict[str, str], fuzzy: bool) -> None:
        self.raw = raw
        self.fields = fields
        self.fuzzy = fuzzy

    @property
    def msgid(self) -> str:
        return self.fields.get("msgid", "")


class CatalogCounts:
    """Translated, fuzzy, and still-empty entry counts."""

    def __init__(self, translated: int, fuzzy: int, untranslated: int) -> None:
        self.translated = translated
        self.fuzzy = fuzzy
        self.untranslated = untranslated


def read_catalog(path: Path) -> tuple[str, tuple[CatalogEntry, ...]]:
    """Parse a PO file into ordered blocks."""
    text = path.read_text(encoding="utf-8")
    pieces = text.split("\n\n")
    return text, tuple(_entry(piece) for piece in pieces if piece.strip())


def resolve_language(
    path: Path,
    entries: tuple[CatalogEntry, ...],
    explicit: str | None,
) -> str:
    """Use the header and filename, or the explicit language when they disagree."""
    header = next((entry for entry in entries if entry.msgid == ""), None)
    header_language = ""
    if header is not None:
        matched = _HEADER_LANGUAGE.search(header.fields.get("msgstr", ""))
        header_language = matched.group(1) if matched else ""
    stem = path.stem
    file_language = stem if _LANGUAGE.fullmatch(stem) else ""
    if explicit:
        return explicit.replace("-", "_")
    if header_language and file_language and not _compatible(header_language, file_language):
        raise CodexWorkflowError(
            f"language {header_language} disagrees with {file_language}; pass --language"
        )
    resolved = header_language or file_language
    if not resolved:
        raise CodexWorkflowError("pass --language; the catalog does not name one")
    return resolved


def empty_entries(entries: tuple[CatalogEntry, ...]) -> tuple[CatalogEntry, ...]:
    """Return non-header, non-fuzzy entries whose msgstr values are empty."""
    return tuple(
        entry for entry in entries if entry.msgid and not entry.fuzzy and _untranslated(entry)
    )


def counts(entries: tuple[CatalogEntry, ...]) -> CatalogCounts:
    """Count translated, fuzzy, and untranslated entries, excluding the header."""
    translated = fuzzy = untranslated = 0
    for entry in entries:
        if not entry.msgid:
            continue
        if entry.fuzzy:
            fuzzy += 1
        elif _untranslated(entry):
            untranslated += 1
        else:
            translated += 1
    return CatalogCounts(translated, fuzzy, untranslated)


def apply_translations(
    entries: tuple[CatalogEntry, ...],
    proposals: dict[str, str | tuple[str, ...]],
) -> tuple[str, tuple[str, ...], int]:
    """Return catalog text, rejected msgids, and the number of filled entries."""
    rejected: list[str] = []
    filled = 0
    blocks: list[str] = []
    for entry in entries:
        proposal = proposals.get(entry.msgid)
        if proposal is None or not entry.msgid or entry.fuzzy or not _untranslated(entry):
            blocks.append(entry.raw)
            continue
        updated, ok = _splice(entry, proposal)
        if not ok:
            rejected.append(entry.msgid)
            blocks.append(entry.raw)
            continue
        filled += 1
        blocks.append(updated)
    return "\n\n".join(blocks) + ("\n" if blocks else ""), tuple(rejected), filled


def tokens(value: str) -> tuple[str, ...]:
    """Return placeholders, tags, and escapes in source order."""
    return tuple(_TOKEN.findall(value))


def _entry(raw: str) -> CatalogEntry:
    fields: dict[str, str] = {}
    current: str | None = None
    chunks: list[str] = []
    fuzzy = any(", fuzzy" in line for line in raw.splitlines() if line.startswith("#"))
    for line in raw.splitlines():
        matched = _FIELD.match(line)
        if matched:
            if current is not None:
                fields[current] = "".join(chunks)
            current = matched.group(1)
            chunks = [_unescape(matched.group(3))]
            continue
        continued = _CONTINUED.match(line)
        if continued and current is not None:
            chunks.append(_unescape(continued.group(1)))
    if current is not None:
        fields[current] = "".join(chunks)
    return CatalogEntry(raw, fields, fuzzy)


def _splice(entry: CatalogEntry, proposal: str | tuple[str, ...]) -> tuple[str, bool]:
    plural_keys = tuple(key for key in entry.fields if key.startswith("msgstr["))
    if plural_keys:
        if not isinstance(proposal, tuple) or len(proposal) != len(plural_keys):
            return entry.raw, False
        pairs = tuple(zip(plural_keys, proposal, strict=True))
    else:
        if not isinstance(proposal, str):
            return entry.raw, False
        pairs = (("msgstr", proposal),)
    source = entry.fields.get("msgid_plural") or entry.msgid
    for key, value in pairs:
        if not value or any(ord(char) < 32 and char != "\t" for char in value):
            return entry.raw, False
        if tokens(source) != tokens(value):
            return entry.raw, False
        if key not in entry.fields:
            return entry.raw, False
    updated = _replace_empty(entry.raw, pairs)
    return (entry.raw, False) if updated is None else (updated, True)


def _replace_empty(raw: str, pairs: tuple[tuple[str, str], ...]) -> str | None:
    text = raw
    for key, value in pairs:
        escaped = _escape(value)
        pattern = re.compile(rf"^{re.escape(key)}\s+\"\"$", re.MULTILINE)
        text, count = pattern.subn(f'{key} "{escaped}"', text, count=1)
        if count != 1:
            return None
    return text


def _untranslated(entry: CatalogEntry) -> bool:
    keys = tuple(key for key in entry.fields if key.startswith("msgstr"))
    return bool(keys) and all(entry.fields[key] == "" for key in keys)


def _compatible(left: str, right: str) -> bool:
    first = left.replace("-", "_")
    second = right.replace("-", "_")
    return first == second or first.startswith(second + "_") or second.startswith(first + "_")


def _unescape(value: str) -> str:
    return value.replace(r"\\", "\\").replace(r"\"", '"').replace(r"\n", "\n").replace(r"\t", "\t")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
