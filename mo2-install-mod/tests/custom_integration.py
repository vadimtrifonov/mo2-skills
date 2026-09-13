"""Test custom archives through native MO2 installers in a disposable instance.

Run from the skill directory in PowerShell:

    mise exec -- python -B tests/custom_integration.py `
        --template 'C:/Path/To/MO2' `
        --workspace "$env:TEMP/mo2-custom-test" `
        --game 'C:/Steam/steamapps/common/SkyrimVR' `
        --cases 'C:/Fixtures/custom-cases.json'

The template must include MO2's Python support and Root Builder.
--game must point to an installed Skyrim VR game; --workspace must be a new
directory under user Temp. The harness copies the application there, uses a
private Temp directory, disables Root Builder deployment, and closes only its
own MO2 process. No game is launched.

The manifest contains `cases`, each with an absolute `archive`, exact `name`, and
`layout` (`data`, `root`, or `prepared-root`). `root` payloads need a Root/ prefix;
`prepared-root` payloads already contain Root/ alongside any Data folders.
Each archive must contain its own root meta.ini.
The tests copy these archives into the workspace, compare extracted payloads,
replace installed mods, and check saved and live metadata after reopening MO2.
Additional generated fixtures exercise metadata errors, Downloads placement,
sidecar conflicts, filename collisions, cancellation, and backup preferences.
The solid-archive cancellation fixture temporarily uses 4 GiB of disk space during
preparation; only the compressed archive is retained.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import time
import zipfile

from integration import Harness, ini, load, payload, save
from sandbox import create


def archive(path, metadata, content=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as output:
        if metadata is not None:
            # Consecutive equal-size metadata files share an archive timestamp.
            output.writestr(zipfile.ZipInfo("meta.ini", (2020, 1, 1, 0, 0, 0)), metadata)
        for name, data in (content or {"SKSE/Plugins/Custom.ini": "[General]\nvalue=1\n"}).items():
            output.writestr(name, data)
    return path


def prepare_extraction(root):
    # Metadata last in one solid block: extracting it must decode the preceding payload.
    with tempfile.TemporaryDirectory(dir=root) as folder:
        source = Path(folder)
        padding = source / "SKSE/Plugins/Padding.bin"
        padding.parent.mkdir(parents=True)
        block = random.Random(0).randbytes(65536)
        with padding.open("wb") as stream:
            for _ in range(65536):
                stream.write(block)
        (source / "meta.ini").write_text("[General]\nversion=1.2.3\n", encoding="utf-8")
        subprocess.run(["tar.exe", "-cf", str(root / "packages/Metadata extraction.7z"), "--format=7zip",
                        "-C", str(source), "SKSE/Plugins/Padding.bin", "meta.ini"], check=True)
    (root / "meta.ini").write_bytes(b"Unrelated temporary metadata\n")


def check_extraction(harness, target):
    root = harness.root
    before = payload(root / "mods")
    started = harness.cli("install", root / "packages/Metadata extraction.7z", "--custom",
                          "--replace", target["name"], "--profile", "Test", "--wait", "0", expected="accepted")
    operation = started["operation"]
    harness.cli("status", operation, expected="installing")
    visible = harness.control("snapshot")["dialogs"]
    assert any(d["class"] == "QProgressDialog" and d["title"] == "Extracting files" for d in visible), visible
    requested = harness.cli("cancel", operation, "--wait", "0")
    assert requested["cancel_requested"] and "error" not in requested, requested
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = harness.cli("status", operation)
        if result["status"] == "needs_input":
            break
        time.sleep(0.1)
    assert result["status"] == "needs_input" and "error" not in result, result
    assert result["dialog"]["title"] == "Extraction cancelled", result
    busy = install(harness, target, replace=True, expected="failed")
    assert busy["error"]["code"] == "MO2_BUSY", busy
    # Acknowledge the native notice as the human would; the controller must not do so.
    harness.control("acknowledge_extraction_cancel")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = harness.cli("status", operation)
        if result["status"] == "cancelled":
            break
        time.sleep(0.1)
    assert result["status"] == "cancelled" and "error" not in result, result
    assert payload(root / "mods") == before
    assert (root / "meta.ini").read_bytes() == b"Unrelated temporary metadata\n"
    assert not list(root.glob("mo2-install-meta-*.ini"))


def install(harness, case, replace=False, **kwargs):
    result = harness.cli("install", case["archive"], "--custom", "--profile", "Test",
                         "--replace" if replace else "--name", case["name"], **kwargs)
    if result["status"] == "complete":
        case["installed_metadata"] = result["metadata"]
        assert result["custom"]
        assert result["metadata"]["version"] == case["version"], result
        assert result["metadata"]["gameName"].lower() == case["gameName"].lower(), result
        assert payload(harness.root / "mods" / case["name"]) == case["expected"], case["name"]
    return result


def check(root, cases, mods=None):
    for case in cases:
        data = ini(root / "mods" / case["name"] / "meta.ini")
        general = data["General"]
        assert general["version"] == case["version"], case["name"]
        assert general["gameName"].lower() == case["gameName"].lower(), case["name"]
        assert general.getint("modid") == 0 and general["repository"] == "", case["name"]
        recorded = Path(general["installationFile"])
        if not recorded.is_absolute():
            recorded = root / "downloads" / recorded
        assert recorded.resolve() == Path(case["archive"]).resolve(), case["name"]
        assert data["installedFiles"].getint("size") == 0, case["name"]
        assert "Do not import" not in general.get("notes", ""), case["name"]
        if mods is not None:
            mod = next(mod for mod in mods if mod["name"] == case["name"])
            assert (mod["version"], mod["gameName"], mod["modID"], mod["repository"]) == (
                general["version"], general["gameName"], 0, ""), mod


def prepare(args, cases):
    root = args.workspace
    create(args.template, root, args.game)
    (root / "packages").mkdir()
    for case in cases:
        destination = root / "packages" / Path(case["archive"]).name
        shutil.copy2(case["archive"], destination)
        case["archive"] = str(destination)
    extra = [
        {"archive": archive(root / "downloads/Custom in Downloads.zip", "[General]\nversion=2.3.4\n"),
         "name": "Test Custom Downloads", "layout": "data"},
        {"archive": archive(root / "packages/Custom root.zip", "[General]\nversion=3.4.5\n",
                            {"d3dx9_42.dll": b"Root Builder test payload"}),
         "name": "Test Custom Root", "layout": "root"},
        {"archive": archive(root / "packages/Prepared root.zip", "[General]\nversion=5.6.7\n",
                            {"Root/example.dll": b"Prepared root file", "SKSE/Plugins/example.ini": "[General]\nvalue=1\n"}),
         "name": "Test Prepared Root", "layout": "prepared-root"},
        {"archive": archive(root / "packages/collision-58101-1-0.zip", "[General]\nversion=4.5.6\ngameName=skyrimse\n"
                            "modid=98765\nrepository=Nexus\nnotes=Do not import\ninstallationFile=wrong.zip\n"
                            "[installedFiles]\nsize=1\n1\\modid=98765\n1\\fileid=4321\n"),
         "name": "Test Custom Identity", "layout": "data"},
    ]
    # MO2 can add an association by basename even for an archive outside Downloads.
    shadow = root / "downloads" / Path(extra[-1]["archive"]).name
    archive(shadow, None, {"SKSE/Plugins/Other.ini": "Another download"})
    (Path(str(shadow) + ".meta")).write_text("[General]\ngameName=SkyrimSE\nmodID=58101\nfileID=799533\n"
                                                "repository=Nexus\nversion=9.0\nfileCategory=1\ninstalled=false\nuninstalled=false\n", encoding="utf-8")
    # An adjacent sidecar must not determine the custom mod's installed identity.
    (Path(str(extra[-1]["archive"]) + ".meta")).write_text("[General]\ngameName=SkyrimVR\nmodID=62089\nfileID=800142\n"
                                                          "repository=Nexus\nversion=8.0\n", encoding="utf-8")
    cases.extend(extra)
    for case in cases:
        case["archive"] = str(case["archive"])
        extracted = root / "expected" / Path(case["archive"]).name
        extracted.mkdir(parents=True)
        subprocess.run(["tar.exe", "-xf", case["archive"], "-C", str(extracted)], check=True)
        case["metadata_path"] = str(extracted / "meta.ini")
        fields = ini(extracted / "meta.ini")["General"]
        case["gameName"] = fields.get("gameName", "SkyrimVR")
        files = payload(extracted)
        case["expected"] = {("Root/" if case["layout"] == "root" else "") + name: digest for name, digest in files.items()}
    prepare_extraction(root)
    save(root / "cases.json", cases)
    return cases


def exercise(root, cases):
    harness = Harness(root)
    archive_hashes = {case["archive"]: hashlib.sha256(Path(case["archive"]).read_bytes()).hexdigest() for case in cases}
    with harness.session():
        for case in cases:
            case["version"] = harness.control("read_ini", path=case["metadata_path"])["canonical_version"]
            result = install(harness, case, expected="complete")
            assert result["installer"] == ("root-builder" if case["layout"] in {"root", "prepared-root"} else "simple"), result
            harness.control("touch_metadata", name=case["name"])
        shadow = root / "downloads/collision-58101-1-0.zip"
        assert not ini(Path(str(shadow) + ".meta"))["General"].getboolean("installed")
        name = "Test Source Transition"
        nexus = harness.cli("install", shadow, "--name", name, "--profile", "Test", expected="complete")
        assert nexus["metadata"]["modID"] == 58101
        transition = dict(cases[0], name=name)
        install(harness, transition, replace=True, expected="complete")
        check(root, [transition], harness.control("snapshot")["mods"])
        nexus = harness.cli("install", shadow, "--replace", name, "--profile", "Test", expected="complete")
        assert nexus["metadata"]["repository"] == "Nexus" and nexus["metadata"]["installedFiles"] == [[58101, 799533]], nexus
        created = harness.control("snapshot")["mods"]
        assert all(not mod["active"] for mod in created), created
        check(root, cases, created)
        for case in cases:
            harness.control("profile", name=case["name"], active=case["layout"] == "data", priority=0)
        baseline = harness.control("snapshot")["mods"]
        check_extraction(harness, cases[0])
        # Errors must not create backups, remove files, or replace saved metadata.
        target = cases[0]
        before = payload(root / "mods")
        saved_meta = (root / "mods" / target["name"] / "meta.ini").read_bytes()
        for name, metadata, content, code in (
            ("missing", None, None, "MISSING_METADATA"),
            ("empty-version", "[General]\nversion=\n", None, "INVALID_METADATA"),
            ("empty-canonical-version", "[General]\nversion=v\n", None, "INVALID_METADATA"),
            ("unknown-game", "[General]\nversion=1.0\ngameName=Unknown\n", None, "INVALID_METADATA"),
            ("nested", None, {"Data/meta.ini": "[General]\nversion=1.0\n", "Data/Example.esp": "Fixture"}, "INSTALLER_REQUIRED"),
            ("fomod", "[General]\nversion=1.0\n", {"fomod/ModuleConfig.xml": "<config/>"}, "INSTALLER_REQUIRED"),
            ("bundle", "[General]\nversion=1.0\n",
             {"inner.zip": archive(root / "packages/inner.zip", "[General]\nversion=2.0\n").read_bytes()}, "INSTALLER_REQUIRED"),
        ):
            path = archive(root / "packages" / (name + ".zip"), metadata, content)
            result = install(harness, dict(target, archive=str(path)), replace=True, expected="failed")
            assert result["error"]["code"] == code, result
            assert payload(root / "mods") == before, name
            assert (root / "mods" / target["name"] / "meta.ini").read_bytes() == saved_meta, name
        save(root / "baseline.json", baseline)
    check(root, cases)

    config = root / "ModOrganizer.ini"
    config.write_text(config.read_text(encoding="utf-8-sig").replace("Simple%20Installer\\silent=false", "Simple%20Installer\\silent=true"), encoding="utf-8")
    for case in cases:
        directory = root / "mods" / case["name"]
        (directory / "obsolete.txt").write_text("Previous package file", encoding="utf-8")
        # Seed user metadata and stale source metadata with MO2 closed.
        data = ini(directory / "meta.ini")
        data["General"]["notes"] = "Keep user notes"
        data["General"]["category"] = "1"
        data["General"]["newestVersion"] = "99.0.0.0"
        data["General"]["ignoredVersion"] = "98.0.0.0"
        with (directory / "meta.ini").open("w", encoding="utf-8") as stream:
            data.write(stream)
    with harness.session():
        reopened = harness.control("snapshot")["mods"]
        assert [(m["name"], m["active"], m["priority"]) for m in reopened] == [(m["name"], m["active"], m["priority"]) for m in baseline]
        check(root, cases, reopened)
        for case in cases:
            old = payload(root / "mods" / case["name"])
            install(harness, case, replace=True, expected="complete")
            assert payload(root / "mods" / (case["name"] + "_backup")) == old
            data = harness.control("read_ini", path=str(root / "mods" / case["name"] / "meta.ini"),
                                   keys=["notes", "category", "newestVersion", "ignoredVersion"])["values"]
            data = {key.lower(): value for key, value in data.items()}
            assert data["notes"] == "Keep user notes" and data["category"].split(",")[0] == "1", data
            assert not data.get("newestversion") and not data.get("ignoredversion"), data
        target = cases[0]
        before = payload(root / "mods")
        harness.control("control", progress=True)
        waiting = harness.cli("install", target["archive"], "--custom", "--replace", target["name"],
                              "--profile", "Test", "--wait", "0.25")
        assert waiting["wait_expired"] and waiting["status"] == "installing", waiting
        harness.cli("cancel", waiting["operation"], expected="cancelled")
        assert payload(root / "mods") == before
        harness.control("control", rename=cases[1]["name"])
        result = install(harness, target, replace=True, expected="failed")
        assert result["error"]["code"] == "WRONG_DESTINATION", result
        assert payload(root / "mods") == before
        check(root, cases, harness.control("snapshot")["mods"])
    check(root, cases)

    config = root / "ModOrganizer.ini"
    config.write_text(config.read_text(encoding="utf-8-sig").replace("backup_install=true", "backup_install=false"), encoding="utf-8")
    with harness.session():
        check(root, cases, harness.control("snapshot")["mods"])
        before_names = sorted(path.name for path in (root / "mods").iterdir())
        # A new archive and lower explicit version must replace the previous association.
        target = cases[0]
        path = archive(root / "replacement.zip", "[General]\nversion=0.5.0\n")
        target.update(archive=str(path), version="0.5.0.0", gameName="SkyrimVR",
                      expected={"SKSE/Plugins/Custom.ini": hashlib.sha256(b"[General]\nvalue=1\n").hexdigest()})
        install(harness, target, replace=True, expected="complete")
        assert before_names == sorted(path.name for path in (root / "mods").iterdir())
        for case in cases:
            harness.control("touch_metadata", name=case["name"])
        check(root, cases, harness.control("snapshot")["mods"])
    check(root, cases)
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in archive_hashes.items())
    assert all(exit["graceful"] and exit["exit"] == 0 for exit in harness.exits), harness.exits
    assert (root / "meta.ini").read_bytes() == b"Unrelated temporary metadata\n"
    assert not list(root.glob("mo2-install-meta-*.ini"))
    save(root / "cases.json", cases)
    report = {"passed": True, "custom_archives": len(cases), "payload_hashes_match": True,
              "saved_and_live_metadata_verified": True, "new_mods_disabled_verified": True,
              "replacement_and_backups_verified": True, "source_transitions_verified": True,
              "invalid_metadata_precedes_replacement": True,
              "cancellation_and_destination_verified": True, "metadata_extraction_cancellation_verified": True,
              "shared_temp_metadata_preserved": True, "prepared_root_layout_verified": True,
              "input_archives_unchanged": True, "exits": harness.exits}
    save(root / "verification.json", report)
    print(report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--game", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    args = parser.parse_args()
    exercise(args.workspace, prepare(args, load(args.cases)["cases"]))
