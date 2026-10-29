"""Keep the simple ``python main.py`` entry point for the monitor."""

# Importing ``main`` from the CLI keeps command-line behavior in one place.
from football_match_monitor.cli import main


if __name__ == "__main__":
    # Return the command's success or failure code to Windows/the shell.
    raise SystemExit(main())
