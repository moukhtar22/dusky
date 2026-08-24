"""Deprecated shim — use dashboard_tui.py (Rich + matugen).

This file exists for backward compatibility for any external import of
`dusky_keylogger.dashboard`. New code should import `dashboard_tui`.
"""

try:
    from .dashboard_tui import main  # noqa: F401
except ImportError as _e:
    # Fallback if dashboard_tui not available
    _msg = str(_e)

    def main(*_a, **_kw):  # type: ignore
        raise RuntimeError(f"dashboard_tui not available: {_msg}")

__all__ = ["main"]
