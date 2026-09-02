# SPDX-License-Identifier: GPL-3.0-only
"""RiverCrossing: poker-run ride timing and scoring desktop app.

``__version__`` is the single source of truth for the installed
distribution version (module-skeletons.md S2); ``pyproject.toml``'s
``[tool.setuptools.dynamic]`` reads it via ``attr =
"rivercrossing.__version__"``.
"""

__version__ = "1.0.6"

__all__ = ["__version__"]
