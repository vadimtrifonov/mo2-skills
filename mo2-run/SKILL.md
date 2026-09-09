---
name: mo2-run
description: Run a configured executable through a Mod Organizer 2 profile. Use when a process must run inside MO2's virtual filesystem, including Skyrim launched in a SteamVR session.
---

# MO2 Run

`run -e <title>` uses the executable settings saved in MO2, including its arguments and working directory.

## Inputs

Identify:

- the portable MO2 root containing `ModOrganizer.exe`;
- the profile name (profiles are stored in `<MO2 root>\profiles` by default);
- the exact configured-executable title in MO2;
- the environment variables that the configured executable must inherit.

Configured executables are stored under `[customExecutables]` in `ModOrganizer.ini`.

### Root Builder

For `run -e`, configure the executable at its deployed game path.
Mod-folder redirection can report “Failed to run” after a successful run and cleanup.
Root Builder supplies the deployed binary during its pre-launch build; it need not exist beforehand.

## Require a fresh MO2 process

MO2 can forward launches to an existing instance, which keeps its own profile and environment.
`-p` validates that instance's profile rather than switching it.
`--multiple` is unsupported.

Check before launch:

```powershell
Get-CimInstance Win32_Process -Filter "Name = 'ModOrganizer.exe'" -ErrorAction Stop |
  Select-Object ProcessId, ExecutablePath, CommandLine
```

Continue only if the query succeeds and returns no processes.
Do not terminate an existing process automatically; have its owner close it, then check again.

Also confirm that the target application is not running, or record its existing process identities so they cannot be mistaken for this run.

## Launch

Use PowerShell 7 and `ProcessStartInfo` to pass arguments and environment properties without shell quoting.
Set `$environment` to a `PSCustomObject`; `$null` leaves the inherited environment unchanged.
For SteamVR, pass the complete `run.environment` object returned by the session helper so MO2 and its descendants inherit it.

```powershell
$mo2Root = [IO.Path]::GetFullPath('<portable MO2 root>')
$profile = '<profile>'
$executableTitle = '<configured title>'
$environment = $null

$info = [Diagnostics.ProcessStartInfo]::new()
$info.FileName = Join-Path $mo2Root 'ModOrganizer.exe'
$info.WorkingDirectory = $mo2Root
$info.UseShellExecute = $false

if ($null -ne $environment) {
    foreach ($property in $environment.PSObject.Properties) {
        $info.Environment[$property.Name] = [string] $property.Value
    }
}

foreach ($argument in @('-p', $profile, 'run', '-e', $executableTitle)) {
    [void] $info.ArgumentList.Add($argument)
}

$mo2Process = [Diagnostics.Process]::Start($info)
[pscustomobject]@{
    ProcessId = $mo2Process.Id
    StartTimeUtc = $mo2Process.StartTime.ToUniversalTime().ToString('o')
    Path = $info.FileName # Process.Path can be null immediately after launch.
}
```

Starting MO2 does not establish target readiness.
Use current-run log, observe the target process, or IPC response.
The default MO2 launch log is `<MO2 root>/logs/mo_interface.log`.

## Lifetime and cleanup

Keep the fresh MO2 process alive while the target uses its virtual filesystem.
MO2's `run -e` operation normally waits for the launched process tree and performs post-run cleanup after it exits.

The invoking task owns the target process. On completion:

1. Close the target application using its recorded identity.
2. Wait for MO2 to finish post-run cleanup, including Root Builder cleanup, and exit.
3. Stop any runtime session you started for this run, such as SteamVR.

If forced termination is necessary, verify the PID, creation time, and executable path first.
Do not terminate all processes with the same name.

Restore temporary executable-setting changes after MO2 exits.
