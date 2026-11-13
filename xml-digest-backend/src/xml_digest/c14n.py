"""Exclusive XML Canonicalization 1.0 (with the 2010 errata), hand-rolled.

The renderer consumes the tree produced by :mod:`xml_digest.xml_model` and
serializes a complete element subtree following the rules of
https://www.w3.org/TR/xml-exc-c14n/ :

* namespace nodes are emitted only when visibly utilized by the element or
  an (un-namespaced) attribute, or when the prefix is named in the
  caller-supplied inclusive list (``prefixes``);
* a declaration that is not utilized where it appears is suppressed and
  re-emitted at the first descendant that actually uses the binding;
* ancestor declarations are inherited rather than copied;
* the default namespace is reset to empty whenever an unprefixed element
  leaves a non-empty inherited default scope (the 2010 erratum);
* namespace nodes are ordered lexicographically by prefix and attribute
  nodes by namespace URI then local name;
* empty elements are expanded, whitespace and text are escaped exactly as
  the standard mandates (``&#xD;`` for every carriage return), comments
  are absent and the XML declaration and document-level trailing text
  never appear.

Two binding maps travel the tree:

* ``rendered``  -- prefixes already emitted by serialized ancestors; a
  node is emitted only when its in-scope URI differs from this map;
* the per-element ``in_scope`` snapshot -- every binding logically visible
  inside the element, used to resolve current URIs (including declarations
  that were suppressed at their point of origin).
"""

from .xml_model import Element, Text, ProcessingInstruction

_XML_URI = "http://www.w3.org/XML/1998/namespace"


class _NoBinding:
    """Sentinel meaning a prefix has no entry in the rendered map."""


_NO_BINDING = _NoBinding()

# --- escaping -------------------------------------------------------------


def _escape_text(value):
    # Ampersand first so the inserted entities stay literal afterwards.
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace("\r", "&#xD;")
    )


def _escape_attr(value):
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace('"', "&quot;")
        .replace("\t", "&#x9;")
        .replace("\n", "&#xA;")
        .replace("\r", "&#xD;")
    )


# --- renderer -------------------------------------------------------------


class _Renderer:
    def __init__(self, inclusive_prefixes):
        # Membership-only; output ordering is always lexicographic.
        self.inclusive = set(inclusive_prefixes)
        self.out = []

    def render(self, subtree):
        # No ancestor has serialized any namespace node before the subtree
        # root: every needed declaration is reconstructed from the binding
        # snapshots stored in the tree.
        self._render_element(subtree, {})
        return "".join(self.out)

    def _render_element(self, element, rendered):
        ns_nodes = self._namespace_nodes(element, rendered)

        # Bindings actually emitted here; this becomes the rendered map
        # visible to descendants.
        child_rendered = dict(rendered)
        for prefix, uri in ns_nodes:
            child_rendered[prefix] = uri

        self.out.append("<")
        self.out.append(element.qname)
        for prefix, uri in ns_nodes:
            if prefix is None:
                self.out.append(' xmlns="%s"' % _escape_attr(uri))
            else:
                self.out.append(
                    ' xmlns:%s="%s"' % (prefix, _escape_attr(uri))
                )

        for _key, qname, value in self._attribute_nodes(element):
            self.out.append(' %s="%s"' % (qname, _escape_attr(value)))
        self.out.append(">")

        for child in element.children:
            if isinstance(child, Element):
                self._render_element(child, child_rendered)
            elif isinstance(child, Text):
                self.out.append(_escape_text(child.data))
            elif isinstance(child, ProcessingInstruction):
                self._render_pi(child)
            # Comments have no tree representation at all.

        self.out.append("</%s>" % element.qname)

    def _namespace_nodes(self, element, rendered):
        """Return the ordered ``(prefix, uri)`` list to serialize here."""
        wanted = {}

        def demand(prefix):
            uri = element.in_scope.get(prefix, _NO_BINDING)
            if uri is _NO_BINDING:
                return  # unbound inclusive token: ignore
            wanted[prefix] = uri

        # Visibly utilized: the element name itself.
        if element.prefix is not None and element.prefix != "xml":
            demand(element.prefix)
        # Visibly utilized: prefixed attributes.  Prefixes appearing inside
        # attribute values are text and deliberately not consulted.
        for attr_prefix, _local, _uri, _value in element.attributes:
            if attr_prefix is not None and attr_prefix != "xml":
                demand(attr_prefix)
        # The default namespace is visibly utilized by an unprefixed
        # element whose expanded name actually has a namespace.
        if element.prefix is None and element.uri:
            demand(None)

        # Inclusive prefix list.
        for token in self.inclusive:
            demand(None if token == "#default" else token)

        # 2010 erratum: an unprefixed element with no namespace name must
        # render an empty default-namespace reset when a non-empty default
        # was serialized by an ancestor.
        if element.prefix is None and not element.uri:
            rendered_default = rendered.get(None, _NO_BINDING)
            if (
                rendered_default is not _NO_BINDING
                and rendered_default != ""
            ):
                wanted[None] = ""

        nodes = []
        for prefix, uri in wanted.items():
            if prefix == "xml":
                continue  # the implicit xml prefix is never declared
            rendered_uri = rendered.get(prefix, _NO_BINDING)
            if rendered_uri == uri:
                continue
            # A fresh empty reset with no non-empty binding above has no
            # observable effect and is omitted.
            if uri == "" and (
                rendered_uri is _NO_BINDING or rendered_uri == ""
            ):
                continue
            nodes.append((prefix, uri))

        # Lexicographic by prefix; the default namespace (key "") first.
        nodes.sort(
            key=lambda item: "" if item[0] is None else item[0]
        )
        return nodes

    def _attribute_nodes(self, element):
        nodes = []
        for attr_prefix, local, uri, value in element.attributes:
            if attr_prefix is None:
                qname = local
                sort_uri = ""
            else:
                qname = "%s:%s" % (attr_prefix, local)
                sort_uri = uri if uri is not None else ""
            nodes.append(((sort_uri, local), qname, value))
        # Namespace URI first, then local name, both codepoint order;
        # unprefixed attributes sort under the empty URI.
        nodes.sort(key=lambda item: item[0])
        return nodes

    def _render_pi(self, pi):
        if pi.data:
            self.out.append("<?%s %s?>" % (pi.target, pi.data))
        else:
            self.out.append("<?%s?>" % pi.target)


def canonicalize(subtree, inclusive_prefixes=()):
    """Return the exclusive-canonical UTF-8 bytes of *subtree*.

    :param subtree: an :class:`~xml_digest.xml_model.Element`
    :param inclusive_prefixes: iterable of prefix tokens (``"#default"``
        or NCNames) that must be declared whenever in scope; unbound tokens
        are ignored.
    """
    renderer = _Renderer(inclusive_prefixes)
    return renderer.render(subtree).encode("utf-8")
