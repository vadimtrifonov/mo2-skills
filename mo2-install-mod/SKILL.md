---
name: mo2-install-mod
description: Install and replace Nexus Skyrim mods in MO2 through Simple Installer or Root Builder.
compatibility: Windows; MO2 2.5.2 with Qt 6.7.1; Python 3.12+.
---

# Install Nexus Mods in MO2

FOMOD installers are unsupported; archives containing `meta.ini` are rejected.
Run the commands below from this skill's directory.

## Instance paths

Read the instance's `ModOrganizer.ini` to resolve its paths.
Under `[Settings]`, `base_directory` defaults to the directory containing that INI.
`download_directory` defaults to `%BASE_DIR%/downloads`; `%BASE_DIR%` refers to the resolved base directory.
Use `$base` for the resolved base directory, `$archive` for the archive's absolute path, and `$profile` for the intended profile name.

## Archive and metadata

`$archive` must be a completed Nexus ZIP, 7z, or RAR directly in the resolved Downloads directory.
Reuse `<archive>.meta` from an MO2 download or earlier installation if it identifies the intended Nexus upload and file version.
If no sidecar exists, create one with MO2 closed using the selected upload's information:

```powershell
python -B scripts/nexus_download_meta.py $archive `
    --game skyrimspecialedition --mod-id 62089 --file-id 800142 `
    --file-name 'Part 2 Engine Fixes VR v1.26' --file-version v1.26a --file-category MAIN `
    --mod-name 'Engine Fixes VR' --mod-category 108
```

| Argument | Meaning |
| --- | --- |
| `--game` | `skyrimspecialedition` only; written as `gameName=SkyrimSE`, including when the managed game is Skyrim VR. |
| `--mod-id`, `--file-id` | Nexus mod and upload IDs within that catalog. |
| `--file-name`, `--file-version` | Selected upload's display name and version—not the archive filename or the mod page's latest version. |
| `--file-category` | Nexus upload category: `MAIN`, `UPDATE`, `OPTIONAL`, `OLD_VERSION`, `MISCELLANEOUS`, `DELETED`, or `ARCHIVED`. |
| `--mod-name`, `--mod-category` | Mod title as plain text and Nexus mod-category ID, which MO2 maps to its local category. |
| `--description-file` | Optional UTF-8 file containing the selected upload's plain-text description for the Downloads tooltip. |
| `--expected-size` | Optional expected archive size in bytes; a mismatch prevents sidecar creation. |

Creation fails if the sidecar exists, preserving MO2's download state, including its Installed/Uninstalled flags.

## Connect

Start the target MO2 instance with `$profile` selected if it is not already running.
Check the connection with the bundled client:

```powershell
$client = (Resolve-Path -LiteralPath 'plugins\mo2_install_mod\client.py').Path
python -B $client --instance $base status
```

`ready` confirms that the running plugin matches this package.
Check the reported profile and paths, and resolve any active installation before starting another.
If the check fails, follow [plugin setup](references/setup.md).

## Install

Leave MO2 idle for the installation.
Use `--name` for a new mod; that name must not already exist:

```powershell
python -B $client --instance $base install $archive --profile $profile --name 'Engine Fixes VR - Part 2'
```

Use `--replace` with the exact name of an installed mod, excluding backups and separators:

```powershell
python -B $client --instance $base install $archive --profile $profile --replace 'Engine Fixes VR - Part 2'
```

Replacement removes the previous package's files and uses MO2's backup preference.
Configuration customizations belong in separate, higher-priority MO2 mods; do not preserve or restore edited package files.

Simple Installer handles Data layouts.
Root Builder's installer must be enabled for game-root packages: root files go under `<mods>/<name>/Root/`, while Data contents such as `Scripts/` or `SKSE/` remain outside `Root/`.
Root Builder's build/clear functionality handles game-directory deployment.

## Read the result

The client returns JSON with these exit codes:

| Exit | Result |
| --- | --- |
| 0 | `complete` or `ready`. |
| 1 | `failed` or `cancelled`. |
| 2 | `needs_input`: inspect the error or dialog; leave unfamiliar installer choices to the user. |
| 3 | Installation is ongoing or completion is unknown. |

For standard message boxes, `dialog` includes `text`, `informative_text`, and `buttons` in addition to its title and class.

`complete` confirms the installed source, version, archive association, and download Installed flag after refresh.
New mods are installed disabled; activation is separate.
Replacement also verifies unchanged enabled state and priority.

`install` waits up to 120 seconds by default; `--wait 0` returns after acceptance.
A timeout, Ctrl+C, or connection failure does not cancel installation.
Use the returned operation and session IDs to query or cancel it rather than repeating `install`:

```powershell
python -B $client --instance $base --session '<session-id>' status '<operation-id>'
python -B $client --instance $base --session '<session-id>' cancel '<operation-id>'
```

`cancel_requested=true` acknowledges the request; `cancelled` confirms it has finished.
Failure or cancellation does not roll back changes already made to the mod.
