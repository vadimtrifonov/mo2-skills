# Setup

## Command-line Python

From this skill's directory, trust its mise configuration and install the selected Python:

```powershell
mise trust mise.toml
mise install python
```

The client and test drivers use mise-managed Python; MO2 loads plugins with its own bundled Python.

## Plugin configuration

The plugin package belongs at `<MO2>/plugins/mo2_install_mod/`.

The controller requires MO2 2.5.2 or 2.5.3 with its bundled Python support.

- Game-root packages: Root Builder with its `installer` setting enabled.
- XML FOMOD archives: FOMOD Plus enabled with `fallback_to_legacy` set to `false`.

Disabling fallback makes **Cancel** stop the installation instead of handing the archive to another installer.

## Diagnose the failed check

| Result | Next step |
| --- | --- |
| `NOT_RUNNING` or `SESSION_ENDED` | Check the resolved instance path, whether MO2 is running, and whether the package exists under the application's `plugins` directory. |
| `VERSION_MISMATCH` | Compare `expected_version` and `loaded_version`: update an older installed copy, restart if its files are already current, or update the skill if the installed version is newer. |
| `PROTOCOL_MISMATCH` | Bring the skill and installed plugin to the same version. |
| `PLUGIN_DISABLED` | Enable **Install Mod** in MO2's plugin settings. |
| `CONTROLLER_STOPPED`, `CONNECTION_ERROR`, or `REQUEST_TIMEOUT` | Inspect MO2 and its log before replacing the package. |

For a closed MO2 instance, read `VERSION` in the installed `wire.py` and the bundled `wire.py`.
If files are missing or may have been modified, compare the installed package with the bundled copy.

Startup errors appear in `logs/mo_interface.log` under the instance directory containing `ModOrganizer.ini`.
If an installed plugin does not load, check MO2's crash blacklist.

## Install or update the package

Close MO2 instances using the target application before changing its plugin files.
Resolve any active installation before closing its MO2 session.

From this skill's directory, with `$mo2` set to the application directory:

```powershell
$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath 'plugins\mo2_install_mod').Path
$exe = (Resolve-Path -LiteralPath (Join-Path $mo2 'ModOrganizer.exe')).Path
$destination = Join-Path $mo2 'plugins\mo2_install_mod'

if (Get-Process ModOrganizer -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }) {
    throw 'Close MO2 before replacing its plugin package.'
}
if (Test-Path -LiteralPath $destination) {
    Remove-Item -LiteralPath $destination -Recurse
}
Copy-Item -LiteralPath $source -Destination $destination -Recurse
```

Start MO2 with the intended profile and enable **Install Mod** if necessary.
Check the loaded plugin using the bundled client, with `$base` set to the instance's resolved base directory:

```powershell
mise exec -- python -B plugins/mo2_install_mod/client.py --instance $base status
```
