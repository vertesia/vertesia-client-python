#!/usr/bin/env python3
"""Validate/stamp an immutable SDK cohort and resume its publication safely."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

PATTERN = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-dev\.(\d{8})\.(\d{6})Z)?$")


def package_version(tag, lang, ref):
    match = PATTERN.fullmatch(tag)
    if not match:
        raise ValueError("Use vX.Y.Z or vX.Y.Z-dev.YYYYMMDD.HHMMSSZ")
    base = ".".join(match.group(i) for i in (1, 2, 3))
    expected_ref = "main" if match[4] else "release/" + ".".join(match.group(i) for i in (1, 2))
    if ref != expected_ref:
        raise ValueError(f"This version must be published from {expected_ref}")
    if lang == "python" and match[4]:
        return base + ".dev" + match[4] + match[5]
    return tag[1:].replace("-dev.", "-alpha.") if lang == "java" else tag[1:]


def run(*args, check=True):
    return subprocess.run(args, text=True, capture_output=True, check=check)


def validate(tag, lang, ref):
    version = package_version(tag, lang, ref)
    source = run("git", "rev-parse", "HEAD").stdout.strip()
    expected = os.environ.get("SOURCE_SHA") or source
    if not re.fullmatch(r"[0-9a-f]{40}", expected) or source != expected:
        raise ValueError("Checkout does not match the immutable source commit")
    spec_version = json.loads(Path("spec/vertesia-openapi.json").read_text())["info"]["version"]
    if spec_version != tag[1:]:
        raise ValueError("Release tag must match the exact synchronized OpenAPI version")
    base = tag[1:].split("-dev")[0]
    config = Path("openapi-generator-config.yaml").read_text()
    key = "artifactVersion" if lang == "java" else "packageVersion"
    configured = re.search(r"(?m)^\s*" + key + r':\s*"?([^"\s]+)', config)
    allowed = {base} if lang in ("java", "go") else {version, base + ".dev0"} if "-dev." in tag else {version}
    if not configured or configured[1] not in allowed:
        raise ValueError("Generator config has the wrong cohort version")
    if lang == "python":
        project = re.search(r'(?m)^version = "([^"]+)"$', Path("pyproject.toml").read_text())
        if not project or project[1] not in allowed:
            raise ValueError("pyproject.toml has the wrong source version")
    if lang == "java":
        import xml.etree.ElementTree as ET

        pom = ET.parse("pom.xml").getroot()
        if pom.find("{http://maven.apache.org/POM/4.0.0}version").text != base + "-SNAPSHOT":
            raise ValueError("pom.xml has the wrong source version")
    check_tag(tag, source)
    return {"tag": tag, "package_version": version, "source_sha": source}


def check_tag(tag, source):
    existing = run("git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}", check=False)
    if existing.returncode == 0 and existing.stdout.strip() != source:
        raise ValueError("Existing tag points to another source commit")
    return existing.returncode == 0


def stamp(version, lang):
    # Working-tree-only metadata: tags always identify the original tested/synchronized commit.
    if lang == "java":
        path = Path("pom.xml")
        text, count = re.subn(
            r"(?m)^    <version>.*</version>$", f"    <version>{version}</version>", path.read_text(), count=1
        )
    elif lang == "python":
        path = Path("pyproject.toml")
        text, count = re.subn(r'(?m)^version = "[^"]+"$', f'version = "{version}"', path.read_text(), count=1)
    else:
        return
    if count != 1:
        raise ValueError("Unable to stamp package metadata")
    path.write_text(text)


def registry_url(version, lang):
    if lang == "java":
        return (
            f"https://repo.maven.apache.org/maven2/io/vertesia/vertesia-client/{version}/vertesia-client-{version}.pom"
        )
    return f"https://pypi.org/pypi/vertesia-client/{version}/json"


def already_published(tag, version, lang, source):
    try:
        with urlopen(registry_url(version, lang), timeout=30):
            if not check_tag(tag, source):
                raise ValueError("Registry version exists without a matching source tag; refusing to claim it")
            return True
    except HTTPError as error:
        if error.code == 404:
            return False
        raise


def tag_source(tag, source):
    if check_tag(tag, source):
        return
    repo = os.environ["GITHUB_REPOSITORY"]
    obj = json.loads(
        run(
            "gh",
            "api",
            f"repos/{repo}/git/tags",
            "-f",
            f"tag={tag}",
            "-f",
            f"message={tag}",
            "-f",
            f"object={source}",
            "-f",
            "type=commit",
        ).stdout
    )
    run("gh", "api", f"repos/{repo}/git/refs", "-f", f"ref=refs/tags/{tag}", "-f", f"sha={obj['sha']}")


def release(tag, source):
    existing = run("gh", "release", "view", tag, check=False)
    assets = [str(path) for path in Path("dist").glob("*") if path.is_file()]
    if existing.returncode == 0:
        if assets:
            run("gh", "release", "upload", tag, *assets, "--clobber")
        return
    args = ["gh", "release", "create", tag, "--target", source, "--title", tag, "--generate-notes"]
    if "-dev." in tag or os.environ.get("MARK_PRERELEASE") == "true":
        args += ["--prerelease"]
    run(*args, *assets)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["validate", "stamp", "registry", "tag", "release"])
    parser.add_argument("--lang", choices=["go", "java", "python"], required=True)
    args = parser.parse_args()
    tag = os.environ["RELEASE_VERSION"]
    ref = os.environ["GITHUB_REF_NAME"]
    version = package_version(tag, args.lang, ref)
    source = os.environ.get("SOURCE_SHA") or run("git", "rev-parse", "HEAD").stdout.strip()
    if args.command == "validate":
        values = validate(tag, args.lang, ref)
    elif args.command == "stamp":
        stamp(version, args.lang)
        values = {}
    elif args.command == "registry":
        values = {"published": str(already_published(tag, version, args.lang, source)).lower()}
    elif args.command == "tag":
        tag_source(tag, source)
        values = {}
    else:
        release(tag, source)
        values = {}
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            for key, value in values.items():
                stream.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
