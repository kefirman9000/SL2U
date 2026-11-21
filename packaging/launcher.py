"""Entry point for the one-click builds (PyInstaller)."""
import multiprocessing

from releasekit.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
