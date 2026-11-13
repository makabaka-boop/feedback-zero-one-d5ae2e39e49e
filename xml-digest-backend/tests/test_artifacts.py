"""Tests driven entirely by independent on-disk fixture files.

The fixtures live under ``tests/fixtures`` and never import the package:

* ``reject/*.xml``               -- documents that must be refused;
* ``reject_selection/*.json``    -- selection documents that must be
  refused (JSON syntax or schema, never XML);
* ``bytes/<case>/``              -- an XML input, a selection document and
  the exact canonical bytes (multiple expected outputs joined by the
  ``\\n===\\n`` separator).
"""

import base64
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from xml_digest.digest import compute_digests  # noqa: E402
from xml_digest.errors import (  # noqa: E402
    DigestError, JSONSyntaxError, SelectionError, XMLValidationError,
)

FIXTURES = os.path.join(ROOT, "tests", "fixtures")
VALID_SELECTION = b'[{"path":[],"prefixes":[]}]'
VALID_XML = b"<a/>"


class RejectXMLFixtureTests(unittest.TestCase):
    def test_every_reject_xml_fixture_is_refused(self):
        directory = os.path.join(FIXTURES, "reject")
        for name in sorted(os.listdir(directory)):
            with self.subTest(fixture=name):
                with open(os.path.join(directory, name), "rb") as f:
                    data = f.read()
                with self.assertRaises(XMLValidationError):
                    compute_digests(
                        data, VALID_SELECTION,
                        xml_name=name, selection_name="selection.json",
                    )


class RejectSelectionFixtureTests(unittest.TestCase):
    def test_each_fixture_raises_declared_error_category(self):
        directory = os.path.join(FIXTURES, "reject_selection")
        # Syntax-level failures versus schema-level failures.
        syntax_only = {"syntax_bad.json", "duplicate_member.json"}
        for name in sorted(os.listdir(directory)):
            with self.subTest(fixture=name):
                with open(os.path.join(directory, name), "rb") as f:
                    data = f.read()
                if name in syntax_only:
                    with self.assertRaises(JSONSyntaxError):
                        compute_digests(
                            VALID_XML, data,
                            xml_name="doc.xml", selection_name=name,
                        )
                else:
                    with self.assertRaises(SelectionError):
                        compute_digests(
                            VALID_XML, data,
                            xml_name="doc.xml", selection_name=name,
                        )

    def test_errors_mention_input_name(self):
        path = os.path.join(
            FIXTURES, "reject_selection", "negative_index.json"
        )
        with open(path, "rb") as f:
            data = f.read()
        try:
            compute_digests(
                VALID_XML, data,
                xml_name="doc.xml", selection_name="negative_index.json",
            )
        except SelectionError as exc:
            self.assertIn("negative_index.json", str(exc))
            # JSON Pointer locates the offending element.
            self.assertEqual(exc.json_path, "/0/path/0")
        else:
            self.fail("expected SelectionError")


class ByteFixtureTests(unittest.TestCase):
    def test_every_byte_fixture_matches_exact_canonical_bytes(self):
        directory = os.path.join(FIXTURES, "bytes")
        for case in sorted(os.listdir(directory)):
            case_dir = os.path.join(directory, case)
            if not os.path.isdir(case_dir):
                continue
            with self.subTest(case=case):
                with open(os.path.join(case_dir, "input.xml"), "rb") as f:
                    xml = f.read()
                with open(
                    os.path.join(case_dir, "selection.json"), "rb"
                ) as f:
                    selection = f.read()
                with open(
                    os.path.join(case_dir, "expected.bin"), "rb"
                ) as f:
                    expected = f.read().split(b"\n===\n")

                results = compute_digests(xml, selection)
                self.assertEqual(len(results), len(expected))
                for result, want in zip(results, expected):
                    got = base64.b64decode(result["base64"])
                    self.assertEqual(got, want)
                    # The sha256 must be the digest of those exact bytes.
                    import hashlib
                    self.assertEqual(
                        result["sha256"],
                        hashlib.sha256(want).hexdigest(),
                    )
                    # Standard base64 with padding.
                    self.assertEqual(
                        base64.b64encode(got).decode(),
                        result["base64"],
                    )


class ErrorHierarchyTests(unittest.TestCase):
    def test_all_errors_share_base(self):
        for cls in (JSONSyntaxError, SelectionError, XMLValidationError):
            self.assertTrue(issubclass(cls, DigestError))


if __name__ == "__main__":
    unittest.main(verbosity=2)
