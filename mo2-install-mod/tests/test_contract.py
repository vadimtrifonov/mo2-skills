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

    def test_old_refresh_callback_cannot_change_new_operation(self):
        old = self.job()
        current = self.job()
        controller = self.controller(current)
        controller.verify(old)
        controller.organizer.modList.assert_not_called()
        self.assertIs(controller.active, current)


if __name__ == "__main__":
    unittest.main()
