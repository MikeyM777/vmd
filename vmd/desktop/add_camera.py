"""The one dialog that sets up the second camera, asked once and never again.

Everything in it is already filled in before it opens. The two cameras on this
desk are the same model on the same network with the same login and the same
stream paths, so the only thing that genuinely differs is the last part of the
address - and even that is guessed, because they are numbered 250 and 251. What
is left for the operator is to press OK, or to correct the guess.

That is deliberate rather than lazy. The alternative was what he was doing:
editing an address by hand in three separate places, and getting two of the
three right - a console showing a picture it could not steer.

Thin on purpose. Everything it decides is decided in `vmd/desktop/presets.py`,
where it can be tested without a window; this is the part that asks.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from vmd.desktop.presets import last_part


@dataclass(frozen=True)
class Answer:
    """What the operator said the other camera is."""

    host: str
    username: str
    password: str
    title: str

    @property
    def name(self) -> str:
        """The folder it goes in, which is what its button will say."""
        return last_part(self.host)


class AddCameraDialog(QDialog):
    """Ask for the other camera. Opens with the answer already in it."""

    def __init__(
        self,
        suggested_host: str,
        username: str,
        password: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("The other camera")

        outer = QVBoxLayout(self)
        blurb = QLabel(
            "This is asked once. After it, the other camera is a button beside "
            "this one and nobody has to type an address again."
        )
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        form = QFormLayout()
        self._host = QLineEdit(suggested_host)
        self._host.setToolTip(
            "The other camera's address. Everything else about it - the login, "
            "the streams - is copied from this camera, so only this has to be "
            "right."
        )
        # The login is shown rather than hidden away, because it is the one
        # thing that might legitimately differ and the operator cannot correct
        # what he cannot see. It is filled in from this camera, which on this
        # site is the same on both.
        self._username = QLineEdit(username)
        self._password = QLineEdit(password)
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._title = QLineEdit()
        self._title.setPlaceholderText("what it watches")
        self._title.setToolTip(
            "The name on the window and on its button. Left empty, the camera's "
            "number is used."
        )

        form.addRow("Address", self._host)
        form.addRow("Username", self._username)
        form.addRow("Password", self._password)
        form.addRow("Name", self._title)
        outer.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self._buttons = buttons

        self._host.textChanged.connect(self._only_with_an_address)
        self._only_with_an_address()

    def _only_with_an_address(self) -> None:
        """OK is refused while there is no address to point a console at.

        A camera folder written with an empty address is a second console that
        opens on nothing, with a button that will not go away.
        """
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setEnabled(bool(self._host.text().strip()))

    def answer(self) -> Answer:
        return Answer(
            host=self._host.text().strip(),
            username=self._username.text().strip(),
            password=self._password.text(),
            title=self._title.text().strip(),
        )
