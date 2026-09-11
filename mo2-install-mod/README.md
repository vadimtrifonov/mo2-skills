# MO2 Install Mod

An agent skill for installing and replacing Nexus Skyrim mods through Mod Organizer 2's Simple Installer and Root Builder.
The agent uses a command-line client to control a plugin inside a running MO2 instance.

## Behavior and scope

The agent can create a mod with an exact name or replace an existing mod.
Replacement keeps MO2 metadata and the current profile's enabled state and priority, but removes the old package files, including local edits.
MO2's backup preference applies; configuration customizations belong in separate override mods.

The skill takes a completed Nexus archive and its MO2 download metadata.
A helper can create missing metadata from upload information for the Skyrim Special Edition Nexus catalog, including VR uploads.
FOMOD installers and non-Nexus packages are not supported.
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

The [native test harness](tests/integration.py) exercises real installers in a disposable MO2 copy under user Temp, with Root Builder deployment disabled.
Run `python -B tests/integration.py --help` for fixture requirements and invocation.

Increment `VERSION` in `plugins/mo2_install_mod/wire.py` whenever deployable plugin files change, including the client; the readiness check uses it to detect an outdated installed package.
