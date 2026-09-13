---
name: mo2-install-plugin
description: Install and update native DLL and Python plugins loaded by MO2 itself.
compatibility: MO2 2.5.2.
---

# Install MO2 Plugins

## Application and instance paths

`$mo2` is the absolute path to the directory containing the target `ModOrganizer.exe`.
`$ini` is the selected instance's `ModOrganizer.ini`, which can be outside `$mo2`.
Read `[Settings]` in `$ini` to resolve `$base` from `base_directory`, which defaults to the INI's directory.
`download_directory` defaults to `%BASE_DIR%/downloads`; `%BASE_DIR%` is MO2's substitution for `$base`, not an environment variable.

Plugin files belong to the MO2 application, not the instance's base directory or a profile.
They are shared by every instance using that application directory; MO2's enablement and plugin options are stored in each instance's INI.

## Plugin files

### Layout and compatibility

Choose the MO2 2.5.2 build when a release offers builds for different MO2 versions.
Python plugins require an enabled Python Proxy and use MO2's bundled Python and PyQt environment, not the system Python environment.
Close MO2 instances using that application directory before replacing plugin files.

| Release layout | Destination |
| --- | --- |
| `plugins/...` and sibling directories such as `translations/...` (FOMOD Plus) | Corresponding directories under `$mo2` |
| Python package with root `__init__.py` (Root Builder's `rootbuilder/`) | `$mo2/plugins/<package>/` |
| Native plugin directory containing a Qt plugin DLL and accompanying files | `$mo2/plugins/<package>/` |
| Standalone plugin `.py` or DLL with resources (Changelog Helper's `changeloggen.py`) | `$mo2/plugins/`, preserving relative layout |

Remove archive-only wrappers, not actual plugin-package directories.
Locally authored plugins use the same layouts with deployable files; a download archive is not required.

MO2 discovers and registers plugins from direct entries under `plugins/` at startup.
Inside a native plugin directory, MO2 selects only the first Qt plugin DLL found.
A plugin package can register several plugins.
An `IPluginTool` plugin provides a Tools-menu action without an external executable entry.

### Updates

Replace the distribution's files and remove its obsolete files; retain or migrate settings, persistent data, separately installed additions, and intentional local customizations.
Compare installed files with the old and new distributions to identify obsolete package files and local changes; follow any release-specific migration instructions.
Within shared directories, replace only the package's files, not the entire directory.
Keep old plugin copies outside `plugins/`; renamed package directories or DLL copies can still be discovered and collide with the replacement's registered names.

Root Builder's code is in `plugins/rootbuilder/`, while its game-file backups and cache are in `plugins/data/RootBuilder/`.
However, `plugins/data/` is not exclusively runtime data: NIF Preview ships resources under `plugins/data/shaders/` that belong to its distribution.
When comparing installed files against archives, do so before starting MO2; plugin initialization can rewrite settings or create data.

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

Edit `ModOrganizer.ini` and download `.meta` sidecars as UTF-8 with MO2 closed; MO2 caches these files.
Change only the intended keys; preserve unrelated entries, groups, and opaque Qt values such as `@ByteArray(...)` and `@Variant(...)`.
Spaces in keys are encoded as `%20`; a literal backslash separates a plugin name from its setting.
Backslashes in string values need `\\`, and embedded quotes need `\"`.
Values containing commas or semicolons, or leading/trailing whitespace, need surrounding quotes.
A literal initial `@` needs `@@`.

## Enablement and settings

Preserve existing enablement and options on updates.
MO2 stores an explicit enablement choice under `[PluginPersistance]` in the selected instance's INI:

```ini
[PluginPersistance]
FOMOD%20Plus\enabled=true
```

The key uses the plugin's registered `name()`, not necessarily its filename, package title, or localized display name.
Without an explicit choice, MO2 uses `enabledByDefault()`.
Child plugins follow their master; unmet requirements can still prevent enablement.
Game-support plugins (`IPluginGame`) ignore this setting; only the instance's managed-game plugin is enabled.

Plugin options use the same registered name under `[Plugins]` and are separate from enablement.
For example, Root Builder 5.1.1 needs `RootBuilder\installer=true` there to install root mods; this option defaults to false even when Root Builder is enabled.
`[pluginBlacklist]` lists native DLL filenames or immediate plugin-directory names, not registered names, in numbered `1\name`, `2\name`, … entries; `size` is the entry count.
Changing enablement does not remove a blacklist entry.

### Loading

On the next MO2 start, check the registered plugins under **Settings → Plugins**; not every plugin adds a menu item.
Startup/load errors appear in `logs/mo_interface.log` under the directory containing `$ini`, not necessarily under `$base`.
Loading does not require running actions such as Root Builder Build, FOMOD Scan, or Sync Plugins, which can change game files or mod/profile data.

## Download archives

### Retention and installation state

Keep original release archives in the resolved Downloads directory with sidecars named `<archive.ext>.meta`.
Repacking changes archive bytes even when the extracted files are identical.
After installing an archive's intended contents, set `installed=true` and `uninstalled=false` under `[General]` in its sidecar.
New sidecars for downloads not yet installed use `installed=false` and `uninstalled=false`.
Each archive has its own metadata and state; these flags are Downloads bookkeeping, not installation reference counts or confirmation that a plugin loaded.

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
Nexus `name` and `version` come from the selected upload, not the archive filename, mod page's latest version, or the version reported by the plugin itself.

`directURL`, `manualURL`, and `prompt` follow Wabbajack's download-source metadata format; they are not MO2 download settings.
For non-Nexus downloads, clear `gameName`, `modID`, `fileID`, and `repository` rather than retaining a stale Nexus identity.
A direct URL must reproduce the retained archive bytes; moving branches and `latest` URLs can later serve a different release.
For Discord, use a message link rather than an expiring attachment CDN URL.
MO2's download/resume `url` field is not a substitute for these source fields.
Apply [INI string quoting](#ini-editing) to URLs when necessary, including those containing commas or semicolons.

## References

- [MO2 2.5.2 plugin discovery and enablement](https://github.com/ModOrganizer2/modorganizer/blob/v2.5.2/src/plugincontainer.cpp)
- [MO2 2.5.2 paths and plugin settings](https://github.com/ModOrganizer2/modorganizer/blob/v2.5.2/src/settings.cpp)
- [MO2 Python plugin discovery](https://github.com/ModOrganizer2/modorganizer-plugin_python/blob/ebede37865f252ead84ffbcc8a0d6a3063a5da6b/src/proxy/proxypython.cpp)
- [Root Builder installation and data](https://github.com/Kezyma/ModOrganizer-Plugins/blob/main/docs/rootbuilder.md)
- [MO2 2.5.2 download metadata](https://github.com/ModOrganizer2/modorganizer/blob/v2.5.2/src/downloadmanager.cpp)
- [Qt 6.7 INI format](https://doc.qt.io/archives/qt-6.7/qsettings.html#Format-enum)
- [Wabbajack download-source metadata format](https://wiki.wabbajack.org/modlist_author_documentation/Meta%20Files.html)
