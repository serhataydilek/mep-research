[CmdletBinding()]
param([string]$Destination = 'validation\ifc_samples\external\official_medical_dental')
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$revision = '7ddf57a201f88a0c213d5322b02ed15e94a60a40'
$repoUrl = 'https://github.com/buildingsmart-community/Community-Sample-Test-Files.git'
$relative = 'IFC 2.3.0.1 (IFC 2x3)/Medical-Dental Clinic'
$expected = [ordered]@{
  'Clinic_Architectural.ifc' = '2ac970ce065ecac4e0c9e5f453a257169e90d0067f419b7e33533a64ef837880'
  'Clinic_Structural.ifc'   = '325287b375f154dc79504c4bbf67b40c501f458f23a8c4ba13b27171e00533d8'
  'Clinic_HVAC.ifc'         = '39c88a79f48fbe56da86afb0fb3ebd188f8930df3df7dbfbd9ecaa535aaeab9b'
  'Clinic_Plumbing.ifc'     = 'e662a8d0273694b745a313fc18ee8d4916db03453caa8f9d00d4d80b62bb6e23'
  'Clinic_Electrical.ifc'   = '70c436de1f1e8a860acfba61b68da1c9243d18dcbbddb98b88b550a642d95d32'
}
$scratch = Join-Path ([IO.Path]::GetTempPath()) 'phase10rb3-medical-dental-lfs'
if (Test-Path $scratch) { Remove-Item -Recurse -Force -LiteralPath $scratch }
git clone --filter=blob:none --no-checkout $repoUrl $scratch
git -C $scratch sparse-checkout set --no-cone $relative
git -C $scratch checkout $revision
git -C $scratch lfs pull --include "$relative/*"
$target = Join-Path $root $Destination; New-Item -ItemType Directory -Force -Path $target | Out-Null
$records = foreach ($name in $expected.Keys) {
  $source = Join-Path (Join-Path $scratch $relative) $name
  if (-not (Test-Path -LiteralPath $source)) { throw "Missing LFS payload: $name" }
  $head = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($source)[0..63])
  if ($head -notmatch 'ISO-10303-21\s*;') { throw "Not an IFC STEP payload: $name" }
  $hash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
  if ($hash -ne $expected[$name]) { throw "Hash mismatch for $name: $hash" }
  Copy-Item -LiteralPath $source -Destination (Join-Path $target $name) -Force
  [ordered]@{ file_name=$name; source_url="$repoUrl/blob/$revision/$([uri]::EscapeDataString($relative).Replace('%2F','/'))/$name"; lfs_object_sha256=$hash; size_bytes=(Get-Item -LiteralPath $source).Length; step_header_verified=$true }
}
[ordered]@{ schema_version=1; dataset='Medical-Dental Clinic'; upstream_revision=$revision; source_repository=$repoUrl; files=$records } | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 (Join-Path $target 'acquisition_manifest.json')
Write-Host "Acquired and verified $($records.Count) IFC payloads at $target"
