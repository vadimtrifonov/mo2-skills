"""MO2-owned installation calls and their local command endpoint."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import sys
import time
import uuid

import mobase
from PyQt6.QtCore import QSettings, QTimer, qVersion

from . import dialogs
from .wire import Error, PROTOCOL, TERMINAL, VERSION, channel, identifier, path_key, read_json, validate_name, write_json


@dataclass
class Installation:
    id: str
    archive: Path
    name: str
    profile: str
    replace: bool
    source: dict
    previous: dict | None
    status: str = "accepted"
    installer: str | None = None
    error: dict | None = None
    dialog: dict | None = None
    cancel_requested: bool = False
    native_started: bool = False
    native_result: object = None
    guessed_name: object = None  # Borrowed only while the synchronous installMod call is on the stack.
    output: dict = field(default_factory=dict)

    def snapshot(self, session: str) -> dict:
        result = {"operation": self.id, "session": session, "status": self.status,
                  "archive": str(self.archive), "name": self.name, "profile": self.profile,
                  "replace": self.replace, "installer": self.installer,
                  "cancel_requested": self.cancel_requested}
        if self.error:
            result["error"] = self.error
        if self.dialog:
            result["dialog"] = self.dialog
        result.update(self.output)
        return result


class InstallMod(mobase.IPluginInstallerSimple):
    def __init__(self):
        super().__init__()
        self.organizer = None
        self.window = None
        self.active: Installation | None = None
        self.operations: dict[str, Installation] = {}
        self.replies: dict[str, dict] = {}
        self.pending: dict[Path, dict] = {}
        self.native_busy = False
        self.calling: Installation | None = None
        self.native_owner: Installation | None = None
        self.accepting = True

    def name(self):
        return "Install Mod"

    def author(self):
        return "Vadim"

    def description(self):
        return "Installs Nexus archives through Simple Installer and Root Builder from a local client."

    def version(self):
        return mobase.VersionInfo(VERSION)

    def settings(self):
        return []

    def priority(self):
        return 0xFFFFFFFF

    def isManualInstaller(self):
        return False

    def init(self, organizer):
        self.organizer = organizer
        if organizer.appVersion() != mobase.VersionInfo("2.5.2") or qVersion() != "6.7.1":
            logging.error("Install Mod requires MO2 2.5.2 / Qt 6.7.1.")
            return False
        organizer.onUserInterfaceInitialized(self.ready)
        return True

    def ready(self, window):
        self.window = window
        self.session = uuid.uuid4().hex
        self.root = channel(self.organizer.basePath())
        self.folder = self.root / self.session
        for name in ("requests", "replies", "results"):
            (self.folder / name).mkdir(parents=True, exist_ok=True)
        self.endpoint = {"protocol": PROTOCOL, "version": VERSION, "pid": os.getpid(),
                         "session": self.session, "instance": self.organizer.basePath(),
                         "mo2_version": self.organizer.appVersion().canonicalString(),
                         "qt_version": qVersion(), "python_version": sys.version.split()[0],
                         "downloads": self.organizer.downloadsPath(), "mods": self.organizer.modsPath()}
        write_json(self.root / "endpoint.json", self.endpoint)
        self.timer = QTimer(window)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    def publish(self, path: Path, value: dict):
        self.pending[path] = value

    def poll(self):
        # Nested native dialog event loops keep this timer running while installMod blocks.
        try:
            for path in list((self.folder / "requests").glob("*.json")):
                request_id = path.stem
                if request_id not in self.replies:
                    try:
                        identifier(request_id)
                        request = read_json(path)
                        if request.get("id") != request_id or request.get("protocol") != PROTOCOL:
                            raise Error("INVALID_REQUEST", "Request ID or protocol does not match.")
                        if (request.get("session") != self.session
                                or path_key(request.get("instance", "")) != path_key(self.organizer.basePath())):
                            raise Error("WRONG_INSTANCE", "Request does not identify this MO2 session.")
                        if not isinstance(request.get("expires"), (int, float)) or request["expires"] < time.time():
                            raise Error("EXPIRED", "Request expired before MO2 accepted it.")
                        result = self.dispatch(request)
                    except Exception as exc:
                        result = {"status": "failed", "error": self.problem(exc)}
                    self.replies[request_id] = result
                self.publish(self.folder / "replies" / path.name, self.replies[request_id])
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            for path, result in list(self.pending.items()):
                try:
                    write_json(path, result)
                    del self.pending[path]
                except OSError:
                    # A reader can briefly prevent replacement on Windows. Retain the reply.
                    pass
            self.drive_dialog()
        except Exception as exc:
            self.accepting = False
            if self.active:
                self.active.error = self.problem(exc)
                self.active.status = "needs_input"
            logging.exception("Install Mod: %s", exc)

    @staticmethod
    def problem(exc: Exception) -> dict:
        return exc.json() if isinstance(exc, Error) else {"code": "MO2_ERROR", "message": str(exc)}

    def dispatch(self, request: dict) -> dict:
        command = request.get("command")
        if command == "status":
            operation = request.get("operation")
            if operation:
                job = self.operations.get(identifier(operation))
                if job is None:
                    raise Error("UNKNOWN_OPERATION", "MO2 has not accepted this operation in this session.")
                return job.snapshot(self.session)
            if not self.organizer.isPluginEnabled(self.name()):
                raise Error("PLUGIN_DISABLED", "Enable Install Mod in MO2's plugin settings.")
            if not self.accepting:
                raise Error("CONTROLLER_STOPPED", "Install Mod stopped accepting installations after an internal error. Inspect MO2's log before restarting.")
            return dict(self.endpoint, status="ready", profile=self.organizer.profileName(),
                        active=self.active.snapshot(self.session) if self.active else None)
        if command == "cancel":
            job = self.operations.get(identifier(request.get("operation")))
            if job is None:
                raise Error("UNKNOWN_OPERATION", "No such installation in this session.")
            if job.status not in TERMINAL:
                job.cancel_requested = True
            return job.snapshot(self.session)
        if command != "install":
            raise Error("INVALID_COMMAND", "Expected install, status, or cancel.")
        if not self.organizer.isPluginEnabled(self.name()):
            raise Error("PLUGIN_DISABLED", "Enable Install Mod in MO2's plugin settings.")
        if not self.accepting or self.active or self.native_busy or dialogs.modal():
            raise Error("MO2_BUSY", "Finish the current MO2 operation or dialog first.")
        if request.get("profile") != self.organizer.profileName():
            raise Error("WRONG_PROFILE", "The active MO2 profile differs from the requested profile.")
        if type(request.get("replace")) is not bool:
            raise Error("INVALID_REQUEST", "replace must be a boolean.")
        name = validate_name(request.get("name"))
        archive = Path(request["archive"]).resolve()
        if (not archive.is_file() or archive.parent != Path(self.organizer.downloadsPath()).resolve()
                or archive.suffix.lower() not in {".zip", ".7z", ".rar"}):
            raise Error("INVALID_ARCHIVE", "Use a completed ZIP, 7z, or RAR directly in MO2's Downloads directory.")
        previous = self.target(name, request["replace"])
        source = self.source(archive)
        job = Installation(request["id"], archive, name, request["profile"], request["replace"], source, previous)
        self.operations[job.id] = job
        self.active = job
        QTimer.singleShot(0, lambda: self.run(job))
        return job.snapshot(self.session)

    def target(self, name: str, replacing: bool) -> dict | None:
        mods = self.organizer.modList()
        mod = mods.getMod(name)
        directory = Path(self.organizer.modsPath()) / name
        exists = directory.exists() or mod is not None
        if exists != replacing:
            raise Error("MOD_EXISTS" if exists else "MOD_NOT_FOUND",
                        "The new destination already exists." if exists else "The replacement destination does not exist.")
        if not replacing:
            return None
        if (mod is None or mod.name() != name or not directory.is_dir() or mod.isBackup() or mod.isSeparator()
                or path_key(mod.absolutePath()) != path_key(directory)):
            raise Error("INVALID_TARGET", "Replace requires a regular installed mod in MO2's Mods directory.")
        return {"priority": mods.priority(name), "active": bool(int(mods.state(name)) & int(mobase.ModState.active))}

    @staticmethod
    def ini(path: Path) -> QSettings:
        settings = QSettings(str(path), QSettings.Format.IniFormat)
        settings.setFallbacksEnabled(False)
        return settings

    @staticmethod
    def associations(settings: QSettings) -> list[list[int]]:
        count = settings.beginReadArray("installedFiles")
        pairs = []
        for index in range(count):
            settings.setArrayIndex(index)
            pairs.append([settings.value("modid", 0, type=int), settings.value("fileid", 0, type=int)])
        settings.endArray()
        return pairs

    def source(self, archive: Path) -> dict:
        sidecar = Path(str(archive) + ".meta")
        if not sidecar.is_file():
            raise Error("MISSING_METADATA", "Prepare the archive's Nexus .meta file before installation.")
        data = self.ini(sidecar)
        result = {key: data.value(key, "") for key in
                  ("gameName", "modID", "fileID", "version", "repository", "fileCategory")}
        try:
            result["modID"] = int(result["modID"])
            result["fileID"] = int(result["fileID"])
            result["fileCategory"] = int(result["fileCategory"])
        except (TypeError, ValueError):
            raise Error("INVALID_METADATA", "Nexus mod/file IDs and fileCategory must be integers.") from None
        if (not 0 < result["modID"] <= 0x7FFFFFFF or not 0 < result["fileID"] <= 0x7FFFFFFF or result["repository"] != "Nexus"
                or self.organizer.getGame(result["gameName"]) is None
                or not result["version"] or not mobase.VersionInfo(result["version"]).isValid()):
            raise Error("INVALID_METADATA", "Provide a supported source game, positive Nexus IDs, repository=Nexus, and selected-file version.")
        return result

    def context(self, job: Installation):
        if self.organizer.profileName() != job.profile:
            raise Error("WRONG_PROFILE", "The profile changed during installation.")

    def run(self, job: Installation):
        if self.active is not job:
            return
        if job.cancel_requested:
            self.finish(job, "cancelled")
            return
        try:
            self.context(job)
            self.target(job.name, job.replace)
            if dialogs.modal():
                raise Error("MO2_BUSY", "A dialog opened before installation started.")
            job.status = "installing"
            self.calling = job
            installed = self.organizer.installMod(str(job.archive), job.name)
            returned_name = installed.name() if installed is not None else None
            del installed
        except Exception as exc:
            job.error = self.problem(exc)
            returned_name = None
        finally:
            self.calling = None
            job.guessed_name = None
        if job.native_started and job.native_result is None:
            # An exception or failed observation is not proof that native installation ended.
            job.status = "needs_input"
            job.error = job.error or {"code": "UNKNOWN_COMPLETION", "message": "Native completion was not observed. Inspect MO2 before continuing."}
            return
        if job.native_result != mobase.InstallResult.SUCCESS or returned_name != job.name:
            if job.error:
                self.finish(job, "failed")
            elif job.native_result == mobase.InstallResult.CANCELED:
                self.finish(job, "cancelled")
            else:
                job.error = {"code": "INSTALL_FAILED", "message": "MO2 did not return the requested installed mod."}
                self.finish(job, "failed")
            return
        job.status = "refreshing"
        job.dialog = None
        try:
            self.organizer.onNextRefresh(lambda: QTimer.singleShot(0, lambda: self.verify(job)), False)
            self.organizer.refresh(True)
        except Exception as exc:
            job.error = self.problem(exc)
            job.status = "needs_input"

    def onInstallationStart(self, archive, reinstallation, currentMod):
        self.native_busy = True
        job = self.calling
        self.native_owner = job if job and path_key(archive) == path_key(job.archive) else None
        if self.native_owner:
            self.native_owner.native_started = True

    def onInstallationEnd(self, result, newMod):
        self.native_busy = False
        job, self.native_owner = self.native_owner, None
        if job is self.active and job is not None and job.native_result is None:
            job.native_result = result

    def isArchiveSupported(self, tree):
        return self.active is not None and self.native_owner is self.active

    def install(self, name, tree, version, nexus_id):
        job = self.active
        if not job:
            return mobase.InstallResult.NOT_ATTEMPTED
        try:
            self.context(job)
            self.target(job.name, job.replace)
            unsupported = []
            def inspect(path, entry):
                if entry.name().lower() in {"moduleconfig.xml", "meta.ini"}:
                    unsupported.append(entry.path())
                    return mobase.IFileTree.STOP
                return mobase.IFileTree.CONTINUE
            tree.walk(inspect)
            if unsupported:
                raise Error("INSTALLER_REQUIRED", f"This archive needs a different installation procedure: {unsupported[0]}")
            if job.cancel_requested:
                return mobase.InstallResult.CANCELED
            # Reset the variants as well: Root Builder initially selects the first variant.
            name.reset(job.name, mobase.GuessQuality.USER)
            name.setFilter(lambda value: job.name)
            job.guessed_name = name
        except Exception as exc:
            job.error = self.problem(exc)
            return mobase.InstallResult.CANCELED
        # Observe and constrain the destination; existing installers still prepare the tree.
        return mobase.InstallResult.NOT_ATTEMPTED

    def drive_dialog(self):
        job = self.active
        if not job or job.native_result is not None or self.native_owner is not job:
            return
        dialog = dialogs.modal()
        if dialog is None:
            return
        detail = dialogs.describe(dialog)
        if job.cancel_requested or job.error:
            if not dialogs.cancel(dialog):
                job.status = "needs_input"
                job.dialog = dict(detail, message="Cancel this dialog in MO2.")
            return
        try:
            self.context(job)
            if detail["kind"] in {"simple", "root-builder"}:
                self.target(job.name, job.replace)
                job.installer = detail["kind"]
                job.status = "installing"
                job.dialog = None
                dialogs.accept_installer(dialog, job.name)
            elif detail["kind"] == "replace":
                if not job.replace or job.guessed_name is None or str(job.guessed_name) != job.name:
                    raise Error("WRONG_DESTINATION", "The overwrite destination is not the requested replacement.")
                self.target(job.name, True)
                dialogs.replace(dialog)
            elif detail["kind"] != "progress":
                job.status = "needs_input"
                job.dialog = detail
        except Exception as exc:
            job.error = self.problem(exc)
            dialogs.cancel(dialog)

    def verify(self, job: Installation):
        if self.active is not job:
            return
        try:
            self.context(job)
            mod = self.organizer.modList().getMod(job.name)
            if mod is None:
                raise Error("VERIFY_FAILED", "The installed mod is missing after refresh.")
            directory = Path(mod.absolutePath())
            meta = self.ini(directory / "meta.ini")
            actual = {"gameName": mod.gameName(), "modID": mod.nexusId(),
                      "version": mod.version().canonicalString(),
                      "installationFile": mod.installationFile(),
                      "installedFiles": self.associations(meta),
                      "repository": meta.value("repository", ""), "category": meta.value("category", ""),
                      "nexusFileStatus": meta.value("nexusFileStatus", 0, type=int)}
            source = job.source
            if (actual["gameName"] != source["gameName"] or actual["modID"] != source["modID"]
                    or mod.version() != mobase.VersionInfo(source["version"])
                    or [source["modID"], source["fileID"]] not in actual["installedFiles"]
                    or Path(actual["installationFile"]).name != job.archive.name):
                raise Error("VERIFY_FAILED", "Installed source, version, or archive association differs from the requested download.")
            if meta.value("repository", "") != "Nexus":
                raise Error("VERIFY_FAILED", "The installed repository is not Nexus.")
            if job.previous and self.target(job.name, True) != job.previous:
                raise Error("PROFILE_CHANGED", "Replacement changed the mod's enabled state or priority.")
            content = sorted(p.name for p in directory.iterdir() if p.name.lower() != "meta.ini")
            if not content:
                raise Error("VERIFY_FAILED", "The installed mod contains no payload.")
            download = self.ini(Path(str(job.archive) + ".meta"))
            if not download.value("installed", False, type=bool) or download.value("uninstalled", False, type=bool):
                raise Error("VERIFY_FAILED", "MO2 has not marked this download installed.")
            job.output = {"mod_path": str(directory), "metadata": actual, "content": content}
            self.finish(job, "complete")
        except Exception as exc:
            job.error = self.problem(exc)
            self.finish(job, "failed")

    def finish(self, job: Installation, status: str):
        job.status = "failed" if job.error else status
        job.dialog = None
        self.publish(self.folder / "results" / f"{job.id}.json", job.snapshot(self.session))
        if self.active is job:
            self.active = None
