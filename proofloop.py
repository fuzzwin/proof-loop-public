#!/usr/bin/env python3
"""Keep the historical checkout entry point and expose the source package."""
if __name__ == '__main__':
    import sys

    sys.dont_write_bytecode = True

    from proofloop.cli import module_main

    raise SystemExit(module_main())

# The checkout has a same-named command and cannot contain a sibling package
# directory. Let imports from the checkout resolve the same modules as pip.
from pathlib import Path

__path__ = [str(Path(__file__).resolve().parent / 'src' / 'proofloop')]
