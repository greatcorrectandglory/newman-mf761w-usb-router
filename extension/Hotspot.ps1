[CmdletBinding()]
param(
    [ValidateSet('Install','Upgrade','Status','Doctor','Start','Stop','Rollback','Uninstall','Watch')]
    [string]$Action='Status',
    [string]$Adb, [string]$Xray, [string]$Serial, [string]$NodesFile, [string]$Python
)
$ErrorActionPreference='Stop'
$base=Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'NewmanHotspot'
$versions=Join-Path $base 'versions'
$current=Join-Path $base 'current'
$settings=Join-Path $base 'settings.json'
$enabled=Join-Path $base 'enabled'
$last=Join-Path $base 'previous'
$priorConfig=Join-Path $base 'previous-settings.json'
$journal=Join-Path $base 'transaction.json'
$taskName='MF761W Hotspot Service'
$engine=Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$userSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$taskName+=' '+$userSid
$entry=Join-Path $base 'service.ps1'
$cmdargs='-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy RemoteSigned -File "'+$entry+'" -Action Watch'
function Read-Pointer([string]$path) {
    if(!(Test-Path -LiteralPath $path -PathType Leaf)){return ''}
    $value=[IO.File]::ReadAllText($path).Trim()
    if($value -notmatch '^v\d+\.\d+\.\d+-[0-9a-f]{12}$'){throw 'Invalid installed version pointer.'}
    return $value
}
function Write-Atomic([string]$path,[string]$content) {
    $tmp=$path+'.new'
    [IO.File]::WriteAllText($tmp,$content,(New-Object Text.UTF8Encoding($false)))
    if([IO.File]::Exists($path)){[IO.File]::Replace($tmp,$path,($path+'.bak'));[IO.File]::Delete(($path+'.bak'))}
    else{[IO.File]::Move($tmp,$path)}
}
function App-Path([string]$name) {
    if($name -notmatch '^v\d+\.\d+\.\d+-[0-9a-f]{12}$'){throw 'No installed release.'}
    return (Join-Path $versions $name)
}
function Prune-OldVersions {
    $keep=@((Read-Pointer $current),(Read-Pointer $last))
    $rootPath=[IO.Path]::GetFullPath($versions)
    if((Get-Item -LiteralPath $versions).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Unexpected versions directory.'}
    foreach($folder in (Get-ChildItem -LiteralPath $versions -Directory)){
        if($folder.Name -notmatch '^v\d+\.\d+\.\d+-[0-9a-f]{12}$' -or $folder.Name -in $keep){continue}
        if($folder.Attributes -band [IO.FileAttributes]::ReparsePoint){continue}
        $resolved=(Resolve-Path -LiteralPath $folder.FullName).Path
        if([IO.Path]::GetDirectoryName($resolved) -ne $rootPath){throw 'Unexpected release directory.'}
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
function Python-Path([string]$hint) {
    if($hint){$exe=(Resolve-Path -LiteralPath $hint).Path}
    elseif(Test-Path -LiteralPath (Join-Path $base 'python.path')){$exe=[IO.File]::ReadAllText((Join-Path $base 'python.path')).Trim()}
    else{$exe=(Get-Command python.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source}
    $out=& $exe -c 'import sys; print(sys.executable); sys.exit(0 if sys.version_info >= (3,8) and sys.maxsize>2**32 else 1)'
    if($LASTEXITCODE -ne 0 -or !$out){throw 'Python 3.8 or newer for Windows x64 is required.'}
    return ([string]($out | Select-Object -First 1)).Trim()
}
function Run-Host([string]$name,[string]$command,[string[]]$extra) {
    $dir=App-Path $name
    & $script:pythonExe (Join-Path $dir 'host\service.py') $command @extra
    if($LASTEXITCODE -ne 0){throw "Hotspot command '$command' did not finish."}
}
function Watch-Task {
    $t=Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if($t -and ($t.Actions.Count -ne 1 -or $t.Actions[0].Arguments -ne $cmdargs -or $t.Actions[0].Execute -ne $engine)){
        throw 'A different application owns the scheduled task name.'
    }
    return $t
}
function Install-Task {
    $null=Watch-Task
    $who=[Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal=New-ScheduledTaskPrincipal -UserId $who -LogonType Interactive -RunLevel Limited
    $logon=New-ScheduledTaskTrigger -AtLogOn -User $who
    $timer=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
    $options=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    $act=New-ScheduledTaskAction -Execute $engine -Argument $cmdargs -WorkingDirectory $base
    $null=Register-ScheduledTask -TaskName $taskName -Action $act -Trigger @($logon,$timer) -Principal $principal -Settings $options -Force
}
function Wake-Task {
    $t=Watch-Task
    if(!$t){Install-Task}
    Start-ScheduledTask -TaskName $taskName
}
function File-Digest([string]$path) {
    $algorithm=[Security.Cryptography.SHA256]::Create()
    $stream=[IO.File]::OpenRead($path)
    try{return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-','').ToLowerInvariant()}
    finally{$stream.Dispose();$algorithm.Dispose()}
}
function Check-Bundle([string]$dir) {
    $file=Join-Path $dir 'MANIFEST.json'
    $manifest=Get-Content -Raw -LiteralPath $file | ConvertFrom-Json
    $version=([IO.File]::ReadAllText((Join-Path $dir 'VERSION'))).Trim()
    if($version -notmatch '^\d+\.\d+\.\d+$' -or $version -ne $manifest.version -or $manifest.format -ne 1){throw 'Invalid package version.'}
    $map=@{}
    foreach($item in $manifest.files){
        $rel=[string]$item.path
        if($rel -notmatch '^[A-Za-z0-9._/-]+$' -or $rel -match '(^/|//|(^|/)\.\.?(/|$))' -or $map.ContainsKey($rel)){throw 'Invalid package file path.'}
        $map[$rel]=$true
        $path=Join-Path $dir $rel
        if((File-Digest $path) -ne $item.sha256){throw "Package checksum failed for $rel."}
    }
    foreach($rel in 'VERSION','Hotspot.ps1','host/service.py','host/device.py','host/profiles.py','host/worker_job.py','gateway.exe','device/relay','device/ap.sh','device/dhcp.conf','device/filter.v4','device/filter.v6'){
        if(!$map.ContainsKey($rel)){throw "Missing required package file $rel."}
    }
    $hash=(File-Digest $file).Substring(0,12)
    return [pscustomobject]@{Name=('v'+$version+'-'+$hash);Manifest=$manifest;Version=$version}
}
function Stage-Version {
    $package=Check-Bundle $PSScriptRoot
    $dst=App-Path $package.Name
    if(!(Test-Path -LiteralPath $dst)){
        $null=[IO.Directory]::CreateDirectory($dst)
    }
    foreach($item in $package.Manifest.files){
        $target=Join-Path $dst $item.path
        $null=[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target))
        if(!(Test-Path -LiteralPath $target) -or (File-Digest $target) -ne $item.sha256){
            Copy-Item -LiteralPath (Join-Path $PSScriptRoot $item.path) -Destination $target -Force
        }
    }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'MANIFEST.json') -Destination (Join-Path $dst 'MANIFEST.json') -Force
    $verified=Check-Bundle $dst
    if($verified.Name -ne $package.Name){throw 'Installed package did not match its source.'}
    return $package.Name
}
function Recover-Change {
    $s=Get-Content -Raw -LiteralPath $journal | ConvertFrom-Json
    $old=[string]$s.old
    $target=[string]$s.target
    if(Test-Path -LiteralPath $enabled){[IO.File]::Delete($enabled)}
    if($old){
        Run-Host $target 'stop'
        Copy-Item -LiteralPath (Join-Path $base 'transaction-settings.json') -Destination $settings -Force
        Run-Host $old 'rollback-device'
        Write-Atomic $current $old
        if($s.wasEnabled){Run-Host $old 'start';Wake-Task}
    } else {
        if($s.deviceChanged -or (Test-Path -LiteralPath $settings) -or (Test-Path -LiteralPath (Join-Path $base 'pending-settings.json'))){Run-Host $target 'remove'}
        foreach($p in $settings,$current){if(Test-Path -LiteralPath $p){[IO.File]::Delete($p)}}
        $task=Watch-Task
        if($task){Unregister-ScheduledTask -TaskName $taskName -Confirm:$false}
    }
    [IO.File]::Delete($journal)
    [IO.File]::Delete((Join-Path $base 'pending-settings.json'))
    [IO.File]::Delete((Join-Path $base 'transaction-settings.json'))
}
if($Action -eq 'Watch'){
    if(!(Test-Path -LiteralPath $current)){return}
    $script:pythonExe=Python-Path ''
    while($true){
        if((Test-Path -LiteralPath $enabled) -and !(Test-Path -LiteralPath $journal)){
            try{Run-Host (Read-Pointer $current) 'run'}catch{Start-Sleep -Seconds 2}
        }
        Start-Sleep -Seconds 3
    }
}
$guard=New-Object Threading.Mutex($false,('Local\MF761WHotspotInstall-'+$userSid))
$owned=$false
try{
    try{$owned=$guard.WaitOne(15000)}catch [Threading.AbandonedMutexException]{$owned=$true}
    if(!$owned){throw 'Another hotspot management command is in progress.'}
    $installed=Read-Pointer $current
    if(!$installed -and !(Test-Path -LiteralPath $journal) -and $Action -eq 'Status'){Write-Output '{"phase":"not-installed"}';return}
    if($Action -eq 'Uninstall' -and !$installed -and !(Test-Path -LiteralPath $journal)){Write-Output 'Hotspot service is not installed.';return}
    $script:pythonExe=Python-Path $Python
    if(Test-Path -LiteralPath $journal){Recover-Change;$installed=Read-Pointer $current}
    if($Action -in @('Status','Doctor','Start','Stop')){
        Run-Host $installed ($Action.ToLowerInvariant())
        if($Action -eq 'Start'){Wake-Task}
        return
    }
    if($Action -eq 'Uninstall'){
        Run-Host $installed 'remove'
        $task=Watch-Task
        if($task){
            Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        }
        foreach($p in 'enabled','settings.json','previous-settings.json','pending-settings.json','state.json','traffic.json','previous','current','python.path','events.log','events.previous.log','transaction-settings.json','service.ps1'){
            [IO.File]::Delete((Join-Path $base $p))
        }
        if(Test-Path -LiteralPath $versions){
            $resolved=(Resolve-Path -LiteralPath $versions).Path
            $expected=[IO.Path]::GetFullPath((Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'NewmanHotspot\versions'))
            if($resolved -ne $expected -or ((Get-Item -LiteralPath $versions).Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Unexpected install directory.'}
            Remove-Item -LiteralPath $resolved -Recurse -Force
        }
        Write-Output 'Device settings restored and hotspot service uninstalled.'
        return
    }
    if($Action -eq 'Upgrade' -and !$installed){throw 'Install the service first.'}
    $null=[IO.Directory]::CreateDirectory($base)
    $null=Watch-Task
    if($Action -eq 'Rollback'){
        $target=Read-Pointer $last
        if(!$target -or !(Test-Path -LiteralPath $priorConfig)){throw 'No previous installed release.'}
        $null=Check-Bundle (App-Path $target)
    }else{
        $target=Stage-Version
    }
    Write-Atomic (Join-Path $base 'python.path') $script:pythonExe
    if($installed){Copy-Item -LiteralPath $settings -Destination (Join-Path $base 'transaction-settings.json') -Force}
    $operation=@{old=$installed;target=$target;wasEnabled=(Test-Path -LiteralPath $enabled);deviceChanged=$false}
    Write-Atomic $journal ($operation | ConvertTo-Json -Compress)
    try{
        if($installed){Run-Host $installed 'stop'}
        if($Action -eq 'Rollback'){
            Copy-Item -LiteralPath $priorConfig -Destination $settings -Force
            Run-Host $target 'rollback-device'
        }else{
            $extra=@()
            foreach($option in @(@('--adb',$Adb),@('--xray',$Xray),@('--serial',$Serial),@('--nodes',$NodesFile))){
                if($option[1]){$extra+=@($option[0],$option[1])}
            }
            Run-Host $target 'install' $extra
        }
        $operation.deviceChanged=$true
        Write-Atomic $journal ($operation | ConvertTo-Json -Compress)
        Copy-Item -LiteralPath (Join-Path (App-Path $target) 'Hotspot.ps1') -Destination $entry -Force
        Install-Task
        Write-Atomic $current $target
        if($installed -and $installed -ne $target){
            Copy-Item -LiteralPath (Join-Path $base 'transaction-settings.json') -Destination $priorConfig -Force
            Write-Atomic $last $installed
        }
        if(!$installed -or $operation.wasEnabled){Run-Host $target 'start';Wake-Task}
        [IO.File]::Delete($journal)
        [IO.File]::Delete((Join-Path $base 'transaction-settings.json'))
        try{Prune-OldVersions}catch{Write-Warning 'A previous release is still in use and can be removed on the next upgrade.'}
        Write-Output ("Installed "+$target+". Use Status or Doctor to inspect the service.")
    }catch{
        $problem=$_.Exception.Message
        Recover-Change
        throw $problem
    }
} finally {if($owned){$guard.ReleaseMutex()};$guard.Dispose()}
