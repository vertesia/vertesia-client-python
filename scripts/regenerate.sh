#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"

openapi-generator generate -c openapi-generator-config.yaml
"$PYTHON_BIN" scripts/patch_forward_compat_enums.py
"$PYTHON_BIN" -m unittest discover -s test
