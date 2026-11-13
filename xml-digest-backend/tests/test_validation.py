"""Validation, rejection and end-to-end pipeline tests."""

import base64
import hashlib
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from xml_digest.digest import compute_digests  # noqa: E402
from xml_digest.errors import (  # noqa: E402
    JSONSyntaxError, SelectionError, XMLValidationError,
)
from xml_digest.json_parser import parse_json  # noqa: E402
from xml_digest.selection import is_ncname, validate_selections  # noqa: E402


# --- JSON syntax ----------------------------------------------------------


class JSONSyntaxTests(unittest.TestCase):
    def _offset(self, data):
        try:
            parse_json(data, "sel.json")
            self.fail("expected JSONSyntaxError")
        except JSONSyntaxError as exc:
            return exc.offset

    def test_trailing_comma_offset(self):
        # Parser detects the problem at the closing ']' (zero-based 3).
        self.assertEqual(self._offset(b'[1,]'), 3)

    def test_unterminated_string_offset(self):
        offset = self._offset(b'[{"path": [')
        self.assertIsInstance(offset, int)

    def test_duplicate_object_member_rejected(self):
        with self.assertRaises(JSONSyntaxError) as ctx:
            parse_json(
                b'[{"path":[],"path":[1],"prefixes":[]}]', "sel.json"
            )
        self.assertIn("duplicate", str(ctx.exception))

    def test_duplicate_top_level_member_name(self):
        with self.assertRaises(JSONSyntaxError):
            parse_json(b'{"a":1,"a":2}', "sel.json")

    def test_unquoted_control_byte_rejected(self):
        with self.assertRaises(JSONSyntaxError):
            parse_json(b'["a\tb"]', "sel.json")

    def test_bad_literal_and_trailing_data(self):
        with self.assertRaises(JSONSyntaxError):
            parse_json(b"tru", "sel.json")
        with self.assertRaises(JSONSyntaxError):
            parse_json(b"[] x", "sel.json")

    def test_valid_unicode_escapes(self):
        node = parse_json(b'["\\u00e9\\uD83D\\uDE00"]', "sel.json")
        self.assertEqual(node.value[0].value, "é😀")

    def test_lone_surrogate_rejected(self):
        with self.assertRaises(JSONSyntaxError):
            parse_json(b'["\\uD800"]', "sel.json")

    def test_error_names_input_and_byte(self):
        try:
            parse_json(b"   {bad}", "choices.json")
        except JSONSyntaxError as exc:
            self.assertIn("choices.json", str(exc))
            self.assertIn("byte 4", str(exc))
        else:
            self.fail("expected error")


# --- selection schema -----------------------------------------------------


class SelectionSchemaTests(unittest.TestCase):
    def validate(self, data):
        return validate_selections(parse_json(data, "sel.json"), "sel.json")

    def test_minimal_valid_selection(self):
        sels = self.validate(b'[{"path":[],"prefixes":[]}]')
        self.assertEqual(sels[0].path, [])
        self.assertEqual(sels[0].prefixes, [])

    def test_root_path_is_empty_array(self):
        sels = self.validate(b'[{"path":[],"prefixes":[]}]')
        self.assertEqual(sels[0].path, [])

    def test_non_array_root_rejected(self):
        with self.assertRaises(SelectionError) as ctx:
            self.validate(b'{"path":[]}')
        self.assertEqual(ctx.exception.json_path, "")

    def test_unknown_member_rejected(self):
        with self.assertRaises(SelectionError) as ctx:
            self.validate(b'[{"path":[],"prefixes":[],"x":1}]')
        self.assertEqual(ctx.exception.json_path, "/0/x")

    def test_missing_member_rejected(self):
        with self.assertRaises(SelectionError) as ctx:
            self.validate(b'[{"path":[]}]')
        self.assertEqual(ctx.exception.json_path, "/0/prefixes")

    def test_path_wrong_type(self):
        with self.assertRaises(SelectionError) as ctx:
            self.validate(b'[{"path":"1","prefixes":[]}]')
        self.assertEqual(ctx.exception.json_path, "/0/path")

    def test_negative_index_rejected(self):
        with self.assertRaises(SelectionError) as ctx:
            self.validate(b'[{"path":[-1],"prefixes":[]}]')
        self.assertEqual(ctx.exception.json_path, "/0/path/0")
        self.assertEqual(ctx.exception.index, 0)

    def test_boolean_is_not_an_integer(self):
        for token in (b"true", b"false"):
            with self.subTest(token=token):
                with self.assertRaises(SelectionError) as ctx:
                    self.validate(
                        b'[{"path":[' + token + b'],"prefixes":[]}]'
                    )
                self.assertEqual(ctx.exception.json_path, "/0/path/0")

    def test_float_index_rejected(self):
        with self.assertRaises(SelectionError):
            self.validate(b'[{"path":[1.0],"prefixes":[]}]')

    def test_null_index_rejected(self):
        with self.assertRaises(SelectionError):
            self.validate(b'[{"path":[null],"prefixes":[]}]')

    def test_prefixes_duplicates_rejected(self):
        with self.assertRaises(SelectionError) as ctx:
            self.validate(
                b'[{"path":[],"prefixes":["p","p"]}]'
            )
        self.assertEqual(ctx.exception.json_path, "/0/prefixes/1")
        self.assertEqual(ctx.exception.index, 1)

    def test_default_token_accepted(self):
        sels = self.validate(b'[{"path":[],"prefixes":["#default"]}]')
        self.assertEqual(sels[0].prefixes, ["#default"])

    def test_bad_ncname_rejected(self):
        for bad in ('"1x"', '"p:q"', '"p q"', '""', '"-x"', '".x"'):
            with self.subTest(bad=bad):
                with self.assertRaises(SelectionError):
                    self.validate(
                        b'[{"path":[],"prefixes":[' + bad.encode() + b']}]'
                    )

    def test_ncname_reserved_words_still_syntactically_valid(self):
        # "xmlns" and "xml" are NCNames lexically; their reserved semantics
        # are enforced (where relevant) at the XML layer instead.
        self.assertTrue(is_ncname("xmlns"))
        self.assertTrue(is_ncname("xml"))

    def test_good_ncname_examples(self):
        for good in ("p", "_", "_a.b-c1", "Café", "λ", "a·b"):
            self.assertTrue(is_ncname(good), good)

    def test_repeated_selections_are_kept(self):
        sels = self.validate(
            b'[{"path":[0],"prefixes":[]},{"path":[0],"prefixes":[]}]'
        )
        self.assertEqual(len(sels), 2)


# --- XML acceptance -------------------------------------------------------


class XMLRejectionTests(unittest.TestCase):
    def reject(self, data):
        with self.assertRaises(XMLValidationError) as ctx:
            compute_digests(
                data, b'[{"path":[],"prefixes":[]}]',
                xml_name="doc.xml", selection_name="sel.json",
            )
        return ctx.exception

    def test_doctype_rejected(self):
        exc = self.reject(b'<?xml version="1.0"?><!DOCTYPE a><a/>')
        self.assertIn("DOCTYPE", str(exc))
        self.assertIn("doc.xml", str(exc))

    def test_non_utf8_encoding_declaration_rejected(self):
        exc = self.reject(
            b'<?xml version="1.0" encoding="ISO-8859-1"?><a/>'
        )
        self.assertIn("UTF-8", str(exc))
        self.assertIsInstance(exc.offset, int)

    def test_xml_1_1_rejected(self):
        exc = self.reject(b'<?xml version="1.1"?><a/>')
        self.assertIn("1.0", str(exc))

    def test_xml_2_0_rejected(self):
        # reject() itself asserts that XMLValidationError is raised.
        self.reject(b'<?xml version="2.0"?><a/>')

    def test_utf8_encoding_declaration_accepted(self):
        for enc in (b"UTF-8", b"utf-8"):
            compute_digests(
                b'<?xml version="1.0" encoding="' + enc + b'"?><a/>',
                b'[{"path":[],"prefixes":[]}]',
            )

    def test_relative_namespace_uri_rejected(self):
        exc = self.reject(b'<a xmlns:p="relative/path"><p:b/></a>')
        self.assertIn("relative namespace URI", str(exc))

    def test_relative_default_namespace_rejected(self):
        with self.assertRaises(XMLValidationError):
            compute_digests(
                b'<a xmlns="not/a/uri"/>',
                b'[{"path":[],"prefixes":[]}]',
            )

    def test_absolute_uri_accepted(self):
        compute_digests(
            b'<a xmlns:p="urn:x" xmlns="http://e.io/n"><b/></a>',
            b'[{"path":[0],"prefixes":[]}]',
        )

    def test_unbound_prefix_rejected(self):
        with self.assertRaises(XMLValidationError):
            compute_digests(
                b"<a><p:b/></a>",
                b'[{"path":[],"prefixes":[]}]',
            )

    def test_xml_prefix_rebinding_rejected(self):
        with self.assertRaises(XMLValidationError):
            compute_digests(
                b'<a xmlns:xml="urn:other"><xml:b/></a>',
                b'[{"path":[],"prefixes":[]}]',
            )

    def test_empty_prefixed_binding_rejected(self):
        with self.assertRaises(XMLValidationError):
            compute_digests(
                b'<a xmlns:p=""><b/></a>',
                b'[{"path":[],"prefixes":[]}]',
            )

    def test_duplicate_namespace_declaration_rejected(self):
        with self.assertRaises(XMLValidationError):
            compute_digests(
                b'<a xmlns:p="urn:x" xmlns:p="urn:y"/>',
                b'[{"path":[],"prefixes":[]}]',
            )

    def test_duplicate_attribute_after_expansion_rejected(self):
        # Two prefixes bound to the same URI cannot both carry an attribute
        # of the same local name (Namespaces 1.0 uniqueness on expanded
        # names).  Unprefixed "x" and p:x do NOT collide, because
        # unprefixed attributes have no namespace name.
        with self.assertRaises(XMLValidationError):
            compute_digests(
                b'<a xmlns:p="urn:x" xmlns:q="urn:x" p:x="1" q:x="2"/>',
                b'[{"path":[],"prefixes":[]}]',
            )

    def test_unprefixed_and_prefixed_same_local_are_distinct(self):
        results = compute_digests(
            b'<a xmlns:p="urn:x" x="1" p:x="2"/>',
            b'[{"path":[],"prefixes":[]}]',
        )
        self.assertEqual(len(results), 1)

    def test_malformed_xml_rejected_with_byte_offset(self):
        try:
            compute_digests(
                b"<a><b></a>",
                b'[{"path":[],"prefixes":[]}]',
                xml_name="doc.xml",
            )
        except XMLValidationError as exc:
            self.assertIn("doc.xml", str(exc))
            self.assertGreaterEqual(exc.offset, 0)
        else:
            self.fail("expected rejection")

    def test_external_entity_rejected(self):
        with self.assertRaises(XMLValidationError):
            compute_digests(
                b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
                b"<a>&x;</a>",
                b'[{"path":[],"prefixes":[]}]',
            )


# --- validation ordering --------------------------------------------------


class OrderingTests(unittest.TestCase):
    def test_json_checked_before_xml(self):
        # Both inputs are bad; the JSON error must win.
        with self.assertRaises(JSONSyntaxError):
            compute_digests(
                b"<not-closed>",
                b"[}",
                xml_name="doc.xml", selection_name="sel.json",
            )

    def test_schema_checked_before_xml(self):
        with self.assertRaises(SelectionError):
            compute_digests(
                b"<not-closed>",
                b'[{"path":{}}]',
            )

    def test_xml_checked_before_paths(self):
        # Valid XML with an unresolvable path: the XML stage must pass
        # first and only then does path resolution fail (as a selection
        # error), proving the stage ordering.
        with self.assertRaises(SelectionError):
            compute_digests(
                b"<a/>",
                b'[{"path":[99],"prefixes":[]}]',
            )

    def test_bad_xml_and_bad_path_reports_xml_first(self):
        # When both stages are invalid the XML error must surface, not the
        # path error, because XML validation precedes path resolution.
        with self.assertRaises(XMLValidationError):
            compute_digests(
                b"<a><b></a>",
                b'[{"path":[99],"prefixes":[]}]',
            )


# --- path resolution ------------------------------------------------------


class PathTests(unittest.TestCase):
    XML = b"<a><b/><b><c/></b><b/></a>"

    def select(self, data, path):
        return compute_digests(
            data,
            json.dumps(
                [{"path": path, "prefixes": []}]
            ).encode(),
        )

    def test_root_selection(self):
        out = self.select(self.XML, [])
        self.assertEqual(len(out), 1)

    def test_element_child_indexing_ignores_text(self):
        out = self.select(b"<a>x<!--c--><b/><?pi d?>y</a>", [0])
        self.assertEqual(
            base64.b64decode(out[0]["base64"]), b"<b></b>"
        )

    def test_out_of_range_path_rejected_as_selection_error(self):
        with self.assertRaises(SelectionError) as ctx:
            self.select(self.XML, [3])
        # Reported with the JSON Pointer of the entry and the failing
        # zero-based path depth.
        self.assertEqual(ctx.exception.json_path, "/0/path")
        self.assertEqual(ctx.exception.index, 0)

    def test_second_entry_path_error_locates_entry(self):
        data = json.dumps([
            {"path": [], "prefixes": []},
            {"path": [5], "prefixes": []},
        ]).encode()
        with self.assertRaises(SelectionError) as ctx:
            compute_digests(self.XML, data, selection_name="sel.json")
        self.assertEqual(ctx.exception.json_path, "/1/path")
        self.assertEqual(ctx.exception.index, 0)
        self.assertIn("sel.json", str(ctx.exception))

    def test_nested_path_error_reports_failing_depth(self):
        with self.assertRaises(SelectionError) as ctx:
            self.select(self.XML, [1, 9])
        self.assertEqual(ctx.exception.json_path, "/0/path")
        self.assertEqual(ctx.exception.index, 1)

    def test_deep_path(self):
        out = self.select(self.XML, [1, 0])
        self.assertEqual(
            base64.b64decode(out[0]["base64"]), b"<c></c>"
        )


# --- digest output --------------------------------------------------------


class DigestOutputTests(unittest.TestCase):
    def test_empty_selection_array_is_empty_result(self):
        self.assertEqual(compute_digests(b"<a/>", b"[]"), [])

    def test_base64_is_padded_and_sha256_lowercase_hex(self):
        results = compute_digests(
            b"<a/>", b'[{"path":[],"prefixes":[]}]'
        )
        entry = results[0]
        raw = base64.b64decode(entry["base64"], validate=True)
        self.assertEqual(raw, b"<a></a>")
        # Padded standard base64 ends with '=' for this 7-byte payload.
        self.assertTrue(entry["base64"].endswith("="))
        self.assertEqual(
            entry["sha256"], hashlib.sha256(raw).hexdigest()
        )
        self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")

    def test_repeated_selections_produce_repeated_entries(self):
        results = compute_digests(
            b"<a><b/></a>",
            b'[{"path":[0],"prefixes":[]},'
            b'{"path":[0],"prefixes":[]}]',
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])

    def test_order_matches_selection_order(self):
        results = compute_digests(
            b"<a><b/><c/></a>",
            b'[{"path":[1],"prefixes":[]},'
            b'{"path":[0],"prefixes":[]}]',
        )
        self.assertEqual(
            base64.b64decode(results[0]["base64"]), b"<c></c>"
        )
        self.assertEqual(
            base64.b64decode(results[1]["base64"]), b"<b></b>"
        )

    def test_non_ascii_utf8_round_trip(self):
        results = compute_digests(
            "<a>Ω≈ç√</a>".encode("utf-8"),
            b'[{"path":[],"prefixes":[]}]',
        )
        self.assertIn("Ω".encode("utf-8"),
                      base64.b64decode(results[0]["base64"]))


# --- CLI atomic replacement ----------------------------------------------


class CLIAtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="digest-cli-")
        self.xml = os.path.join(self.tmp, "doc.xml")
        self.sel = os.path.join(self.tmp, "sel.json")
        self.out = os.path.join(self.tmp, "out.json")
        with open(self.xml, "wb") as f:
            f.write(b"<a><b/></a>")
        with open(self.sel, "wb") as f:
            f.write(b'[{"path":[0],"prefixes":[]}]')

    def _run_cli(self, *args):
        from xml_digest.cli import main
        return main(list(args))

    def test_success_replaces_output(self):
        with open(self.out, "wb") as f:
            f.write(b"OLD CONTENT")
        code = self._run_cli(self.xml, self.sel, self.out)
        self.assertEqual(code, 0)
        with open(self.out, "rb") as f:
            payload = f.read()
        data = json.loads(payload)
        self.assertEqual(
            base64.b64decode(data[0]["base64"]), b"<b></b>"
        )

    def test_failure_preserves_old_file(self):
        with open(self.out, "wb") as f:
            f.write(b"PRECIOUS")
        # Break the selection so the pipeline aborts before writing.
        with open(self.sel, "wb") as f:
            f.write(b"[}")
        code = self._run_cli(self.xml, self.sel, self.out)
        self.assertEqual(code, 1)
        with open(self.out, "rb") as f:
            self.assertEqual(f.read(), b"PRECIOUS")

    def test_no_temp_files_left_behind(self):
        self._run_cli(self.xml, self.sel, self.out)
        leftovers = [
            n for n in os.listdir(self.tmp)
            if n.startswith(".xml-digest-")
        ]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
