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


def spdx_denied(expression: str) -> bool:
    """True when every alternative of an SPDX expression is denied (an OR lets the user pick another)."""
    for alternative in re.split(r"\s+OR\s+", expression.strip("() "), flags=re.I):
        parts = re.split(r"\s+(?:AND|WITH)\s+", alternative, flags=re.I)
        terms = [re.sub(r"(-only|-or-later|\+)$", "", t.strip("() ").lower()) for t in parts]
        if not any(t in DENIED_IDS or t in DENIED_EXACT for t in terms):
            return False
    return True


def verdict(meta) -> tuple[str, bool]:
    expression = meta.get("License-Expression")
    if expression:
        return expression, spdx_denied(expression)
    classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    if classifiers:
        return "; ".join(c.split(" :: ")[-1] for c in classifiers), any(c in DENIED_CLASSIFIERS for c in classifiers)
    text = (meta.get("License") or "").strip()
    if text and len(text) <= 100 and "\n" not in text:
        return text, bool(FREE_TEXT_DENIED.search(text)) or spdx_denied(text)
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
