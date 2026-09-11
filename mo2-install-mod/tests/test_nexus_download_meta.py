from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import nexus_download_meta


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.archive = Path(self.folder.name) / "archive.zip"
        self.archive.write_bytes(b"abc")
        self.metadata = {"game": "skyrimspecialedition", "mod_id": 62089, "file_id": 800142,
                         "file_name": 'Quoted "name", semi; & café', "file_version": "v1.26a",
                         "file_category": "MAIN", "mod_name": "@Literal &amp; mod name", "mod_category": 108,
                         "description": 'Line 1\nLine 2 with \\ and "quote", semi; café'}

    def test_selected_version_and_category_meanings(self):
        values = nexus_download_meta.fields(**self.metadata)
        self.assertEqual(values["version"], "v1.26a")
        self.assertEqual(values["gameName"], "SkyrimSE")
        self.assertEqual(values["category"], 108)
        self.assertEqual(values["fileCategory"], 1)
        self.assertEqual(values["modName"], self.metadata["mod_name"])
        self.assertEqual(values["repository"], "Nexus")
        self.assertFalse(values["installed"])
        self.assertNotIn("newestVersion", values)
        self.assertNotIn("url", values)

    def test_escaped_text_and_existing_sidecar(self):
        sidecar = nexus_download_meta.create(self.archive, **self.metadata)
        text = sidecar.read_text(encoding="utf-8")
        self.assertIn('modName="@@Literal &amp; mod name"\n', text)
        self.assertIn('name="Quoted \\"name\\", semi; & café"\n', text)
        self.assertIn("<br />\\n", text)
        self.assertIn("&quot;quote&quot;", text)
        with self.assertRaises(FileExistsError):
            nexus_download_meta.create(self.archive, **self.metadata)
        self.assertEqual(text, sidecar.read_text(encoding="utf-8"))

    def test_invalid_information_or_size_writes_nothing(self):
        for key, value in (("mod_id", 0), ("file_id", 0x80000000), ("game", "skyrim"),
                           ("file_category", "unknown"), ("file_version", " "), ("expected_size", 4)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                nexus_download_meta.create(self.archive, **dict(self.metadata, **{key: value}))
            self.assertFalse(Path(str(self.archive) + ".meta").exists())

    def test_cli_accepts_explicit_facts_and_utf8_description(self):
        description = Path(self.folder.name) / "description.txt"
        description.write_text(self.metadata["description"], encoding="utf-8-sig")
        arguments = [str(self.archive), "--expected-size", "3", "--description-file", str(description)]
        for key, value in self.metadata.items():
            if key != "description":
                arguments.extend(["--" + key.replace("_", "-"), str(value)])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(nexus_download_meta.main(arguments), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "complete")
        self.assertIn("Line 1<br />\\nLine 2", Path(result["sidecar"]).read_text(encoding="utf-8"))

    def test_positive_decimal_values(self):
        for value in (True, "0", "-1", "1.5", "1e3", "abc"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                nexus_download_meta.positive(value)


if __name__ == "__main__":
    unittest.main()
