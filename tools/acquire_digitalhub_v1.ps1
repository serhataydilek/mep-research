<#
Download RWTH DigitalHub Version 1 IFC files discovered from the GitHub tree.

The script lists the pinned revision, selects the Version_1 directory from that
listing, and downloads the architecture, ventilation, heating, and sanitary IFC
files whose names are present in that directory. It does not download Version_2.
#>
[CmdletBinding()]
param(
  [string]$Revision = "36565d529b4dadeca625de2b793d7e16700171e9",
  [string]$Repository = "RWTH-E3D/DigitalHub"
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$dest = Join-Path $root "validation\ifc_samples\external\rwth_digitalhub_v1"
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$headers = @{ "User-Agent" = "hvac-research-digitalhub-v1"; "Accept" = "application/vnd.github+json" }
function Get-Listing([string]$path) {
  $uri = if ([string]::IsNullOrEmpty($path)) {
    "https://api.github.com/repos/$Repository/contents/?ref=$Revision"
  } else {
    "https://api.github.com/repos/$Repository/contents/${path}?ref=$Revision"
  }
  return Invoke-RestMethod -Headers $headers -Uri $uri
}
$rootListing = Get-Listing ""
$versionDir = $rootListing | Where-Object { $_.type -eq "dir" -and $_.name -eq "Version_1" } | Select-Object -First 1
if (-not $versionDir) { throw "Pinned revision $Revision has no Version_1 directory." }
$versionListing = Get-Listing $versionDir.path
$tokens = @("ARC", "LFT", "HZG", "SAN")
$selected = @()
foreach ($token in $tokens) {
  $match = $versionListing | Where-Object {
    $_.type -eq "file" -and $_.name -like "*.ifc" -and $_.name -notlike "*with_SB*" -and ($_.name -split "[-_.]") -contains $token
  } | Select-Object -First 1
  if (-not $match) { throw "Version_1 listing has no IFC file whose name contains $token." }
  $selected += $match
}
$license = $rootListing | Where-Object { $_.type -eq "file" -and $_.name -eq "LICENSE" } | Select-Object -First 1
if ($license) { $selected += $license }
$records = @()
foreach ($item in $selected) {
  $out = Join-Path $dest $item.name
  $url = "https://raw.githubusercontent.com/$Repository/$Revision/$($item.path)"
  if (-not (Test-Path $out) -or (Get-Item $out).Length -ne [int64]$item.size) {
    Write-Host "Downloading $($item.path)"
    curl.exe -L --fail --retry 3 --retry-delay 2 -o $out $url
    if ($LASTEXITCODE -ne 0) { throw "Download failed: $url" }
  } else {
    Write-Host "Present $($item.name)"
  }
  $hash = (Get-FileHash -Algorithm SHA256 $out).Hash.ToLowerInvariant()
  $records += [ordered]@{
    repository_path = $item.path
    file_name = $item.name
    source_url = $url
    github_size_bytes = [int64]$item.size
    size_bytes = (Get-Item $out).Length
    sha256 = $hash
  }
}
$manifest = [ordered]@{
  dataset_id = "rwth-digitalhub-v1"
  repository = "https://github.com/$Repository"
  revision = $Revision
  version_directory = $versionDir.path
  license = "MIT"
  license_attribution = "Copyright (c) 2020 RWTH Aachen University - E3D Institute of Energy Efficiency and Sustainable Building"
  technical_report_doi = "10.18154/RWTH-2020-11683"
  excluded_version = "Version_2"
  files = $records
}
$manifestPath = Join-Path $dest "acquisition_record.json"
$manifest | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 $manifestPath
Write-Host "Acquisition record: $manifestPath"
