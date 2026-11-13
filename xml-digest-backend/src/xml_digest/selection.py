"""Schema validation for the JSON selection document.

The JSON document must be an array of selection objects.  Each object has
exactly two members:

* ``path``     -- a non-empty (possibly empty) array of non-negative JSON
  integers.  Booleans are not numbers; floating point values, negative
  values and ``null`` are rejected.
* ``prefixes`` -- an array of unique strings, each of which is either the
  literal ``"#default"`` or an XML NCName (Namespaces 1.0 production [4]).

Validation errors carry a JSON Pointer (RFC 6901) and, for array problems,
a zero-based index.
"""

from .errors import SelectionError
from .json_parser import JValue

# --- NCName (XML 1.0 fifth edition / Namespaces 1.0) ----------------------
#
# NameStartChar ::= ":" | [A-Z] | "_" | [a-z] | [#xC0-#xD6] | [#xD8-#xF6] |
#                   [#xF8-#x2FF] | [#x370-#x37D] | [#x37F-#x1FFF] |
#                   [#x200C-#x200D] | [#x2070-#x218F] | [#x2C00-#x2FEF] |
#                   [#x3001-#xD7FF] | [#xF900-#xFDCF] | [#xFDF0-#xFFFD] |
#                   [#x10000-#xEFFFF]
#
# NameChar adds "-", ".", [0-9], #xB7, #x0300-#x036F and #x203F-#x2040.
# An NCName is (NameStartChar)(NameChar)* without any ":" -- the colon is
# simply omitted from the ranges below.

_START_RANGES = (
    (0x41, 0x5A), (0x5F, 0x5F), (0x61, 0x7A),
    (0xC0, 0xD6), (0xD8, 0xF6), (0xF8, 0x2FF),
    (0x370, 0x37D), (0x37F, 0x1FFF),
    (0x200C, 0x200D), (0x2070, 0x218F), (0x2C00, 0x2FEF),
    (0x3001, 0xD7FF), (0xF900, 0xFDCF), (0xFDF0, 0xFFFD),
    (0x10000, 0xEFFFF),
)
_CHAR_RANGES = _START_RANGES + (
    (0x2D, 0x2D), (0x2E, 0x2E), (0x30, 0x39),
    (0xB7, 0xB7), (0x300, 0x36F), (0x203F, 0x2040),
)


def _in_ranges(cp, ranges):
    return any(lo <= cp <= hi for lo, hi in ranges)


def is_ncname(text):
    """Return ``True`` if *text* is a valid Namespaces 1.0 NCName."""
    if not isinstance(text, str) or text == "":
        return False
    first = ord(text[0])
    if not _in_ranges(first, _START_RANGES):
        return False
    for ch in text[1:]:
        if not _in_ranges(ord(ch), _CHAR_RANGES):
            return False
    return True


class Selection:
    """One validated entry of the selection array."""

    __slots__ = ("path", "prefixes")

    def __init__(self, path, prefixes):
        self.path = path
        self.prefixes = prefixes

    def __repr__(self):  # pragma: no cover - debugging aid
        return "Selection(path=%r, prefixes=%r)" % (self.path, self.prefixes)


def _pointer(path_tokens):
    escaped = []
    for token in path_tokens:
        token = str(token).replace("~", "~0").replace("/", "~1")
        escaped.append(token)
    return "/" + "/".join(escaped) if escaped else ""


def _fail(message, input_name, tokens, index=None):
    raise SelectionError(message, _pointer(tokens), input_name, index)


def _require_type(node, expected, label, input_name, tokens):
    py = node.value
    ok = False
    if expected == "object":
        ok = isinstance(py, dict)
    elif expected == "array":
        ok = isinstance(py, list)
    elif expected == "string":
        ok = isinstance(py, str)
    elif expected == "integer":
        # bool is a subclass of int; the spec explicitly forbids booleans.
        ok = isinstance(py, int) and not isinstance(py, bool)
    if not ok:
        _fail("%s must be %s" % (label, expected), input_name, tokens)
    return py


def validate_selections(root_node, input_name):
    """Validate the parsed JSON tree and return a list of :class:`Selection`.

    Duplicate selections are deliberately *not* removed: repeated entries
    must produce repeated digest outputs.
    """
    if not isinstance(root_node, JValue):
        raise TypeError("root_node must be a JValue")
    root = root_node.value
    if not isinstance(root, list):
        _fail(
            "top-level JSON document must be an array of selection objects",
            input_name,
            [],
        )

    selections = []
    for i, item_node in enumerate(root):
        tokens = [i]
        item = _require_type(
            item_node, "object", "selection item", input_name, tokens
        )

        members = set(item.keys())
        unknown = members - {"path", "prefixes"}
        if unknown:
            _fail(
                "unknown member %r" % sorted(unknown)[0],
                input_name,
                tokens + [sorted(unknown)[0]],
            )
        missing = {"path", "prefixes"} - members
        if missing:
            _fail(
                "missing required member %r" % sorted(missing)[0],
                input_name,
                tokens + [sorted(missing)[0]],
            )

        # --- path -------------------------------------------------------
        path_tokens = tokens + ["path"]
        path_node = item["path"]
        path_raw = _require_type(
            path_node, "array", "member 'path'", input_name, path_tokens
        )
        path = []
        for j, step_node in enumerate(path_raw):
            step_tokens = path_tokens + [j]
            step = step_node.value
            if isinstance(step, bool) or not isinstance(step, int):
                _fail(
                    "path elements must be non-negative integers "
                    "(booleans are not allowed)",
                    input_name,
                    step_tokens,
                    index=j,
                )
            if step < 0:
                _fail(
                    "path elements must be non-negative integers",
                    input_name,
                    step_tokens,
                    index=j,
                )
            path.append(step)

        # --- prefixes ---------------------------------------------------
        pref_tokens = tokens + ["prefixes"]
        pref_node = item["prefixes"]
        prefixes_raw = _require_type(
            pref_node, "array", "member 'prefixes'", input_name, pref_tokens
        )
        prefixes = []
        seen = set()
        for j, pref_node in enumerate(prefixes_raw):
            pref_tokens_j = pref_tokens + [j]
            pref = pref_node.value
            if not isinstance(pref, str):
                _fail(
                    "prefixes entries must be strings",
                    input_name,
                    pref_tokens_j,
                    index=j,
                )
            if pref != "#default" and not is_ncname(pref):
                _fail(
                    "prefix %r is neither '#default' nor a valid NCName"
                    % pref,
                    input_name,
                    pref_tokens_j,
                    index=j,
                )
            if pref in seen:
                _fail(
                    "duplicate prefix %r in prefixes" % pref,
                    input_name,
                    pref_tokens_j,
                    index=j,
                )
            seen.add(pref)
            prefixes.append(pref)

        selections.append(Selection(path, prefixes))

    return selections
