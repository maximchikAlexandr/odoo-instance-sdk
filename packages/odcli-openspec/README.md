# odcli-openspec

Bind one OpenSpec change to an OdCLI environment and show its planning progress separately from its implementation-task progress.

```console
uv tool install --with-executables-from odcli-openspec odoo-instance-sdk
odcli env checkout PROJ-1 --openspec-change refactor-cli-output-boundary
odcli-openspec env bind refactor-cli-output-boundary
odcli-openspec env unbind
```

The package version is `0.1.0`. The package-qualified release tag is `odcli-openspec-v0.1.0`. It depends on `odoo-instance-sdk>=0.1.0,<0.2` and does not require Node or the OpenSpec CLI at install time.

`odcli env list` remains the inventory. This package contributes one `openspec` column through the environment-facts entry point.
