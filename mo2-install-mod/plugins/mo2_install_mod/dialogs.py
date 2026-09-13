"""Controls for MO2 2.5.2 installers and native Replace."""

from PyQt6.QtWidgets import QApplication, QComboBox, QMessageBox, QProgressDialog, QPushButton, QWidget

from . import fomod


def kind(dialog: QWidget) -> str:
    name = dialog.metaObject().className()
    if name == "FomodInstallerWindow":
        return "fomod-plus"
    if name == "SimpleInstallDialog" and dialog.findChild(QComboBox, "nameCombo"):
        return "simple"
    if name == "QueryOverwriteDialog" and dialog.findChild(QPushButton, "replaceBtn"):
        return "replace"
    root_widget = dialog.findChild(QWidget, "installDialogMenu")
    if (root_widget is not None and root_widget.metaObject().className() == "RootBuilderInstall"
            and dialog.findChild(QComboBox, "modNameCombo")
            and dialog.findChild(QPushButton, "installBtn")):
        return "root-builder"
    if isinstance(dialog, QProgressDialog):
        return "progress"
    return "other"


def current() -> QWidget | None:
    window = QApplication.activeModalWidget()
    # A native notice takes precedence over the non-modal FOMOD Plus wizard.
    return window if window is not None and window.isVisible() else fomod.window()


def describe(dialog: QWidget) -> dict:
    if kind(dialog) == "fomod-plus":
        return fomod.Wizard(dialog).snapshot()
    detail = {"kind": kind(dialog), "title": dialog.windowTitle(),
              "class": dialog.metaObject().className()}
    if isinstance(dialog, QMessageBox):
        detail.update(text=dialog.text(), informative_text=dialog.informativeText(),
                      buttons=[button.text() for button in dialog.buttons()])
    return detail


def accept_installer(dialog: QWidget, name: str) -> str:
    installer = kind(dialog)
    combo_name, button_name = {
        "simple": ("nameCombo", "okBtn"),
        "root-builder": ("modNameCombo", "installBtn"),
    }[installer]
    combo = dialog.findChild(QComboBox, combo_name)
    combo.setCurrentText(name)
    if combo.currentText() != name:
        raise RuntimeError("The installer did not accept the requested mod name.")
    button = dialog.findChild(QPushButton, button_name)
    if button is None or not button.isEnabled():
        raise RuntimeError("The installer's Install button is unavailable.")
    button.click()
    return installer


def replace(dialog: QWidget) -> None:
    # Keep the native backup checkbox unchanged.
    dialog.findChild(QPushButton, "replaceBtn").click()


def cancel(dialog: QWidget) -> bool:
    if kind(dialog) == "fomod-plus":
        fomod.cancel(dialog)
        return True
    if kind(dialog) in {"simple", "root-builder", "replace"}:
        dialog.findChild(QPushButton, "cancelBtn").click()
        return True
    if isinstance(dialog, QProgressDialog):
        buttons = [button for button in dialog.findChildren(QPushButton) if button.isVisible() and button.isEnabled()]
        if len(buttons) == 1:
            # Click the real button so its canceled signal reaches the native worker.
            buttons[0].click()
            return True
    return False
