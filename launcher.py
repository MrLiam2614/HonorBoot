"""Portable executable entry point; no arguments opens the setup window."""
import sys
from honorboot.__main__ import main
from honorboot.setup import setup

if __name__ == '__main__':
    if len(sys.argv) == 1:
        if getattr(sys, 'frozen', False):
            import ctypes
            processes = (ctypes.c_ulong * 2)()
            if ctypes.windll.kernel32.GetConsoleProcessList(processes, 2) == 1:
                ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
        setup()
    else:
        main()
