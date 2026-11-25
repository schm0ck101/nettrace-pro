"""Lanceur de NetTrace Pro (utilisé aussi par PyInstaller)."""
import sys

from nettrace.app import main

if __name__ == "__main__":
    sys.exit(main())
