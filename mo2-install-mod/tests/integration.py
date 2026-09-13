"""Exercise real installers in a disposable portable MO2 instance.

Run from the skill directory in PowerShell:

    mise exec -- python -B tests/integration.py `
        --template 'C:/Path/To/MO2' `
        --workspace "$env:TEMP/mo2-install-test" `
        --game 'C:/Steam/steamapps/common/SkyrimVR' `
        --cases 'C:/Fixtures/cases.json'

The template must include MO2's Python support and Root Builder.
--game must point to an installed Skyrim VR game.
The workspace must be a new directory under user Temp.
The harness uses --multiple and closes only its own process.
It refuses to launch a second copy of the same sandbox.
It copies the application into the workspace and disables Root Builder deployment.
MO2 and its client use the workspace as their private Temp directory.
No game is launched.

The manifest is a JSON object with four `cases`, in this order: two Data
packages, a root-only package, and SKSEVR.
Each case supplies an absolute `archive` path, a target `name`, a `layout`, and
Nexus `metadata`: `game`, `mod_id`, `file_id`, `file_name`, `file_version`,
`file_category`, `mod_name`, and `mod_category`, with optional plain-text
`description` and `expected_size`.
An optional `update` object has the same fields for a newer archive targeting
the same mod name.

Layouts describe the expected archive structure:
- `data`: files are already at the Data root, without an outer wrapper.
- `root`: all files belong under the installed mod's Root directory.
- `skse`: the standard SKSEVR wrapper, with Data content and root files;
  the src directory is excluded.

The tests compare payload hashes, exercise replacement and cancellation, and
reopen MO2 to check metadata and profile state.
Results and logs remain in the workspace; verification.json summarizes a
completed run.
"""

from __future__ import annotations

import argparse
import configparser
from contextlib import contextmanager
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

from sandbox import PROJECT, create


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2), encoding="utf-8")


def payload(directory):
    directory = directory.resolve()
    if sys.platform == "win32" and not str(directory).startswith("\\\\?\\"):
        directory = Path("\\\\?\\" + str(directory))
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*") if p.is_file() and p.name.lower() != "meta.ini"}


def ini(path):
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8-sig")
    return parser


def ini_value(value):
    """Encode fixture values for Qt's INI reader."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int):
        return str(value)
    if value.startswith("@"):
        value = "@" + value
    quote = value != value.strip() or any(c in value for c in '\\"\n\r\t,;')
    value = (value.replace("\\", "\\\\").replace('"', '\\"')
             .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))
    return '"' + value + '"' if quote else value


def write_nexus_sidecar(archive, *, game, mod_id, file_id, file_name, file_version,
                        file_category, mod_name, mod_category, description="", expected_size=None):
    """Create fresh Nexus metadata for test archives."""
    if game != "skyrimspecialedition":
        raise ValueError("Fixture metadata must use the skyrimspecialedition catalog.")
    if expected_size is not None and archive.stat().st_size != int(expected_size):
        raise ValueError("Fixture archive size differs from expected_size.")
    categories = {"MAIN": 1, "UPDATE": 2, "OPTIONAL": 3, "OLD_VERSION": 4,
                  "MISCELLANEOUS": 5, "DELETED": 6, "ARCHIVED": 7}
    description = description.replace("\r\n", "\n").replace("\r", "\n")
    values = {"gameName": "SkyrimSE", "modID": int(mod_id), "fileID": int(file_id),
              "repository": "Nexus", "name": file_name, "version": file_version,
              "fileCategory": categories[file_category], "modName": mod_name,
              "category": int(mod_category),
              "description": html.escape(description).replace("\n", "<br />\n"),
              "installed": False, "uninstalled": False}
    sidecar = Path(str(archive) + ".meta")
    with sidecar.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write("[General]\n" + "".join(f"{key}={ini_value(value)}\n" for key, value in values.items()))
    return sidecar


def check_metadata(root, cases, mods=None):
    for case in cases:
        source, installed = case["metadata"], case["installed_metadata"]
        identity = (installed["gameName"], int(source["mod_id"]), installed["version"], Path(case["archive"]).name)
        settings = ini(root / "mods" / case["name"] / "meta.ini")
        general, pairs = settings["General"], settings["installedFiles"]
        assert (general["gameName"], general.getint("modid"), general["version"],
                Path(general["installationFile"]).name) == identity, case["name"]
        assert general["repository"] == "Nexus", case["name"]
        assert pairs.getint("size") == 1, case["name"]
        assert (pairs.getint("1\\modid"), pairs.getint("1\\fileid")) == (int(source["mod_id"]), int(source["file_id"])), case["name"]
        download = ini(root / "downloads" / (Path(case["archive"]).name + ".meta"))["General"]
        assert download.getboolean("installed") and not download.getboolean("uninstalled"), case["name"]
        if mods is not None:
            mod = next(mod for mod in mods if mod["name"] == case["name"])
            assert (mod["gameName"], mod["modID"], mod["version"], Path(mod["archive"]).name) == identity, case["name"]


class Harness:
    def __init__(self, root):
        self.root = root
        self.client = PROJECT / "plugins/mo2_install_mod/client.py"
        # Keep MO2's native temporary extraction and the client's channel in this sandbox.
        self.env = dict(os.environ, TEMP=str(root.resolve()), TMP=str(root.resolve()), TMPDIR=str(root.resolve()))
        self.responses = []
        self.exits = []
        self.sequence = 0

    def cli(self, *args, expected=None):
        started = time.perf_counter()
        process = subprocess.run([sys.executable, "-B", str(self.client), "--instance", str(self.root), *map(str, args)],
                                 capture_output=True, text=True, encoding="utf-8", timeout=150, env=self.env)
        result = json.loads(process.stdout)
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.responses.append({"args": list(map(str, args)), "exit": process.returncode, "result": result,
                               "stderr": process.stderr, "elapsed_ms": elapsed_ms})
        save(self.root / "responses.json", self.responses)
        if expected is not None:
            assert result["status"] == expected, result
        return result

    def control(self, command, **arguments):
        self.sequence += 1
        path = self.root / f"test-{self.sequence}-{uuid.uuid4().hex}.json"
        # Publish complete JSON before the probe can see the .json suffix.
        temporary = path.with_suffix(".tmp")
        save(temporary, dict(arguments, command=command))
        temporary.rename(path)
        reply = path.with_name("reply-" + path.name)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if reply.exists():
                return load(reply)
            time.sleep(0.1)
        raise RuntimeError(f"Test probe did not respond to {command}.")

    @contextmanager
    def session(self):
        executable = str((self.root / "ModOrganizer.exe").resolve()).replace("'", "''")
        others = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                                 "@(Get-Process ModOrganizer -ErrorAction SilentlyContinue | "
                                 f"Where-Object {{ $_.Path -eq '{executable}' }}).Count"],
                                capture_output=True, text=True, check=True)
        if int(others.stdout.strip()):
            raise RuntimeError("This sandbox's MO2 is already running. It will not be stopped by this harness.")
        process = subprocess.Popen([str(self.root / "ModOrganizer.exe"), "--multiple", "-p", "Test"], cwd=self.root, env=self.env)
        graceful = False
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"MO2 exited during startup: {process.returncode}")
                result = self.cli("status")
                if result["status"] == "ready" and result["pid"] == process.pid:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("MO2 did not become ready.")
            yield
        finally:
            if process.poll() is None:
                try:
                    self.control("stop")
                    process.wait(timeout=20)
                    graceful = True
                except (subprocess.TimeoutExpired, RuntimeError):
                    process.kill()
                    process.wait(timeout=10)
            self.exits.append({"pid": process.pid, "exit": process.returncode, "graceful": graceful})
            save(self.root / "exits.json", self.exits)

    def install(self, case, replace=False, **kwargs):
        result = self.cli("install", self.root / "downloads" / Path(case["archive"]).name,
                          "--replace" if replace else "--name", case["name"],
                          "--profile", "Test", **kwargs)
        if result["status"] == "complete":
            case["installed_metadata"] = result["metadata"]
        return result


def prepare(args, cases):
    create(args.template, args.workspace, args.game)
    for case in cases + [case["update"] for case in cases if "update" in case]:
        archive = args.workspace / "downloads" / Path(case["archive"]).name
        shutil.copy2(case["archive"], archive)
        write_nexus_sidecar(archive, **case["metadata"])
        extracted = args.workspace / "expected" / archive.name
        extracted.mkdir(parents=True)
        subprocess.run(["tar.exe", "-xf", str(archive), "-C", str(extracted)], check=True)
        if case["layout"] == "root":
            files = payload(extracted)
            case["expected"] = {"Root/" + key: value for key, value in files.items()}
        elif case["layout"] == "skse":
            wrapper, = [p for p in extracted.iterdir() if p.is_dir()]
            case["expected"] = {}
            for path, digest in payload(wrapper).items():
                if path.startswith("Data/"):
                    case["expected"][path[5:]] = digest
                elif not path.startswith("src/"):
                    case["expected"]["Root/" + path] = digest
        else:
            case["expected"] = payload(extracted)
    save(args.workspace / "cases.json", cases)
    values = {"name": 'Quoted "name", semi; & café', "modName": "@Literal mod name",
              "description": 'Line 1\nLine 2 with \\ and "quote", semi; café'}
    (args.workspace / "text-fixture.meta").write_text("[General]\n" + "".join(f"{k}={ini_value(v)}\n" for k, v in values.items()), encoding="utf-8")
    save(args.workspace / "text-fixture.json", values)


def exercise(root, cases):
    harness = Harness(root)
    with harness.session():
        files = list((root / "plugins/mo2_install_mod").glob("*.py"))
        before = {str(p): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in files}
        harness.cli("status", expected="ready")
        assert before == {str(p): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in files}
        decoded = harness.control("read_ini", path=str(root / "text-fixture.meta"))["values"]
        assert decoded == load(root / "text-fixture.json"), decoded
        for case in cases:
            result = harness.install(case, expected="complete")
            assert payload(root / "mods" / case["name"]) == case["expected"], case["name"]
            assert result["installer"] == ("root-builder" if case["layout"] in {"root", "skse"} else "simple"), result
        created = harness.control("snapshot")["mods"]
        assert all(not mod["active"] for mod in created), created
        check_metadata(root, cases, created)
        for case in cases:
            harness.control("profile", name=case["name"], active=case["layout"] == "data", priority=0)
        baseline = harness.control("snapshot")["mods"]
        save(root / "baseline.json", baseline)
        harness.install(cases[0], expected="failed")
        harness.cli("install", root / "downloads" / Path(cases[0]["archive"]).name,
                    "--name", "Wrong profile", "--profile", "Not Test", expected="failed")
        harness.cli("install", root / "downloads" / Path(cases[0]["archive"]).name,
                    "--replace", "Missing mod", "--profile", "Test", expected="failed")
    assert all(e["graceful"] and e["exit"] == 0 for e in harness.exits), harness.exits
    check_metadata(root, cases)

    for case in cases:
        mod = root / "mods" / case["name"]
        (mod / "obsolete.txt").write_text("Previous package file", encoding="utf-8")
        if case["layout"] == "root":
            (mod / "Root/obsolete.dll").write_bytes(b"Previous root file")
        for path in mod.rglob("*.toml"):
            path.write_text("# Locally edited packaged configuration\n", encoding="utf-8")

    with harness.session():
        reopened = harness.control("snapshot")["mods"]
        assert [(m["name"], m["active"], m["priority"]) for m in reopened] == [(m["name"], m["active"], m["priority"]) for m in baseline]
        check_metadata(root, cases, reopened)
        for case in cases:
            update = case.get("update", case)
            old_payload = payload(root / "mods" / case["name"])
            harness.install(update, replace=True, expected="complete")
            assert payload(root / "mods" / case["name"]) == update["expected"], case["name"]
            assert payload(root / "mods" / (case["name"] + "_backup")) == old_payload
            case.update(update)
            case.pop("update", None)
        target = cases[0]
        before = payload(root / "mods" / target["name"])
        harness.control("control", hold=True)
        paused = harness.install(target, replace=True, expected="needs_input")
        assert dict(paused["dialog"], buttons=sorted(paused["dialog"]["buttons"])) == {
            "kind": "other", "title": "Test installation pause", "class": "QMessageBox",
            "text": 'A test-owned installer needs a choice for "Café & Co".',
            "informative_text": "Keep the package?\nNo choice is made automatically.",
            "buttons": ["Keep &waiting", "Stop &review"],
        }, paused
        harness.install(cases[1], replace=True, expected="failed")
        cancellation = harness.cli("cancel", paused["operation"], expected="needs_input")
        assert cancellation["cancel_requested"]
        assert all(cancellation["dialog"][key] == value for key, value in paused["dialog"].items())
        assert payload(root / "mods" / target["name"]) == before
        harness.control("release")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            cancelled = harness.cli("status", paused["operation"])
            if cancelled["status"] == "cancelled":
                break
            time.sleep(0.2)
        assert cancelled["status"] == "cancelled", cancelled
        assert payload(root / "mods" / target["name"]) == before
        harness.control("control", progress=True)
        waiting = harness.cli("install", root / "downloads" / Path(target["archive"]).name,
                              "--replace", target["name"], "--profile", "Test", "--wait", "0.25")
        assert waiting["wait_expired"] and waiting["status"] == "installing", waiting
        harness.install(cases[1], replace=True, expected="failed")
        harness.cli("cancel", waiting["operation"], expected="cancelled")
        assert payload(root / "mods" / target["name"]) == before
        harness.control("control", hold=True, progress=True)
        paused = harness.install(target, replace=True, expected="needs_input")
        harness.control("release")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            resumed = harness.cli("status", paused["operation"])
            if resumed["status"] == "installing":
                break
            time.sleep(0.2)
        assert resumed["status"] == "installing" and "dialog" not in resumed and "error" not in resumed, resumed
        visible = harness.control("snapshot")["dialogs"]
        assert any(dialog["class"] == "QProgressDialog" for dialog in visible), visible
        harness.cli("cancel", paused["operation"], expected="cancelled")
        assert payload(root / "mods" / target["name"]) == before
        save(root / "replaced.json", harness.control("snapshot"))
    check_metadata(root, cases)

    # Silent Simple Installer and backup-off are native preferences, set with MO2 closed.
    config = root / "ModOrganizer.ini"
    text = config.read_text(encoding="utf-8-sig")
    assert "Simple%20Installer\\silent=false" in text
    text = text.replace("Simple%20Installer\\silent=false", "Simple%20Installer\\silent=true")
    text = text.replace("backup_install=true", "backup_install=false")
    config.write_text(text, encoding="utf-8")
    with harness.session():
        check_metadata(root, cases, harness.control("snapshot")["mods"])
        original = {case["name"]: payload(root / "mods" / case["name"]) for case in cases}
        harness.control("control", rename=cases[1]["name"])
        wrong = harness.install(cases[0], replace=True, expected="failed")
        assert wrong["error"]["code"] == "WRONG_DESTINATION", wrong
        assert original == {case["name"]: payload(root / "mods" / case["name"]) for case in cases}
        before_mods = sorted(p.name for p in (root / "mods").iterdir())
        for case in cases[:3]:
            harness.install(case, replace=True, expected="complete")
        assert before_mods == sorted(p.name for p in (root / "mods").iterdir())
        save(root / "final-live.json", harness.control("snapshot"))
        harness.control("gui_install", archive=str(root / "downloads" / Path(cases[0]["archive"]).name), name="Test GUI install")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not (root / "mods/Test GUI install/meta.ini").exists():
            time.sleep(0.1)
        assert payload(root / "mods/Test GUI install") == cases[0]["expected"]
        assert harness.cli("status", expected="ready")["active"] is None
    for case in cases:
        assert payload(root / "mods" / case["name"]) == case["expected"], case["name"]
    check_metadata(root, cases)
    assert all(e["graceful"] and e["exit"] == 0 for e in harness.exits), harness.exits
    save(root / "verification.json", {"passed": True, "real_packages": len(cases),
                                      "payload_hashes_match": True, "backup_preferences_verified": True,
                                      "saved_metadata_verified": True, "new_mods_disabled_verified": True,
                                      "replacement_and_cancellation_verified": True,
                                      "wrong_destination_blocked": True, "client_timeout_verified": True,
                                      "qt_text_roundtrip_verified": True, "message_box_diagnostics_verified": True,
                                      "dialog_resumption_verified": True,
                                      "gui_delegation_verified": True, "exits": harness.exits})
    print(json.dumps(load(root / "verification.json"), indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--game", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    args = parser.parse_args()
    cases = load(args.cases)["cases"]
    prepare(args, cases)
    exercise(args.workspace, cases)
