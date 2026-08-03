# SPDX-License-Identifier: GPL-3.0-only
"""UI package: the only place ``wx`` may be imported (R-71).

Importing this package -- or anything under it, including
``rivercrossing.ui.presenters`` -- never imports ``wx`` itself.
Presenters must be importable headless (module-skeletons.md S1);
an eager guard import at package-init time broke that. Call
:func:`require_wx` at the point real wx use begins instead; it
performs the guarded import there, raising a named, catchable
error rather than a bare ``ImportError`` when wxPython is missing
or broken.
"""

from typing import Any

__all__ = ["WxUnavailableError", "require_wx"]


class WxUnavailableError(ImportError):
    """Raised when wxPython cannot be imported.

    Subclasses ``ImportError`` so callers that already handle
    import errors keep working unchanged.
    """


def require_wx() -> Any:  # noqa: ANN401 - wx ships no stubs; Any is honest
    """Import and return ``wx``, guarding against it being missing.

    Returns:
        The imported ``wx`` module.

    Raises:
        WxUnavailableError: If ``wx`` cannot be imported.
    """
    try:
        import wx  # noqa: PLC0415 - deliberately lazy; see module docstring
    except ImportError as exc:
        raise WxUnavailableError(
            "wxPython could not be imported; install the pinned "
            "dependency wxPython~=4.3.1 (see pyproject.toml)."
        ) from exc
    return wx
