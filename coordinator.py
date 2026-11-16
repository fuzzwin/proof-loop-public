#!/usr/bin/env python3
"""Keep the documented checkout command for the coordinator."""
if __name__ == '__main__':
    import sys

    sys.dont_write_bytecode = True

    from proofloop.coordinator import main

    raise SystemExit(main())
