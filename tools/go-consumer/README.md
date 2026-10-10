# Go contract consumer fixture

This directory is delivery tooling, not a Python runtime dependency. The
schema fixture mirrors the selected request/result/error/alias/plugin DTOs in
the authoritative operation bundle. Regeneration is pinned to
`github.com/atombender/go-jsonschema@v0.20.0`:

```sh
./generate.sh
./test.sh
```

The generated fixture is intentionally small enough to compile in isolation;
normal OdCLI startup and provider installation never invoke Go.
