param([switch]$Restore)
$ErrorActionPreference = 'Stop'
$local = Join-Path (Split-Path $PSScriptRoot -Parent) '.local'
[IO.Directory]::CreateDirectory($local) | Out-Null
$statePath = Join-Path $local 'windows-sharing-before.json'
$resultPath = Join-Path $local 'windows-sharing-result.json'
$manager = New-Object -ComObject HNetCfg.HNetShare
function Get-Connections {
    foreach ($connection in $manager.EnumEveryConnection()) {
        $properties = $manager.NetConnectionProps($connection)
        $configuration = $manager.INetSharingConfigurationForINetConnection($connection)
        [pscustomobject]@{
            Id = ([guid]$properties.Guid).Guid
            Name = $properties.Name
            Configuration = $configuration
            Enabled = [bool]$configuration.SharingEnabled
            Role = if ($configuration.SharingEnabled) { [int]$configuration.SharingConnectionType }
        }
    }
}
function Restore-Sharing($original, $managed) {
    $entries = @(Get-Connections)
    foreach ($entry in $entries) {
        if ($entry.Enabled -and $entry.Id -in $managed) {
            $entry.Configuration.DisableSharing()
        }
    }
    foreach ($item in @($original | Sort-Object Role)) {
        $current = @(Get-Connections | Where-Object Id -eq $item.Id)
        if ($current.Count -eq 1 -and !$current[0].Enabled) {
            $current[0].Configuration.EnableSharing([int]$item.Role)
        }
    }
}
$started = $false
try {
    $all = @(Get-Connections)
    if ($Restore) {
        $saved = Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json
        Restore-Sharing $saved.Original @($saved.PublicId,$saved.PrivateId)
        $result = @{Success=$true; Mode='restored'}
    } else {
        $usb = @(Get-NetAdapter -Physical | Where-Object { $_.Status -eq 'Up' -and $_.InterfaceDescription -like 'Remote NDIS based Internet Sharing Device*' -and (Get-CimInstance Win32_NetworkAdapter -Filter ("InterfaceIndex=" + $_.ifIndex)).PNPDeviceID -like 'USB\VID_19D2&PID_0581*' })
        if ($usb.Count -ne 1) { throw 'MF761W USB network adapter could not be identified uniquely.' }
        $internet = @(Get-NetConnectionProfile | Where-Object { $_.IPv4Connectivity -eq 'Internet' -and $_.InterfaceIndex -ne $usb[0].ifIndex })
        $uplinks = @(Get-NetAdapter -Physical | Where-Object { $_.Status -eq 'Up' -and $_.ifIndex -in $internet.InterfaceIndex })
        if ($uplinks.Count -ne 1) { throw 'A unique physical Internet uplink was not found.' }
        $publicId = ([guid]$uplinks[0].InterfaceGuid).Guid
        $privateId = ([guid]$usb[0].InterfaceGuid).Guid
        $former = @($all | Where-Object Enabled | Select-Object Id,Role)
        if (($former | Where-Object {$_.Role -eq 0 -and $_.Id -ne $publicId}).Count -gt 0) {
            throw 'An unrelated public sharing connection is already active.'
        }
        $pub = @($all | Where-Object Id -eq $publicId)
        $priv = @($all | Where-Object Id -eq $privateId)
        if ($pub.Count -ne 1 -or $priv.Count -ne 1) { throw 'Internet Connection Sharing did not enumerate both adapters.' }
        if (!(Test-Path -LiteralPath $statePath)) {
            @{PublicId=$publicId; PrivateId=$privateId; Original=$former} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $statePath -Encoding UTF8
        }
        $started = $true
        if (!$pub[0].Enabled -or $pub[0].Role -ne 0) { $pub[0].Configuration.EnableSharing(0) }
        $priv = Get-Connections | Where-Object Id -eq $privateId
        if (!$priv.Enabled -or $priv.Role -ne 1) { $priv.Configuration.EnableSharing(1) }
        $after = @(Get-Connections)
        if (!($after | Where-Object {$_.Id -eq $privateId -and $_.Enabled -and $_.Role -eq 1}) -or !($after | Where-Object {$_.Id -eq $publicId -and $_.Enabled -and $_.Role -eq 0})) { throw 'Sharing roles failed readback.' }
        $result = @{Success=$true; PublicAdapter=$uplinks[0].Name; PrivateAdapter=$usb[0].Name; Mode='ics'}
    }
} catch {
    $result = @{Success=$false; Reason=$_.Exception.Message}
    if ($started) {
        try { Restore-Sharing $former @($publicId,$privateId); $result.Restored=$true } catch { $result.Restored=$false }
    }
}
$result | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
if (!$result.Success) { exit 1 }
