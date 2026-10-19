# SmartSites MEP / IFC sample downloader
# Extract this kit into the existing hvac_research repository root.
[CmdletBinding()]
param(
  [switch]$IncludeStructure,
  [switch]$IncludeHeavy
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not (Test-Path (Join-Path $repo 'src\ifc_model.py'))) {
  throw "This script must be inside the existing hvac_research/tools folder: $repo"
}
$dest = Join-Path $repo 'validation\ifc_samples\external'
New-Item -ItemType Directory -Force -Path $dest | Out-Null
$base = 'https://huggingface.co/datasets/sylvainHellin/ifc-bench/resolve/main/projects'
$models = @(
  @{ Group='small'; Project='fzk_house'; Name='arc.ifc'; Discipline='Architecture' },
  @{ Group='medium'; Project='dental_clinic'; Name='arc.ifc'; Discipline='Architecture' },
  @{ Group='medium'; Project='digital_hub'; Name='arc.ifc'; Discipline='Architecture' },
  @{ Group='medium'; Project='digital_hub'; Name='ventilation.ifc'; Discipline='MEP Ventilation' }
)
if ($IncludeStructure) {
  $models += @{ Group='medium'; Project='dental_clinic'; Name='str.ifc'; Discipline='Structure' }
}
if ($IncludeHeavy) {
  Write-Host 'Heavy IFC model selected: dental_clinic/mep.ifc is ~126 MB.' -ForegroundColor Yellow
  $models += @{ Group='large'; Project='dental_clinic'; Name='mep.ifc'; Discipline='MEP' }
}
function Download-File([string]$uri,[string]$path,[bool]$isIfc) {
  $folder = Split-Path -Parent $path
  New-Item -ItemType Directory -Force -Path $folder | Out-Null
  $tmp = "$path.partial"
  try {
    Invoke-WebRequest -Uri $uri -OutFile $tmp -MaximumRedirection 10 -UseBasicParsing -ErrorAction Stop
    if (-not (Test-Path $tmp)) { throw "Download did not create a file: $uri" }
    if ($isIfc) {
      $length = (Get-Item $tmp).Length
      if ($length -lt 1024) { throw "Downloaded IFC is suspiciously small ($length bytes): $uri" }
      $stream = [System.IO.File]::OpenRead($tmp)
      try {
        $buf = New-Object byte[] 256
        $n = $stream.Read($buf,0,$buf.Length)
        $header = [System.Text.Encoding]::ASCII.GetString($buf,0,$n)
      } finally { $stream.Dispose() }
      if ($header -notmatch 'ISO-10303-21\s*;') { throw "Not a valid IFC STEP header: $uri" }
    }
    Move-Item -Force $tmp $path
  } catch {
    Remove-Item $tmp -ErrorAction SilentlyContinue
    throw
  }
}
$records = @()
foreach ($m in $models) {
  $rel = "$($m.Group)\$($m.Project)\$($m.Name)"
  $target = Join-Path $dest $rel
  $uri = "$base/$($m.Project)/$($m.Name)"
  Write-Host "Downloading $rel ..."
  if (-not (Test-Path $target)) {
    Download-File -uri $uri -path $target -isIfc $true
  }
  $records += [ordered]@{
    path=$rel.Replace('\','/'); source_url=$uri; discipline=$m.Discipline;
    size_bytes=(Get-Item $target).Length; sha256=(Get-FileHash $target -Algorithm SHA256).Hash.ToLowerInvariant()
  }
  Write-Host "OK: $rel" -ForegroundColor Green
}
$projects = $models | ForEach-Object { $_.Project } | Sort-Object -Unique
foreach ($p in $projects) {
  $folder = Join-Path $dest "licenses\$p"
  foreach ($info in @('license.txt','model_card.md')) {
    $url = "$base/$p/$info"
    $path = Join-Path $folder $info
    try { if (-not (Test-Path $path)) { Download-File -uri $url -path $path -isIfc $false } }
    catch { Write-Warning "Could not retrieve $p/$info; see the dataset source page: $($_.Exception.Message)" }
  }
}
$manifest = [ordered]@{
  pack='SmartSites public IFC validation samples'
  source_dataset='https://huggingface.co/datasets/sylvainHellin/ifc-bench'
  generated_utc=(Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
  note='Geometry/testing samples. Does not imply routing-ready connectors or zero-clash.'
  files=$records
}
$manifestPath=Join-Path $dest 'download_manifest.json'
$manifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $manifestPath
Write-Host "Complete. Models stored in: $dest" -ForegroundColor Green
Write-Host "Checksums: $manifestPath"
Write-Host "Check Git exclusion: git check-ignore validation/ifc_samples/external/small/fzk_house/arc.ifc"
