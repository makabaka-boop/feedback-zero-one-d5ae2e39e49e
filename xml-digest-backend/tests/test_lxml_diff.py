"""Differential testing against lxml's canonical serializer.

lxml 5 ships only C14N 2.0 (the older node-set based C14N 1.0 API was
removed), and libxml2 cannot canonicalize a detached subtree whose
namespace declarations live on an ancestor.  The differential comparison
is therefore restricted to the *legal intersection*:

* whole-document canonicalization where every namespace used by the
  subtree is declared inside the subtree itself (or on the subtree root,
  which lxml can see);
* input accepted by both parsers;
* scenarios in which C14N 2.0 and exclusive C14N 1.0 produce identical
  bytes (the known divergence -- an empty default-namespace reset -- is
  excluded here and covered by dedicated fixture tests instead).

The renderer's exclusive semantics on inherited/rebound namespaces and the
2010 erratum are pinned by hand-written byte fixtures in
``test_fixtures.py``.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
VENDOR = "/tmp/vendor/pylib"
if os.path.isdir(VENDOR):
    sys.path.insert(0, VENDOR)

from xml_digest.c14n import canonicalize  # noqa: E402
from xml_digest.xml_model import parse_xml  # noqa: E402

try:
    from lxml import etree as lxml_etree
    LXML_AVAILABLE = True
except ImportError:  # pragma: no cover
    LXML_AVAILABLE = False

from _helpers import normalize_to_c14n1 as _normalize_to_c14n1  # noqa: E402


# Each entry is an XML document; the test canonicalizes both the whole
# document and every element subtree (when lxml can see all needed
# declarations, i.e. subtrees without ancestor-only namespaces).
DOCUMENTS = [
    b"<a><b>text</b></a>",
    b'<a xmlns:p="urn:x"><p:b>text</p:b></a>',
    b'<a xmlns:p="urn:x"><p:b p:z="1" w="2">x</p:b></a>',
    b"<a><b/><c></c></a>",
    b"<a>x&amp;y&lt;z>w</a>",
    b'<a xmlns="urn:d"><b>c</b><d e="f"/></a>',
    b'<a xmlns:p="urn:x" xmlns:q="urn:q"><p:b q:a="1" p:b="2" c="3"/>'
    b"</a>",
    b"<a>line1&#13;line2&#10;tab&#9;end</a>",
    b'<a t="x&#13;y" z="a&#x9;b&#xA;c">v</a>',
    b"<a><?appl data?>text<?pi?></a>",
    b'<a xmlns:p="urn:x"><!-- a comment --><p:b/></a>',
    b"<a><b>  preserved  whitespace  </b></a>",
    b'<a xmlns:p="urn:x"><p:b><p:c><p:d/></p:c></p:b></a>',
    b'<a xmlns:p="urn:x" xmlns:q="urn:q"><p:b><q:c/></p:b></a>',
    b'<a b="1" a="2" z="3"/>',
    b'<a xmlns:p="urn:x" p:a="1" xmlns:q="urn:q" q:b="2" c="3"/>',
    b"<a><![CDATA[2 < 3 & 5]]></a>",
    b'<a xml:lang="en" xml:id="i1">v</a>',
    b"<a>pre&#xD;post&#xD;&#xA;end</a>",
    b'<a xmlns="urn:d" xmlns:p="urn:x"><b p:a="1"/></a>',
    b'<a xmlns:p="urn:x"><p:b xmlns:p="urn:y"><p:c/></p:b></a>',
    b'<a xmlns:p="urn:x"><b xmlns:p="urn:y"></b><p:z/></a>',
    b"<a><b>t1</b><b>t2</b><b>t3</b></a>",
    b'<a xmlns:p="urn:x"><p:b q="&#x9;&#xA;&#xD;"/></a>',
    b"<a>mixed <b>bold</b> tail</a>",
    b'<a xmlns="urn:d1"><b xmlns="urn:d2">c</b></a>',
]


def _lxml_canonical(xml_bytes):
    tree = lxml_etree.fromstring(xml_bytes)
    data = lxml_etree.canonicalize(xml_data=tree).encode("utf-8")
    return _normalize_to_c14n1(data)


def _lxml_subtree_ok(xml_bytes):
    """Return lxml subtree canonical bytes for every element, if possible."""
    tree = lxml_etree.fromstring(xml_bytes)
    out = []

    def walk(element):
        try:
            raw = lxml_etree.canonicalize(xml_data=element).encode("utf-8")
        except ValueError:
            # Namespace declared on an ancestor: lxml cannot handle a
            # detached subtree; the renderer-specific behavior is covered
            # by byte fixtures instead.
            return
        out.append(_normalize_to_c14n1(raw))
        for child in element:
            walk(child)

    walk(tree)
    return out


@unittest.skipUnless(LXML_AVAILABLE, "lxml not installed")
class LXMLDifferentialTests(unittest.TestCase):
    def _assert_doc(self, doc):
        expected = _lxml_canonical(doc)
        root = parse_xml(doc, "diff.xml")
        got = canonicalize(root, ())
        self.assertEqual(
            got, expected,
            "\n  input: %r\n  got:  %r\n  want: %r"
            % (doc, got, expected),
        )

    def test_whole_documents_match_lxml(self):
        for doc in DOCUMENTS:
            with self.subTest(doc=doc):
                self._assert_doc(doc)

    def test_self_contained_subtrees_match_lxml(self):
        for doc in DOCUMENTS:
            root = parse_xml(doc, "diff.xml")
            lxml_parts = _lxml_subtree_ok(doc)

            ours = []

            def walk(element):
                ours.append(canonicalize(element, ()))
                from xml_digest.xml_model import Element
                for child in element.children:
                    if isinstance(child, Element):
                        walk(child)

            walk(root)
            # lxml_parts may be shorter when ancestor-namespaced subtrees
            # were skipped; compare the aligned prefix it could produce.
            self.assertEqual(
                ours[: len(lxml_parts)], lxml_parts,
                "subtree mismatch for %r" % doc,
            )

    def test_attribute_ordering_matches_lxml(self):
        doc = (
            b'<r xmlns:a="urn:a" xmlns:b="urn:b" xmlns:c="urn:c">'
            b'<e c:z="1" a:y="2" b:x="3" plain="4" a:a="5"/></r>'
        )
        root = parse_xml(doc, "diff.xml")
        from xml_digest.xml_model import Element
        target = [c for c in root.children if isinstance(c, Element)][0]
        got = canonicalize(target, ())
        want = _normalize_to_c14n1(
            lxml_etree.canonicalize(
                xml_data=lxml_etree.fromstring(
                    b'<e xmlns:a="urn:a" xmlns:b="urn:b" xmlns:c="urn:c" '
                    b'c:z="1" a:y="2" b:x="3" plain="4" a:a="5"/>'
                )
            ).encode("utf-8")
        )
        self.assertEqual(got, want)

    def test_fuzz_like_generated_trees(self):
        # Randomized but deterministic generation of namespace-heavy trees
        # that remain self-contained (every prefix is declared on the same
        # element that first uses it, so lxml can compare subtrees too).
        import random
        rng = random.Random(20261113)
        uris = ["urn:alpha", "urn:beta", "urn:gamma", "http://ex.io/ns"]
        for iteration in range(120):
            doc = _build_self_contained(rng, uris)
            with self.subTest(iteration=iteration, doc=doc):
                expected = _lxml_canonical(doc)
                root = parse_xml(doc, "fuzz.xml")
                self.assertEqual(canonicalize(root, ()), expected)


def _build_self_contained(rng, uris):
    """Build a self-contained namespace-heavy XML document.

    Two C14N 2.0 vs exclusive-C14N-1.0 divergence zones are deliberately
    avoided because this differential test compares byte equality:

    * one namespace URI is ever bound to exactly one prefix across the
      whole document (C14N 2.0 may otherwise rewrite the author's prefix,
      even across disjoint sibling scopes, whereas exclusive 1.0 always
      preserves the source qname);
    * a prefix is never rebound to another URI here (covered by byte
      fixtures instead).
    """
    parts = []
    depth = rng.randint(1, 4)
    # Document-wide URI -> prefix ownership.
    global_owner = {}

    def descend(level, available):
        available = dict(available)
        prefixes = list(available)
        use_prefixed = rng.choice([True, False])
        if use_prefixed and prefixes:
            pre = rng.choice(prefixes)
            name = "%s:e%d" % (pre, level)
        else:
            name = "e%d" % level
        decl = ""
        if rng.random() < 0.6:
            pre = "p%d" % rng.randint(0, 99)
            uri = rng.choice(uris)
            if pre not in available and uri not in global_owner:
                decl = ' xmlns:%s="%s"' % (pre, uri)
                available[pre] = uri
                global_owner[uri] = pre
        attrs = []
        seen = set()
        for _ in range(rng.randint(0, 3)):
            if available and rng.random() < 0.5:
                pre = rng.choice(list(available))
                key = (pre, "k%d" % rng.randint(0, 9999))
            else:
                key = (None, "k%d" % rng.randint(0, 9999))
            if key in seen:
                continue
            seen.add(key)
            if key[0] is None:
                attrs.append('%s="v"' % key[1])
            else:
                attrs.append('%s:%s="v"' % key)
        attr_text = (" " + " ".join(attrs)) if attrs else ""
        parts.append("<%s%s%s>" % (name, decl, attr_text))
        if level < depth:
            for _ in range(rng.randint(0, 3)):
                descend(level + 1, available)
        else:
            parts.append("txt%d" % rng.randint(0, 99))
        parts.append("</%s>" % name)

    descend(0, {})
    return "".join(parts).encode("utf-8")


if __name__ == "__main__":
    unittest.main(verbosity=2)
