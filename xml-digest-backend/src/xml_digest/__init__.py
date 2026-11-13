"""Research XML digest backend.

Public entry point: :func:`xml_digest.digest.compute_digests`.
"""

from .digest import compute_digests
from .errors import (
    DigestError,
    JSONSyntaxError,
    SelectionError,
    XMLValidationError,
)

__all__ = [
    "compute_digests",
    "DigestError",
    "JSONSyntaxError",
    "SelectionError",
    "XMLValidationError",
]
