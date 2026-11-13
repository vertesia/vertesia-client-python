#!/usr/bin/env python3
"""Patch generated Python enum handling, union decoding and per-request header precedence.

A newer server can add an enum value or a union variant without a breaking API change. Enums keep
an unknown value as received. A union keeps an unknown variant as the JSON value it was received as
(`actual_instance` and `raw` return it, `to_dict`/`to_json` write it back) instead of failing the
whole response. A union with a published discriminator dispatches on it: a known value must decode
into its branch, an unknown value is kept raw, and a missing value is an error.
"""

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
        # A wire name that is not a Python identifier, such as `_option_id`, is an alias.
        alias = re.search(r"^    (\w+): .*\balias=\"" + re.escape(field) + r"\"", text, re.MULTILINE)
        attribute = alias[1] if alias else field
        pattern = (
            r"(@field_validator\('" + re.escape(attribute) + r"'\)\n"
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


def union_literal_tags(schemas: dict) -> dict:
    """Map each object union to the properties every branch declares as a distinct literal.

    The tag may be optional, as `_option_id` is in ModelOptions: when present, it still names the branch.
    """
    tags = {}
    for union_name, union in schemas.items():
        variants = union.get("oneOf", union.get("anyOf", []))
        names = [item["$ref"].rsplit("/", 1)[-1] for item in variants if "$ref" in item]
        if len(names) < 2 or len(names) != len(variants):
            continue
        branches = [schemas.get(name, {}) for name in names]
        shared = set.intersection(*(set(branch.get("properties", {})) for branch in branches))
        for field in sorted(shared):
            definitions = [branch["properties"][field] for branch in branches]
            values = [definition.get("const") for definition in definitions]
            if all(isinstance(value, str) for value in values) and len(set(values)) == len(values):
                tags.setdefault(union_name, {})[field] = dict(zip(names, definitions, strict=True))
    return tags


def union_literal_schemas(schemas: dict) -> dict:
    """Find distinct literal tags that discriminate object union branches."""
    tagged = {}
    for fields in union_literal_tags(schemas).values():
        for field, branches in fields.items():
            for name, definition in branches.items():
                tagged.setdefault(name, {"properties": {}})["properties"][field] = definition
    return tagged


UNION_MEMBERS = '''\
    # A variant added by a newer server, kept as received. Set only by _from_raw().
    _raw: object = None

    @property
    def raw(self) -> object:
        """A variant added by a newer server, as it was received, or None for a known variant."""
        return self.actual_instance if self._raw is not None and self._raw is self.actual_instance else None

    @classmethod
    def _from_raw(cls, value: object) -> Self:
        instance = cls.model_construct(actual_instance=value)
        instance._raw = value
        return instance

    @staticmethod
    def _check_lossless(json_str: str, variant: object) -> None:
        # The first branch that validates can drop or rewrite received properties, for example options
        # merged from several families: then the next branch is tried, and the payload is kept raw if
        # no branch writes back every received property unchanged.
        received = json.loads(json_str)
        written = variant.to_dict() if hasattr(variant, "to_dict") else variant
        if isinstance(received, dict) and isinstance(written, dict):
            for key, value in received.items():
                if key not in written or written[key] != value:
                    raise ValueError(f"The variant would not keep `{key}`")

    @field_serializer("actual_instance", mode="wrap")
    def _serialize_actual_instance(self, value: object, handler: Callable[[object], object]) -> object:
        # A raw variant is none of the declared types: write it as received, without a warning.
        return value if value is not None and value is self._raw else handler(value)

'''

KEEP_RAW = """\
            # A variant added by a newer server: keep it as received.
            return cls._from_raw(json.loads(json_str))
"""


def union_discriminators(schemas: dict) -> dict:
    """Map each discriminated union to its discriminator property and {value: branch class}."""
    return {
        name: (
            schema["discriminator"]["propertyName"],
            {value: ref.rsplit("/", 1)[-1] for value, ref in schema["discriminator"].get("mapping", {}).items()},
        )
        for name, schema in schemas.items()
        if "discriminator" in schema and ("oneOf" in schema or "anyOf" in schema)
    }


def _dispatching_from_json(name: str, prop: str, mapping: dict[str, str]) -> str:
    branches = ", ".join(f"{value!r}: {branch}" for value, branch in mapping.items())
    return (
        "    @classmethod\n"
        "    def from_json(cls, json_str: str) -> Self:\n"
        f'        """Returns the object represented by the json string, dispatched on `{prop}`"""\n'
        "        data = json.loads(json_str)\n"
        f"        tag = data.get({prop!r}) if isinstance(data, dict) else None\n"
        f"        branches = {{{branches}}}\n"
        "        if isinstance(tag, str) and tag in branches:\n"
        "            instance = cls.model_construct()\n"
        "            instance.actual_instance = branches[tag].from_json(json_str)\n"
        "            return instance\n"
        "        if isinstance(tag, str) and tag:\n"
        "            # A variant added by a newer server: keep it as received.\n"
        "            return cls._from_raw(data)\n"
        f'        raise ValueError("Missing `{prop}` when deserializing the JSON string into {name}: " + json_str)\n'
        "\n"
    )


def patch_union_fallback(text: str, discriminators: dict, lossless: set[str] = frozenset()) -> str:
    """Keep a union variant this client does not know instead of failing the response."""
    match = re.search(r"^class (\w+)\(BaseModel\):", text, re.MULTILINE)
    if not match or "def _from_raw(cls" in text:
        return text
    name = match[1]
    one_of = "    one_of_schemas: Set[str] = " in text
    if not one_of and "    any_of_schemas: Set[str] = " not in text:
        return text
    from_dict = "    @classmethod\n    def from_dict("
    text = text.replace(from_dict, UNION_MEMBERS + from_dict, 1)
    imports = "from collections.abc import Callable\nfrom pydantic import field_serializer\n"
    text = text.replace("\nimport json\n", "\nimport json\n" + imports, 1)
    start = text.index("    @classmethod\n    def from_json(cls, json_str: str) -> Self:")
    end = text.index("    def to_json(self) -> str:", start)
    if name in discriminators:
        prop, mapping = discriminators[name]
        missing = [branch for branch in mapping.values() if not re.search(rf"^from \S+ import {branch}$", text, re.M)]
        if missing:
            raise SystemExit(f"{name}: discriminator branches {missing} are not imported")
        return text[:start] + _dispatching_from_json(name, prop, mapping) + text[end:]
    body = text[start:end]
    if one_of:
        # The generated decoder raises unless exactly one known variant matches.
        anchor = "        if match > 1:\n"
        keep = '        if match != 1 and json_str.strip() != "null":\n' + KEEP_RAW
    else:
        # The generated decoder returns the first known variant that matches.
        anchor = "        if error_messages:\n"
        keep = '        if json_str.strip() != "null":\n' + KEEP_RAW
        if name in lossless:
            body = re.sub(
                r"( +instance\.actual_instance = \w+\.from_json\(json_str\)\n)( +return instance\n)",
                r"\1            cls._check_lossless(json_str, instance.actual_instance)\n\2",
                body,
            )
    if body.count(anchor) != 1:
        raise SystemExit(f"{name}: unexpected generated from_json")
    return text[:start] + body.replace(anchor, keep + anchor) + text[end:]


def main() -> None:
    client = ROOT / "vertesia_client" / "openapi" / "api_client.py"
    client.write_text(patch_client_headers(client.read_text()))
    spec_schemas = json.loads((ROOT / "spec/vertesia-openapi.json").read_text())["components"]["schemas"]
    schemas = union_literal_schemas(spec_schemas)
    discriminators = union_discriminators(spec_schemas)
    # anyOf unions whose branches carry a literal tag: a payload no single branch keeps whole is kept raw.
    lossless = {name for name in union_literal_tags(spec_schemas) if "anyOf" in spec_schemas[name]}
    changed = 0
    for path in sorted(MODELS_DIR.glob("*.py")):
        original = path.read_text()
        updated = patch_schema_constants(patch_inline_enum_validators(patch_standalone_enum(original)), schemas)
        updated = patch_union_fallback(updated, discriminators, lossless)
        if updated != original:
            path.write_text(updated)
            changed += 1
    print(f"Patched {changed} generated model files for forward-compatible enums and unions.")


if __name__ == "__main__":
    main()
