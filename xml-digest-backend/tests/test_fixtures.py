"""Exact-byte fixtures for the exclusive canonicalization semantics.

These cases deliberately target behavior that cannot be cross-checked with
the bundled lxml build (C14N 2.0 diverges from exclusive C14N 1.0 around
inherited declarations, prefix rebinding and the 2010 default-namespace
erratum).  Every expected byte string is derived from the W3C exclusive
c14n rules and kept in this file as an independent, readable oracle.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from xml_digest.c14n import canonicalize  # noqa: E402
from xml_digest.xml_model import parse_xml  # noqa: E402


def canon(doc, path=(), prefixes=()):
    root = parse_xml(doc, "fixture.xml")
    node = root
    for index in path:
        from xml_digest.xml_model import Element
        node = [c for c in node.children if isinstance(c, Element)][index]
    return canonicalize(node, prefixes)


class SpecExampleTests(unittest.TestCase):
    def test_worked_example_from_specification(self):
        # <a xmlns:p="urn:x"><p:b/></a>, select [0], prefixes=[]
        self.assertEqual(
            canon(b'<a xmlns:p="urn:x"><p:b/></a>', path=(0,)),
            b'<p:b xmlns:p="urn:x"></p:b>',
        )

    def test_no_trailing_newline_or_declaration(self):
        out = canon(b'<?xml version="1.0"?><a/>')
        self.assertFalse(out.endswith(b"\n"))
        self.assertEqual(out, b"<a></a>")


class ExclusiveNamespaceTests(unittest.TestCase):
    def test_unused_ancestor_declaration_suppressed_then_reemitted(self):
        doc = b'<a xmlns:p="urn:x"><b><p:c/></b></a>'
        self.assertEqual(canon(doc), b"<a><b><p:c xmlns:p=\"urn:x\"></p:c>"
                                      b"</b></a>")

    def test_unused_declaration_on_used_element_suppressed(self):
        doc = b'<a xmlns:p="urn:x" xmlns:q="urn:q"><b/></a>'
        self.assertEqual(canon(doc), b"<a><b></b></a>")

    def test_inherited_binding_not_restated(self):
        # The root element does not use p, so the declaration moves to the
        # first element that does; descendants then inherit it silently.
        doc = b'<a xmlns:p="urn:x"><p:b><p:c/></p:b></a>'
        self.assertEqual(
            canon(doc),
            b'<a><p:b xmlns:p="urn:x"><p:c></p:c></p:b></a>',
        )

    def test_rebinding_prefix_to_new_uri(self):
        # b does not use p, so the rebinding is emitted at the first point
        # of use (p:c), where the in-scope URI is urn:y.
        doc = b'<a xmlns:p="urn:x"><b xmlns:p="urn:y"><p:c/></b></a>'
        self.assertEqual(
            canon(doc),
            b'<a><b><p:c xmlns:p="urn:y"></p:c></b></a>',
        )

    def test_rebinding_ends_at_element_boundary(self):
        doc = b'<a xmlns:p="urn:x"><b xmlns:p="urn:y"/><p:c/></a>'
        self.assertEqual(
            canon(doc),
            b'<a><b></b><p:c xmlns:p="urn:x"></p:c></a>',
        )

    def test_author_prefix_preserved_when_two_prefixes_share_uri(self):
        # Exclusive 1.0 keeps the source qname; only the needed binding is
        # emitted at the point of use.
        doc = b'<a xmlns:p="urn:x"><b xmlns:q="urn:x"><q:c/></b></a>'
        self.assertEqual(
            canon(doc),
            b'<a><b><q:c xmlns:q="urn:x"></q:c></b></a>',
        )

    def test_subtree_root_reconstructs_ancestor_namespace(self):
        # Selecting a detached subtree must still emit the inherited
        # binding it visibly uses.
        doc = b'<a xmlns:p="urn:x"><b><p:c/></b></a>'
        self.assertEqual(
            canon(doc, path=(0, 0)),
            b'<p:c xmlns:p="urn:x"></p:c>',
        )

    def test_namespace_declarations_sorted_by_prefix(self):
        doc = (b'<a xmlns:z="urn:z" xmlns:a="urn:a" a:x="1" z:y="2"/>')
        self.assertEqual(
            canon(doc),
            b'<a xmlns:a="urn:a" xmlns:z="urn:z" a:x="1" z:y="2"></a>',
        )

    def test_attributes_sorted_by_uri_then_localname(self):
        doc = (
            b'<a xmlns:c="urn:c" xmlns:a="urn:a" xmlns:b="urn:b" '
            b'c:z="1" a:y="2" b:x="3" plain="4" a:a="5"/>'
        )
        self.assertEqual(
            canon(doc),
            b'<a xmlns:a="urn:a" xmlns:b="urn:b" xmlns:c="urn:c" '
            b'plain="4" a:a="5" a:y="2" b:x="3" c:z="1"></a>',
        )

    def test_prefixed_attribute_emits_its_own_binding(self):
        doc = b'<a><b xmlns:p="urn:x" p:k="v"/></a>'
        self.assertEqual(
            canon(doc, path=(0,)),
            b'<b xmlns:p="urn:x" p:k="v"></b>',
        )

    def test_prefix_inside_attribute_value_is_not_usage(self):
        doc = b'<a xmlns:p="urn:x"><b r="p:not-a-usage"/></a>'
        self.assertEqual(canon(doc), b'<a><b r="p:not-a-usage"></b></a>')


class DefaultNamespaceTests(unittest.TestCase):
    def test_default_namespace_used_only_on_demand(self):
        doc = b'<a xmlns="urn:d"><b>c</b></a>'
        self.assertEqual(
            canon(doc), b'<a xmlns="urn:d"><b>c</b></a>'
        )

    def test_2010_erratum_empty_default_reset(self):
        doc = b'<a xmlns="urn:d"><b xmlns="">c</b></a>'
        self.assertEqual(
            canon(doc), b'<a xmlns="urn:d"><b xmlns="">c</b></a>'
        )

    def test_empty_default_reset_in_subtree(self):
        # As a detached subtree nothing above c ever rendered a default
        # binding, so the empty reset has no observable effect and is
        # omitted (the erratum reset matters only inside a full document).
        doc = b'<a xmlns="urn:d"><b><c xmlns="">x</c></b></a>'
        self.assertEqual(
            canon(doc, path=(0, 0)), b"<c>x</c>"
        )

    def test_empty_default_reset_inside_full_document(self):
        # Same document serialized from the root: the non-empty default is
        # rendered on a, so c must visibly reset it.
        doc = b'<a xmlns="urn:d"><b><c xmlns="">x</c></b></a>'
        self.assertEqual(
            canon(doc),
            b'<a xmlns="urn:d"><b><c xmlns="">x</c></b></a>',
        )

    def test_default_namespace_rebinding(self):
        doc = b'<a xmlns="urn:d1"><b xmlns="urn:d2">c</b></a>'
        self.assertEqual(
            canon(doc),
            b'<a xmlns="urn:d1"><b xmlns="urn:d2">c</b></a>',
        )

    def test_unprefixed_attribute_ignores_default_namespace(self):
        doc = b'<a xmlns="urn:d" k="v"/>'
        out = canon(doc)
        self.assertIn(b'k="v"', out)


class InclusivePrefixListTests(unittest.TestCase):
    def test_inclusive_prefix_forces_declaration_at_subtree_root(self):
        doc = b'<a xmlns:p="urn:x" xmlns:q="urn:q"><b/></a>'
        self.assertEqual(
            canon(doc, path=(0,), prefixes=("p",)),
            b'<b xmlns:p="urn:x"></b>',
        )

    def test_inclusive_prefix_not_repeated_when_already_rendered(self):
        doc = b'<a xmlns:p="urn:x"><p:b><c/></p:b></a>'
        self.assertEqual(
            canon(doc, prefixes=("p",)),
            b'<a xmlns:p="urn:x"><p:b><c></c></p:b></a>',
        )

    def test_unbound_inclusive_token_ignored(self):
        doc = b'<a><b/></a>'
        self.assertEqual(
            canon(doc, path=(0,), prefixes=("ghost",)), b"<b></b>"
        )

    def test_default_inclusive_token(self):
        doc = b'<a xmlns="urn:d"><b/></a>'
        self.assertEqual(
            canon(doc, path=(0,), prefixes=("#default",)),
            b'<b xmlns="urn:d"></b>',
        )

    def test_inclusive_follows_rebinding(self):
        doc = b'<a xmlns:p="urn:x"><b xmlns:p="urn:y"><c/></b></a>'
        self.assertEqual(
            canon(doc, prefixes=("p",)),
            b'<a xmlns:p="urn:x"><b xmlns:p="urn:y"><c></c></b></a>',
        )

    def test_multiple_inclusive_prefixes_sorted(self):
        doc = b'<a xmlns:a="urn:a" xmlns:b="urn:b"><c/></a>'
        self.assertEqual(
            canon(doc, path=(0,), prefixes=("b", "a")),
            b'<c xmlns:a="urn:a" xmlns:b="urn:b"></c>',
        )


class TextEscapingAndNodeTests(unittest.TestCase):
    def test_carriage_return_always_charref(self):
        self.assertEqual(
            canon(b"<a>x&#13;y</a>"), b"<a>x&#xD;y</a>"
        )
        self.assertEqual(
            canon(b"<a>x&#13;&#10;y</a>"), b"<a>x&#xD;\ny</a>"
        )

    def test_text_escapes_amp_and_lt_but_not_gt(self):
        self.assertEqual(
            canon(b"<a>x &amp; y &lt; z > w</a>"),
            b"<a>x &amp; y &lt; z > w</a>",
        )

    def test_attribute_escapes(self):
        self.assertEqual(
            canon(b'<a t="a&amp;b&lt;c&quot;d&#9;e&#10;f&#13;g"/>'),
            b'<a t="a&amp;b&lt;c&quot;d&#x9;e&#xA;f&#xD;g"></a>',
        )

    def test_cdata_becomes_escaped_text(self):
        self.assertEqual(
            canon(b"<a><![CDATA[2 < 3 & 5]]></a>"),
            b"<a>2 &lt; 3 &amp; 5</a>",
        )

    def test_comments_are_absent(self):
        self.assertEqual(
            canon(b"<a><!-- drop me --><b/><!-- also --></a>"),
            b"<a><b></b></a>",
        )

    def test_processing_instructions_preserved(self):
        self.assertEqual(
            canon(b"<a><?appl data?>x<?pi?></a>"),
            b"<a><?appl data?>x<?pi?></a>",
        )

    def test_empty_element_expanded(self):
        self.assertEqual(canon(b"<a><b/></a>"), b"<a><b></b></a>")

    def test_whitespace_preserved(self):
        self.assertEqual(
            canon(b"<a>  <b>  x  </b>  </a>"),
            b"<a>  <b>  x  </b>  </a>",
        )

    def test_predefined_entities_round_trip(self):
        # In text nodes only & and < (plus CR) are escaped; >, " and '
        # stay literal.
        self.assertEqual(
            canon(b"<a>&amp;&lt;&gt;&quot;&apos;</a>"),
            b"<a>&amp;&lt;>\"'</a>",
        )

    def test_xml_prefix_never_redeclared(self):
        self.assertEqual(
            canon(b'<a xml:lang="en" xml:id="i"/>'),
            b'<a xml:id="i" xml:lang="en"></a>',
        )

    def test_utf8_multibyte_content_preserved(self):
        doc = '<a xmlns:p="urn:λ">中文<p:b>Ω</p:b></a>'.encode("utf-8")
        out = canon(doc)
        self.assertIn("中文".encode("utf-8"), out)
        self.assertIn("Ω".encode("utf-8"), out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
