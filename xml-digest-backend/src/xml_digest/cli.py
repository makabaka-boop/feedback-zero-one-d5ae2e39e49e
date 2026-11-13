"""Command line entry point.

Usage::

    python -m xml_digest XML_FILE SELECTION_FILE [OUTPUT_FILE]

The digest array is written as compact UTF-8 JSON.  When an output file is
given, the replacement is atomic: a sibling temporary file is flushed and
fsynced, then renamed over the destination, so a failed run leaves any
pre-existing output file untouched.  Errors print a single ``ERROR: ...``
line to stderr and exit with status 1.
"""

import json
import os
import sys
import tempfile

from .digest import compute_digests
from .errors import DigestError


def _read(path):
    with open(path, "rb") as handle:
        return handle.read()


def _atomic_write(path, payload):
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp_path = tempfile.mkstemp(
        prefix=".xml-digest-", suffix=".tmp", dir=directory
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) not in (2, 3):
        sys.stderr.write(
            "usage: python -m xml_digest XML_FILE SELECTION_FILE "
            "[OUTPUT_FILE]\n"
        )
        return 2

    xml_path, selection_path = argv[0], argv[1]
    output_path = argv[2] if len(argv) == 3 else None

    try:
        xml_bytes = _read(xml_path)
        selection_bytes = _read(selection_path)
        results = compute_digests(
            xml_bytes, selection_bytes,
            xml_name=xml_path, selection_name=selection_path,
        )
        payload = (
            json.dumps(results, separators=(",", ":"), ensure_ascii=False)
            .encode("utf-8")
        )
        if output_path is None:
            sys.stdout.buffer.write(payload)
            sys.stdout.buffer.write(b"\n")
        else:
            _atomic_write(output_path, payload)
    except DigestError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 1
    except OSError as exc:
        sys.stderr.write("ERROR: %s\n" % exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
