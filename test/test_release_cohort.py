import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "release_cohort", Path(__file__).resolve().parents[1] / "scripts" / "release_cohort.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReleaseCohortTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        original = Path.cwd()
        os.chdir(self.directory.name)
        self.addCleanup(os.chdir, original)
        for args in [("init", "-q"), ("config", "user.name", "Test"), ("config", "user.email", "test@example.test")]:
            subprocess.run(["git", *args], check=True)
        Path("source.txt").write_text("original")
        subprocess.run(["git", "add", "source.txt"], check=True)
        subprocess.run(["git", "commit", "-qm", "Source"], check=True)
        self.source = module.run("git", "rev-parse", "HEAD").stdout.strip()

    def test_same_dev_cohort_has_unique_pypi_and_maven_versions(self):
        tag = "v1.6.0-dev.20261021.123456Z"
        self.assertEqual(module.package_version(tag, "python", "main"), "1.6.0.dev20261021123456")
        self.assertEqual(module.package_version(tag, "java", "main"), tag[1:].replace("-dev.", "-alpha."))
        self.assertEqual(module.package_version(tag, "go", "main"), tag[1:])

    def test_channel_branch_and_version_validation(self):
        for tag, ref in [
            ("v1.6.0", "main"),
            ("v1.6.0-dev.20261021.123456Z", "release/1.5"),
            ("v1.6.0-dev.20261021.123456Z", "feat-sdk"),
            ("v1.6.0", "release/1.5"),
            ("v1.6.0-dev", "main"),
            ("v01.6.0", "release/01.6"),
            ("v1.6.0;echo bad", "release/1.6"),
        ]:
            with self.subTest(tag=tag, ref=ref), self.assertRaises(ValueError):
                module.package_version(tag, "go", ref)
        self.assertEqual(module.package_version("v1.6.0", "java", "release/1.6"), "1.6.0")
        self.assertEqual(
            module.package_version("v1.6.0-dev.20261021.123456Z", "java", "release/1.6"),
            "1.6.0-alpha.20261021.123456Z",
        )

    def test_existing_tag_can_be_resumed_but_never_repointed(self):
        self.assertFalse(module.check_tag("v1.6.0", self.source))
        module.run("git", "tag", "v1.6.0")
        self.assertTrue(module.check_tag("v1.6.0", self.source))
        with self.assertRaises(ValueError):
            module.check_tag("v1.6.0", "b" * 40)
        with patch.object(module, "run", wraps=module.run) as run:
            module.tag_source("v1.6.0", self.source)
            self.assertFalse(any(call.args[0] == "gh" for call in run.call_args_list))

    def test_publisher_stamps_working_metadata_without_a_version_commit(self):
        Path("pom.xml").write_text("<project>\n    <version>1.6.0-SNAPSHOT</version>\n</project>\n")
        Path("pyproject.toml").write_text('[project]\nversion = "1.6.0.dev0"\n')
        Path("README.md").write_text("```sh\npip install vertesia-client==1.5.0\n```\n")
        module.stamp("1.6.0-dev.20261021.123456Z", "java")
        module.stamp("1.6.0.dev20261021123456", "python")
        self.assertIn("1.6.0-dev.20261021.123456Z", Path("pom.xml").read_text())
        self.assertIn("1.6.0.dev20261021123456", Path("pyproject.toml").read_text())
        pinned = "pip install vertesia-client==1.6.0.dev20261021123456"
        self.assertEqual(Path("README.md").read_text(), f"```sh\n{pinned}\n```\n")
        self.assertEqual(module.run("git", "rev-parse", "HEAD").stdout.strip(), self.source)

    def test_publisher_pins_an_unversioned_readme_install(self):
        Path("pyproject.toml").write_text('[project]\nversion = "1.6.0.dev0"\n')
        local = "\n\nUse `pip install vertesia-client[dev]` locally.\n"
        Path("README.md").write_text("pip install vertesia-client" + local)
        module.stamp("1.6.0", "python")
        self.assertEqual(Path("README.md").read_text(), "pip install vertesia-client==1.6.0" + local)

    def test_repository_readme_has_exactly_one_install_instruction(self):
        # The release stamp, and the post-release update of the branch README, both refuse any other count.
        readme = Path(__file__).resolve().parents[1] / "README.md"
        self.assertEqual(len(module.README_INSTALL.findall(readme.read_text())), 1)

    def test_publisher_refuses_a_readme_install_it_cannot_identify(self):
        Path("pyproject.toml").write_text('[project]\nversion = "1.6.0.dev0"\n')
        for readme in ("no install here\n", "pip install vertesia-client\npip install vertesia-client==1.5.0\n"):
            with self.subTest(readme=readme):
                Path("README.md").write_text(readme)
                with self.assertRaisesRegex(ValueError, "one pip install instruction"):
                    module.stamp("1.6.0", "python")
                self.assertEqual(Path("README.md").read_text(), readme)

    def test_registry_version_cannot_be_claimed_without_source_tag(self):
        with patch.object(module, "urlopen", side_effect=lambda *_args, **_kwargs: io.BytesIO(b"{}")):
            with self.assertRaises(ValueError):
                module.already_published("v1.6.0", "1.6.0", "python", self.source)
            module.run("git", "tag", "v1.6.0")
            self.assertTrue(module.already_published("v1.6.0", "1.6.0", "python", self.source))


if __name__ == "__main__":
    unittest.main()
