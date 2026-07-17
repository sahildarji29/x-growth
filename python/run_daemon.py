"""
Thin runner for the XActions growth daemon.
Bypasses xeepy/__init__.py's heavy imports so the daemon can start
without every scraper/action module being importable.

Usage:
  python3 run_daemon.py run      # foreground (debug / systemd)
  python3 run_daemon.py start    # fork to background
  python3 run_daemon.py stop
  python3 run_daemon.py status
  python3 run_daemon.py restart
"""

import sys
import importlib
import importlib.util
import types
from pathlib import Path

# Make sure we're running from the python/ directory
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Register a lightweight 'xeepy' package namespace so daemon.py's
# lazy sub-imports resolve without triggering __init__.py
pkg = types.ModuleType("xeepy")
pkg.__path__ = [str(ROOT / "xeepy")]
pkg.__package__ = "xeepy"
sys.modules["xeepy"] = pkg

# Load daemon.py as xeepy.daemon
daemon_path = ROOT / "xeepy" / "daemon.py"
spec = importlib.util.spec_from_file_location("xeepy.daemon", daemon_path)
daemon_mod = importlib.util.module_from_spec(spec)
daemon_mod.__package__ = "xeepy"
sys.modules["xeepy.daemon"] = daemon_mod
spec.loader.exec_module(daemon_mod)

# Trigger the CLI entry point (same as `if __name__ == "__main__"`)
daemon_mod.main()
