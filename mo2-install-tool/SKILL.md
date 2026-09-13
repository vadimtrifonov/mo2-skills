---
name: mo2-install-tool
description: Install and update portable tools used with MO2.
compatibility: MO2 2.5.2.
---

# Install MO2 Tools

## Instance paths

`$ini` is the absolute path to the selected instance's `ModOrganizer.ini`, which can be outside the MO2 application directory.
Read `[Settings]` in `$ini` to resolve `$base` from `base_directory`, which defaults to the INI's directory.
`download_directory`, `profiles_directory`, and `mod_directory` default to `%BASE_DIR%/downloads`, `%BASE_DIR%/profiles`, and `%BASE_DIR%/mods` respectively.
`%BASE_DIR%` is MO2's substitution for `$base`, not an environment variable.

## Tool files

### Layout and updates

For new instance-local tools, use `$base/tools/<tool>/`; keep the existing location on updates.
Omit an enclosing archive wrapper but retain the distribution's relative layout.
Close the affected tool before replacing its files.

Replace the distribution's files and remove its obsolete files; retain or migrate settings and separately installed additions.
Use release instructions and old/new archives to determine which files fall into each category.
Portable distributions can still require external runtimes and store settings outside their directory.

Bethini Pie's base archive includes bundled application definitions under `apps/`.
The separate Skyrim VR add-on supplies `apps/Skyrim VR (infernalryan)/`, while the generated `Bethini.ini` stores preferences, game paths, and the selected definition.
Preserving all of `apps/` during a base update can retain outdated bundled definitions.

When comparing installed package files against their archives, do so before launching the tool; startup may rewrite configuration.

### Archive inspection

Use `$archive` for the archive's absolute path and `$stage` for a fresh directory under user Temp.
For 7z archives, inspect and extract with Windows `tar.exe`:

```powershell
tar.exe -tf $archive
tar.exe -xf $archive -C $stage
```

Use PowerShell's `Expand-Archive` for ZIP files; Windows bsdtar 3.8.8 can alter Unicode ZIP filenames.

```powershell
Expand-Archive -LiteralPath $archive -DestinationPath $stage
```

## INI editing

Edit `ModOrganizer.ini`, profile `settings.ini`, and download `.meta` sidecars as UTF-8.
Close MO2 before editing these files, which it caches while running.
Change only the intended keys; preserve unrelated entries, groups, and opaque Qt values such as `@ByteArray(...)` and `@Variant(...)`.

Array keys use a literal backslash, while backslashes in string values need `\\`.
Forward slashes in paths avoid that escaping.
Embedded quotes need `\"`; values containing commas or semicolons, or leading/trailing whitespace, need surrounding quotes.
A literal initial `@` needs `@@`.
For example, a quoted input path in an argument string is stored as:

```ini
1\arguments="\"C:/input path/file,a.esp\" --flag"
```

## Executable entries

Read `[customExecutables]` in `$ini` and locate an existing entry by its exact title.
Preserve existing titles and other launch modes.
For a new title, append at `size + 1` and increment `size` without renumbering existing entries.

Example of a single-entry array:

```ini
[customExecutables]
1\title=xEdit
1\binary=C:/instance/tools/xEdit/xTESEdit.exe
1\arguments=-TES5VR
1\workingDirectory=
size=1
```

Use absolute binary paths and set the working directory according to the tool's requirements.
An empty `workingDirectory` uses the executable's containing directory.
Register the required launch modes, not every shipped executable.
xEdit's normal and Quick Auto Clean entries share a binary but use `-TES5VR` and `-TES5VR -quickautoclean` respectively.
If paths change, update every affected entry; a tool update does not otherwise require rewriting its launchers.
Use ASCII titles for MO2 2.5.2 command-line launches with `run -e`.

### Profile output routing

**Create files in mod instead of overwrite** is stored in the selected profile's `settings.ini`, keyed by executable title with spaces encoded as `%20`:

```ini
[custom_overwrites]
xEdit%20-%20Quick%20Auto%20Clean=xEdit Output
```

The output mod must exist and be enabled in that profile when the tool runs, or MO2 refuses the launch.
This redirects new files that would otherwise enter Overwrite, not edits to existing files or files sent to an explicit output directory.
An empty value selects Overwrite again.

## Download archives

### Retention and installation state

Keep original release archives in the resolved Downloads directory with sidecars named `<archive.ext>.meta`.
Repacking changes archive bytes even when the extracted files are identical.

After installing an archive's intended contents, set `installed=true` and `uninstalled=false` under `[General]` in its sidecar.
New sidecars for downloads not yet installed use `installed=false` and `uninstalled=false`.
Each archive has its own metadata and installation state, even when a tool is assembled from several archives.
These flags are Downloads bookkeeping, not installation reference counts.

### Source metadata

Reuse matching sidecars; when correcting source fields, preserve their download state.
All fields below belong under `[General]`.
Record one source and preserve the field capitalization shown:

| Source | Fields |
| --- | --- |
| Nexus | `gameName`, `modID`, `fileID`, `repository=Nexus`, plus the selected upload's `name` and `version` |
| Public release archive | `directURL` pointing to the archive |
| Manual download | `manualURL` pointing to a page or message, and `prompt` with download and access instructions |

`gameName` identifies the source catalog, not the managed game: use `site` for Nexus Modding Tools and `SkyrimSE` for `skyrimspecialedition`, including VR uploads.
Write `gameName`, `modID`, and `fileID` values without surrounding quotes.
Nexus `name` and `version` come from the selected upload, not the archive filename, mod page's latest version, or executable's Windows version properties.

`directURL`, `manualURL`, and `prompt` follow Wabbajack's download-source metadata format; they are not MO2 download settings.
For non-Nexus downloads, clear `gameName`, `modID`, `fileID`, and `repository` rather than retaining a stale Nexus identity.

A direct URL must reproduce the retained archive bytes; moving branches and `latest` URLs can later serve a different release.
For Discord, use a message link rather than an expiring attachment CDN URL.
MO2's download/resume `url` field is not a substitute for these source fields.
Apply [Qt string quoting](#ini-editing) to URLs when necessary, including those containing commas or semicolons:

```ini
directURL="https://example.test/tool;release.zip"
```

## References

- [MO2 2.5.2 download metadata](https://github.com/ModOrganizer2/modorganizer/blob/v2.5.2/src/downloadmanager.cpp)
- [MO2 2.5.2 executable fields](https://github.com/ModOrganizer2/modorganizer/blob/v2.5.2/src/executableslist.cpp)
- [MO2 2.5.2 launcher defaults](https://github.com/ModOrganizer2/modorganizer/blob/v2.5.2/src/processrunner.cpp)
- [MO2 2.5.2 output routing](https://github.com/ModOrganizer2/modorganizer/blob/v2.5.2/src/organizercore.cpp)
- [Qt 6.7 INI format](https://doc.qt.io/archives/qt-6.7/qsettings.html#Format-enum)
- [Wabbajack download-source metadata format](https://wiki.wabbajack.org/modlist_author_documentation/Meta%20Files.html)
