# Plugin Setup

The plugin package belongs at `<MO2>/plugins/mo2_install_mod/`.

The controller requires MO2 2.5.2, its bundled Python support, and Qt 6.7.1.
Game-root packages need Root Builder's installer enabled (tested with 5.1.1).

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

Start MO2 with the intended profile, enable **Install Mod** if necessary, and rerun `status` using this skill's bundled client.
