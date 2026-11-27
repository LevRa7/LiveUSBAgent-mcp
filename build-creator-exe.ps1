# ==============================================================================
#  LiveUSBAgent-mcp - build the single-file Windows LiveUSB Creator executable
#  Produces dist\LiveUSBAgent-Creator-<version>.exe (+ .sha256).
#  Uses only Windows in-box .NET Framework 4.x csc.exe + Compress-Archive.
# ==============================================================================
[CmdletBinding()]
param(
    [string]$OutputDir = ''
)

$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
if (-not $repo) { $repo = (Get-Location).Path }
if (-not $OutputDir) { $OutputDir = Join-Path $repo 'dist' }

$versionPath = Join-Path $repo 'VERSION'
$version = (Get-Content -LiteralPath $versionPath -Raw -Encoding UTF8).Trim()
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    throw ("VERSION is not MAJOR.MINOR.PATCH: '" + $version + "'")
}
$coreVersionPath = Join-Path $repo 'core\version.py'
$moduleMatch = Select-String -LiteralPath $coreVersionPath -Pattern '^__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $moduleMatch) { throw 'core\version.py has no __version__ declaration' }
$moduleVersion = $moduleMatch.Matches[0].Groups[1].Value
if ($moduleVersion -ne $version) {
    throw ('version mismatch: VERSION says ' + $version + ' but core/version.py says ' + $moduleVersion)
}

$cscCandidates = @(
    "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
    "$env:WINDIR\Microsoft.NET\Framework\v4.0.30319\csc.exe"
)
$csc = $cscCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $csc) { throw 'csc.exe (.NET Framework 4.x) not found' }

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$stage = Join-Path ([System.IO.Path]::GetTempPath()) ("rhm-creator-stage-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $stage | Out-Null

try {
    $stageTools = Join-Path $stage 'tools'
    $stageOps = Join-Path $stage 'ops\live-usb'
    New-Item -ItemType Directory -Force -Path $stageTools, $stageOps | Out-Null
    Copy-Item -LiteralPath (Join-Path $repo 'tools\liveusb-creator-gui.ps1') -Destination $stageTools
    Copy-Item -LiteralPath (Join-Path $repo 'ops\live-usb\write-usb-windows.ps1') -Destination $stageOps

    $zipPath = Join-Path $stage 'creator-payload.zip'
    Compress-Archive -Path (Join-Path $stage 'tools'), (Join-Path $stage 'ops') -DestinationPath $zipPath -CompressionLevel Optimal

    $csPath = Join-Path $stage 'CreatorLauncher.cs'
    $csCode = @"
using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Reflection;

class CreatorLauncher {
    [STAThread]
    static int Main(string[] args) {
        string tempDir = Path.Combine(Path.GetTempPath(), "LiveUSBAgent-Creator-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(tempDir);
        string zipPath = Path.Combine(tempDir, "payload.zip");
        using (Stream s = Assembly.GetExecutingAssembly().GetManifestResourceStream("creator-payload.zip"))
        using (FileStream fs = File.Create(zipPath)) {
            s.CopyTo(fs);
        }
        ZipFile.ExtractToDirectory(zipPath, tempDir);
        string scriptPath = Path.Combine(tempDir, "tools", "liveusb-creator-gui.ps1");
        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = "powershell.exe";
        psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -STA -File \"" + scriptPath + "\" -RepoRoot \"" + tempDir + "\"";
        psi.UseShellExecute = false;
        Process p = Process.Start(psi);
        p.WaitForExit();
        return p.ExitCode;
    }
}
"@
    Set-Content -LiteralPath $csPath -Value $csCode -Encoding ASCII

    $outExe = Join-Path $OutputDir ("LiveUSBAgent-Creator-" + $version + ".exe")
    & $csc /nologo /target:winexe /optimize+ `
        /r:System.IO.Compression.FileSystem.dll `
        "/resource:$zipPath,creator-payload.zip" `
        "/out:$outExe" $csPath
    if ($LASTEXITCODE -ne 0) { throw "csc.exe failed with exit code $LASTEXITCODE" }

    $hash = (Get-FileHash -LiteralPath $outExe -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath ($outExe + '.sha256') -Value ($hash + '  ' + (Split-Path -Leaf $outExe)) -Encoding ASCII
    Write-Host ("Built: " + $outExe + " (sha256=" + $hash + ")")
} finally {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $stage
}
