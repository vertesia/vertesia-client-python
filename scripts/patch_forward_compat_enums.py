#!/usr/bin/env python3
"""Patch generated Python enum handling for forward-compatible responses."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "vertesia_client" / "openapi" / "models"

ENUM_MISSING_METHOD = """\
    @classmethod
    def _missing_(cls, value: object) -> Self:
        if not isinstance(value, str):
            raise ValueError(f"{value!r} is not a valid {cls.__name__}")
        unknown = str.__new__(cls, value)
        unknown._name_ = "UNKNOWN_DEFAULT_OPEN_API"
        unknown._value_ = value
        cls._value2member_map_[value] = unknown
        return unknown

"""

INLINE_ENUM_VALIDATOR_RE = re.compile(
    r"\n(?P<indent> {8})if value not in set\((?P<values>\[[^\n]+\])\):"
    r"\n(?P=indent) {4}raise ValueError\([^\n]*\)"
    r"\n(?P=indent)return value"
)


def patch_standalone_enum(text: str) -> str:
    if "(str, Enum)" not in text or "def _missing_(cls, value: object)" in text:
        return text
    marker = "    @classmethod\n    def from_json"
    return text.replace(marker, ENUM_MISSING_METHOD + marker, 1)


def patch_inline_enum_validators(text: str) -> str:
    return INLINE_ENUM_VALIDATOR_RE.sub(r"\n\g<indent>return value", text)


def main() -> None:
    changed = 0
    for path in sorted(MODELS_DIR.glob("*.py")):
        original = path.read_text()
        updated = patch_inline_enum_validators(patch_standalone_enum(original))
        if updated != original:
            path.write_text(updated)
            changed += 1
    print(f"Patched {changed} generated model files for forward-compatible enums.")


if __name__ == "__main__":
    main()
