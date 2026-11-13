#!/usr/bin/env bash
# Run the full test suite.  Uses the stdlib-only suite when lxml cannot be
# imported; set LXML_PATH to a directory containing lxml to enable the
# differential/property oracle tests.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"
if [ -n "${LXML_PATH:-}" ]; then
  export PYTHONPATH="${LXML_PATH}:${PYTHONPATH}"
fi
exec python3 -I -m unittest discover -s tests -v
