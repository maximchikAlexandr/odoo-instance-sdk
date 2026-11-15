# odcli-codex

Optional Codex workflows for OdCLI. Core `odcli` does not depend on this package.

Install only this executable:

```console
uv tool install odcli-codex
```

Install it beside `odcli` in one tool environment:

```console
uv tool install --with-executables-from odcli-codex odoo-instance-sdk
```

`odcli-codex translations fill` fills empty entries in an existing `.po` file. `odcli-codex git message` prints one English commit description. `odcli translations export --auto-translate` and `odcli git commit --generate` call this package after the core command has done its own work.

Settings live in the existing `.odcli/project.toml`:

```toml
[codex]
model = "MODEL"
max_context_bytes = 262144
```

The package does not store API keys. Codex keeps its own login. The release tag for this distribution is `odcli-codex-v0.1.0`.
