#!/usr/bin/env python3
# pylint: disable=missing-function-docstring
import sys

import general_superstaq.check

if __name__ == "__main__":
    exit(general_superstaq.check.coverage_.run(*sys.argv[1:]))
