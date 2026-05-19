import json
import pathlib
import re
import unittest


class VersionTest(unittest.TestCase):
    def test_generator_package_version_matches_openapi_spec(self):
        spec = json.loads(pathlib.Path("spec/vertesia-openapi.json").read_text())
        spec_version = spec["info"]["version"]
        self.assertTrue(spec_version)

        config = pathlib.Path("openapi-generator-config.yaml").read_text()
        match = re.search(r"(?m)^\s*packageVersion:\s*\"?([^\"\s]+)\"?\s*$", config)
        self.assertIsNotNone(match, "openapi-generator-config.yaml packageVersion is missing")
        self.assertEqual(match.group(1), spec_version)

        pyproject = pathlib.Path("pyproject.toml").read_text()
        match = re.search(r"(?m)^version = \"([^\"]+)\"$", pyproject)
        self.assertIsNotNone(match, "pyproject.toml project version is missing")
        self.assertEqual(match.group(1), spec_version)


if __name__ == "__main__":
    unittest.main()
