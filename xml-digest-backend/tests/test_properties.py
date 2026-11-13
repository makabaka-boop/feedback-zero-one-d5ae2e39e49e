"""Property-style randomized tests.

Two invariants are hammered with deterministic seeds:

1. For self-contained documents in the legal C14N-1.0/C14N-2.0
   intersection, our canonical bytes equal lxml's after normalizing the
   single known escape difference (``>`` in text).
2. For a stream of randomly assembled (often malformed) documents, our
   parser's accept/reject decision matches lxml's decision on the
   Namespaces 1.0 / XML 1.0 rules that both implement (our extra project
   rules are excluded from generation).
"""

import os
import random
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
from xml_digest.errors import XMLValidationError  # noqa: E402

try:
    from lxml import etree as lxml_etree
    LXML_AVAILABLE = True
except ImportError:  # pragma: no cover
    LXML_AVAILABLE = False

from _helpers import normalize_to_c14n1  # noqa: E402

TEXT_CHUNKS = [
    "plain", "a & b < c > d", "line1\rline2", "tab\there",
    "Ω≈ç√", "混合 文本", "&amp;", "<![CDATA[raw < & >]]>",
    "  spaces  ", "quote\"apost'", "\n\n", "x]]>y",
]
ATTR_CHUNKS = [
    "v", "a&b", "tab\t", "nl\n", "cr\rz", 'say "hi"',
    "unicode λ", "1<2",
]
URIS = ["urn:a", "urn:b", "http://x.io/n", "https://y.io/m"]


def _text(rng):
    n = rng.randint(0, 2)
    return "".join(rng.choice(TEXT_CHUNKS) for _ in range(n))


@unittest.skipUnless(LXML_AVAILABLE, "lxml not installed")
class CanonicalPropertyTests(unittest.TestCase):
    def _build(self, rng):
        parts = []
        global_uri_owner = {}

        def descend_state(level, available, global_uri_owner):
            available = dict(available)
            tag = "e%d" % level
            if available and rng.random() < 0.5:
                pre = rng.choice(list(available))
                tag = "%s:e%d" % (pre, level)
            decls = []
            if rng.random() < 0.5:
                pre = "p%d" % rng.randint(0, 49)
                uri = rng.choice(URIS)
                if pre not in available and uri not in global_uri_owner:
                    decls.append((pre, uri))
                    available[pre] = uri
                    global_uri_owner[uri] = pre
            attrs = []
            seen = set()
            for _ in range(rng.randint(0, 3)):
                if available and rng.random() < 0.5:
                    pre = rng.choice(list(available))
                    key = (pre, "k%d" % rng.randint(0, 9))
                else:
                    key = (None, "k%d" % rng.randint(0, 9))
                if key in seen:
                    continue
                seen.add(key)
                val = rng.choice(ATTR_CHUNKS)
                if key[0] is None:
                    attrs.append('%s="%s"' % (key[1], val))
                else:
                    attrs.append('%s:%s="%s"' % (key[0], key[1], val))
            decl_text = "".join(
                ' xmlns:%s="%s"' % d for d in decls
            )
            attr_text = (" " + " ".join(attrs)) if attrs else ""
            parts.append("<%s%s%s>" % (tag, decl_text, attr_text))
            if rng.random() < 0.7:
                parts.append(_text(rng))
            if level < rng.randint(1, 4):
                for _ in range(rng.randint(0, 3)):
                    descend_state(level + 1, available, global_uri_owner)
            if rng.random() < 0.3:
                parts.append("<?pi%d data?>" % rng.randint(0, 5))
            parts.append("</%s>" % tag)

        descend_state(0, {}, global_uri_owner)
        return "".join(parts).encode("utf-8")

    def test_random_documents_match_lxml(self):
        rng = random.Random(99173)
        checked = 0
        attempts = 0
        # Keep going until enough well-formed documents were compared;
        # the text generator can deliberately produce raw &/< that makes
        # a draw invalid, and those are simply skipped.
        while checked < 200 and attempts < 3000:
            attempts += 1
            doc = self._build(rng)
            try:
                tree = lxml_etree.fromstring(doc)
            except lxml_etree.XMLSyntaxError:
                continue
            want = normalize_to_c14n1(
                lxml_etree.canonicalize(xml_data=tree).encode("utf-8")
            )
            root = parse_xml(doc, "prop.xml")
            got = canonicalize(root, ())
            self.assertEqual(
                got, want,
                "\ninput: %r\ngot:  %r\nwant: %r" % (doc, got, want),
            )
            checked += 1
        self.assertGreaterEqual(checked, 200)


@unittest.skipUnless(LXML_AVAILABLE, "lxml not installed")
class DecisionParityTests(unittest.TestCase):
    """Accept/reject parity for namespace well-formedness rules."""

    TEMPLATES = [
        '<a xmlns:p="{u}"><p:b/></a>',
        '<a><{p}:b/></a>',
        '<a xmlns:{p}="{u}"/>',
        '<a xmlns:p="{u}" {p2}:x="1"/>',
        '<a xmlns="{u}"><b/></a>',
        '<a xmlns:p="{u}"><b xmlns:p="{u2}"/></a>',
    ]

    def test_parity(self):
        rng = random.Random(4242)
        names = ["p", "q", "xml", "xmlns", "1bad", "ok", "_z", "a:b"]
        uris = ["urn:x", "rel/path", "http://e.io", "", "urn:y"]
        mismatches = 0
        total = 0
        for _ in range(600):
            tpl = rng.choice(self.TEMPLATES)
            doc = tpl.format(
                p=rng.choice(names), p2=rng.choice(names),
                u=rng.choice(uris), u2=rng.choice(uris),
            ).encode()
            total += 1
            try:
                parse_xml(doc, "d")
                mine_ok = True
                mine_msg = ""
            except XMLValidationError as exc:
                mine_ok = False
                mine_msg = exc.message
            try:
                lxml_etree.fromstring(doc)
                lxml_ok = True
            except lxml_etree.XMLSyntaxError:
                lxml_ok = False
            if mine_ok != lxml_ok:
                mismatches += 1
                # The relative-URI rejection is a project-specific rule on
                # top of Namespaces 1.0; lxml has no equivalent, so those
                # draws are expected to diverge and do not count.
                if "relative namespace URI" not in mine_msg:
                    self.fail(
                        "parity mismatch %r mine=%s(%s) lxml=%s"
                        % (doc, mine_ok, mine_msg, lxml_ok)
                    )
        self.assertGreater(total, 500)


if __name__ == "__main__":
    unittest.main(verbosity=2)
