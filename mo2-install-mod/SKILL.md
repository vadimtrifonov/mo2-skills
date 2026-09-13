---
name: mo2-install-mod
description: Install and replace Nexus and custom Skyrim archives in MO2 through Simple Installer, Root Builder, or interactive FOMOD Plus choices.
compatibility: MO2 2.5.2.
---

# Install Mods in MO2

Run the commands below from this skill's directory.

## Instance paths

Read the instance's `ModOrganizer.ini` to resolve its paths.
Under `[Settings]`, `base_directory` defaults to the directory containing that INI.
`download_directory` defaults to `%BASE_DIR%/downloads`; `%BASE_DIR%` refers to the resolved base directory.
Use `$base` for the resolved base directory, `$archive` for the archive's absolute path, and `$profile` for the intended profile name.

## Archive and metadata

Use a completed ZIP, 7z, or RAR archive with a Simple Installer, Root Builder, or XML FOMOD layout.
Legacy scripted FOMOD installers are unsupported.

### Nexus archives

`$archive` must be directly in the resolved Downloads directory.
Reuse existing MO2 download metadata in `<archive>.meta` if it identifies the intended Nexus upload and file version.
If no sidecar exists, create a UTF-8 `<archive>.meta` with MO2 closed using the selected upload's information:

```ini
[General]
gameName=SkyrimSE
modID=62089
fileID=800142
repository=Nexus
name=Part 2 Engine Fixes VR v1.26
version=v1.26a
fileCategory=1
modName=Engine Fixes VR
category=108
installed=false
uninstalled=false
```

Preserve the field capitalization shown; write `gameName`, `modID`, and `fileID` without surrounding quotes.

| Field | Meaning |
| --- | --- |
| `gameName` | Source catalog, not the managed game: `SkyrimSE` for `skyrimspecialedition`, including VR uploads. |
| `modID`, `fileID` | Positive Nexus mod and upload IDs within that catalog. |
| `name`, `version` | Selected upload's display name and version—not the archive filename or the mod page's latest version. |
| `fileCategory` | Nexus upload category: MAIN `1`, UPDATE `2`, OPTIONAL `3`, OLD_VERSION `4`, MISCELLANEOUS `5`, DELETED `6`, ARCHIVED `7`. |
| `modName`, `category` | Mod title as plain text and Nexus mod-category ID, which MO2 maps to its local category. |

Qt INI string values need `\\` for a literal backslash and `\"` for an embedded quote.
Enclose values containing commas or semicolons, or leading/trailing whitespace, in double quotes.
Escape a literal initial `@` as `@@`, and line breaks and tabs as `\n`, `\r`, and `\t`.
Optional `description` supplies the Downloads tooltip: HTML-escape the selected upload's plain text, replace line breaks with `<br />` followed by a newline, then apply Qt INI string encoding.
The false installation flags above are for new sidecars; preserve existing sidecars' stored fields and download state.

### Custom archives

`--custom` reads package metadata from a UTF-8 `meta.ini` at the archive root:

```ini
[General]
version=1.0.0
gameName=SkyrimSE
```

`version` is the package's declared release version and is required.
Optional `gameName` is MO2's game short name, matched case-insensitively; if omitted, the target instance's game is used.
`SkyrimSE` can identify an SE package installed into Skyrim VR.
Only these two fields are imported from the package's `meta.ini`.

## Connect

Start the target MO2 instance with `$profile` selected if it is not already running.
Check the connection with the bundled client:

```powershell
$client = (Resolve-Path -LiteralPath 'plugins\mo2_install_mod\client.py').Path
mise exec -- python -B $client --instance $base status
```

`ready` confirms that the running plugin's version matches this client's.
Check the reported profile and paths, and resolve any active installation before starting another.
If the check fails, follow [setup](references/setup.md).

## Install

`--profile` must match MO2's active profile; it does not switch profiles.
Use `--name` for a new mod; that name must not already exist:

```powershell
mise exec -- python -B $client --instance $base install $archive --profile $profile --name 'Engine Fixes VR - Part 2'
```

Use `--replace` with the exact name of an installed mod, excluding backups and separators:

```powershell
mise exec -- python -B $client --instance $base install $archive --profile $profile --replace 'Engine Fixes VR - Part 2'
```

For custom archives, add `--custom` to either operation:

```powershell
mise exec -- python -B $client --instance $base install $archive --custom --profile $profile --name 'Azurite Weathers - HDR'
```

Replacement removes the previous package's files from the shared mod directory, affecting every profile using that mod.
MO2's backup preference applies.
Configuration customizations to Nexus packages belong in separate, higher-priority MO2 mods; do not preserve or restore edited Nexus package files.

Simple Installer handles Data layouts.
Root Builder places game-root files under `<mods>/<name>/Root/`, while Data contents such as `Scripts/` or `SKSE/` remain outside `Root/`.

## Read the result

The client returns JSON with these exit codes:

| Exit | Result |
| --- | --- |
| 0 | `complete` or `ready`. |
| 1 | `failed` or `cancelled`. |
| 2 | `needs_input`: answer FOMOD choices, or leave the reported error/dialog to the user. |
| 3 | Installation is ongoing or completion is unknown. |

`complete` confirms the installed game, version, and archive association after refresh.
Versions use MO2's canonical formatting; for example, `1.0.0` is reported as `1.0.0.0`.
Nexus installations also verify the upload association and download Installed flag; custom mods are recorded without Nexus identities or upload associations.
New mods are installed disabled; activation is separate.
Replacement also verifies unchanged enabled state and priority in the active profile.

Use the returned `operation` and `session` IDs as `$operation` and `$session` for follow-up commands.
To query status:

```powershell
mise exec -- python -B $client --instance $base --session $session status $operation
```

For standard message boxes, `dialog` includes `text`, `informative_text`, and `buttons` in addition to its title and class.
After the user handles a reported error or dialog in MO2, query the same operation for its outcome.

To request cancellation:

```powershell
mise exec -- python -B $client --instance $base --session $session cancel $operation
```

`install` waits up to 120 seconds by default; `--wait 0` returns after acceptance.
`respond` uses the same wait limit, but its initial reply follows the applied answer.
A timeout, Ctrl+C, or connection failure does not cancel installation; query the same operation rather than repeating `install`.
`cancel_requested=true` acknowledges the request; `cancelled` confirms the installation has stopped.
Failure or cancellation does not roll back changes already made to the mod.

## FOMOD choices

FOMOD Plus evaluates dependencies and enforces choice constraints.
The client reads and operates its wizard without automatically advancing or installing.

`needs_input` with `dialog.kind="fomod-plus"` exposes the current step, group IDs, selection counts, available `actions`, and a `view` token.
Selections can be native defaults or restored choices.
List a group's choices, then retrieve descriptions as needed:

```powershell
mise exec -- python -B $client --instance $base --session $session status $operation --group '0.1'
mise exec -- python -B $client --instance $base --session $session status $operation --option '0.1.0'
```

Choices include their IDs, names, selected/enabled state, and radio/checkbox control type.
Respond using the current `dialog.view` token:

```powershell
mise exec -- python -B $client --instance $base --session $session respond $operation `
    --view '<view-token>' --select '0.1.0' --action next
```

Repeat `--select` or `--deselect` for multiple choices; unspecified options stay unchanged.
For radio groups, select another option, including `None` when offered, rather than deselecting the current one.
`--action` defaults to `stay`; `back`, `next`, and `install` are available only when reported by the wizard.

Read the returned state after each answer and use its current IDs and token.
`STALE_VIEW` rejects an answer when the observed step or selections have changed, including selections on earlier pages.
A failed response does not itself fail the installation; query its status before answering again.
For `CHOICE_REJECTED`, some selections may have changed, but navigation was not performed.

## References

- [MO2 2.5.2 download metadata](https://github.com/ModOrganizer2/modorganizer/blob/v2.5.2/src/downloadmanager.cpp)
- [Qt 6.7 INI format](https://doc.qt.io/archives/qt-6.7/qsettings.html#Format-enum)
