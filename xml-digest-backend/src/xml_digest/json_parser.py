"""A minimal strict JSON parser that records byte offsets.

The standard library :mod:`json` module cannot report precise byte offsets
and silently accepts duplicate object members, so this package ships its own
recursive-descent parser.  It implements the grammar of RFC 8259 and adds
two extra rules required by the specification:

* duplicate member names inside one object are rejected;
* only objects, arrays, strings, integer/decimal numbers, ``true``,
  ``false`` and ``null`` are accepted (the grammar itself already excludes
  anything else).

Each parsed value is wrapped in a :class:`JValue` that records its
``[start, end)`` byte span, which lets the schema layer emit precise error
locations.
"""

from .errors import JSONSyntaxError


class JValue:
    """A parsed JSON value together with its source byte span."""

    __slots__ = ("value", "start", "end")

    def __init__(self, value, start, end):
        self.value = value
        self.start = start
        self.end = end

    def __repr__(self):  # pragma: no cover - debugging aid
        return "JValue(%r, %d, %d)" % (self.value, self.start, self.end)


class _JSONParser:
    def __init__(self, data, input_name):
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("JSON input must be bytes")
        self.data = bytes(data)
        self.input_name = input_name
        self.pos = 0
        self.length = len(self.data)

    # -- utilities -------------------------------------------------------

    def error(self, message, pos=None):
        raise JSONSyntaxError(
            message, self.pos if pos is None else pos, self.input_name
        )

    def skip_ws(self):
        data = self.data
        n = self.length
        i = self.pos
        while i < n and data[i] in (0x20, 0x09, 0x0A, 0x0D):
            i += 1
        self.pos = i

    def expect(self, byte, what):
        if self.pos >= self.length or self.data[self.pos] != byte:
            self.error("expected %s" % what)
        self.pos += 1

    # -- entry point -----------------------------------------------------

    def parse(self):
        self.skip_ws()
        if self.pos >= self.length:
            self.error("empty document")
        result = self.parse_value()
        self.skip_ws()
        if self.pos != self.length:
            self.error("trailing data after JSON value")
        return result

    def parse_value(self):
        if self.pos >= self.length:
            self.error("unexpected end of input")
        b = self.data[self.pos]
        if b == 0x7B:  # {
            return self.parse_object()
        if b == 0x5B:  # [
            return self.parse_array()
        if b == 0x22:  # "
            return self.parse_string()
        if b == 0x74:  # t
            return self.parse_literal(b"true", True)
        if b == 0x66:  # f
            return self.parse_literal(b"false", False)
        if b == 0x6E:  # n
            return self.parse_literal(b"null", None)
        if b == 0x2D or 0x30 <= b <= 0x39:  # - or digit
            return self.parse_number()
        self.error("unexpected byte 0x%02x" % b)

    def parse_literal(self, literal, value):
        start = self.pos
        end = start + len(literal)
        if self.data[start:end] != literal:
            self.error("invalid literal")
        self.pos = end
        return JValue(value, start, end)

    def parse_number(self):
        start = self.pos
        data = self.data
        n = self.length
        i = start
        if i < n and data[i] == 0x2D:
            i += 1
        if i >= n:
            self.error("unexpected end of number", start)
        if data[i] == 0x30:
            i += 1
        elif 0x31 <= data[i] <= 0x39:
            i += 1
            while i < n and 0x30 <= data[i] <= 0x39:
                i += 1
        else:
            self.error("invalid number", start)
        is_float = False
        if i < n and data[i] == 0x2E:
            is_float = True
            i += 1
            if i >= n or not (0x30 <= data[i] <= 0x39):
                self.error("invalid fraction", start)
            while i < n and 0x30 <= data[i] <= 0x39:
                i += 1
        if i < n and data[i] in (0x65, 0x45):  # e E
            is_float = True
            i += 1
            if i < n and data[i] in (0x2B, 0x2D):
                i += 1
            if i >= n or not (0x30 <= data[i] <= 0x39):
                self.error("invalid exponent", start)
            while i < n and 0x30 <= data[i] <= 0x39:
                i += 1
        text = self.data[start:i].decode("ascii")
        value = float(text) if is_float else int(text)
        self.pos = i
        return JValue(value, start, i)

    def parse_string(self):
        start = self.pos
        value = self._decode_string(start)
        return JValue(value, start, self.pos)

    def _decode_string(self, start):
        # Assumes data[pos] == '"'.
        data = self.data
        n = self.length
        self.pos += 1
        chunks = []
        chunk_start = self.pos
        while True:
            if self.pos >= n:
                self.error("unterminated string", start)
            b = data[self.pos]
            if b == 0x22:  # closing quote
                chunks.append(data[chunk_start:self.pos])
                self.pos += 1
                return b"".join(chunks).decode("utf-8")
            if b < 0x20:
                self.error("unescaped control character in string")
            if b == 0x5C:  # backslash
                chunks.append(data[chunk_start:self.pos])
                self.pos += 1
                if self.pos >= n:
                    self.error("unterminated escape", start)
                e = data[self.pos]
                simple = {
                    0x22: b'"',
                    0x5C: b"\\",
                    0x2F: b"/",
                    0x62: b"\b",
                    0x66: b"\f",
                    0x6E: b"\n",
                    0x72: b"\r",
                    0x74: b"\t",
                }
                if e in simple:
                    chunks.append(simple[e])
                    self.pos += 1
                elif e == 0x75:  # \uXXXX
                    cp = self._read_hex4(start)
                    if 0xD800 <= cp <= 0xDBFF:
                        # high surrogate; require a following \uXXXX low
                        # half.  self.pos sits just past the high escape;
                        # the next two bytes must be backslash + 'u'.
                        if (
                            self.pos + 6 <= n
                            and data[self.pos] == 0x5C
                            and data[self.pos + 1] == 0x75
                        ):
                            self.pos += 1  # move onto the 'u'
                            low = self._read_hex4(start)
                            if not 0xDC00 <= low <= 0xDFFF:
                                self.error(
                                    "invalid UTF-16 surrogate pair", start
                                )
                            cp = 0x10000 + (
                                (cp - 0xD800) << 10
                            ) + (low - 0xDC00)
                        else:
                            self.error("unpaired high surrogate", start)
                    elif 0xDC00 <= cp <= 0xDFFF:
                        self.error("unpaired low surrogate", start)
                    chunks.append(chr(cp).encode("utf-8"))
                else:
                    self.error("invalid escape", start)
                chunk_start = self.pos
            else:
                self.pos += 1

    def _read_hex4(self, start):
        data = self.data
        # self.pos points at 'u'; the four hex digits occupy pos+1..pos+4,
        # which must all be valid indices.
        if self.pos + 5 > self.length:
            self.error("incomplete unicode escape", start)
        digits = data[self.pos + 1:self.pos + 5]
        try:
            cp = int(digits, 16)
        except ValueError:
            self.error("invalid unicode escape", start)
        self.pos += 5
        return cp

    def parse_array(self):
        start = self.pos
        self.pos += 1  # [
        items = []
        self.skip_ws()
        if self.pos < self.length and self.data[self.pos] == 0x5D:
            self.pos += 1
            return JValue([], start, self.pos)
        while True:
            self.skip_ws()
            items.append(self.parse_value())
            self.skip_ws()
            if self.pos >= self.length:
                self.error("unterminated array", start)
            b = self.data[self.pos]
            if b == 0x5D:
                self.pos += 1
                return JValue(items, start, self.pos)
            if b != 0x2C:
                self.error("expected ',' or ']' in array")
            self.pos += 1

    def parse_object(self):
        start = self.pos
        self.pos += 1  # {
        members = {}
        order = []
        self.skip_ws()
        if self.pos < self.length and self.data[self.pos] == 0x7D:
            self.pos += 1
            return JValue({}, start, self.pos)
        while True:
            self.skip_ws()
            if self.pos >= self.length or self.data[self.pos] != 0x22:
                self.error("expected string member name in object")
            key_node = self.parse_string()
            key = key_node.value
            if key in members:
                self.error(
                    "duplicate object member %r" % key, key_node.start
                )
            self.skip_ws()
            self.expect(0x3A, "':' after member name")
            self.skip_ws()
            value_node = self.parse_value()
            members[key] = value_node
            order.append(key)
            self.skip_ws()
            if self.pos >= self.length:
                self.error("unterminated object", start)
            b = self.data[self.pos]
            if b == 0x7D:
                self.pos += 1
                # Preserve first-seen order (members are unique anyway).
                ordered = {k: members[k] for k in order}
                return JValue(ordered, start, self.pos)
            if b != 0x2C:
                self.error("expected ',' or '}' in object")
            self.pos += 1


def parse_json(data, input_name="<selection>"):
    """Parse ``data`` and return the root :class:`JValue`."""
    return _JSONParser(data, input_name).parse()
