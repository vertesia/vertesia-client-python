"""Fail when an installed distribution is under a license the policy denies.

Run with the interpreter of the environment to scan (it reads the installed metadata), so the
transitive closure that GitHub's dependency graph misses is covered. The denied set is the one in
.github/workflows/license-check.yaml (deny-licenses); keep the two in step.
"""

import re
import sys
from importlib.metadata import distributions

SELF = "vertesia-client"

# SPDX ids from deny-licenses, compared case-insensitively after trimming "+" and the -only/-or-later suffix.
DENIED_IDS = {"agpl-1.0", "agpl-3.0", "gpl-1.0", "gpl-2.0", "gpl-3.0", "lgpl-2.0", "lgpl-2.1", "lgpl-3.0"}
DENIED_EXACT = {"sspl-1.0", "busl-1.1", "elastic-2.0", "json"}

# Trove classifiers name licenses instead of using SPDX ids. Only the denied families are mapped;
# every other classifier is accepted as is.
DENIED_CLASSIFIERS = {
    "License :: OSI Approved :: GNU Affero General Public License v3",
    "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)",
    "License :: OSI Approved :: GNU General Public License (GPL)",
    "License :: OSI Approved :: GNU General Public License v2 (GPLv2)",
    "License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)",
    "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
    "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
    "License :: OSI Approved :: GNU Lesser General Public License v2 (LGPLv2)",
    "License :: OSI Approved :: GNU Lesser General Public License v2 or later (LGPLv2+)",
    "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)",
    "License :: OSI Approved :: GNU Lesser General Public License v3 or later (LGPLv3+)",
    "License :: OSI Approved :: GNU Library or Lesser General Public License (LGPL)",
}

# Short free-text License fields such as "GPLv3" or "GNU General Public License v3".
FREE_TEXT_DENIED = re.compile(r"\b(a|l)?gpl|gnu (affero |lesser |library )?general public|sspl|busl|elastic", re.I)


def _license_id_denied(license_id: str) -> bool:
    return re.sub(r"(-only|-or-later|\+)$", "", license_id.lower()) in DENIED_IDS | DENIED_EXACT


class _SpdxParser:
    """Recursive-descent parser for SPDX license expressions that evaluates "is this expression denied".

    or   := and ('OR' and)*        denied only if every alternative is denied (the user may pick another)
    and  := with ('AND' with)*     denied if any operand is denied (every operand applies)
    with := atom ('WITH' id)?      as the licence alone: a linking exception does not lift a denial
    atom := '(' or ')' | id

    Raises ValueError on a malformed expression, so it can never pass silently as allowed.
    """

    def __init__(self, expression: str) -> None:
        self.tokens = re.findall(r"[()]|[^\s()]+", expression)
        self.pos = 0

    def _peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _keyword(self, word: str) -> bool:
        token = self._peek()
        if token is not None and token.upper() == word:
            self.pos += 1
            return True
        return False

    def parse(self) -> bool:
        denied = self._or()
        if self._peek() is not None:
            raise ValueError(f"unexpected {self._peek()!r}")
        return denied

    def _or(self) -> bool:
        results = [self._and()]
        while self._keyword("OR"):
            results.append(self._and())
        return all(results)

    def _and(self) -> bool:
        results = [self._with()]
        while self._keyword("AND"):
            results.append(self._with())
        return any(results)

    def _with(self) -> bool:
        denied = self._atom()
        if self._keyword("WITH"):
            exception = self._peek()
            if exception is None or exception in "()" or exception.upper() in ("AND", "OR", "WITH"):
                raise ValueError("WITH without an exception id")
            self.pos += 1
        return denied

    def _atom(self) -> bool:
        token = self._peek()
        if token is None:
            raise ValueError("unexpected end of expression")
        if token == "(":
            self.pos += 1
            denied = self._or()
            if self._peek() != ")":
                raise ValueError("unbalanced parentheses")
            self.pos += 1
            return denied
        if token == ")" or token.upper() in ("AND", "OR", "WITH"):
            raise ValueError(f"unexpected {token!r}")
        self.pos += 1
        return _license_id_denied(token)


def spdx_denied(expression: str) -> bool:
    """True when an SPDX expression is denied. Raises ValueError when it is malformed."""
    return _SpdxParser(expression).parse()


def verdict(meta) -> tuple[str, bool]:
    expression = meta.get("License-Expression")
    if expression:
        try:
            return expression, spdx_denied(expression)
        except ValueError as error:
            return f"UNKNOWN (malformed License-Expression {expression!r}: {error})", False
    classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return "; ".join(c.split(" :: ")[-1] for c in classifiers), any(c in DENIED_CLASSIFIERS for c in classifiers)
    text = (meta.get("License") or "").strip()
    if text and len(text) <= 100 and "\n" not in text:
        try:
            as_spdx = spdx_denied(text)
        except ValueError:  # free text is rarely an SPDX expression; the pattern above still applies
            as_spdx = False
        return text, bool(FREE_TEXT_DENIED.search(text)) or as_spdx
    return "UNKNOWN", False  # reported, not failed, like dependency-review does


def main() -> int:
    failures = []
    for dist in sorted(distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        if name.lower().replace("_", "-") == SELF:
            continue
        label, denied = verdict(dist.metadata)
        print(f"{'DENIED ' if denied else 'ok     '} {name} {dist.version}: {label}")
        if denied:
            failures.append(name)
    if failures:
        print(f"::error::Denied license in the resolved dependency set: {', '.join(failures)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
