# Research XML Digest Backend

A self-contained Python service that turns a UTF-8 XML document plus a
JSON selection array into an ordered array of
`{"base64", "sha256"}` digests.  Canonicalization follows
[Exclusive XML Canonicalization 1.0](https://www.w3.org/TR/xml-exc-c14n/)
**including the 2010 errata**, implemented by hand on top of the standard
Expat parser.

## What it does

For every selection entry the backend:

1. walks `path` (zero-based element-child indices from the document
   element; `[]` selects the root) to an element;
2. exclusive-canonicalizes the **complete subtree** (element, attributes,
   namespace nodes, descendants, text and processing instructions);
3. returns the padded standard Base64 of those canonical bytes and the
   lowercase hex SHA-256 of the same bytes.

Duplicate selections produce duplicate outputs.  An empty selection
array produces `[]`.  Output order matches input order.

### Canonicalization rules implemented

* namespace nodes are emitted only where **visibly utilized** (element
  name or an attribute's prefix), or for a prefix listed in `prefixes`;
  prefixes that merely appear inside attribute *values* are not usage;
* ancestor declarations are inherited, never copied wholesale; unused
  declarations are suppressed and re-emitted at the first point of use;
* prefix rebinding and default-namespace clearing
  (`xmlns=""`, including the 2010 erratum for unprefixed elements) are
  handled;
* the source prefixes are preserved (no prefix rewriting);
* namespace nodes sort lexicographically by prefix, attributes by
  namespace URI then local name;
* empty elements expand to open/close pairs, whitespace is preserved,
  every carriage return becomes `&#xD;`, and the attribute whitespace
  characters tab/newline/CR become `&#x9;`/`&#xA;`/`&#xD;`;
* comments and the XML declaration never appear; root-level trailing
  text is excluded;
* `xml:` attributes are usable without any `xmlns:xml` declaration and
  that declaration is never serialized.

### Acceptance rules

The input is restricted to XML 1.0 (fifth edition) and Namespaces 1.0,
UTF-8 only.  Rejected:

* DOCTYPE declarations (and therefore internal DTD subsets);
* external parsed entity references;
* any non-UTF-8 encoding declaration, and any XML version other than 1.0;
* relative namespace URIs (an absolute URI reference is required);
* unbound prefixes, reserved-prefix misuse, duplicate namespace
  declarations and attributes that collide after namespace expansion.

Predefined entities, numeric character references, CDATA sections and
processing instructions are allowed.

### Validation order and error reporting

Stages run in a fixed order; any stage may fail and later stages are not
attempted:

1. **JSON** — syntax first, then schema.  Syntax errors report the input
   name and a zero-based **byte** offset.  Schema errors report a
   [JSON Pointer](https://www.rfc-editor.org/rfc/rfc6901) and, for array
   problems, a zero-based index.  Reject duplicate object members, unknown
   members, and illegal types (booleans are not integers).
2. **XML** — reports the input name and a zero-based byte offset.
3. **Path resolution** — reports the selection entry's JSON Pointer and
   the failing zero-based path depth.

## Layout

```
src/xml_digest/
  json_parser.py   strict offset-aware JSON parser (rejects dup members)
  selection.py     selection schema + NCName validation
  xml_model.py     Expat-based restricted XML parser and tree model
  c14n.py          hand-written exclusive C14N 1.0 (+2010 errata)
  digest.py        ordered pipeline and digest computation
  errors.py        JSONSyntaxError / SelectionError / XMLValidationError
  cli.py           command line front end with atomic output replacement
tests/
  test_lxml_diff.py  differential tests vs lxml on the legal intersection
  test_fixtures.py   exact-byte fixtures for exclusive-1.0-only semantics
  test_validation.py schema, rejection, ordering, CLI atomic-write tests
  test_properties.py randomized canonical/parity properties
  test_artifacts.py  independent on-disk fixture tests
  fixtures/          reject/ and bytes/ fixture files
```

Only the Python standard library is required at runtime
(`xml.parsers.expat`, `base64`, `hashlib`, `json` is used only for
emitting output).  lxml is used solely by the test suite as an
independent oracle.

## Usage

As a library:

```python
from xml_digest import compute_digests

results = compute_digests(xml_bytes, selection_bytes,
                          xml_name="doc.xml",
                          selection_name="selection.json")
# [{"base64": "...", "sha256": "..."}, ...]
```

From the command line:

```
python -m xml_digest doc.xml selection.json [output.json]
```

Without an output file the compact JSON result is written to stdout.
With one, the file is replaced atomically (temp file in the same
directory, fsync, then `os.replace`); a failed run leaves any
pre-existing output untouched.

## Running the tests

```
PYTHONPATH=src python3 -m unittest discover -s tests
```

The lxml-backed differential and property tests are skipped automatically
when lxml is not importable; all other tests run on the standard library
alone.

## Selection document shape

```json
[
  {"path": [0, 2], "prefixes": ["p", "#default"]},
  {"path": [], "prefixes": []}
]
```

* `path` — array of non-negative JSON integers (booleans rejected);
* `prefixes` — unique array of NCNames and/or the literal `"#default"`;
  tokens that are not bound in the selected subtree are ignored.
