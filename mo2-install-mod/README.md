# MO2 Install Mod

An agent skill for installing and replacing Nexus and custom Skyrim archives through Mod Organizer 2's Simple Installer and Root Builder.
The agent uses a command-line client to control a plugin inside a running MO2 instance.

## Behavior and scope

The agent can create a mod with an exact name or replace an existing mod.
Replacement keeps notes, categories, and the current profile's enabled state and priority, while replacing the package files, including local edits.
MO2's backup preference applies.
Configuration customizations to Nexus packages belong in separate override mods.

Nexus archives use MO2 download metadata for the selected upload.
A helper can create missing metadata from upload information for the Skyrim Special Edition Nexus catalog, including VR uploads.
Custom archives contain a root `meta.ini` declaring a version and optional game name, and are installed directly from the supplied path.

FOMOD installers are not supported.
Unrecognized dialogs require input in MO2.

Mod activation, load-order changes, and Root Builder deployment into the game directory are separate from installation.

## Requirements

- Windows and MO2 2.5.2 with its bundled Python support and Qt 6.7.1.
- Python 3.12 or later for the command-line client and metadata helper.
- Root Builder with its installer enabled for game-root packages (tested with 5.1.1).

## Use

Add this directory to your agent's skills.
[SKILL.md](SKILL.md) contains the agent's commands and required inputs; [plugin setup](references/setup.md) covers installing or updating the MO2 plugin.

## Development

Run unit tests from this directory:

```powershell
python -B -m unittest discover -s tests -v
```

The native harnesses for [Nexus archives](tests/integration.py) and [custom archives](tests/custom_integration.py) exercise installers in disposable MO2 copies under user Temp, with Root Builder deployment disabled.
Run either script with `--help` for fixture requirements and invocation.

Keep `VERSION` in `plugins/mo2_install_mod/wire.py` unchanged while iterating on the same release.
The readiness check uses it to detect an outdated installed package.
