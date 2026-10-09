#!/usr/bin/env sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$root"
go run github.com/atombender/go-jsonschema@v0.20.0 -p consumer schema.json > generated/contract.go
