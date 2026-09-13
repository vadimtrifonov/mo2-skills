"""FOMOD Plus package checks and wizard controls. Native code owns choices."""

from __future__ import annotations

import hashlib
import json
from xml.dom import minidom
from xml.parsers.expat import ExpatError

from PyQt6.QtCore import QCoreApplication, QEvent
from PyQt6.QtGui import QTextDocument
from PyQt6.QtWidgets import (QAbstractButton, QApplication, QCheckBox, QComboBox, QGroupBox, QLabel,
                             QPushButton, QRadioButton, QScrollArea, QStackedWidget)

from .wire import Error


def one(values, description):
    if len(values) != 1:
        raise Error("INSTALLER_REQUIRED", f"Cannot identify FOMOD Plus's {description}.")
    return values[0]


def window():
    windows = [w for w in QApplication.topLevelWidgets()
               if w.isVisible() and w.metaObject().className() == "FomodInstallerWindow"]
    return one(windows, "installer window") if windows else None


def button(dialog, *texts):
    translated = {QCoreApplication.translate("FomodInstallerWindow", text) for text in texts}
    return one([b for b in dialog.findChildren(QPushButton) if b.text() in translated], f"{'/'.join(texts)} button")


def cancel(dialog):
    button(dialog, "Cancel").click()


def check_destinations(config_path, archive, prefix):
    """Reject any mapping that could supply MO2's root metadata, selected or not."""
    try:
        config = minidom.parse(str(config_path))
    except ExpatError as exc:
        raise Error("INVALID_ARCHIVE", f"Cannot read FOMOD ModuleConfig.xml: {exc}") from exc
    with config:
        for mapping in config.getElementsByTagName("*"):
            # Keep literal names, like FOMOD Plus's pugixml parser: a default
            # namespace still permits <file>, but <prefix:file> is not a mapping.
            if mapping.tagName not in {"file", "folder"}:
                continue
            qualified = prefix + "/" + mapping.getAttribute("source")
            source = archive.find(qualified)
            if source is None:
                continue  # FOMOD Plus skips missing sources.
            destination = mapping.getAttribute("destination") if mapping.hasAttribute("destination") else qualified
            # Use the same native tree operations as FOMOD Plus. Do not evaluate
            # conditions or combine mutually exclusive mappings into one file tree.
            projected = archive.createOrphanTree()
            if source.isDir():
                copies = [(entry, destination + "/" + entry.name() if destination else entry.name()) for entry in source]
            else:
                copies = [(source, destination)]
            for entry, target in copies:
                if projected.copy(entry, target) is None:
                    raise Error("INSTALLER_REQUIRED", f"Cannot check FOMOD destination: {target}")
            if any(entry.name().partition(":")[0].rstrip(" .").casefold() == "meta.ini" for entry in projected):
                raise Error("INSTALLER_REQUIRED",
                            f"FOMOD copy from {qualified!r} to {destination!r} can overwrite the installed mod's meta.ini, "
                            "which stores MO2 notes and categories. All file-copy instructions are checked, including conditional and unselected ones.")


class Wizard:
    """Borrow widgets for one UI-thread call; never retain this object across calls."""

    def __init__(self, dialog):
        self.dialog = dialog
        self.stack = one(dialog.findChildren(QStackedWidget), "step stack")
        self.name = one(dialog.findChildren(QComboBox), "mod name")
        self.forward = button(dialog, "Next", "Install")
        self.back = button(dialog, "Back")
        self.step = self.stack.currentIndex()
        self.page = self.stack.currentWidget()
        self.groups = {}
        self.options = {}
        for index, group in enumerate(self.page.findChildren(QGroupBox)):
            group_id = f"{self.step}.{index}"
            controls = [b for b in group.children() if isinstance(b, (QCheckBox, QRadioButton))]
            self.groups[group_id] = (group.title(), controls)
            for option_index, control in enumerate(controls):
                self.options[f"{group_id}.{option_index}"] = control

    @staticmethod
    def option(option_id, control):
        return {"id": option_id, "name": control.text(), "selected": control.isChecked(),
                "enabled": control.isEnabled(), "control": "radio" if isinstance(control, QRadioButton) else "checkbox"}

    def snapshot(self):
        advance = self.forward.text()
        if advance == QCoreApplication.translate("FomodInstallerWindow", "Next"):
            action = "next"
        elif advance == QCoreApplication.translate("FomodInstallerWindow", "Install"):
            action = "install"
        else:
            raise Error("INSTALLER_REQUIRED", "Cannot identify FOMOD Plus's current navigation action.")
        actions = ["stay"]
        if self.back.isEnabled():
            actions.append("back")
        if self.forward.isEnabled():
            actions.append(action)
        detail = {"kind": "fomod-plus", "title": self.dialog.windowTitle(), "class": "FomodInstallerWindow",
                  "step": self.step, "name": self.page.title() if isinstance(self.page, QGroupBox) else "Installation",
                  "actions": actions,
                  "groups": [{"id": key, "name": name, "options": len(controls),
                              "selected": sum(b.isChecked() for b in controls)}
                             for key, (name, controls) in self.groups.items()]}
        # Approval covers selections on every page, including pages revisited
        # before returning here. Focus and scrolling do not change the token.
        selections = [[(b.text(), b.isChecked(), b.isEnabled())
                       for b in self.stack.widget(step).findChildren(QAbstractButton)
                       if isinstance(b, (QCheckBox, QRadioButton))]
                      for step in range(self.stack.count())]
        state = [detail, self.name.currentText(), selections]
        detail["view"] = hashlib.sha256(json.dumps(state, ensure_ascii=False).encode()).hexdigest()[:16]
        return detail

    def choices(self, group_id):
        if group_id not in self.groups:
            raise Error("INVALID_CHOICE", "The group is not on the current FOMOD step.")
        name, controls = self.groups[group_id]
        return {"id": group_id, "name": name,
                "options": [self.option(f"{group_id}.{i}", b) for i, b in enumerate(controls)]}

    def describe_option(self, option_id):
        control = self.options.get(option_id)
        if control is None:
            raise Error("INVALID_CHOICE", "The option is not on the current FOMOD step.")
        # Ask the native hover handler for the description, not a second XML model.
        QApplication.sendEvent(control, QEvent(QEvent.Type.HoverEnter))
        label = one([area.widget() for area in self.dialog.findChildren(QScrollArea)
                     if isinstance(area.widget(), QLabel)], "description")
        document = QTextDocument()
        document.setHtml(label.text())
        return dict(self.option(option_id, control), description=document.toPlainText())

    def validate(self, request):
        detail = self.snapshot()
        if request.get("view") != detail["view"]:
            raise Error("STALE_VIEW", "The FOMOD step or selections changed. Read status before answering again.")
        if request.get("action") not in detail["actions"]:
            raise Error("INVALID_CHOICE", "That navigation action is not available on this step.")
        selected, deselected = request.get("select", []), request.get("deselect", [])
        if (not isinstance(selected, list) or not isinstance(deselected, list)
                or any(not isinstance(key, str) for key in selected + deselected)
                or len(set(selected + deselected)) != len(selected + deselected)):
            raise Error("INVALID_CHOICE", "Provide distinct option IDs to select or deselect.")
        for keys, value in ((selected, True), (deselected, False)):
            for key in keys:
                control = self.options.get(key)
                if control is None:
                    raise Error("INVALID_CHOICE", f"Option {key} is not on the current step.")
                if control.isChecked() == value:
                    continue
                if not control.isEnabled():
                    raise Error("INVALID_CHOICE", f"Option {key} is disabled by FOMOD Plus.")
                if not value and isinstance(control, QRadioButton):
                    raise Error("INVALID_CHOICE", "Select another radio option, including None when offered, instead of deselecting it.")

    def respond(self, request, name):
        self.validate(request)
        desired = {key: value for field, value in (("select", True), ("deselect", False)) for key in request.get(field, [])}
        for key, value in desired.items():
            control = self.options[key]
            if control.isChecked() != value:
                if not control.isEnabled():
                    raise Error("CHOICE_REJECTED", f"FOMOD Plus disabled option {key} after another selection. Read the updated choices.")
                # clicked, as well as toggled, records manual deselection in FOMOD Plus.
                control.click()
        if any(self.options[key].isChecked() != value for key, value in desired.items()):
            raise Error("CHOICE_REJECTED", "FOMOD Plus did not retain the requested selections. Read the updated choices; no navigation was performed.")
        action = request["action"]
        if action not in self.snapshot()["actions"]:
            raise Error("CHOICE_REJECTED", "Selections changed the available navigation. Read status before continuing.")
        if action == "install":
            self.name.setCurrentText(name)
            if self.name.currentText() != name:
                raise Error("WRONG_DESTINATION", "FOMOD Plus did not accept the requested mod name.")
        if action != "stay":
            (self.back if action == "back" else self.forward).click()
