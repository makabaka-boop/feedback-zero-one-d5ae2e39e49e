"""Error types for the XML digest backend.

Three error categories are distinguished, because each one carries a
different location convention:

* :class:`JSONSyntaxError` -- the selection document is not well-formed JSON.
  Reported with the input name and a zero-based byte offset.
* :class:`SelectionError` -- the selection document is well-formed JSON but
  violates the required schema.  Reported with a JSON Pointer path and, where
  relevant, a zero-based array index.
* :class:`XMLValidationError` -- the XML document is not accepted.  Reported
  with the input name and a zero-based byte offset.

Every error renders as a single-line ``ERROR: ...`` message via
:class:`str`.
"""


class DigestError(Exception):
    """Base class for all errors raised by this package."""


class JSONSyntaxError(DigestError):
    """Malformed JSON input.

    :param message: human readable description
    :param offset: zero-based byte offset at which the error was detected
    :param input_name: logical name of the offending input
    """

    def __init__(self, message, offset, input_name):
        self.message = message
        self.offset = offset
        self.input_name = input_name
        super().__init__(
            "ERROR: %s: invalid JSON at byte %d: %s"
            % (input_name, offset, message)
        )


class SelectionError(DigestError):
    """Schema violation inside a structurally valid JSON selection array.

    :param message: human readable description
    :param json_path: JSON Pointer (RFC 6901) of the offending value
    :param index: optional zero-based array index associated with the error
    :param input_name: logical name of the offending input
    """

    def __init__(self, message, json_path, input_name, index=None):
        self.message = message
        self.json_path = json_path
        self.index = index
        self.input_name = input_name
        if index is None:
            location = json_path
        else:
            location = "%s (index %d)" % (json_path, index)
        super().__init__(
            "ERROR: %s: invalid selection at %s: %s"
            % (input_name, location, message)
        )


class XMLValidationError(DigestError):
    """The XML document violates one of the stated acceptance rules.

    :param message: human readable description
    :param offset: zero-based byte offset at which the error was detected
    :param input_name: logical name of the offending input
    """

    def __init__(self, message, offset, input_name):
        self.message = message
        self.offset = offset
        self.input_name = input_name
        super().__init__(
            "ERROR: %s: invalid XML at byte %d: %s"
            % (input_name, offset, message)
        )
