#!/usr/bin/env python3
"""Patch generated Python enum handling and per-request header precedence."""

from __future__ import annotations

import json
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


def patch_client_headers(text: str) -> str:
    return text.replace(
        "        header_params.update(self.default_headers)",
        "        for name, value in self.default_headers.items():\n"
        "            if not any(key.lower() == name.lower() for key in header_params):\n"
        "                header_params[name] = value",
    )


def patch_schema_constants(text: str, schemas: dict) -> str:
    """Keep literal tags strict so permissive enum handling cannot merge union branches."""
    match = re.search(r"class (\w+)\(BaseModel\):", text)
    if not match:
        return text
    schema = schemas.get(match[1], {})
    for field, definition in schema.get("properties", {}).items():
        if "const" not in definition or not isinstance(definition["const"], str):
            continue
        constant = repr(definition["const"])
        pattern = (
            r"(@field_validator\('" + re.escape(field) + r"'\)\n"
            r"    def \w+_validate_enum\(cls, value\):\n"
            r'        """Validates the enum"""\n)'
            r".*?(?=\n    @|\n    model_config)"
        )
        message = repr(f"Expected {field} to be {definition['const']}")
        body = (
            f"        if value is not None and value != {constant}:\n"
            f"            raise ValueError({message})\n"
            "        return value\n"
        )
        text = re.sub(pattern, lambda m, replacement=body: m[1] + replacement, text, flags=re.DOTALL)
    return text


def union_literal_schemas(schemas: dict) -> dict:
    """Find required, distinct literal tags that discriminate object union branches."""
    tagged = {}
    for union in schemas.values():
        variants = union.get("oneOf", union.get("anyOf", []))
        names = [item["$ref"].rsplit("/", 1)[-1] for item in variants if "$ref" in item]
        if len(names) < 2 or len(names) != len(variants):
            continue
        branches = [schemas.get(name, {}) for name in names]
        shared = set.intersection(*(set(branch.get("required", [])) for branch in branches))
        for field in shared:
            definitions = [branch.get("properties", {}).get(field, {}) for branch in branches]
            values = [definition.get("const") for definition in definitions]
            if not all(isinstance(value, str) for value in values) or len(set(values)) != len(values):
                continue
            for name, definition in zip(names, definitions):
                tagged.setdefault(name, {"properties": {}})["properties"][field] = definition
    return tagged


def main() -> None:
    client = ROOT / "vertesia_client" / "openapi" / "api_client.py"
    client.write_text(patch_client_headers(client.read_text()))
    schemas = json.loads((ROOT / "spec/vertesia-openapi.json").read_text())["components"]["schemas"]
    schemas = union_literal_schemas(schemas)
    changed = 0
    for path in sorted(MODELS_DIR.glob("*.py")):
        original = path.read_text()
        updated = patch_schema_constants(patch_inline_enum_validators(patch_standalone_enum(original)), schemas)
        if updated != original:
            path.write_text(updated)
            changed += 1
    print(f"Patched {changed} generated model files for forward-compatible enums.")


if __name__ == "__main__":
    main()
