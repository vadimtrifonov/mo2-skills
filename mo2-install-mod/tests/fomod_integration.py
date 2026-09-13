"""Test FOMOD Plus installation and choices in a disposable MO2 instance.

Run from the skill directory in PowerShell:

    mise exec -- python -B tests/fomod_integration.py `
        --template 'C:/Path/To/MO2' `
        --workspace "$env:TEMP/mo2-fomod-test" `
        --game 'C:/Steam/steamapps/common/SkyrimVR' `
        --cases 'C:/Fixtures/fomod.json'

The template must include MO2's Python support, Root Builder, and FOMOD Plus.
--game must point to an installed Skyrim VR game; --workspace must be a new
directory under user Temp. The harness copies the application there, uses a
private Temp directory, disables Root Builder deployment, and closes only its
own MO2 process. No game is launched.

The JSON manifest has a `cases` array. Each case supplies an absolute `archive`
path, an exact mod `name`, a `role`, and Nexus `metadata`: `game`, `mod_id`,
`file_id`, `file_name`, `file_version`, `file_category`, `mod_name`, and
`mod_category`, with optional plain-text `description` and `expected_size`.
Set `role` to `skyvraan` for SkyVRaan 2.0.0, `patches` for Lux Orbis Misc
Patches 1.10, and `large` for Lux patch hub 7.1.
The archives are copied to the workspace, where additional fixtures are generated
for choice constraints, metadata protection, and approval checks.

verification.json summarizes completed runs. Expected SkyVRaan payloads use
explicitly selected XML file declarations and pattern indices.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
import zipfile

from integration import Harness, load, payload, save, write_nexus_sidecar
from sandbox import create


class FomodHarness(Harness):
    def __init__(self, root):
        super().__init__(root)
        self.mods = root / "mods"

    def start(self, case, replace=False):
        args = ["install", case["archive"], "--replace" if replace else "--name", case["name"], "--profile", "Test"]
        if case.get("custom"):
            args.append("--custom")
        return self.cli(*args, expected="needs_input")

    def answer(self, state, action="stay", select=(), deselect=(), expected="needs_input"):
        args = ["respond", state["operation"], "--view", state["dialog"]["view"], "--action", action]
        for key in select:
            args.extend(["--select", key])
        for key in deselect:
            args.extend(["--deselect", key])
        return self.cli(*args, expected=expected)

    def cancel(self, state):
        result = self.cli("cancel", state["operation"])
        deadline = time.monotonic() + 15
        while result["status"] == "needs_input" and result.get("dialog", {}).get("kind") == "fomod-plus" and time.monotonic() < deadline:
            time.sleep(0.1)
            result = self.cli("status", state["operation"])
        assert result["status"] == "cancelled", result

    def options(self, state):
        return {group["name"]: self.cli("status", state["operation"], "--group", group["id"],
                                       expected="needs_input")["choices"]["options"] for group in state["dialog"]["groups"]}

    def finish_defaults(self, state, select=()):
        observed = []
        found = set()
        while state["status"] == "needs_input":
            assert state["dialog"]["kind"] == "fomod-plus", state
            groups = self.options(state)
            observed.append({"step": state["dialog"]["name"], "groups": groups})
            options = [o for options in groups.values() for o in options if o["name"] in select]
            found.update(o["name"] for o in options)
            action = "install" if "install" in state["dialog"]["actions"] else "next"
            state = self.answer(state, action, select=[o["id"] for o in options], expected="complete" if action == "install" else "needs_input")
        assert found == set(select), (found, select)
        return state, observed


def prepare(args, cases):
    create(args.template, args.workspace, args.game)
    for name in ("fomod_plus_installer.dll", "fomod_plus_scanner.dll"):
        shutil.copy2(args.template / "plugins" / name, args.workspace / "plugins" / name)
    for case in cases:
        source = Path(case["archive"])
        case["source_archive"] = str(source)
        case["source_hash"] = hashlib.sha256(source.read_bytes()).hexdigest()
        archive = args.workspace / "downloads" / source.name
        shutil.copy2(source, archive)
        write_nexus_sidecar(archive, **case["metadata"])
        case["archive"] = str(archive)
        if case["role"] == "skyvraan":
            extracted = args.workspace / "expected/skyvraan"
            extracted.mkdir(parents=True)
            subprocess.run(["tar.exe", "-xf", str(archive), "-C", str(extracted)], check=True)
            case["xml"] = str(next(extracted.rglob("ModuleConfig.xml")))
    save(args.workspace / "cases.json", cases)
    return cases


def expected_sky(case, choices, patterns):
    xml = Path(case["xml"])
    tree = ET.parse(xml)
    files = []
    for step in tree.findall("./installSteps/installStep"):
        for group in step.findall("./optionalFileGroups/group"):
            for option in group.findall("./plugins/plugin"):
                if option.get("name") in choices.get(group.get("name"), []):
                    files.extend(option.findall("./files/file"))
    conditional = tree.findall("./conditionalFileInstalls/patterns/pattern")
    for index in patterns:
        files.extend(conditional[index].findall("./files/file"))
    return {f.get("destination").replace("\\", "/"): hashlib.sha256(
        (xml.parent.parent / f.get("source").replace("\\", "/")).read_bytes()).hexdigest() for f in files}


RICH_SKY = {
    "Vanilla or RW2": ["Realistic Water 2"], "Main Watercolor": ["Fantasy"],
    "Volcanic Water": ["Poisonous"], "Blackreach Water": ["Blackreach Water"],
    "VR Tweaks for RW2?": ["RW2 VR Tweaks (Recommended for VR)"],
    "SkyVRaan Rocky River Beds": ["Rocky River Beds"], "Density": ["High Density"],
    "Rock Shade": ["Dark (MD's Landscapes)"], "Rock Color": ["Brown"],
    "SkyVRaan Patchers": ["VR ONLY!!!! Install SkyVRaan Weather Patchers"], "Rally's Water Foam": ["2K"],
}


def skyvraan(h, case):
    state = h.start(case)
    assert state["dialog"]["step"] == 0 and state["installer"] == "fomod-plus", state
    before = h.options(state)
    natural = next(o for o in before["Main Watercolor"] if o["name"] == "Natural")
    description = h.cli("status", state["operation"], "--option", natural["id"])["choice"]["description"]
    assert "weather" in description and "Natural waters" in description, description
    assert h.options(state) == before, "Reading details changed selections"
    invalid = h.answer(state, "install", expected="failed")
    assert invalid["error"]["code"] == "INVALID_CHOICE", invalid
    old = state
    state = h.answer(state, select=[natural["id"]])
    stale = h.answer(old, "next", expected="failed")
    assert stale["error"]["code"] == "STALE_VIEW", stale
    state = h.answer(state, "next")
    assert state["dialog"]["step"] == 2, state
    state = h.answer(state, "back")
    assert state["dialog"]["step"] == 0, state
    result, pages = h.finish_defaults(state)
    assert [p["step"] for p in pages] == ["VR Water Color Patches", "SkyVRaan Rocky Riverbeds", "SKSE Weather Patcher plugin", "Extras"]
    minimal = expected_sky(case, {}, [6])
    assert payload(h.mods / case["name"]) == minimal
    h.control("profile", name=case["name"], active=True, priority=0)
    state = h.start(case, replace=True)
    restored = h.options(state)["Main Watercolor"]
    assert next(o for o in restored if o["name"] == "Natural")["selected"], restored
    busy = h.cli("install", case["archive"], "--name", "Another installation", "--profile", "Test", expected="failed")
    assert busy["error"]["code"] == "MO2_BUSY", busy
    steps = []
    while state["status"] == "needs_input":
        steps.append(state["dialog"]["step"])
        groups = h.options(state)
        chosen = [o["id"] for name, options in groups.items() for o in options if o["name"] in RICH_SKY.get(name, [])]
        action = "install" if "install" in state["dialog"]["actions"] else "next"
        state = h.answer(state, action, select=chosen, expected="complete" if action == "install" else "needs_input")
    assert steps == [0, 1, 2, 3, 4, 5], steps
    rich = expected_sky(case, RICH_SKY, [5, 12])
    assert payload(h.mods / case["name"]) == rich
    assert payload(h.mods / (case["name"] + "_backup")) == minimal
    assert not (h.mods / case["name"] / "NewVRWaterColor-Vanilla-Default.esp").exists()
    case["installed_metadata"] = state["metadata"]
    case["expected"] = rich
    paused = h.start(case, replace=True)
    h.cancel(paused)
    assert payload(h.mods / case["name"]) == rich
    assert h.cli("status", expected="ready")["active"] is None
    return {"branches": steps, "installed_files": len(rich), "replacement_backups_and_cancellation": True,
            "descriptions_and_stale_answers": True, "restored_choices": True}


def custom_fixture(root):
    archive = root / "Custom FOMOD.zip"
    xml = '''<config><moduleName>Custom choices</moduleName><installSteps order="Explicit"><installStep name="Choices">
<optionalFileGroups order="Explicit">
<group name="Required" type="SelectAll"><plugins><plugin name="Base"><description>Required base</description><files><file source="files/base.ini" destination="SKSE/Plugins/example.ini" priority="0"/></files><typeDescriptor><type name="Required"/></typeDescriptor></plugin></plugins></group>
<group name="Variant" type="SelectExactlyOne"><plugins order="Explicit">
<plugin name="Alpha"><description>Alpha</description><files><file source="files/a.ini" destination="SKSE/Plugins/example.ini" priority="10"/></files><typeDescriptor><type name="Optional"/></typeDescriptor></plugin>
<plugin name="Beta"><description>Beta</description><files><file source="files/b.ini" destination="SKSE/Plugins/example.ini" priority="10"/></files><typeDescriptor><type name="Optional"/></typeDescriptor></plugin>
</plugins></group>
<group name="Optional" type="SelectAny"><plugins><plugin name="Extra"><description>Optional extra</description><files><file source="files/extra.ini" destination="SKSE/Plugins/extra.ini"/></files><typeDescriptor><type name="Recommended"/></typeDescriptor></plugin></plugins></group>
<group name="Unavailable" type="SelectAny"><plugins><plugin name="Unavailable"><description>Unavailable choice</description><files><file source="files/never.ini" destination="SKSE/Plugins/never.ini"/></files><typeDescriptor><type name="NotUsable"/></typeDescriptor></plugin></plugins></group>
</optionalFileGroups></installStep></installSteps></config>'''
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("meta.ini", "[General]\nversion=2.1.0\ngameName=SkyrimSE\n")
        z.writestr("fomod/ModuleConfig.xml", xml)
        z.writestr("fomod/info.xml", "<fomod><Name>Custom choices</Name><Version>99.0</Version></fomod>")
        for name in ("base", "a", "b", "extra", "never"):
            z.writestr(f"files/{name}.ini", f"[General]\nvalue={name}\n")
    return {"name": "Test Custom FOMOD", "archive": str(archive), "custom": True}


def custom_choices(h):
    case = custom_fixture(h.root)
    state = h.start(case)
    groups = h.options(state)
    required, = groups["Required"]
    unavailable, = groups["Unavailable"]
    extra, = groups["Optional"]
    assert required["selected"] and not required["enabled"], required
    assert not unavailable["enabled"] and not unavailable["selected"], unavailable
    assert extra["selected"] and extra["enabled"], extra
    for kwargs in ({"select": [unavailable["id"]]}, {"deselect": [required["id"]]}, {"select": ["99.0.0"]}):
        bad = h.answer(state, expected="failed", **kwargs)
        assert bad["error"]["code"] == "INVALID_CHOICE", bad
    beta = next(o for o in groups["Variant"] if o["name"] == "Beta")
    conflict = h.answer(state, "install", select=[o["id"] for o in groups["Variant"]], expected="failed")
    assert conflict["error"]["code"] == "CHOICE_REJECTED", conflict
    assert not (h.mods / case["name"]).exists()
    state = h.cli("status", state["operation"], expected="needs_input")
    assert "error" not in state, state
    result = h.answer(state, "install", select=[beta["id"]], deselect=[extra["id"]], expected="complete")
    assert result["metadata"]["version"] == "2.1.0.0", result
    assert result["metadata"]["modID"] == 0 and result["metadata"]["installedFiles"] == [], result
    expected = {"SKSE/Plugins/example.ini": hashlib.sha256(b"[General]\nvalue=b\n").hexdigest()}
    assert payload(h.mods / case["name"]) == expected
    restored = h.start(case, replace=True)
    groups = h.options(restored)
    assert next(o for o in groups["Variant"] if o["name"] == "Beta")["selected"], groups
    assert not groups["Optional"][0]["selected"], groups
    h.cancel(restored)
    assert payload(h.mods / case["name"]) == expected
    return {"required_and_disabled_choices": True, "priority_mapping": True, "custom_metadata": True,
            "manual_deselection_persisted": True}


def contract_fixtures(root):
    """Small synthetic archives for destination and native wizard contracts."""
    base = '''<config><moduleName>Contract fixture</moduleName><installSteps order="Explicit">
<installStep name="Choices"><optionalFileGroups><group name="Variant" type="SelectExactlyOne"><plugins order="Explicit">
<plugin name="Alpha"><description>Alpha</description><files>MAPPING</files><typeDescriptor><type name="Optional"/></typeDescriptor></plugin>
<plugin name="Beta"><description>Beta</description><files><file source="files/beta.ini" destination="SKSE/Plugins/chosen.ini"/></files><typeDescriptor><type name="Optional"/></typeDescriptor></plugin>
</plugins></group></optionalFileGroups></installStep>
<installStep name="Approval"><optionalFileGroups><group name="Final" type="SelectAll"><plugins><plugin name="Ready"><description>Ready</description><files><file source="files/final.ini" destination="SKSE/Plugins/final.ini"/></files><typeDescriptor><type name="Required"/></typeDescriptor></plugin></plugins></group></optionalFileGroups></installStep>
</installSteps></config>'''
    normal = base.replace("MAPPING", '<file source="files/alpha.ini" destination="SKSE/Plugins/chosen.ini"/>')
    packaged = "[General]\nnotes=PACKAGED NOTES\ncomments=PACKAGED COMMENTS\ncategory=48\n"

    def archive(label, xml, custom=False):
        path = root / "downloads" / (label + ".zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as output:
            output.writestr("fomod/ModuleConfig.xml", xml)
            for variant in ("alpha", "beta", "final"):
                output.writestr("files/" + variant + ".ini", variant)
            output.writestr("files/renamed.txt", packaged)
            if custom:
                output.writestr("meta.ini", "[General]\nversion=2.1.0\ngameName=SkyrimSE\n")
            else:
                output.writestr("files/meta.ini", packaged)
        if not custom:
            write_nexus_sidecar(path, game="skyrimspecialedition", mod_id=58101, file_id=799533,
                                file_name=label, file_version="1.0", file_category="MAIN", mod_name="Contract fixture", mod_category=108)
        return {"name": label, "archive": str(path), "custom": custom, "source_hash": hashlib.sha256(path.read_bytes()).hexdigest()}

    unsafe = [archive(label, base.replace("MAPPING", mapping), custom) for label, mapping, custom in (
        ("Folder metadata", '<folder source="files" destination=""/>', False),
        ("Empty file destination", '<file source="files/meta.ini" destination=""/>', False),
        ("Renamed metadata", '<file source="files/renamed.txt" destination="MeTa.InI"/>', False),
        ("Custom renamed metadata", '<file source="files/renamed.txt" destination="meta.ini"/>', True))]
    conditional = normal.replace("</config>", '''<conditionalFileInstalls><patterns><pattern><dependencies><flagDependency flag="never" value="On"/></dependencies><files><folder source="files" destination=""/></files></pattern></patterns></conditionalFileInstalls></config>''')
    unsafe.extend((
        archive("Conditional metadata", conditional),
        archive("Default namespace file", base.replace("<config>", '<config xmlns="urn:fomod">').replace(
            "MAPPING", '<file source="files/renamed.txt" destination="meta.ini"/>')),
        archive("Default namespace folder", base.replace(
            "MAPPING", '<folder xmlns="urn:fomod" source="files" destination=""/>'))))
    zero = ET.fromstring(normal)
    zero.remove(zero.find("installSteps"))
    required = ET.SubElement(zero, "requiredInstallFiles")
    ET.SubElement(required, "file", source="files/alpha.ini", destination="SKSE/Plugins/chosen.ini")
    hidden = ET.fromstring(normal)
    visible = ET.Element("visible")
    ET.SubElement(visible, "flagDependency", flag="never", value="On")
    hidden.findall("./installSteps/installStep")[-1].insert(0, visible)
    # The default namespace is ignored by FOMOD Plus; the prefixed element is not a file copy.
    safe = base.replace("<config>", '<config xmlns="urn:fomod">').replace(
        "MAPPING", '<folder source="files" destination="SKSE/Plugins"/>'
        '<extra:file xmlns:extra="urn:fomod" source="files/renamed.txt" destination="meta.ini"/>')
    fixtures = {"unsafe": unsafe, "choices": archive("Test Approval", normal),
                "safe": archive("Test Reserved Metadata", safe),
                "zero": archive("Test Zero Steps", ET.tostring(zero, encoding="unicode")),
                "hidden_final": archive("Test Hidden Final", ET.tostring(hidden, encoding="unicode"))}
    target = root / "mods" / fixtures["safe"]["name"]
    target.mkdir()
    (target / "old.ini").write_text("Existing payload", encoding="utf-8")
    (target / "meta.ini").write_text("[General]\ngameName=SkyrimSE\nmodid=58101\nrepository=Nexus\nversion=1.0\nnotes=USER NOTES\ncomments=USER COMMENTS\ncategory=2\n", encoding="utf-8")
    return fixtures


def interaction_contracts(h, fixtures):
    target = h.mods / fixtures["safe"]["name"]
    before = (target / "meta.ini").read_bytes(), payload(target)
    for case in fixtures["unsafe"]:
        args = ["install", case["archive"], "--replace", target.name, "--profile", "Test"]
        if case["custom"]:
            args.append("--custom")
        rejected = h.cli(*args, expected="failed")
        assert rejected["error"]["code"] == "INSTALLER_REQUIRED" and "meta.ini" in rejected["error"]["message"], rejected
        assert "unselected" in rejected["error"]["message"] and "files" in rejected["error"]["message"], rejected
        assert ((target / "meta.ini").read_bytes(), payload(target)) == before
        assert not (h.mods / (target.name + "_backup")).exists()
    h.finish_defaults(h.start(fixtures["safe"], replace=True), select=["Alpha"])
    assert (target / "SKSE/Plugins/meta.ini").read_text().startswith("[General]\nnotes=PACKAGED NOTES")
    assert not (target / "old.ini").exists()

    case = fixtures["choices"]
    state = h.start(case)
    final = h.answer(state, "next", select=["0.0.0"])
    for label in ("Back", "Beta", "Next"):
        h.control("fomod_control", click=label)
    current = h.cli("status", final["operation"], expected="needs_input")
    assert current["dialog"]["view"] != final["dialog"]["view"]
    rejected = h.answer(final, "install", expected="failed")
    assert rejected["error"]["code"] == "STALE_VIEW", rejected
    assert not (h.mods / case["name"]).exists()
    h.answer(current, "install", expected="complete")
    assert (h.mods / case["name"] / "SKSE/Plugins/chosen.ini").read_text() == "beta"
    before = payload(h.mods / case["name"])

    state = h.answer(h.start(case, replace=True), "next")
    for label in ("Back", "Reset Choices"):
        h.control("fomod_control", focus=label)
        focused = h.cli("status", state["operation"], expected="needs_input")
        assert focused["dialog"]["view"] == state["dialog"]["view"] and "error" not in focused, focused
        h.cli("status", expected="ready")
    state = h.answer(state, "back")
    h.cancel(state)
    assert payload(h.mods / case["name"]) == before

    h.control("fomod_fallback", enabled=True)
    rejected = h.cli("install", case["archive"], "--replace", case["name"], "--profile", "Test", expected="failed")
    assert rejected["error"]["code"] == "INSTALLER_REQUIRED" and "fallback_to_legacy" in rejected["error"]["message"], rejected
    h.control("fomod_fallback", enabled=False)
    state = h.start(case, replace=True)
    h.control("fomod_control", click="Cancel")
    deadline = time.monotonic() + 15
    while state["status"] not in {"cancelled", "failed", "complete"} and time.monotonic() < deadline:
        time.sleep(0.1)
        state = h.cli("status", state["operation"])
    assert state["status"] == "cancelled" and not state["cancel_requested"], state
    assert payload(h.mods / case["name"]) == before
    assert not (h.mods / (case["name"] + "_backup")).exists()
    for key in ("zero", "hidden_final"):
        case = fixtures[key]
        state = h.start(case)
        assert "install" in state["dialog"]["actions"] and not (h.mods / case["name"]).exists(), state
        h.answer(state, "install", expected="complete")
    return {"metadata_mappings_blocked": len(fixtures["unsafe"]), "nested_metadata_allowed": True,
            "cross_page_approval": True, "focus_and_cli_cancel": True, "fallback_gate_and_gui_cancel": True,
            "zero_step_and_hidden_final_approval": True}


def exercise(root, cases):
    fixtures = contract_fixtures(root)
    h = FomodHarness(root)
    report = {}
    with h.session():
        report["contracts"] = interaction_contracts(h, fixtures)
        for case in cases:
            if case["role"] == "skyvraan":
                report["skyvraan"] = skyvraan(h, case)
            else:
                chosen = ["SMIM"] if case["role"] == "patches" else ["Alternate Start - Live Another Life"]
                result, pages = h.finish_defaults(h.start(case), select=chosen)
                save(root / (case["role"] + "-choices.json"), pages)
                assert payload(h.mods / case["name"]), result
                case["installed_metadata"] = result["metadata"]
                report[case["role"]] = {"steps": len(pages), "options": sum(len(o) for p in pages for o in p["groups"].values()),
                                       "installed_files": len(payload(h.mods / case["name"]))}
        report["custom"] = custom_choices(h)
        saved = {c["name"]: h.control("fomod_saved", name=c["name"])["fomod"] for c in cases}
        save(root / "saved-choices.json", saved)
        for value in saved.values():
            assert value and value["steps"], value
    with h.session():
        for case in cases:
            assert h.control("fomod_saved", name=case["name"])["fomod"] == saved[case["name"]]
            if case.get("expected"):
                assert payload(h.mods / case["name"]) == case["expected"]
        metadata = h.control("read_ini", path=str(h.mods / fixtures["safe"]["name"] / "meta.ini"), keys=["notes", "comments", "category"])["values"]
        assert metadata["notes"] == "USER NOTES" and metadata["comments"] == "USER COMMENTS" and metadata["category"].rstrip(",") == "2", metadata
    assert all(e["graceful"] and e["exit"] == 0 for e in h.exits), h.exits
    generated = fixtures["unsafe"] + [fixtures[key] for key in ("safe", "choices", "zero", "hidden_final")]
    assert all(hashlib.sha256(Path(c["archive"]).read_bytes()).hexdigest() == c["source_hash"] for c in generated)
    assert all(hashlib.sha256(Path(c["source_archive"]).read_bytes()).hexdigest() == c["source_hash"] for c in cases)
    report.update(passed=True, exits=h.exits, choices_persisted_after_reopening=True, source_archives_unchanged=True)
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
