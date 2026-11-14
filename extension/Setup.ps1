[CmdletBinding()]param()
$ErrorActionPreference='Stop'
Write-Host 'Newman MF761W P005 | Windows hotspot setup'
Write-Host 'Select files on this computer. Enter node links in your private UTF-8 text file, one per line.'
$adb=Read-Host 'Path to Android Platform Tools adb.exe'
$xray=Read-Host 'Path to Xray x64 xray.exe'
$nodes=Read-Host 'Path to the private node text file'
$adb=$adb.Trim().Trim('"')
$xray=$xray.Trim().Trim('"')
$nodes=$nodes.Trim().Trim('"')
if(!$adb -or !$xray -or !$nodes){throw 'All three file paths are required.'}
& (Join-Path $PSScriptRoot 'Hotspot.ps1') -Action Install -Adb $adb -Xray $xray -NodesFile $nodes
& (Join-Path $PSScriptRoot 'Hotspot.ps1') -Action Status
