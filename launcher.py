"""Portable executable entry point; no arguments opens the setup window."""
import sys
from honorboot.__main__ import main
from honorboot.setup import setup

if __name__ == '__main__':
    if len(sys.argv) == 1:
        setup()
    else:
        main()
