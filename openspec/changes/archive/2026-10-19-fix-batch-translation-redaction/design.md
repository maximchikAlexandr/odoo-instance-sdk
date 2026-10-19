## Context

The batch exporter already supports repeated module/language flags. The bottleneck is captured stdout redaction before parsing, not Odoo export itself.

## Decisions

Scan maximal candidate key runs, detect sensitive markers inside each key, and consume a value only for a sensitive assignment. Continue scanning ordinary values so nested secret assignments remain protected. Preserve case-insensitive key characters and the existing quoted/unquoted and multiline whitespace semantics. Reuse all remaining canonical redaction stages unchanged.

The private installer previously accepted only the plain PEP 610 repository URL; the installed uv tool records `git+https://...git@<commit>`. Accept that exact additional form only when its embedded SHA equals the already validated commit. Foreign repositories and mismatched refs remain rejected before package import.

## Risks and validation

Assignment-boundary changes could expose credentials. Run existing whole-value, streaming, failure and timeout regressions, plus adjacent/nested/multiline cases. A large encoded response must retain all ordinary bytes and redact a synthetic password within a bounded timeout. Test one live batch in a newly initialized isolated root/worktree; do not copy canonical state or overwrite the completed translation worktree.

## Rollback

Do not merge or replace canonical OdCLI. The pinned hotfix remains independently installed and its export files remain in a disposable test checkout.
