# MO2 Install Mod

An agent skill for installing and replacing Nexus and custom Skyrim archives through Mod Organizer 2's Simple Installer, Root Builder, and FOMOD Plus.

## Behavior and scope

The agent can create a mod with an exact name or replace an existing mod.
Replacement keeps notes, categories, and the current profile's enabled state and priority, while replacing the package files, including local edits.
MO2's backup preference applies.
Configuration customizations to Nexus packages belong in separate override mods.

Nexus archives use MO2 download metadata for the selected upload.
Custom archives contain a root `meta.ini` declaring a version and optional game name, and are installed directly from the supplied path.

For XML FOMOD installers, the agent can read option descriptions, make selections, and confirm installation.
FOMOD Plus handles dependencies and remembers choices for later replacements.
Legacy scripted FOMOD installers are not supported.
Unrecognized dialogs require input in MO2.

New mods are installed disabled.
Mod activation, load-order changes, and Root Builder deployment into the game directory are separate from installation.

## Requirements

- Windows and MO2 2.5.2 or 2.5.3 with its bundled Python support.
- [mise](https://mise.jdx.dev/) for the external client and test drivers.
- Root Builder with its installer enabled for game-root packages (tested with 5.1.1).
- FOMOD Plus enabled for XML FOMOD archives (tested with 1.20.0).

## Use

Add this directory to your agent's skills.
[SKILL.md](SKILL.md) contains the agent's commands, required inputs, and plugin setup instructions.

## Development

Run unit tests from this directory:

```powershell
mise run test
```

The integration tests for [Nexus archives](tests/integration.py), [custom archives](tests/custom_integration.py), and [FOMOD choices](tests/fomod_integration.py) describe their setup and invocation in `--help`.

Keep `VERSION` in `plugins/mo2_install_mod/wire.py` unchanged while iterating on the same release.
The readiness check uses it to detect an outdated installed package.
