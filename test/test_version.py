import json
import pathlib
import re
import unittest

SPEC_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(-.+)?$")


class VersionTest(unittest.TestCase):
    def test_generator_package_version_matches_openapi_spec(self):
        spec = json.loads(pathlib.Path("spec/vertesia-openapi.json").read_text())
        spec_version = spec["info"]["version"]
        self.assertTrue(spec_version)
        expected_version = python_package_version_from_spec(spec_version)

        config = pathlib.Path("openapi-generator-config.yaml").read_text()
        match = re.search(r"(?m)^\s*packageVersion:\s*\"?([^\"\s]+)\"?\s*$", config)
        self.assertIsNotNone(match, "openapi-generator-config.yaml packageVersion is missing")
        self.assertEqual(match.group(1), expected_version)

        pyproject = pathlib.Path("pyproject.toml").read_text()
        match = re.search(r"(?m)^version = \"([^\"]+)\"$", pyproject)
        self.assertIsNotNone(match, "pyproject.toml project version is missing")
        self.assertEqual(match.group(1), expected_version)

    def test_python_package_version_from_spec(self):
        cases = {
            "1.5.0": "1.5.0",
            "1.5.0-dev": "1.5.0.dev0",
            "1.5.0-dev.20260615.051508Z": "1.5.0.dev0",
        }

        for spec_version, expected_version in cases.items():
            with self.subTest(spec_version=spec_version):
                self.assertEqual(python_package_version_from_spec(spec_version), expected_version)

    def test_python_package_version_from_spec_rejects_unsupported_versions(self):
        for spec_version in ["", "1.5", "v1.5.0", "1.5.0+build"]:
            with self.subTest(spec_version=spec_version):
                with self.assertRaises(ValueError):
                    python_package_version_from_spec(spec_version)


def python_package_version_from_spec(spec_version):
    match = SPEC_VERSION_RE.fullmatch(spec_version)
    if match is None:
        raise ValueError(f"unsupported OpenAPI spec info.version {spec_version!r} (want X.Y.Z[-prerelease])")

    base_version = ".".join(match.group(index) for index in range(1, 4))
    if match.group(4):
        return f"{base_version}.dev0"
    return base_version


if __name__ == "__main__":
    unittest.main()
