"""End-to-end digest pipeline.

The pipeline enforces the required validation order -- JSON first, XML
second, path resolution last -- so that a malformed selection document is
always reported before the XML document is even inspected.
"""

import base64
import hashlib

from .c14n import canonicalize
from .errors import SelectionError
from .json_parser import parse_json
from .selection import validate_selections
from .xml_model import PathResolutionError, parse_xml, resolve_path


def compute_digests(xml_bytes, selection_bytes, xml_name="<xml>",
                    selection_name="<selection>"):
    """Validate both inputs and return the ordered list of result objects.

    Each result is a ``{"base64": ..., "sha256": ...}`` dict.  Repeated
    selections produce repeated entries; an empty selection array yields
    the empty list.
    """
    # Stage 1: JSON syntax and schema (prior to touching the XML input).
    root_node = parse_json(selection_bytes, selection_name)
    selections = validate_selections(root_node, selection_name)

    # Stage 2: XML acceptance and tree construction.
    root = parse_xml(xml_bytes, xml_name)

    # Stage 3: resolve each path in order and canonicalize.  A path that
    # leaves the element tree is a selection problem, so it is reported
    # with the JSON Pointer of the offending entry and the failing
    # zero-based depth rather than as an XML byte error.
    results = []
    for entry_index, selection in enumerate(selections):
        try:
            subtree = resolve_path(root, selection.path)
        except PathResolutionError as lookup:
            raise SelectionError(
                "path does not resolve to an element: %s"
                % lookup.message,
                "/%d/path" % entry_index,
                selection_name,
                index=lookup.depth,
            )
        canonical = canonicalize(subtree, selection.prefixes)
        results.append(
            {
                "base64": base64.b64encode(canonical).decode("ascii"),
                "sha256": hashlib.sha256(canonical).hexdigest(),
            }
        )
    return results
