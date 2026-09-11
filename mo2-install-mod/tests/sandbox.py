"""Create a disposable portable MO2 from an installed application."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
MARKER = "INSTALL_MOD_TEST_INSTANCE"


def create(template: Path, destination: Path, game: Path):
    if not destination.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise ValueError("Sandbox must be in the user Temp directory.")
    destination.mkdir(parents=True, exist_ok=False)
    (destination / MARKER).write_text("Disposable MO2 install test\n", encoding="utf-8")
    for path in template.iterdir():
        if path.is_file() and path.suffix.lower() in {".exe", ".dll"}:
            shutil.copy2(path, destination / path.name)
    for name in ("dlls", "platforms", "resources", "translations", "styles", "stylesheets", "tutorials", "qml", "licenses", "loot"):
        source = template / name
        if source.is_dir():
            shutil.copytree(source, destination / name)
    plugins = destination / "plugins"
    plugins.mkdir()
    for name in ("game_skyrim.dll", "game_skyrimse.dll", "game_skyrimvr.dll",
                 "installer_quick.dll", "installer_manual.dll", "installer_bundle.dll"):
        shutil.copy2(template / "plugins" / name, plugins / name)
    for name in ("plugin_python", "rootbuilder"):
        shutil.copytree(template / "plugins" / name, plugins / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(PROJECT / "plugins/mo2_install_mod", plugins / "mo2_install_mod",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(PROJECT / "tests" / "probe_plugin.py", plugins / "install_test_probe.py")
    for name in ("categories.dat", "nexuscatmap.dat"):
        shutil.copy2(template / name, destination / name)
    for name in ("downloads", "mods", "overwrite", "profiles/Test"):
        (destination / name).mkdir(parents=True)
    (destination / "portable.txt").touch()
    profile = destination / "profiles/Test"
    for name in ("modlist.txt", "plugins.txt", "loadorder.txt", "archives.txt", "lockedorder.txt", "Skyrim.ini", "SkyrimPrefs.ini"):
        (profile / name).write_text("", encoding="utf-8")
    config = f"""[General]
gameName=Skyrim VR
gamePath=@ByteArray({game.as_posix()})
selected_profile=@ByteArray(Test)
version=2.5.2
first_start=false
backup_install=true

[Settings]
base_directory={destination.as_posix()}
mod_directory=%BASE_DIR%/mods
download_directory=%BASE_DIR%/downloads
profiles_directory=%BASE_DIR%/profiles
overwrite_directory=%BASE_DIR%/overwrite
check_for_updates=false
offline_mode=true
autocheck_update_install=false

[Plugins]
Simple%20Installer\\silent=false
RootBuilder\\installer=true
RootBuilder\\priority=110
RootBuilder\\autobuild=false
RootBuilder\\redirect=false
RootBuilder\\migrated=true

[PluginPersistance]
Install%20Mod\\enabled=true
"""
    (destination / "ModOrganizer.ini").write_text(config, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--game", type=Path, required=True)
    args = parser.parse_args()
    create(args.template.resolve(), args.destination.resolve(), args.game.resolve())
