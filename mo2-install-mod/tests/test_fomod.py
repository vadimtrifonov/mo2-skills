"""Adapter contracts without requiring Qt in the external Python environment.

Native focus, navigation, and file mapping are also exercised by the MO2 harness.
"""

import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
from xml.parsers.expat import ExpatError


class Button: pass
class PushButton(Button): pass
class RadioButton(Button): pass
class CheckBox(Button): pass
class GroupBox: pass
class ComboBox: pass
class Stack: pass


def adapter():
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project / "plugins"))
    spec = importlib.util.spec_from_file_location("mo2_install_mod.fomod_unit", project / "plugins/mo2_install_mod/fomod.py")
    module = importlib.util.module_from_spec(spec)
    core = SimpleNamespace(QCoreApplication=SimpleNamespace(translate=lambda context, text: text), QEvent=object)
    widgets = SimpleNamespace(QAbstractButton=Button, QPushButton=PushButton, QRadioButton=RadioButton, QCheckBox=CheckBox,
                              QGroupBox=GroupBox, QComboBox=ComboBox, QStackedWidget=Stack,
                              QApplication=object, QLabel=object, QScrollArea=object)
    with patch.dict(sys.modules, {"PyQt6": SimpleNamespace(), "PyQt6.QtCore": core,
                                 "PyQt6.QtWidgets": widgets, "PyQt6.QtGui": SimpleNamespace(QTextDocument=object)}):
        spec.loader.exec_module(module)
    return module


fomod = adapter()


def widget(kind, **methods):
    result = Mock(spec=kind)
    for name, value in methods.items():
        setattr(result, name, Mock(return_value=value))
    return result


class DestinationTests(unittest.TestCase):
    def test_destination_checks_use_native_xml_names(self):
        file = '<file source="files/bookkeeping.txt" destination="meta.ini"/>'
        cases = (
            (f'<config>{file}</config>', False, True),
            (f'<config xmlns="urn:fomod">{file}</config>', False, True),
            ('<config><folder xmlns="urn:fomod" source="files" destination=""/></config>', True, True),
            ('<config xmlns:x="urn:fomod"><x:file source="files/bookkeeping.txt" destination="meta.ini"/></config>', False, False),
        )
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "ModuleConfig.xml"
            for xml, folder, checked in cases:
                with self.subTest(xml=xml):
                    config.write_text(xml, encoding="utf-8")
                    entry = widget(object, name="meta.ini")
                    source = MagicMock()
                    source.isDir.return_value = folder
                    source.__iter__.return_value = [entry]
                    projected = MagicMock()
                    projected.__iter__.return_value = [entry]
                    archive = Mock()
                    archive.find.return_value = source
                    archive.createOrphanTree.return_value = projected
                    if checked:
                        with self.assertRaises(fomod.Error) as caught:
                            fomod.check_destinations(config, archive, "wrapper")
                        self.assertEqual(caught.exception.code, "INSTALLER_REQUIRED")
                        self.assertIn("meta.ini", str(caught.exception))
                        archive.find.assert_called_once_with("wrapper/files" if folder else "wrapper/files/bookkeeping.txt")
                        projected.copy.assert_called_once_with(entry if folder else source, "meta.ini")
                    else:
                        fomod.check_destinations(config, archive, "wrapper")
                        archive.find.assert_not_called()

    def test_malformed_configuration_reports_an_archive_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "ModuleConfig.xml"
            config.write_text("<config><file></config>", encoding="utf-8")
            with self.assertRaises(fomod.Error) as caught:
                fomod.check_destinations(config, Mock(), "")
            self.assertEqual(caught.exception.code, "INVALID_ARCHIVE")
            self.assertIsInstance(caught.exception.__cause__, ExpatError)


class WizardTests(unittest.TestCase):
    def setUp(self):
        self.alpha = widget(RadioButton, text="Alpha", isChecked=True, isEnabled=True)
        self.beta = widget(RadioButton, text="Beta", isChecked=False, isEnabled=True)
        self.ready = widget(CheckBox, text="Ready", isChecked=True, isEnabled=False)
        pages = []
        for title, controls in (("Choices", [self.alpha, self.beta]), ("Approval", [self.ready])):
            group = widget(GroupBox, title="Options", children=controls)
            page = widget(GroupBox, title=title)
            page.findChildren = lambda kind, group=group, controls=controls: [group] if kind is GroupBox else controls
            pages.append(page)
        stack = widget(Stack, currentIndex=1, currentWidget=pages[1], count=len(pages))
        stack.widget = Mock(side_effect=pages.__getitem__)
        combo = widget(ComboBox, currentText="Test mod")
        self.buttons = {text: widget(PushButton, text=text, isEnabled=True, isDefault=text == "Install", click=None)
                        for text in ("Back", "Install", "Reset Choices", "Cancel")}
        self.dialog = widget(object, windowTitle="Test mod")
        self.dialog.findChildren = lambda kind: {Stack: [stack], ComboBox: [combo], PushButton: list(self.buttons.values())}[kind]

    def test_focus_does_not_identify_navigation_or_change_approval(self):
        before = fomod.Wizard(self.dialog).snapshot()
        for focus in ("Back", "Reset Choices", "Cancel"):
            with self.subTest(focus=focus):
                for name, button in self.buttons.items():
                    button.isDefault.return_value = name == focus
                wizard = fomod.Wizard(self.dialog)
                self.assertIs(wizard.forward, self.buttons["Install"])
                self.assertEqual(wizard.snapshot(), before)
                self.assertEqual(before["actions"], ["stay", "back", "install"])

    def test_final_approval_covers_earlier_page_selections(self):
        old = fomod.Wizard(self.dialog).snapshot()
        self.alpha.isChecked.return_value = False
        self.beta.isChecked.return_value = True
        wizard = fomod.Wizard(self.dialog)
        self.assertNotEqual(wizard.snapshot()["view"], old["view"])
        with self.assertRaises(fomod.Error) as caught:
            wizard.respond({"view": old["view"], "action": "install"}, "Test mod")
        self.assertEqual(caught.exception.code, "STALE_VIEW")
        self.buttons["Install"].click.assert_not_called()
        self.assertEqual([o["name"] for o in wizard.choices("1.0")["options"]], ["Ready"])
        with self.assertRaises(fomod.Error):
            wizard.choices("0.0")


if __name__ == "__main__":
    unittest.main()
