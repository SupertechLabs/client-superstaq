#!/usr/bin/env python3
import sys

import checks_superstaq as checks

if __name__ == "__main__":
    exit(checks.requirements.run(*sys.argv[1:]))
