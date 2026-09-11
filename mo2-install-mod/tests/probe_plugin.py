"""Sandbox-only observation and test controls. Never deployed with Install Mod."""

import json
from pathlib import Path
import tempfile

import mobase
from PyQt6.QtCore import QSettings, QTimer, Qt
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QProgressDialog, QTreeView, QVBoxLayout


class Probe(mobase.IPluginInstallerSimple):
    def __init__(self):
        super().__init__()
        self.control = {}
        self.dialog = None
        self.seen = set()

    def name(self): return "Install Test Probe"
    def author(self): return "Test"
    def description(self): return "Disposable instance test controls"
    def version(self): return mobase.VersionInfo("1.0.0")
    def settings(self): return []
    def priority(self): return 0xFFFFFFFE
    def isManualInstaller(self): return False

    def init(self, organizer):
        self.organizer = organizer
        self.root = Path(organizer.basePath()).resolve()
        if (not self.root.is_relative_to(Path(tempfile.gettempdir()).resolve())
                or not (self.root / "INSTALL_MOD_TEST_INSTANCE").is_file()):
            return False
        self.seen = {p.name for p in self.root.glob("test-*.json")}
        organizer.onUserInterfaceInitialized(self.ready)
        return True

    def emit(self, value):
        with (self.root / "hooks.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value) + "\n")

    def ready(self, window):
        self.window = window
        self.timer = QTimer(window)
        self.timer.timeout.connect(self.poll)
        self.timer.start(100)
        self.emit({"event": "ready"})

    def poll(self):
        for path in self.root.glob("test-*.json"):
            if path.name in self.seen:
                continue
            self.seen.add(path.name)
            try:
                command = json.loads(path.read_text(encoding="utf-8"))
                action = command["command"]
                if action == "stop":
                    QTimer.singleShot(0, self.window.close)
                elif action == "control":
                    self.control = command
                elif action == "release" and self.dialog is not None:
                    self.dialog.reject()
                elif action == "snapshot":
                    mods = []
                    for name in self.organizer.modList().allMods():
                        mod = self.organizer.modList().getMod(name)
                        if mod and Path(mod.absolutePath()).resolve().is_relative_to(self.root / "mods"):
                            mods.append({"name": name, "path": mod.absolutePath(), "version": mod.version().canonicalString(),
                                         "archive": mod.installationFile(), "gameName": mod.gameName(), "modID": mod.nexusId(),
                                         "priority": self.organizer.modList().priority(name),
                                         "active": bool(int(self.organizer.modList().state(name)) & int(mobase.ModState.active))})
                    command["mods"] = mods
                    view = self.window.findChild(QTreeView, "downloadView")
                    model = view.model()
                    command["downloads"] = {
                        "headers": [str(model.headerData(c, Qt.Orientation.Horizontal)) for c in range(model.columnCount())],
                        "rows": [[str(model.index(r, c).data()) for c in range(model.columnCount())] for r in range(model.rowCount())],
                    }
                    command["dialogs"] = [{"title": w.windowTitle(), "class": w.metaObject().className(),
                                             "object": w.objectName()} for w in QApplication.topLevelWidgets() if w.isVisible()]
                elif action == "read_ini":
                    source = Path(command["path"]).resolve()
                    assert source.is_relative_to(self.root)
                    settings = QSettings(str(source), QSettings.Format.IniFormat)
                    command["values"] = {key: settings.value(key) for key in settings.allKeys()}
                elif action == "profile":
                    self.organizer.modList().setActive(command["name"], command["active"])
                    self.organizer.modList().setPriority(command["name"], command["priority"])
                elif action == "gui_install":
                    QTimer.singleShot(0, lambda c=command: self.organizer.installMod(c["archive"], c["name"]))
                (self.root / ("reply-" + path.name)).write_text(json.dumps(command), encoding="utf-8")
            except Exception as exc:
                self.emit({"event": "probe_error", "error": str(exc)})

    def onInstallationStart(self, archive, reinstallation, mod):
        self.emit({"event": "start", "archive": archive, "reinstallation": reinstallation})

    def onInstallationEnd(self, result, mod):
        self.emit({"event": "end", "result": str(result), "mod": mod.name() if mod else None})

    def isArchiveSupported(self, tree): return True

    def install(self, name, tree, version, nexus_id):
        self.emit({"event": "installer", "name": str(name), "version": version})
        control, self.control = self.control, {}
        if control.get("rename"):
            name.reset(control["rename"], mobase.GuessQuality.USER)
        if control.get("hold") or control.get("progress"):
            if control.get("progress"):
                self.dialog = QProgressDialog("Test installer is waiting", "Cancel", 0, 0, self.window)
                self.dialog.canceled.connect(self.dialog.reject)
            else:
                self.dialog = QDialog(self.window)
                self.dialog.setWindowTitle("Test installation pause")
                layout = QVBoxLayout(self.dialog)
                layout.addWidget(QLabel("A test-owned installer is waiting for release."))
            self.dialog.exec()
            self.dialog = None
            return mobase.InstallResult.CANCELED
        return mobase.InstallResult.NOT_ATTEMPTED


def createPlugin(): return Probe()
