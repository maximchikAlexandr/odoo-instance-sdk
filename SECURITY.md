# Security Policy

## Reporting Security Issues

If you discover a security vulnerability in odoo-instance-sdk, please **do not** open a GitHub issue. Instead, please email the maintainer directly:

**Email**: maximchik.alexandr@yandex.ru

Please include:

- Description of the vulnerability
- Steps to reproduce (if applicable)
- Potential impact
- Suggested fix (if you have one)

## Supported Versions

| Version | Supported          |
|---------|-------------------|
| 0.1.x   | ✅ Current        |

## Lifecycle and diagnostic boundaries

Structured CLI output sanitizes passwords, tokens, credentials, absolute
paths, and process diagnostics. JSON and TOON commands emit one document on
stdout; diagnostics are kept on stderr, and Rich output never exposes secret
values. Backup URLs and request graphs are not retained in failure objects.

`backup`, `db restore`, and `db drop` require exact identity and perform
preflight plus immediate revalidation before mutation. Restore and deletion
fail closed for stale, malformed, pending, or unknown ownership evidence.
Interrupted operations close owned handles and locks, return exit `130` where
applicable, and retain artifacts whose publication or database postcondition
was already confirmed. `resource list` and `resource doctor` are observation
only: they sanitize local paths and never reconcile, delete, or repair state.

Do not treat logical PostgreSQL size as proof of host-space reclamation, and do
not remove an unknown or symlinked filestore based only on a diagnostic view.
