# SPDX-License-Identifier: GPL-3.0-only
"""UI package: the only place ``wx`` may be imported (R-71).

Importing this package guards the ``wx`` import so a missing or
broken wxPython installation raises a named, catchable error here
instead of a bare ``ImportError`` leaking out of a view.
"""

__all__ = ["WxUnavailableError"]


class WxUnavailableError(ImportError):
    """Raised when wxPython cannot be imported.

    Subclasses ``ImportError`` so callers that already handle
    import errors keep working unchanged.
    """


try:
    import wx  # noqa: F401
except ImportError as exc:
    raise WxUnavailableError(
        "wxPython could not be imported; install the pinned "
        "dependency wxPython~=4.3.1 (see pyproject.toml)."
    ) from exc
