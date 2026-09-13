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

from . import dialogs, fomod
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
    custom: bool = False
    status: str = "accepted"
    installer: str | None = None
    error: dict | None = None
    dialog: dict | None = None
    cancel_requested: bool = False
    responding: bool = False
    native_started: bool = False
    native_result: object = None
    native_name: str | None = None
    guessed_name: object = None  # Borrowed only while the synchronous installMod call is on the stack.
    output: dict = field(default_factory=dict)

    def snapshot(self, session: str) -> dict:
        result = {"operation": self.id, "session": session, "status": self.status,
                  "archive": str(self.archive), "name": self.name, "profile": self.profile,
                  "replace": self.replace, "custom": self.custom, "installer": self.installer,
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
        self.replies: dict[str, dict | None] = {}
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
        return "Installs Nexus and custom archives through Simple Installer, Root Builder, and FOMOD Plus from a local client."

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
                        try:
                            request = read_json(path)
                        except OSError:
                            # Windows can briefly deny access to a newly published file.
                            # Nothing has been dispatched; leave it for the next tick.
                            continue
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
                if self.replies[request_id] is not None:
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

    def dispatch(self, request: dict) -> dict | None:
        command = request.get("command")
        if command == "status":
            operation = request.get("operation")
            if operation:
                job = self.operations.get(identifier(operation))
                if job is None:
                    raise Error("UNKNOWN_OPERATION", "MO2 has not accepted this operation in this session.")
                if request.get("group") is not None or request.get("option") is not None:
                    if request.get("group") is not None and request.get("option") is not None:
                        raise Error("INVALID_REQUEST", "Request a group or an option, not both.")
                    wizard = self.waiting_wizard(job)
                    job.dialog = wizard.snapshot()
                    result = job.snapshot(self.session)
                    if request.get("group") is not None:
                        result["choices"] = wizard.choices(request["group"])
                    else:
                        result["choice"] = wizard.describe_option(request["option"])
                    return result
                return job.snapshot(self.session)
            if request.get("group") is not None or request.get("option") is not None:
                raise Error("INVALID_REQUEST", "FOMOD details require an operation ID.")
            if not self.organizer.isPluginEnabled(self.name()):
                raise Error("PLUGIN_DISABLED", "Enable Install Mod in MO2's plugin settings.")
            if not self.accepting:
                raise Error("CONTROLLER_STOPPED", "Install Mod stopped accepting installations after an internal error. Inspect MO2's log before restarting.")
            return dict(self.endpoint, status="ready", profile=self.organizer.profileName(),
                        active=self.active.snapshot(self.session) if self.active else None)
        if command == "respond":
            job = self.operations.get(identifier(request.get("operation")))
            if job is None:
                raise Error("UNKNOWN_OPERATION", "No such installation in this session.")
            wizard = self.waiting_wizard(job)
            wizard.validate(request)
            job.responding = True
            # Reserve the request before Qt callbacks can enter another event loop.
            # Unlike install acceptance, this reply describes the applied answer.
            QTimer.singleShot(0, lambda: self.respond(job, request))
            return None
        if command == "cancel":
            job = self.operations.get(identifier(request.get("operation")))
            if job is None:
                raise Error("UNKNOWN_OPERATION", "No such installation in this session.")
            if job.status not in TERMINAL:
                job.cancel_requested = True
            return job.snapshot(self.session)
        if command != "install":
            raise Error("INVALID_COMMAND", "Expected install, status, respond, or cancel.")
        if not self.organizer.isPluginEnabled(self.name()):
            raise Error("PLUGIN_DISABLED", "Enable Install Mod in MO2's plugin settings.")
        if not self.accepting or self.active or self.native_busy or dialogs.current():
            raise Error("MO2_BUSY", "Finish the current MO2 operation or dialog first.")
        if request.get("profile") != self.organizer.profileName():
            raise Error("WRONG_PROFILE", "The active MO2 profile differs from the requested profile.")
        custom = request.get("custom", False)
        if type(request.get("replace")) is not bool or type(custom) is not bool:
            raise Error("INVALID_REQUEST", "replace and custom must be booleans.")
        name = validate_name(request.get("name"))
        archive = Path(request["archive"]).resolve()
        if not archive.is_file() or archive.suffix.lower() not in {".zip", ".7z", ".rar"}:
            raise Error("INVALID_ARCHIVE", "Use a completed ZIP, 7z, or RAR archive.")
        if not custom and archive.parent != Path(self.organizer.downloadsPath()).resolve():
            raise Error("INVALID_ARCHIVE", "Use a Nexus archive directly in MO2's Downloads directory.")
        if request["replace"] and archive.is_relative_to((Path(self.organizer.modsPath()) / name).resolve()):
            raise Error("INVALID_ARCHIVE", "Replacement would remove the input archive from the target mod.")
        previous = self.target(name, request["replace"])
        source = {} if custom else self.source(archive)
        job = Installation(request["id"], archive, name, request["profile"], request["replace"], source, previous, custom)
        self.operations[job.id] = job
        self.active = job
        QTimer.singleShot(0, lambda: self.run(job))
        return job.snapshot(self.session)

    def waiting_wizard(self, job: Installation, *, applying=False):
        if (self.active is not job or self.native_owner is not job or job.native_result is not None
                or job.cancel_requested or job.error or (job.responding and not applying)):
            raise Error("NOT_WAITING", "This operation is not accepting FOMOD choices.")
        self.context(job)
        dialog = dialogs.current()
        if dialog is None or dialogs.kind(dialog) != "fomod-plus":
            raise Error("NOT_WAITING", "The owned FOMOD Plus wizard is not available. Read status.")
        return fomod.Wizard(dialog)

    def respond(self, job: Installation, request: dict):
        problem = None
        try:
            wizard = self.waiting_wizard(job, applying=True)
            self.target(job.name, job.replace)
            wizard.respond(request, job.name)
        except Exception as exc:
            # Invalid answers fail this request, not the installation. Choices may have
            # changed; the next status reads the native wizard rather than a saved plan.
            problem = self.problem(exc)
        finally:
            job.responding = False
        try:
            self.drive_dialog()
        except Exception as exc:
            self.accepting = False
            job.error = self.problem(exc)
            job.status = "needs_input"
            problem = problem or job.error
        result = ({"status": "failed", "operation": job.id, "session": self.session, "error": problem}
                  if problem else job.snapshot(self.session))
        self.replies[request["id"]] = result
        self.publish(self.folder / "replies" / f"{request['id']}.json", result)

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

    def custom_source(self, entry) -> dict | None:
        # Native extraction and cleanup use entry.path() under shared Temp.
        # A unique mapping also prevents QSettings from reusing another archive's values.
        if not entry.parent().move(entry, f"mo2-install-meta-{uuid.uuid4().hex}.ini"):
            raise Error("INSTALL_FAILED", "MO2 could not prepare the archive's meta.ini for extraction.")
        extracted = self._manager().extractFile(entry, False)
        if not extracted:
            # For a file entry, MO2 2.5.2 returns empty on cancellation; errors raise.
            return None
        data = self.ini(Path(extracted))
        version = data.value("version", "")
        game_name = data.value("gameName", self.organizer.managedGame().gameShortName())
        valid_ini = data.status() == QSettings.Status.NoError
        del data
        parsed = mobase.VersionInfo(version) if isinstance(version, str) and version.strip() else None
        if not valid_ini or parsed is None or not parsed.isValid() or not parsed.canonicalString():
            raise Error("INVALID_METADATA", "The archive's meta.ini must declare a nonempty version in [General].")
        game = self.organizer.getGame(game_name) if isinstance(game_name, str) else None
        if game is None:
            raise Error("INVALID_METADATA", "gameName must identify a game supported by this MO2 instance.")
        return {"version": parsed.canonicalString(), "gameName": game.gameShortName(),
                "modID": 0, "repository": ""}

    def check_fomod_files(self, tree, config_path, prefix):
        # Extract a copy under a unique native Temp path. Leave ModuleConfig.xml
        # at its original archive path for FOMOD Plus, including on cancellation.
        temporary = tree.createOrphanTree()
        entry = temporary.copy(tree.find(config_path), f"mo2-install-config-{uuid.uuid4().hex}.xml")
        if entry is None:
            raise Error("INSTALL_FAILED", "MO2 could not prepare the FOMOD configuration for inspection.")
        extracted = self._manager().extractFile(entry, False)
        if not extracted:
            return False
        fomod.check_destinations(extracted, tree, prefix)
        return True

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
            if dialogs.current():
                raise Error("MO2_BUSY", "A dialog opened before installation started.")
            job.status = "installing"
            self.calling = job
            installed = self.organizer.installMod(str(job.archive), job.name)
            # FOMOD Plus refreshes in its end hook. The returned interface can
            # already be invalid; use the name copied during the native hook.
            returned_name = job.native_name if installed is not None else None
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
            callback = self.apply_custom_metadata if job.custom else self.verify
            self.organizer.onNextRefresh(lambda: QTimer.singleShot(0, lambda: callback(job)), False)
            self.organizer.refresh(True)
        except Exception as exc:
            job.error = self.problem(exc)
            job.status = "needs_input"

    def apply_custom_metadata(self, job: Installation):
        if self.active is not job:
            return
        try:
            self.context(job)
            self.target(job.name, True)
            # The preceding refresh saved native installed-file associations and released
            # dirty mod interfaces. Edit the saved metadata, then reload it before observing.
            meta = self.ini(Path(self.organizer.modsPath()) / job.name / "meta.ini")
            for key, value in job.source.items():
                meta.setValue("modid" if key == "modID" else key, value)
            for key in ("newestVersion", "ignoredVersion", "nexusDescription", "nexusFileStatus", "nexusCategory",
                        "lastNexusQuery", "lastNexusUpdate", "nexusLastModified", "endorsed", "tracked"):
                meta.remove(key)
            meta.remove("installedFiles")
            meta.beginWriteArray("installedFiles", 0)
            meta.endArray()
            meta.sync()
            if meta.status() != QSettings.Status.NoError:
                raise Error("VERIFY_FAILED", "MO2 could not save the custom mod's metadata.")
            del meta
        except Exception as exc:
            job.error = self.problem(exc)
            self.finish(job, "failed")
            return
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
            job.native_name = newMod.name() if newMod is not None else None

    def isArchiveSupported(self, tree):
        return self.active is not None and self.native_owner is self.active

    def install(self, name, tree, version, nexus_id):
        job = self.active
        if not job:
            return mobase.InstallResult.NOT_ATTEMPTED
        try:
            self.context(job)
            self.target(job.name, job.replace)
            if job.custom and job.source:
                raise Error("INSTALLER_REQUIRED", "Custom archives must contain the mod files directly, rather than a nested installer archive.")
            unsupported = []
            metadata = []
            configs = []
            def inspect(path, entry):
                if entry.name().lower() == "meta.ini" and job.custom and entry.path("/").lower() == "meta.ini" and entry.isFile():
                    metadata.append(entry)
                elif entry.name().lower() == "moduleconfig.xml" and entry.isFile():
                    configs.append(entry.path("/"))
                elif entry.name().lower() == "meta.ini":
                    unsupported.append(entry.path("/"))
                return mobase.IFileTree.CONTINUE
            tree.walk(inspect)
            if configs and not job.custom:
                # FOMOD source folders can contain unused authoring metadata.
                # Reserve the mod-root metadata, not every file in those folders.
                prefix = "/".join(configs[0].split("/")[:-2])
                reserved = {"meta.ini", (prefix + "/meta.ini").lstrip("/").lower()}
                unsupported = [path for path in unsupported if path.lower() in reserved]
            if unsupported:
                raise Error("INSTALLER_REQUIRED", f"This archive needs a different installation procedure: {unsupported[0]}")
            if job.custom:
                if len(metadata) != 1:
                    raise Error("MISSING_METADATA", "A custom archive must contain meta.ini at its root.")
                source = self.custom_source(metadata[0])
                if source is None:
                    return mobase.InstallResult.CANCELED
                job.source = source
                # Package metadata is input, not a replacement for MO2's installed bookkeeping.
                metadata[0].detach()
            if configs:
                # Match the wrapper layout accepted by FOMOD Plus before delegating;
                # otherwise a silent Simple Installer could bypass the wizard.
                root = tree
                while len(root) == 1 and root[0].isDir() and root[0].name().lower() != "fomod":
                    root = root[0]
                expected = (root.path("/").rstrip("/") + "/fomod/moduleconfig.xml").lstrip("/").lower()
                if len(configs) != 1 or configs[0].lower() != expected:
                    raise Error("INSTALLER_REQUIRED", "Use one FOMOD directory at the mod root, optionally inside a single wrapper folder.")
                if not self.organizer.isPluginEnabled("FOMOD Plus"):
                    raise Error("INSTALLER_REQUIRED", "Enable FOMOD Plus to install XML FOMOD archives.")
                if self.organizer.pluginSetting("FOMOD Plus", "fallback_to_legacy"):
                    raise Error("INSTALLER_REQUIRED", "Disable FOMOD Plus's fallback_to_legacy setting before installation; Cancel must stop the installer.")
                if not self.check_fomod_files(tree, configs[0], root.path("/")):
                    return mobase.InstallResult.CANCELED
            if job.cancel_requested or job.error:
                return mobase.InstallResult.CANCELED
            # Reset the variants as well: Root Builder initially selects the first variant.
            name.reset(job.name, mobase.GuessQuality.USER)
            name.setFilter(lambda value: job.name)
            job.guessed_name = name
        except Exception as exc:
            job.error = self.problem(exc)
            return mobase.InstallResult.CANCELED
        # -1 prevents filename guesses or a previous mod's Nexus ID from surviving Replace.
        # Existing installers still prepare the payload and perform extraction.
        if job.custom:
            return mobase.InstallResult.NOT_ATTEMPTED, tree, job.source["version"], -1
        return mobase.InstallResult.NOT_ATTEMPTED

    def drive_dialog(self):
        job = self.active
        if not job or (self.native_owner is not job
                       and not (self.calling is job and job.native_result is not None)):
            return
        # Recompute dialog waits from the current modal; retain unresolved errors.
        job.dialog = None
        if not job.error:
            job.status = "installing"
        dialog = dialogs.current()
        if dialog is None:
            return
        detail = dialogs.describe(dialog)
        if job.native_result is not None:
            # MO2 can show a notice after the end hook, before installMod returns.
            # Observe it as a handoff; do not approve or cancel post-install dialogs.
            if detail["kind"] != "progress":
                job.status = "needs_input"
                job.dialog = detail
            return
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
                dialogs.accept_installer(dialog, job.name)
            elif detail["kind"] == "replace":
                if not job.replace or job.guessed_name is None or str(job.guessed_name) != job.name:
                    raise Error("WRONG_DESTINATION", "The overwrite destination is not the requested replacement.")
                self.target(job.name, True)
                dialogs.replace(dialog)
            elif detail["kind"] != "progress":
                if detail["kind"] == "fomod-plus":
                    job.installer = "fomod-plus"
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
                      "repository": mod.repository(), "category": meta.value("category", ""),
                      "nexusFileStatus": meta.value("nexusFileStatus", 0, type=int)}
            source = job.source
            associated = Path(actual["installationFile"])
            if not associated.is_absolute():
                associated = Path(self.organizer.downloadsPath()) / associated
            if (actual["gameName"] != source["gameName"] or actual["modID"] != source["modID"]
                    or mod.version() != mobase.VersionInfo(source["version"])
                    or actual["repository"] != source["repository"]
                    or meta.value("repository", "") != source["repository"]
                    or path_key(associated) != path_key(job.archive)):
                raise Error("VERIFY_FAILED", "Installed game, version, identity, or archive association differs from the requested package.")
            if job.custom:
                if actual["installedFiles"]:
                    raise Error("VERIFY_FAILED", "The custom mod retained Nexus upload associations.")
            elif [source["modID"], source["fileID"]] not in actual["installedFiles"]:
                raise Error("VERIFY_FAILED", "The installed Nexus upload association is missing.")
            if job.previous and self.target(job.name, True) != job.previous:
                raise Error("PROFILE_CHANGED", "Replacement changed the mod's enabled state or priority.")
            content = sorted(p.name for p in directory.iterdir() if p.name.lower() != "meta.ini")
            if not content:
                raise Error("VERIFY_FAILED", "The installed mod contains no payload.")
            if not job.custom:
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
