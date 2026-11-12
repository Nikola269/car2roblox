"""Entry point used by the .exe builds and for double-click / `python launcher.py` use.

No arguments  -> opens the graphical interface.
With arguments -> behaves like the command line:  launcher.py convert car.dff -o out
"""
import sys

from car2roblox.cli import main

if __name__ == "__main__":
    sys.exit(main())
