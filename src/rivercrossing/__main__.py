# SPDX-License-Identifier: GPL-3.0-only
"""Allow ``python -m rivercrossing`` to launch the GUI."""

import sys

from rivercrossing.ui.app import main

if __name__ == "__main__":
    sys.exit(main())
