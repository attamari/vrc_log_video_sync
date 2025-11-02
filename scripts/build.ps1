#requires -version 5.1
param(
  [string]$Python = '3.12',
  [string]$OutputDir = 'dist',
  [string]$AppName = 'vrc-log-video-sync',
  [switch]$UseIcon,
  [string]$Icon = 'icon.ico',
  [switch]$SelfSign
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Step($m){ Write-Host "[build] $m" -ForegroundColor Cyan }
function OK($m){ Write-Host "[ok] $m" -ForegroundColor Green }

Push-Location (Split-Path -Parent $PSCommandPath)
Push-Location ..
try {
  if (-not (Test-Path 'src/vrc_log_video_sync/__main__.py')) { throw 'entry not found: src/vrc_log_video_sync/__main__.py' }

  $vcvars = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat'
  if (-not (Test-Path $vcvars)) { throw "vcvars not found: $vcvars" }

  # Build into dist directly, then rename __main__.dist -> dist\$AppName
  if (-not (Test-Path $OutputDir)) { New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null }
  $buildDir = $OutputDir
  $tempOut = Join-Path $OutputDir '__main__.dist'
  $stage   = Join-Path $OutputDir $AppName
  if (Test-Path $tempOut) { Remove-Item $tempOut -Recurse -Force }
  if (Test-Path $stage)   { Remove-Item $stage   -Recurse -Force }

  $args = @(
    '--standalone',
    '--msvc=14.3',
    "--output-dir=$buildDir",
    '--remove-output',
    '--lto=no',
    "--output-filename=$AppName.exe",
    '--file-version=0.1.0',
    '--product-version=0.1.0',
    '--company-name=attamari',
    '--product-name="VRChat Log Video Sync"',
    '--file-description="VRChat Log Video Sync"',
    '--nofollow-import-to=ssl,hashlib,bz2,lzma,wmi,_wmi,decimal,_decimal',
    '--noinclude-dlls=libssl-3-x64.dll,libcrypto-3-x64.dll',
    '--python-flag=no_site'
  )
  if ($UseIcon -and (Test-Path $Icon)) { $args += "--windows-icon-from-ico=$Icon" }
  $args += 'src\vrc_log_video_sync\__main__.py'

  Step 'compile (MSVC)'
  $cmd = '"' + $vcvars + '" && uvx --python ' + $Python + ' --from nuitka nuitka ' + ($args -join ' ')
  Write-Host $cmd
  cmd /c $cmd | Out-Host

  $buildOut = $tempOut
  if (-not (Test-Path $buildOut)) { throw "build output not found: $buildOut" }
  Move-Item $buildOut $stage -Force

  # replace runtimes with signed ones if available
  Step 'replace runtimes (signed)'
  $vcRoot = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Redist\MSVC'
  if (Test-Path $vcRoot) {
    $crt  = Get-ChildItem $vcRoot -Recurse -Filter vcruntime140.dll  -ErrorAction SilentlyContinue | Where-Object { $_.FullName -match 'Microsoft\.VC143\.CRT' -and $_.FullName -match '\\x64\\' } | Select-Object -First 1 -ExpandProperty FullName
    $crt1 = Get-ChildItem $vcRoot -Recurse -Filter vcruntime140_1.dll -ErrorAction SilentlyContinue | Where-Object { $_.FullName -match 'Microsoft\.VC143\.CRT' -and $_.FullName -match '\\x64\\' } | Select-Object -First 1 -ExpandProperty FullName
    if ($crt)  { Copy-Item $crt  (Join-Path $stage 'vcruntime140.dll')   -Force }
    if ($crt1) { Copy-Item $crt1 (Join-Path $stage 'vcruntime140_1.dll') -Force }
  }
  $py312 = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python312.dll'
  if (Test-Path $py312) { Copy-Item $py312 (Join-Path $stage 'python312.dll') -Force }
  $libffi = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\DLLs\libffi-8.dll'
  if (Test-Path $libffi) { Copy-Item $libffi (Join-Path $stage 'libffi-8.dll') -Force }

  if ($SelfSign) {
    Step 'self-sign exe'
    $signtool = Get-ChildItem 'C:\Program Files (x86)\Windows Kits' -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue | Where-Object { $_.FullName -match '\\x64\\' } | Select-Object -First 1 -ExpandProperty FullName
    if ($signtool) {
      $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=$AppName (local dev)" -KeyAlgorithm RSA -KeyLength 3072 -KeyExportPolicy Exportable -KeyUsage DigitalSignature -NotAfter (Get-Date).AddYears(1) -FriendlyName "$AppName Dev" -CertStoreLocation Cert:\CurrentUser\My
      & $signtool sign /fd SHA256 /sha1 $cert.Thumbprint /s MY (Join-Path $stage "$AppName.exe") | Out-Host
    }
  }

  Step 'smoke (--help)'
  Push-Location $stage; try { & ".\${AppName}.exe" --help | Out-Host } finally { Pop-Location }

  OK "done: $stage"
}
finally { Pop-Location; Pop-Location }
