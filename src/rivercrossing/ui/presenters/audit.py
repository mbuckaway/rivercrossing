# SPDX-License-Identifier: GPL-3.0-only
"""Audit presenter -- audit_dlg, the read-only audit trail (R-38).

Pure Python -- no ``wx`` import may ever land here (R-71).
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from rivercrossing.ui.presenters.data_source import AuditRow, DataSource


@runtime_checkable
class AuditView(Protocol):
    """View surface for the audit trail dialog (audit_dlg)."""

    def show_audit_rows(self, rows: list[AuditRow]) -> None:
        """Render audit_list, newest first."""
        ...

    def set_entry_filter(self, entry: str) -> None:
        """Pre-fill audit_search when deep-linked from entry detail."""
        ...


class AuditPresenter:
    """Presenter for the audit trail dialog (audit_dlg, R-38).

    No-op beyond storing its collaborators; Phase 5 wires the
    entry/action filters to this view.
    """

    def __init__(self, view: AuditView, data_source: DataSource) -> None:
        """Store the view and data source this presenter drives."""
        self.view = view
        self.data_source = data_source
