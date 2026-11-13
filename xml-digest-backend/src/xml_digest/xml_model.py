"""Restricted XML parsing and the in-memory document model.

The parser is built on :mod:`xml.parsers.expat` running *without* expat's
own namespace processing.  Expat therefore retains the original prefixed
qualified names (which exclusive canonicalization must preserve) while
still enforcing XML 1.0 (fifth edition) well-formedness.  All Namespaces
1.0 resolution and validation are implemented in this module on top of the
raw name events:

* prefix bindings are maintained as an explicit stack, including the
  implicit ``xml`` prefix and per-element ``xmlns`` declarations;
* unbound prefixes, malformed qualified names, undeclaration of a non-empty
  prefix, misuse of the reserved ``xml``/``xmlns`` prefixes and relative
  namespace names are all rejected;
* the project-specific restrictions (no DOCTYPE, no external entities,
  UTF-8-only encoding declarations) are enforced as well.
"""

import re

from xml.parsers import expat

from .errors import XMLValidationError

_XML_URI = "http://www.w3.org/XML/1998/namespace"
_XMLNS_URI = "http://www.w3.org/2000/xmlns/"

# RFC 3986 section 3.1: scheme = ALPHA *( ALPHA / DIGIT / "+" / "-" / "." )
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+\-.]*:")

# Matches the XML text declaration when present on the document entity,
# capturing the version and encoding pseudo-attribute values.  Only used
# to reject non-1.0 versions and non-UTF-8 encodings with precise
# locations; expat performs the real byte-level decoding.
_XML_DECL_RE = re.compile(
    rb"""<\?xml[ \t\r\n]+
         version\s*=\s*(['"])([A-Za-z0-9_.:\-]+)\1
         (?:[ \t\r\n]+encoding\s*=\s*(['"])([A-Za-z][-A-Za-z0-9._]*)\3)?""",
    re.VERBOSE,
)

# NCName ::= NameStartChar NameChar* (colon excluded).
_NAME_START = (
    (0x41, 0x5A), (0x5F, 0x5F), (0x61, 0x7A),
    (0xC0, 0xD6), (0xD8, 0xF6), (0xF8, 0x2FF),
    (0x370, 0x37D), (0x37F, 0x1FFF),
    (0x200C, 0x200D), (0x2070, 0x218F), (0x2C00, 0x2FEF),
    (0x3001, 0xD7FF), (0xF900, 0xFDCF), (0xFDF0, 0xFFFD),
    (0x10000, 0xEFFFF),
)
_NAME_CHAR = _NAME_START + (
    (0x2D, 0x2D), (0x2E, 0x2E), (0x30, 0x39),
    (0xB7, 0xB7), (0x300, 0x36F), (0x203F, 0x2040),
)


def _in_ranges(cp, ranges):
    return any(lo <= cp <= hi for lo, hi in ranges)


def _is_ncname(text):
    if not text:
        return False
    if not _in_ranges(ord(text[0]), _NAME_START):
        return False
    return all(_in_ranges(ord(ch), _NAME_CHAR) for ch in text[1:])


# --- tree model -----------------------------------------------------------


class Element:
    """An element node.

    ``decls`` holds explicit namespace declarations as
    ``(prefix_or_None, uri)`` pairs in source order.  ``prefix`` is the
    source prefix (``None`` for unprefixed names); ``uri`` the resolved
    namespace URI (``None`` for no namespace).
    """

    __slots__ = (
        "qname", "prefix", "local", "uri", "decls", "attributes",
        "children", "in_scope",
    )

    def __init__(self, qname, prefix, local, uri, decls, in_scope):
        self.qname = qname
        self.prefix = prefix
        self.local = local
        self.uri = uri
        self.decls = decls
        # Immutable snapshot of prefix -> URI bindings inside this element,
        # i.e. after its own declarations were applied.
        self.in_scope = in_scope
        # list of (prefix_or_None, local, uri_or_None, value)
        self.attributes = []
        # Element / Text / ProcessingInstruction nodes in document order
        self.children = []


class Text:
    __slots__ = ("data",)

    def __init__(self, data):
        self.data = data


class ProcessingInstruction:
    __slots__ = ("target", "data")

    def __init__(self, target, data):
        self.target = target
        self.data = data


class PathResolutionError(Exception):
    """A selection path leaves the element tree.

    Carries the zero-based depth (position within the path array) at
    which resolution failed; the pipeline translates this into a
    selection error with the appropriate JSON Pointer.
    """

    def __init__(self, message, depth):
        super().__init__(message)
        self.message = message
        self.depth = depth


_UNBOUND = object()


# --- parser ---------------------------------------------------------------


class _TreeBuilder:
    def __init__(self, data, input_name):
        self.data = data
        self.input_name = input_name
        self.parser = expat.ParserCreate()  # no namespace processing
        self.parser.ordered_attributes = True

        # Prefix (``None`` denotes the default namespace) -> URI.  The
        # implicit xml prefix is always present and must never be rebound.
        self._bindings = {"xml": _XML_URI}
        # Shadow stack of previous binding values, one entry per explicit
        # declaration, popped on element end.
        self._history = []
        self._stack = []
        self.root = None
        self._text_node = None

        p = self.parser
        p.StartElementHandler = self._on_start
        p.EndElementHandler = self._on_end
        p.CharacterDataHandler = self._on_text
        p.ProcessingInstructionHandler = self._on_pi
        p.CommentHandler = self._on_comment
        p.StartDoctypeDeclHandler = self._on_doctype
        p.ExternalEntityRefHandler = self._on_external_entity

    # -- helpers ---------------------------------------------------------

    def fail(self, message, offset=None):
        if offset is None:
            offset = self.parser.CurrentByteIndex
        raise XMLValidationError(message, offset, self.input_name)

    def _check_uri(self, uri):
        if not _SCHEME_RE.match(uri):
            self.fail("relative namespace URI %r is not allowed" % uri)

    def _split_qname(self, qname):
        # Namespaces 1.0: QName ::= (Prefix ':')? LocalPart, both NCNames,
        # at most one colon.
        parts = qname.split(":")
        if len(parts) > 2:
            self.fail("malformed qualified name %r" % qname)
        if len(parts) == 2:
            prefix, local = parts
            if not _is_ncname(prefix) or not _is_ncname(local):
                self.fail("malformed qualified name %r" % qname)
            if prefix == "xmlns":
                self.fail(
                    "the reserved prefix 'xmlns' must not be used on names"
                )
            return prefix, local
        if not _is_ncname(parts[0]):
            self.fail("malformed element name %r" % qname)
        return None, parts[0]

    def _resolve_prefix(self, prefix):
        if prefix is None:
            return None
        uri = self._bindings.get(prefix, _UNBOUND)
        if uri is _UNBOUND or uri == "":
            self.fail("unbound namespace prefix %r" % prefix)
        return uri

    # -- expat handlers --------------------------------------------------

    def _on_doctype(self, *args):
        self.fail("DOCTYPE declarations are not allowed")

    def _on_external_entity(self, context, base, system_id, public_id):
        self.fail("external entity references are not allowed")
        return False

    def _on_comment(self, data):
        # Comments are accepted input but never appear in the tree.
        self._text_node = None

    def _on_start(self, qname, flat_attrs):
        decls = []
        declared_here = set()
        # First pass: collect and validate the explicit declarations so
        # that names on this very element can resolve against them.
        for raw_name, value in zip(flat_attrs[0::2], flat_attrs[1::2]):
            if raw_name == "xmlns":
                if None in declared_here:
                    self.fail(
                        "duplicate default namespace declaration on one "
                        "element"
                    )
                declared_here.add(None)
                self._push_decl(None, value, decls)
            elif raw_name.startswith("xmlns:"):
                prefix = raw_name[6:]
                if not _is_ncname(prefix) or prefix == "xmlns":
                    self.fail(
                        "illegal namespace declaration %r" % raw_name
                    )
                if prefix in declared_here:
                    self.fail(
                        "duplicate namespace declaration for prefix %r on "
                        "one element" % prefix
                    )
                declared_here.add(prefix)
                if value == "":
                    self.fail(
                        "prefix %r cannot be bound to the empty namespace "
                        "name" % prefix
                    )
                self._push_decl(prefix, value, decls)

        prefix, local = self._split_qname(qname)
        if prefix is None:
            # Unprefixed elements take the default namespace when one is in
            # scope; otherwise they have no namespace name at all.
            uri = self._bindings.get(None) or None
        else:
            uri = self._resolve_prefix(prefix)

        element = Element(
            qname, prefix, local, uri, decls,
            # Full in-scope map after applying this element's declarations;
            # a fresh dict so later pushes cannot mutate this snapshot.
            dict(self._bindings),
        )

        # Second pass: ordinary attributes.
        seen_attrs = set()
        for raw_name, value in zip(flat_attrs[0::2], flat_attrs[1::2]):
            if raw_name == "xmlns" or raw_name.startswith("xmlns:"):
                continue
            attr_prefix, attr_local = self._split_qname(raw_name)
            if attr_prefix is None:
                # Unprefixed attributes are never in the default namespace.
                attr_uri = None
            else:
                attr_uri = self._resolve_prefix(attr_prefix)
            attr_key = (attr_uri, attr_local)
            if attr_key in seen_attrs:
                self.fail(
                    "duplicate attribute %r after namespace expansion"
                    % raw_name
                )
            seen_attrs.add(attr_key)
            element.attributes.append(
                (attr_prefix, attr_local, attr_uri, value)
            )

        if self._stack:
            self._stack[-1].children.append(element)
        else:
            self.root = element
        self._stack.append(element)
        self._text_node = None

    def _push_decl(self, prefix, uri, decls):
        if prefix == "xml":
            if uri != _XML_URI:
                self.fail(
                    "the reserved prefix 'xml' cannot be rebound to %r"
                    % uri
                )
            # Declaring xml with its canonical URI is harmless; it is kept
            # as an explicit declaration but never needs serialization.
        elif prefix is None and uri == _XMLNS_URI:
            # The xmlns namespace name may only ever be bound implicitly.
            self.fail("the reserved namespace name 'xmlns' is not allowed")
        # A prefixed xmlns:* declaration is already rejected in _on_start.
        if uri != "":
            self._check_uri(uri)
        self._history.append(self._bindings.get(prefix, _UNBOUND))
        self._bindings[prefix] = uri
        decls.append((prefix, uri))

    def _on_end(self, qname):
        element = self._stack.pop()
        for prefix, _ in reversed(element.decls):
            previous = self._history.pop()
            if previous is _UNBOUND:
                self._bindings.pop(prefix, None)
            else:
                self._bindings[prefix] = previous
        self._text_node = None

    def _on_text(self, data):
        if not self._stack:
            # Text outside the document element is discarded: the canonical
            # form never includes root-level whitespace.
            self._text_node = None
            return
        parent = self._stack[-1]
        if (
            self._text_node is not None
            and parent.children
            and parent.children[-1] is self._text_node
        ):
            self._text_node.data += data
        else:
            node = Text(data)
            parent.children.append(node)
            self._text_node = node

    def _on_pi(self, target, data):
        if not self._stack:
            # PIs outside the root cannot be reached by any path.
            self._text_node = None
            return
        self._stack[-1].children.append(ProcessingInstruction(target, data))
        self._text_node = None

    def parse(self):
        try:
            self.parser.Parse(self.data, True)
        except XMLValidationError:
            raise
        except expat.ExpatError as exc:
            self.fail(
                expat.ErrorString(exc.code), self.parser.CurrentByteIndex
            )
        if self.root is None:
            self.fail("document has no root element", 0)
        return self.root


# --- public API -----------------------------------------------------------


def _check_xml_declaration(data, input_name):
    """Enforce XML 1.0 and UTF-8-only encoding declarations."""
    if data.startswith(b"\xef\xbb\xbf"):
        probe = data[3:]
        bom_offset = 3
    else:
        probe = data
        bom_offset = 0
    match = _XML_DECL_RE.match(probe)
    if match is None:
        return
    version = match.group(2)
    if version != b"1.0":
        raise XMLValidationError(
            "XML version %r is not supported (only 1.0)"
            % version.decode("ascii", "replace"),
            bom_offset + match.start(2),
            input_name,
        )
    if match.group(4) is not None:
        encoding = match.group(4).decode("ascii")
        if encoding.upper().replace("-", "") != "UTF8":
            raise XMLValidationError(
                "encoding declaration %r is not UTF-8" % encoding,
                bom_offset + match.start(4),
                input_name,
            )


def parse_xml(data, input_name="<xml>"):
    """Parse *data* and return the root :class:`Element`.

    Raises :class:`XMLValidationError` for every rejected document.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("XML input must be bytes")
    data = bytes(data)
    _check_xml_declaration(data, input_name)
    return _TreeBuilder(data, input_name).parse()


def resolve_path(root, path):
    """Walk *path* from *root* using element children only.

    Indices are zero-based positions among element child nodes, matching
    the selection grammar.  Raises :class:`PathResolutionError` carrying
    the failing zero-based depth when the path leaves the tree; the
    pipeline converts that into a selection error with a JSON Pointer.
    """
    current = root
    for depth, index in enumerate(path):
        element_children = [
            child for child in current.children
            if isinstance(child, Element)
        ]
        if index < 0 or index >= len(element_children):
            raise PathResolutionError(
                "index %d has no element child (element has %d element "
                "children)" % (index, len(element_children)),
                depth,
            )
        current = element_children[index]
    return current
