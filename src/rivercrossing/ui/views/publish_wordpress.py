# SPDX-License-Identifier: GPL-3.0-only
"""The publish form and its modal progress window.

Results ▸ Publish to WordPress… opens :class:`PublishWordpressDialog`;
OK hands the collected field values to the app's publish path, which
renders the results page and POSTs it to the site. The five decisions
the form makes are all UI-side and live here, not in the app: what each
field opens with, which status labels the choice offers, what the
``wp_kind_choice`` dropdown changes when it moves (the page kind, and
with it the title and slug defaults), why a publish is refused before
the site is ever contacted, and that a refused publish leaves the
dialog open for a correction.

:class:`PublishRunningDialog` is the modal progress window the app shows
while that publish runs, mirroring ``views/simulator.SimRunningDialog``
over ``sim_running_dlg``: resolve the frozen controls, point Escape at
the custom-id Cancel button, and defer the work into the modal loop
:meth:`PublishRunningDialog.run` opens, so the app starts the worker
inside it. The worker is off-loop (R-02: a wx call from a worker thread
bus-errors the process), so every status update reaches this window
through ``wx.CallAfter``.

**Cancel does not abort an HTTP request.** The publish is a blocking
``urllib`` call with a 30-second timeout, so a request already in flight
cannot be stopped; Cancel marks the attempt abandoned and the app's
worker stops at its next checkpoint -- before the next HTTP call, or as
soon as the in-flight one returns -- and then reports a cancel rather
than a result. The window's docstring claims nothing more.

These are views in the module-skeletons.md sense: they render values
into their controls, read them back, and forward events -- they hold no
site state and contact nothing. The pure decisions all come from
:mod:`rivercrossing.ui.presenters.publish_wordpress`
(``STATUS_CHOICES``, ``PUBLISH_KIND_CHOICES``, ``default_title``,
``default_slug``, ``status_value``, ``kind_value``, and
:meth:`PublishForm.errors`), so the rules are unit-tested without a
display and this module only wires widgets.

OK's own order is deliberate: collect, validate, and only then publish.
:meth:`PublishForm.errors` reads the same textual rules the client's
HTTPS refusal enforces (a non-``https`` site URL is refused here too,
before any credential leaves the machine), and the messages arrive in
one native alert -- then the dialog stays up so the operator can fix
the field, the form not being lost to a close. Everything that can
fail *after* that -- DNS, TLS, a refused Application Password, a
WordPress error document -- belongs to the app's off-thread publish
path, which reports it in the Retry/Cancel alert after the progress
window's modal ends.
"""

from typing import TYPE_CHECKING, Any

import wx
import wx.xrc

from rivercrossing.ui import ids, std_dialogs
from rivercrossing.ui.presenters.publish_wordpress import (
    PUBLISH_KIND_CHOICES,
    STATUS_CHOICES,
    PublishForm,
    default_slug,
    default_title,
    kind_value,
    status_value,
)
from rivercrossing.ui.views._support import DialogFindMixin, load_dialog

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivercrossing.ui.presenters.settings import AppSettings

__all__ = ["PublishRunningDialog", "PublishWordpressDialog", "load_publish_running_window"]

# The alert's caption when the fields cannot be published -- the
# dialog's own title, so the message names the act that was refused.
_ERROR_TITLE = "Publish to WordPress"

# The index a choice opens on for a stored value. A stored value this
# build does not offer (an older or hand-edited settings file) falls
# back to the first item -- Draft for wp_status_choice, Full results
# for wp_kind_choice, each presenter's own default -- so a choice is
# never left unselected.
_DEFAULT_CHOICE_INDEX = 0


def _status_index(status: str) -> int:
    """Return the ``wp_status_choice`` item index for *status*."""
    for index, (_label, value) in enumerate(STATUS_CHOICES):
        if value == status:
            return index
    return _DEFAULT_CHOICE_INDEX


def _kind_index(kind: str) -> int:
    """Return the ``wp_kind_choice`` item index for *kind*."""
    for index, (_label, value) in enumerate(PUBLISH_KIND_CHOICES):
        if value == kind:
            return index
    return _DEFAULT_CHOICE_INDEX


class PublishWordpressDialog(DialogFindMixin):  # _find: ui.views._support
    """Code-side behaviour for ``publish_wordpress_dlg`` (Phase 4b).

    Mirrors ``SettingsDialog``'s shape: resolve the frozen controls
    once, render the values the dialog opens with, and collect a
    fresh record when OK is clicked. The record is a
    :class:`PublishForm` -- the same frozen snapshot the publish path
    takes -- so the dialog cannot change out from under a publish
    already in flight.
    """

    def __init__(  # noqa: PLR0913 -- (dialog) + the ride's name and the two seams
        self,
        dialog: wx.Dialog,
        *,
        settings: AppSettings,
        ride_name: str,
        on_publish: Callable[[PublishForm], None],
    ) -> None:
        """Decorate an already-loaded ``publish_wordpress_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` the app bootstrap loaded from
                ``dialogs.xrc``.
            settings: The live :class:`AppSettings` the site fields
                open on -- its ``wp_url``/``wp_username``/
                ``wp_password``/``wp_parent``/``wp_status``/``wp_kind``,
                so the operator re-publishes an already-configured site
                (and page kind) without retyping anything, and the
                password never has to be re-entered.
            ride_name: The live ride's name; the title and slug open
                derived from it (:func:`default_title`,
                :func:`default_slug`), and ``wp_kind_choice`` re-derives
                them when it moves.
            on_publish: Called with the collected form once it passes
                :meth:`PublishForm.errors`; the app bootstrap wires it
                to render + publish.
        """
        self.dialog = dialog
        self.on_publish = on_publish
        self.ride_name = ride_name

        self.kind_choice = self._find(ids.WP_KIND_CHOICE, wx.Choice)
        self.url_input = self._find(ids.WP_URL_INPUT, wx.TextCtrl)
        self.username_input = self._find(ids.WP_USERNAME_INPUT, wx.TextCtrl)
        self.password_input = self._find(ids.WP_PASSWORD_INPUT, wx.TextCtrl)
        self.title_input = self._find(ids.WP_TITLE_INPUT, wx.TextCtrl)
        self.slug_input = self._find(ids.WP_SLUG_INPUT, wx.TextCtrl)
        self.parent_input = self._find(ids.WP_PARENT_INPUT, wx.TextCtrl)
        self.status_choice = self._find(ids.WP_STATUS_CHOICE, wx.Choice)

        self.show_form(settings, ride_name)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_ok, id=wx.ID_OK)
        self.dialog.Bind(wx.EVT_CHOICE, self._on_kind_change, self.kind_choice)

    def show_form(self, settings: AppSettings, ride_name: str) -> None:
        """Render the dialog's opening values.

        The site fields come from *settings* (the operator's own stored
        site), and the title and slug are derived from *ride_name* --
        this ride's page, not the previous one's, and through the stored
        page kind rather than the full results alone. The two choices
        get their items here rather than in XRC: each one's labels and
        values come from a single place (:data:`STATUS_CHOICES`,
        :data:`PUBLISH_KIND_CHOICES`), so a label can never render
        beside another value's name.

        *ride_name* is kept as well as rendered:
        ``wp_kind_choice``'s handler re-derives the title and slug from
        it when the operator changes the kind.

        Args:
            settings: The live settings the site fields open on.
            ride_name: The live ride's name, for the title and slug.
        """
        self.ride_name = ride_name
        self.url_input.SetValue(settings.wp_url)
        self.username_input.SetValue(settings.wp_username)
        self.password_input.SetValue(settings.wp_password)
        self.parent_input.SetValue(settings.wp_parent)
        # Set() clears any selection, so each index follows its Set().
        self.kind_choice.Set([label for label, _value in PUBLISH_KIND_CHOICES])
        self.kind_choice.SetSelection(_kind_index(settings.wp_kind))
        self.status_choice.Set([label for label, _value in STATUS_CHOICES])
        self.status_choice.SetSelection(_status_index(settings.wp_status))
        # The title and slug follow the selected kind, not the stored
        # one: an unrecognised stored value falls back to the choice's
        # first item (Full results), and the two must agree.
        self._fill_page_name()

    def _fill_page_name(self) -> None:
        """Re-derive the title and slug from the selected page kind.

        Reads the choice rather than the settings, so the rendered
        selection is the one source: the labels and their values meet in
        :func:`kind_value`, and an unrecognised label falls back to the
        full-results kind there.
        """
        kind = kind_value(self.kind_choice.GetStringSelection())
        self.title_input.SetValue(default_title(self.ride_name, kind))
        self.slug_input.SetValue(default_slug(self.ride_name, kind))

    def collect_form(self) -> PublishForm:
        """Read the controls into a fresh :class:`PublishForm`.

        Every text field is trimmed: a pasted field commonly carries
        edge whitespace, and ``errors``' own emptiness checks must see
        a whitespace-only box as empty rather than as input. The two
        choices read through :func:`status_value` and
        :func:`kind_value`, so a label this build does not know can
        never publish something by accident.
        """
        return PublishForm(
            base_url=self.url_input.GetValue().strip(),
            username=self.username_input.GetValue().strip(),
            password=self.password_input.GetValue().strip(),
            title=self.title_input.GetValue().strip(),
            slug=self.slug_input.GetValue().strip(),
            parent=self.parent_input.GetValue().strip(),
            status=status_value(self.status_choice.GetStringSelection()),
            kind=kind_value(self.kind_choice.GetStringSelection()),
        )

    # wx handler signature; re-fill is the reaction, no Skip needed
    def _on_kind_change(self, event: Any) -> None:  # noqa: ANN401, ARG002
        """Re-derive the title and slug for the newly chosen kind.

        The dropdown decides what the page carries, so the two fields it
        seeds follow it. Both stay editable afterwards: this writes the
        newly derived default, and only a later kind change overwrites
        an edit already made.
        """
        self._fill_page_name()

    # wx handler signature; EndModal is explicit, no Skip needed
    def _on_ok(self, event: Any) -> None:  # noqa: ANN401, ARG002
        """Collect, validate, then publish -- or stay open.

        A refused form shows the presenter's own messages in one native
        alert and leaves the dialog up with the operator's input intact,
        so the correction is a field edit rather than a re-entry (UX
        core rule 7). Cancel needs no handler: wx binds Escape and a
        click on ``wxID_CANCEL`` itself, and the dialog closes without
        publishing.
        """
        form = self.collect_form()
        errors = form.errors()
        if errors:
            std_dialogs.show_error(self.dialog, _ERROR_TITLE, "\n".join(errors))
            return
        self.on_publish(form)
        self.dialog.EndModal(wx.ID_OK)


def load_publish_running_window(parent: wx.Window) -> wx.Dialog:
    """Load ``publish_running_dlg`` from the shared XRC resource.

    ``views.simulator._load_running_window``'s shape: the app's own
    bootstrap fills the process-global ``XmlResource``
    (``app._load_xrc_resources``), so the progress window loads from
    the same resource every other app-owned window comes from, through
    :func:`~rivercrossing.ui.views._support.load_dialog` so a degraded
    singleton load self-heals instead of answering ``None``. The
    *parent* is the console frame the publish form closed back to, and
    it survives the self-heal's rebuild.
    """
    return load_dialog(wx.xrc.XmlResource.Get(), ids.PUBLISH_RUNNING_DLG, parent=parent)


class PublishRunningDialog(DialogFindMixin):  # _find: ui.views._support
    """Code-side behaviour for ``publish_running_dlg`` (Phase 4b).

    Mirrors ``SimRunningDialog``: the app loads the window, decorates it
    with this view and calls :meth:`run`, which opens the modal and
    defers :meth:`_start` into that modal's own loop. The publish runs
    on a worker thread, so the app's worker reports back through
    ``wx.CallAfter`` -- :meth:`set_status` and :meth:`pulse` are the
    main-thread seams it calls -- and calls :meth:`finish` to end the
    modal once the attempt is over.

    Cancel is a request, not an abort: the publish's HTTP call is
    blocking and cannot be interrupted, so :meth:`_on_cancel` only
    records the operator's decision (``is_cancelled``), and the worker
    stops at its next checkpoint -- before the next HTTP call, or as
    soon as the one in flight returns.
    """

    def __init__(self, dialog: wx.Dialog, *, on_start: Callable[[], None]) -> None:
        """Decorate an already-loaded ``publish_running_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` loaded from ``dialogs.xrc``.
            on_start: Called once :meth:`run`'s modal loop is up; the
                app wires it to start the off-thread publish worker.
        """
        self.dialog = dialog
        self.on_start = on_start
        self._cancelled = False

        self.progress_gauge = self._find(ids.PROGRESS_GAUGE, wx.Gauge)
        self.publish_status_lbl = self._find(ids.PUBLISH_STATUS_LBL, wx.StaticText)
        self.cancel_btn = self._find(ids.CANCEL_BTN, wx.Button)

        # wx's native Escape handling only ever looks for wxID_CANCEL,
        # and cancel_btn carries a custom id, so the escape id is set
        # explicitly -- Escape then lands on the same cancel path a
        # click does (SimRunningDialog's/views.dialogs.wire_escape_to's
        # own measured reasoning).
        self.dialog.SetEscapeId(self.cancel_btn.GetId())
        self.dialog.Bind(wx.EVT_BUTTON, self._on_cancel, self.cancel_btn)
        self.dialog.Fit()

    def _on_cancel(self, _event: wx.CommandEvent) -> None:
        """Record the cancel request; the worker polls it.

        Nothing is aborted here: the publish's HTTP call is blocking,
        so the worker notices the flag at its next checkpoint and
        abandons the attempt (the module docstring's Cancel contract).
        """
        self._cancelled = True

    def is_cancelled(self) -> bool:
        """Return whether the operator has abandoned this attempt."""
        return self._cancelled

    def set_status(self, text: str) -> None:
        """Set the live status line.

        Called on the main thread -- the app routes it through
        ``wx.CallAfter`` from the worker.
        """
        self.publish_status_lbl.SetLabel(text)

    def pulse(self) -> None:
        """Animate the gauge and pump the loop so it repaints.

        ``wx.Yield()`` runs the pending events -- the gauge paint and
        any Cancel click -- while the main thread waits on the worker.
        """
        self.progress_gauge.Pulse()
        wx.Yield()

    def run(self) -> None:
        """Show the dialog modally; return once :meth:`finish` ends it.

        The worker is started with ``wx.CallAfter`` so it begins inside
        the modal loop this call opens, then ``ShowModal`` blocks until
        the app's completion path calls :meth:`finish`.
        """
        wx.CallAfter(self._start)
        self.dialog.ShowModal()

    def _start(self) -> None:
        """Begin the work: forward to the app's own start seam."""
        self.on_start()

    def finish(self) -> None:
        """End the modal: the app's seam for closing the window."""
        self.dialog.EndModal(wx.ID_OK)
