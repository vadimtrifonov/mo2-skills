from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "plugins"))
sys.path.insert(0, str(PROJECT / "plugins/mo2_install_mod"))
import client
import wire


def native_module():
    qt = SimpleNamespace(QSettings=object, QTimer=Mock(), qVersion=lambda: "6.7.1")
    base = SimpleNamespace(IPluginInstallerSimple=object,
                           InstallResult=SimpleNamespace(SUCCESS=0, CANCELED=2, NOT_ATTEMPTED=4))
    spec = importlib.util.spec_from_file_location("mo2_install_mod.native_unit", PROJECT / "plugins/mo2_install_mod/native.py")
    module = importlib.util.module_from_spec(spec)
    modules = {"mobase": base, "PyQt6": SimpleNamespace(), "PyQt6.QtCore": qt,
               "mo2_install_mod.dialogs": SimpleNamespace(modal=lambda: None), spec.name: module}
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


native = native_module()


class WireTests(unittest.TestCase):
    def test_names_and_ids(self):
        for name in ("Engine Fixes VR - Part 2", "A Mod (VR)", "Café"):
            self.assertEqual(wire.validate_name(name), name)
        for name in ("", "../Other", "C:\\Other", "Other ", "CON", "NUL.txt", "Other."):
            with self.subTest(name=name), self.assertRaises(wire.Error):
                wire.validate_name(name)
        self.assertEqual(wire.identifier("a" * 32), "a" * 32)
        with self.assertRaises(wire.Error):
            wire.identifier("../request")

    def test_json_publication_and_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "response.json"
            wire.write_json(path, {"name": "Café"})
            self.assertEqual(wire.read_json(path), {"name": "Café"})
            self.assertEqual(list(Path(folder).glob("*.tmp")), [])
            with self.assertRaises(wire.Error):
                wire.read_json(path, limit=2)


class ClientTests(unittest.TestCase):
    def test_json_keeps_unicode_on_a_non_utf8_output_stream(self):
        text = 'Keep "Café & 猫"?'
        for failure in (False, True):
            with self.subTest(failure=failure):
                target = Mock()
                target.status.return_value = {"status": "needs_input", "dialog": {"text": text}}
                if failure:
                    target.status.side_effect = wire.Error("TEST_ERROR", text)
                output = io.BytesIO()
                stream = io.TextIOWrapper(output, encoding="ascii", write_through=True)
                with patch.object(client, "Client", return_value=target), redirect_stdout(stream):
                    code = client.main(["--instance", ".", "status"])
                result = json.loads(output.getvalue().decode("ascii"))
                self.assertEqual(code, 1 if failure else 2)
                self.assertEqual(result["error"]["message"] if failure else result["dialog"]["text"], text)

    def test_custom_install_keeps_the_shared_command_and_exact_target(self):
        target = Mock()
        target.request.return_value = {"status": "accepted", "operation": "a" * 32}
        with patch.object(client, "Client", return_value=target), redirect_stdout(io.StringIO()):
            code = client.main(["--instance", ".", "install", "example.zip", "--custom",
                                "--profile", "Test", "--replace", "Exact target", "--wait", "0"])
        self.assertEqual(code, 3)
        target.request.assert_called_once_with("install", archive=str(Path("example.zip").resolve()),
                                               profile="Test", name="Exact target", replace=True, custom=True)
        target.wait.assert_not_called()

    def test_ready_check_uses_one_live_request(self):
        target = object.__new__(client.Client)
        target.request = Mock(return_value={"status": "ready", "version": wire.VERSION})
        with patch.object(client, "read_json", side_effect=AssertionError("No file inventory needed")):
            self.assertEqual(target.status(None)["status"], "ready")
        target.request.assert_called_once_with("status", operation=None)

    def test_version_mismatch_blocks_readiness_and_installation(self):
        target = object.__new__(client.Client)
        target.session = "a" * 32
        target.endpoint = {"session": target.session, "pid": 1, "version": "0.0.0"}
        with patch.object(client, "alive", return_value=True), patch.object(client, "write_json") as write:
            with self.assertRaises(wire.Error) as caught:
                target.request("install")
        self.assertEqual(caught.exception.code, "VERSION_MISMATCH")
        write.assert_not_called()
        target.request = Mock(return_value=dict(target.endpoint, status="ready"))
        with self.assertRaises(wire.Error) as caught:
            target.status(None)
        self.assertEqual(caught.exception.context["expected_version"], wire.VERSION)

    def test_timeout_does_not_cancel_or_retry(self):
        target = object.__new__(client.Client)
        target.status = Mock()
        result = {"status": "installing", "operation": "a" * 32}
        expired = target.wait(result, 0)
        self.assertEqual(expired["status"], "installing")
        self.assertTrue(expired["wait_expired"])
        target.status.assert_not_called()

    def test_unknown_completion_has_operation_and_session(self):
        error = wire.Error("REQUEST_TIMEOUT", "No acknowledgement", operation="a" * 32, session="b" * 32)
        output = io.StringIO()
        with patch.object(client, "Client", side_effect=error), redirect_stdout(output):
            code = client.main(["--instance", ".", "status"])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 3)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["operation"], "a" * 32)
        self.assertEqual(result["session"], "b" * 32)

    def test_transport_read_error_keeps_the_installation_id(self):
        with tempfile.TemporaryDirectory() as folder:
            target = object.__new__(client.Client)
            target.folder = Path(folder)
            target.instance = folder
            target.session = "b" * 32
            target.endpoint = {"session": target.session, "pid": 1, "version": wire.VERSION}
            with patch.object(client, "alive", return_value=True), patch.object(client, "read_json", side_effect=OSError("Read failed")):
                with self.assertRaises(wire.Error) as caught:
                    target.request("install", name="Example")
            self.assertEqual(caught.exception.code, "CONNECTION_ERROR")
            operation = caught.exception.context["operation"]
            self.assertTrue((target.folder / "requests" / f"{operation}.json").exists())

    def test_interrupt_keeps_operation_ids_without_cancelling(self):
        for phase in ("publication", "waiting", "saved_status"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as folder:
                target = object.__new__(client.Client)
                target.folder = Path(folder)
                target.instance = folder
                target.session = "b" * 32
                target.operation = None
                target.endpoint = {"session": target.session, "pid": 1, "version": wire.VERSION}
                operation = "a" * 32
                def reply(path, limit=None):
                    if phase == "saved_status":
                        raise KeyboardInterrupt
                    return {"status": "accepted", "operation": path.stem, "session": target.session}
                def publish(path, value):
                    wire.write_json(path, value)
                    if phase == "publication":
                        raise KeyboardInterrupt
                arguments = (["status", operation] if phase == "saved_status" else
                             ["install", "archive.7z", "--profile", "Test", "--name", "Example"])
                output = io.StringIO()
                with patch.object(client, "Client", return_value=target), patch.object(client, "alive", return_value=True), \
                        patch.object(client, "read_json", side_effect=reply), patch.object(client, "write_json", side_effect=publish), \
                        patch.object(client.time, "sleep", side_effect=KeyboardInterrupt), redirect_stdout(output):
                    code = client.main(["--instance", folder, *arguments])
                result = json.loads(output.getvalue())
                self.assertEqual((code, result["status"], result["error"]["code"]), (3, "unknown", "INTERRUPTED"))
                self.assertEqual(result["session"], target.session)
                requests = list(target.folder.glob("requests/*.json"))
                if phase == "saved_status":
                    self.assertEqual(requests, [])
                else:
                    self.assertEqual(len(requests), 1)
                    request = wire.read_json(requests[0])
                    self.assertEqual(request["command"], "install")
                    operation = request["id"]
                self.assertEqual(result["operation"], operation)

    def test_finished_result_can_be_read_after_session_ends(self):
        with tempfile.TemporaryDirectory() as folder:
            target = object.__new__(client.Client)
            target.folder = Path(folder)
            target.request = Mock(side_effect=AssertionError("Should not contact MO2"))
            operation = "a" * 32
            wire.write_json(target.folder / "results" / f"{operation}.json", {"status": "complete"})
            self.assertEqual(target.status(operation), {"status": "complete"})


class OperationTests(unittest.TestCase):
    def job(self):
        return native.Installation("a" * 32, Path("archive.7z"), "Target", "Test", False, {}, None)

    def controller(self, job):
        controller = native.InstallMod()
        controller.active = job
        controller.session = "b" * 32
        controller.folder = Path(tempfile.gettempdir()) / "unused-install-mod-test"
        controller.organizer = Mock()
        controller.context = Mock()
        controller.target = Mock()
        return controller

    def test_disabled_or_stopped_plugin_is_not_ready(self):
        controller = self.controller(self.job())
        controller.organizer.isPluginEnabled.return_value = False
        with self.assertRaises(native.Error) as caught:
            controller.dispatch({"command": "status"})
        self.assertEqual(caught.exception.code, "PLUGIN_DISABLED")
        controller.organizer.isPluginEnabled.return_value = True
        controller.accepting = False
        with self.assertRaises(native.Error) as caught:
            controller.dispatch({"command": "status"})
        self.assertEqual(caught.exception.code, "CONTROLLER_STOPPED")

    def test_cancel_before_start_never_enters_native_installation(self):
        job = self.job()
        job.cancel_requested = True
        controller = self.controller(job)
        controller.run(job)
        controller.organizer.installMod.assert_not_called()
        self.assertEqual(job.status, "cancelled")
        self.assertIsNone(controller.active)

    def test_unknown_native_completion_retains_ownership(self):
        job = self.job()
        controller = self.controller(job)
        def incomplete(*args):
            job.native_started = True
            raise RuntimeError("Native observation failed")
        controller.organizer.installMod.side_effect = incomplete
        controller.run(job)
        self.assertIs(controller.active, job)
        self.assertEqual(job.status, "needs_input")
        self.assertIsNone(job.guessed_name)
        self.assertFalse(controller.pending)

    def test_cancelled_native_call_leaves_its_files(self):
        job = self.job()
        controller = self.controller(job)
        with tempfile.TemporaryDirectory() as folder:
            written = Path(folder) / "installed.txt"
            def cancelled(*args):
                job.native_started = True
                written.write_text("New package content", encoding="utf-8")
                job.native_result = native.mobase.InstallResult.CANCELED
                self.assertIs(controller.active, job)
                self.assertFalse(controller.pending)
            controller.organizer.installMod.side_effect = cancelled
            controller.run(job)
            self.assertEqual(written.read_text(encoding="utf-8"), "New package content")
        self.assertEqual(job.status, "cancelled")
        self.assertIsNone(controller.active)

    def test_gui_hooks_do_not_complete_an_unrelated_job(self):
        job = self.job()
        job.native_started = True
        job.status = "needs_input"
        controller = self.controller(job)
        controller.onInstallationStart("other.7z", False, None)
        controller.onInstallationEnd(native.mobase.InstallResult.SUCCESS, None)
        self.assertIsNone(job.native_result)
        self.assertFalse(controller.isArchiveSupported(None))

    def test_preparation_error_never_calls_mo2(self):
        job = self.job()
        controller = self.controller(job)
        controller.context.side_effect = native.Error("WRONG_PROFILE", "Profile changed")
        controller.run(job)
        self.assertEqual(job.status, "failed")
        self.assertIsNone(controller.active)
        controller.organizer.installMod.assert_not_called()

    def test_unresolved_error_prevents_success(self):
        job = self.job()
        job.error = {"code": "MO2_ERROR", "message": "Controller failed during installation"}
        controller = self.controller(job)
        controller.finish(job, "complete")
        result = controller.pending[controller.folder / "results" / f"{job.id}.json"]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], job.error)
        self.assertIsNone(controller.active)

    def test_message_box_still_requires_input_without_an_automatic_answer(self):
        job = self.job()
        controller = self.controller(job)
        controller.native_owner = job
        detail = {"kind": "other", "title": "Installer question", "class": "QMessageBox",
                  "text": "Keep the package?", "informative_text": "Choose in MO2.",
                  "buttons": ["Keep", "Stop"]}
        controls = Mock()
        controls.describe.return_value = detail
        controls.cancel.return_value = False
        with patch.object(native, "dialogs", controls):
            controller.drive_dialog()
            self.assertEqual(job.status, "needs_input")
            self.assertEqual(job.snapshot(controller.session)["dialog"], detail)
            controls.cancel.assert_not_called()
            job.cancel_requested = True
            controller.drive_dialog()
        self.assertEqual(job.status, "needs_input")
        self.assertEqual(job.dialog, dict(detail, message="Cancel this dialog in MO2."))
        controls.accept_installer.assert_not_called()
        controls.replace.assert_not_called()
        self.assertIs(controller.active, job)

    def test_answered_dialog_clears_the_wait_without_losing_errors_or_cancellation(self):
        for next_kind in (None, "progress"):
            for pending in (None, "error", "cancel"):
                with self.subTest(next_kind=next_kind, pending=pending):
                    job = self.job()
                    controller = self.controller(job)
                    controller.native_owner = job
                    controls = Mock()
                    controls.describe.return_value = {"kind": "other", "title": "Continue?", "class": "QMessageBox"}
                    with patch.object(native, "dialogs", controls):
                        controller.drive_dialog()
                        self.assertEqual(job.status, "needs_input")
                        error = {"code": "MO2_ERROR", "message": "Controller failed"} if pending == "error" else None
                        job.error = error
                        job.cancel_requested = pending == "cancel"
                        controls.modal.return_value = None if next_kind is None else object()
                        controls.describe.return_value = {"kind": "progress", "title": "Extracting", "class": "QProgressDialog"}
                        controls.cancel.return_value = True
                        controller.drive_dialog()
                    self.assertEqual(job.status, "needs_input" if error else "installing")
                    self.assertNotIn("dialog", job.snapshot(controller.session))
                    self.assertIs(job.error, error)
                    self.assertEqual(job.cancel_requested, pending == "cancel")
                    self.assertIs(controller.active, job)
                    self.assertIsNone(job.native_result)
                    controls.accept_installer.assert_not_called()
                    controls.replace.assert_not_called()

    def test_refresh_exceptions_retain_ownership_but_metadata_write_failure_finishes(self):
        for phase in ("first_refresh", "metadata_write", "register_refresh", "second_refresh"):
            with self.subTest(phase=phase):
                job = self.job()
                job.custom = True
                job.native_started = True
                job.native_result = native.mobase.InstallResult.SUCCESS
                job.status = "refreshing"
                controller = self.controller(job)
                controller.organizer.modsPath.return_value = tempfile.gettempdir()
                controller.organizer.installMod.return_value.name.return_value = job.name
                settings = Mock()
                settings.status.return_value = 1 if phase == "metadata_write" else 0
                controller.ini = Mock(return_value=settings)
                if phase == "register_refresh":
                    controller.organizer.onNextRefresh.side_effect = RuntimeError("Registration failed")
                else:
                    controller.organizer.refresh.side_effect = RuntimeError("Refresh failed")
                with patch.object(native, "QSettings", SimpleNamespace(Status=SimpleNamespace(NoError=0))):
                    if phase == "first_refresh":
                        controller.run(job)
                    else:
                        controller.apply_custom_metadata(job)
                if phase == "metadata_write":
                    self.assertEqual(job.status, "failed")
                    self.assertIsNone(controller.active)
                    self.assertTrue(controller.pending)
                    controller.organizer.onNextRefresh.assert_not_called()
                else:
                    self.assertEqual(job.status, "needs_input")
                    self.assertIs(controller.active, job)
                    self.assertFalse(controller.pending)
                    with self.assertRaises(native.Error) as caught:
                        controller.dispatch({"command": "install"})
                    self.assertEqual(caught.exception.code, "MO2_BUSY")
                self.assertIsNotNone(job.error)

    def test_native_end_notice_is_a_handoff_until_the_call_returns(self):
        job = self.job()
        controller = self.controller(job)
        controls = Mock()
        detail = {"kind": "other", "class": "QMessageBox", "title": "Extraction cancelled"}
        controls.describe.return_value = detail
        def cancelled(*args):
            controls.modal.return_value = object()
            controller.onInstallationStart(str(job.archive), False, None)
            job.cancel_requested = True
            controller.onInstallationEnd(native.mobase.InstallResult.CANCELED, None)
            controller.drive_dialog()
            self.assertEqual(job.status, "needs_input")
            self.assertEqual(job.dialog, detail)
            self.assertIs(controller.active, job)
            self.assertFalse(controller.pending)
            return None
        controller.organizer.installMod.side_effect = cancelled
        with patch.object(native, "dialogs", controls):
            controls.modal.return_value = None
            controller.run(job)
        self.assertEqual(job.status, "cancelled")
        self.assertIsNone(job.error)
        self.assertIsNone(controller.active)
        self.assertTrue(job.cancel_requested)
        controls.cancel.assert_not_called()
        controls.accept_installer.assert_not_called()
        controls.replace.assert_not_called()

    def test_old_refresh_callback_cannot_change_new_operation(self):
        old = self.job()
        current = self.job()
        controller = self.controller(current)
        controller.verify(old)
        controller.apply_custom_metadata(old)
        controller.organizer.modList.assert_not_called()
        self.assertIs(controller.active, current)


class CustomTests(unittest.TestCase):
    job = OperationTests.job
    controller = OperationTests.controller

    def test_storage_and_metadata_source_are_selected_explicitly(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            downloads = root / "downloads"
            downloads.mkdir()
            target = root / "mods/Target"
            target.mkdir(parents=True)
            controller = self.controller(self.job())
            controller.organizer.isPluginEnabled.return_value = True
            controller.organizer.profileName.return_value = "Test"
            controller.organizer.downloadsPath.return_value = str(downloads)
            controller.organizer.modsPath.return_value = str(root / "mods")
            controller.source = Mock(return_value={})
            request = {"command": "install", "id": "a" * 32, "profile": "Test", "name": "Target", "replace": False}
            for directory, custom, replacing, error in ((root, True, False, None), (downloads, True, False, None),
                                                         (root, False, False, "INVALID_ARCHIVE"),
                                                         (downloads, False, False, None),
                                                         (target, True, True, "INVALID_ARCHIVE")):
                with self.subTest(directory=directory, custom=custom, replacing=replacing):
                    archive = directory / "package.zip"
                    archive.write_bytes(b"Archive contents are read by MO2")
                    controller.active = None
                    controller.source.reset_mock()
                    arguments = dict(request, archive=str(archive), custom=custom, replace=replacing)
                    if error:
                        with self.assertRaises(native.Error) as caught:
                            controller.dispatch(arguments)
                        self.assertEqual(caught.exception.code, error)
                    else:
                        result = controller.dispatch(arguments)
                        self.assertEqual((result["status"], result["custom"]), ("accepted", custom))
                        self.assertEqual(controller.source.call_count, 0 if custom else 1)

    def test_embedded_metadata_requires_version_and_resolves_game(self):
        controller = self.controller(self.job())
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        extracted = Path(folder.name) / "meta.ini"
        extracted.write_text("[General]\nversion=1.2.3\n", encoding="utf-8")
        controller._manager = Mock()
        controller._manager.return_value.extractFile.return_value = str(extracted)
        controller.organizer.managedGame.return_value.gameShortName.return_value = "SkyrimVR"
        games = {name.lower(): SimpleNamespace(gameShortName=lambda n=name: n) for name in ("SkyrimSE", "SkyrimVR")}
        controller.organizer.getGame.side_effect = lambda name: games.get(name.lower())
        version = Mock()
        version.isValid.return_value = True
        version.canonicalString.return_value = "1.2.3.0"
        for fields, error in (({"version": "1.2.3", "gameName": "skyrimse", "modid": 999}, None),
                              ({"version": "1.2.3"}, None), ({}, "INVALID_METADATA"),
                              ({"version": "   "}, "INVALID_METADATA"), ({"version": "v"}, "INVALID_METADATA"),
                              ({"version": ["1", "2"]}, "INVALID_METADATA"),
                              ({"version": "1.2.3", "gameName": "Unknown"}, "INVALID_METADATA")):
            with self.subTest(fields=fields):
                version.canonicalString.return_value = "" if fields.get("version") == "v" else "1.2.3.0"
                settings = Mock()
                settings.status.return_value = 0
                settings.value.side_effect = lambda key, default: fields.get(key, default)
                controller.ini = Mock(return_value=settings)
                with patch.object(native, "QSettings", SimpleNamespace(Status=SimpleNamespace(NoError=0))), \
                        patch.object(native.mobase, "VersionInfo", return_value=version, create=True):
                    if error:
                        with self.assertRaises(native.Error) as caught:
                            controller.custom_source(Mock())
                        self.assertEqual(caught.exception.code, error)
                    else:
                        result = controller.custom_source(Mock())
                        self.assertEqual(result, {"version": "1.2.3.0", "gameName": "SkyrimSE" if "gameName" in fields else "SkyrimVR",
                                                  "modID": 0, "repository": ""})

    def test_metadata_extraction_uses_unique_native_paths_and_processes_events(self):
        controller = self.controller(self.job())
        controller._manager = Mock()
        settings = Mock()
        settings.value.side_effect = lambda key, default: {"version": "1.2.3", "gameName": "SkyrimSE"}.get(key, default)
        settings.status.return_value = 0
        controller.ini = Mock(return_value=settings)
        version = Mock()
        version.canonicalString.return_value = "1.2.3.0"
        paths, silent_flags = [], []
        with tempfile.TemporaryDirectory() as folder:
            def extract(entry, silent):
                path = Path(folder) / entry.path()
                path.write_text("[General]\nversion=1.2.3\n", encoding="utf-8")
                paths.append(path)
                silent_flags.append(silent)
                return str(path)
            controller._manager.return_value.extractFile.side_effect = extract
            for _ in range(2):
                entry = Mock()
                entry.path.return_value = "meta.ini"
                def move(moved, name):
                    moved.path.return_value = name
                    return True
                entry.parent.return_value.move.side_effect = move
                with patch.object(native, "QSettings", SimpleNamespace(Status=SimpleNamespace(NoError=0))), \
                        patch.object(native.mobase, "VersionInfo", return_value=version, create=True):
                    controller.custom_source(entry)
        self.assertEqual(silent_flags, [False, False])
        self.assertEqual(len(set(paths)), 2)
        self.assertTrue(all(path.name != "meta.ini" for path in paths))
        self.assertEqual([call.args[0] for call in controller.ini.call_args_list], paths)

    def test_metadata_extraction_cancellation_is_not_a_metadata_error(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                job = self.job()
                job.custom = True
                controller = self.controller(job)
                controller._manager = Mock()
                controller._manager.return_value.extractFile.return_value = ""
                if failed:
                    controller._manager.return_value.extractFile.side_effect = RuntimeError("Extraction failed")
                entry = Mock()
                entry.name.return_value = "meta.ini"
                entry.path.return_value = "meta.ini"
                tree = Mock()
                tree.walk.side_effect = lambda visit: visit("", entry)
                name = Mock()
                with patch.object(native.mobase, "IFileTree", SimpleNamespace(STOP=1, CONTINUE=0), create=True):
                    result = controller.install(name, tree, "date fallback", 12345)
                self.assertEqual(result, native.mobase.InstallResult.CANCELED)
                self.assertEqual(job.source, {})
                self.assertIsNone(job.guessed_name)
                name.reset.assert_not_called()
                entry.detach.assert_not_called()
                if failed:
                    self.assertEqual(job.error, {"code": "MO2_ERROR", "message": "Extraction failed"})
                else:
                    self.assertIsNone(job.error)

    def test_nested_archive_cannot_replace_source_metadata(self):
        job = self.job()
        job.custom = True
        job.source = {"version": "1.0.0.0"}
        controller = self.controller(job)
        tree = Mock()
        result = controller.install(Mock(), tree, "2.0.0", 0)
        self.assertEqual(result, native.mobase.InstallResult.CANCELED)
        self.assertEqual(job.error["code"], "INSTALLER_REQUIRED")
        self.assertEqual(job.source, {"version": "1.0.0.0"})
        tree.walk.assert_not_called()

    def test_custom_metadata_is_consumed_before_native_delegation(self):
        for custom, entry_path, expected in ((True, "meta.ini", "delegate"), (True, None, "MISSING_METADATA"),
                                            (True, "Data/meta.ini", "INSTALLER_REQUIRED"),
                                            (True, "fomod/moduleconfig.xml", "INSTALLER_REQUIRED"),
                                            (False, "meta.ini", "INSTALLER_REQUIRED")):
            with self.subTest(custom=custom, path=entry_path):
                job = self.job()
                job.custom = custom
                controller = self.controller(job)
                controller.custom_source = Mock(return_value={"version": "1.2.3.0"})
                entry = Mock()
                entry.name.return_value = entry_path.rsplit("/", 1)[-1] if entry_path else ""
                entry.path.return_value = entry_path
                entry.isFile.return_value = True
                tree = Mock()
                tree.walk.side_effect = lambda visit: visit("", entry) if entry_path else None
                name = Mock()
                with patch.object(native.mobase, "IFileTree", SimpleNamespace(STOP=1, CONTINUE=0), create=True), \
                        patch.object(native.mobase, "GuessQuality", SimpleNamespace(USER=3), create=True):
                    result = controller.install(name, tree, "date fallback", 12345)
                if expected == "delegate":
                    self.assertEqual(result, (native.mobase.InstallResult.NOT_ATTEMPTED, tree, "1.2.3.0", -1))
                    entry.detach.assert_called_once_with()
                    self.assertIsNone(job.error)
                else:
                    self.assertEqual(result, native.mobase.InstallResult.CANCELED)
                    self.assertEqual(job.error["code"], expected)
                    entry.detach.assert_not_called()


if __name__ == "__main__":
    unittest.main()
