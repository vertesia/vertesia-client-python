"""Unit tests for check_licenses.py (stdlib only). Run: python -m unittest discover -s .github/scripts"""

import unittest
from email.message import Message

import check_licenses as cl


def meta(expression=None, classifiers=(), license_text=None) -> Message:
    message = Message()
    if expression:
        message["License-Expression"] = expression
    for classifier in classifiers:
        message["Classifier"] = classifier
    if license_text:
        message["License"] = license_text
    return message


class SpdxExpressionTest(unittest.TestCase):
    def test_denied(self):
        for expression in [
            "GPL-3.0-only AND (MIT OR Apache-2.0)",
            "(MIT OR Apache-2.0) AND GPL-3.0-only",
            "(GPL-3.0-only OR LGPL-2.1-only)",
            "MIT AND AGPL-3.0-only",
            "GPL-2.0-only WITH Classpath-exception-2.0",
            "LGPL-2.1+",
            "GPL-3.0-or-later",
            "gpl-3.0-only and (mit or apache-2.0)",
            "SSPL-1.0",
            "(GPL-3.0-only OR LGPL-2.1-only) AND MIT",
        ]:
            with self.subTest(expression):
                self.assertTrue(cl.spdx_denied(expression))

    def test_allowed(self):
        for expression in [
            "MIT OR (GPL-3.0-only AND Apache-2.0)",
            "Apache-2.0 OR GPL-3.0-only",
            "MIT AND (Apache-2.0 OR BSD-3-Clause)",
            "MIT OR (GPL-3.0-only AND LGPL-2.1-only)",
            "(MIT)",
            "Apache-2.0 WITH LLVM-exception",
            "MIT",
        ]:
            with self.subTest(expression):
                self.assertFalse(cl.spdx_denied(expression))

    def test_malformed_is_an_error_not_allowed(self):
        for expression in [
            "(MIT",
            "MIT)",
            "MIT OR",
            "AND MIT",
            "MIT AND AND GPL-3.0-only",
            "()",
            "",
            "MIT WITH",
            "MIT Apache-2.0",
        ]:
            with self.subTest(expression):
                with self.assertRaises(ValueError):
                    cl.spdx_denied(expression)


class VerdictTest(unittest.TestCase):
    def test_malformed_expression_is_reported_unknown(self):
        label, denied = cl.verdict(meta(expression="(MIT"))
        self.assertTrue(label.startswith("UNKNOWN"))
        self.assertIn("(MIT", label)
        self.assertFalse(denied)

    def test_expression(self):
        self.assertTrue(cl.verdict(meta(expression="GPL-3.0-only AND (MIT OR Apache-2.0)"))[1])
        self.assertFalse(cl.verdict(meta(expression="Apache-2.0 OR GPL-3.0-only"))[1])

    def test_classifiers(self):
        gpl = "License :: OSI Approved :: GNU General Public License v3 (GPLv3)"
        mit = "License :: OSI Approved :: MIT License"
        self.assertTrue(cl.verdict(meta(classifiers=[gpl]))[1])
        self.assertFalse(cl.verdict(meta(classifiers=[mit]))[1])

    def test_free_text(self):
        self.assertTrue(cl.verdict(meta(license_text="GPLv3"))[1])
        self.assertTrue(cl.verdict(meta(license_text="GNU General Public License v3"))[1])
        self.assertFalse(cl.verdict(meta(license_text="Apache Software License"))[1])
        self.assertFalse(cl.verdict(meta(license_text="MIT License (see file"))[1])

    def test_free_text_spdx_expression_keeps_its_grouping(self):
        # A valid dual license in the legacy License field is judged by the parser, not the pattern.
        self.assertFalse(cl.verdict(meta(license_text="MIT OR GPL-3.0-only"))[1])
        self.assertFalse(cl.verdict(meta(license_text="(MIT OR GPL-3.0-only) AND Apache-2.0"))[1])
        self.assertFalse(cl.verdict(meta(license_text="MIT OR GPLv3"))[1])
        self.assertTrue(cl.verdict(meta(license_text="MIT AND GPL-3.0-only"))[1])
        self.assertTrue(cl.verdict(meta(license_text="GPL-2.0-or-later"))[1])
        # Ids that are not SPDX, but name a denied family, still count as denied inside an expression.
        self.assertTrue(cl.verdict(meta(license_text="MIT AND GPLv3"))[1])
        self.assertTrue(cl.verdict(meta(license_text="LGPL"))[1])

    def test_missing_metadata_is_unknown(self):
        self.assertEqual(cl.verdict(meta()), ("UNKNOWN", False))


if __name__ == "__main__":
    unittest.main()
