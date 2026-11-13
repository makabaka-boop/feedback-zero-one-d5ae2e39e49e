"""Shared helpers for the test suite (no package-level assumptions)."""

import re


def normalize_to_c14n1(data):
    """Normalize lxml's C14N 2.0 bytes to the C14N 1.0 escape rules.

    C14N 2.0 escapes ``>`` as ``&gt;`` in text content, whereas C14N 1.0
    keeps it literal (attribute values are untouched in both versions).
    The replacement is therefore applied only outside element tags.
    """
    out = []
    pos = 0
    for match in re.finditer(rb"<[^>]*>", data):
        out.append(data[pos:match.start()].replace(b"&gt;", b">"))
        out.append(match.group(0))
        pos = match.end()
    out.append(data[pos:].replace(b"&gt;", b">"))
    return b"".join(out)
