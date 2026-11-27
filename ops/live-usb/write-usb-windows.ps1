param(
    [Parameter(Mandatory=$true)][string]$IsoPath,
    [Parameter(Mandatory=$true)][int]$DiskNumber,
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"

function Write-Log([string]$msg) {
    $line = "[$(Get-Date -Format 'HH:mm:ss')] $msg"
    Write-Host $line
    if ($LogPath) {
        Add-Content -Path $LogPath -Value $line -Encoding UTF8
    }
}

if ($LogPath) {
    Set-Content -Path $LogPath -Value "" -Encoding UTF8
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdmin) {
    Write-Log "ERROR: Administrator privileges required."
    exit 1
}

if (-not (Test-Path -LiteralPath $IsoPath)) {
    Write-Log "ERROR: ISO not found: $IsoPath"
    exit 1
}

$disk = Get-Disk -Number $DiskNumber
if ($disk.BusType -ne "USB") {
    Write-Log "ERROR: Refusing to write to Disk $DiskNumber ($($disk.FriendlyName)) because BusType is '$($disk.BusType)', not 'USB'."
    exit 2
}

$isoInfo = Get-Item -LiteralPath $IsoPath
if ($isoInfo.Length -gt $disk.Size) {
    Write-Log "ERROR: ISO size ($($isoInfo.Length)) exceeds Disk $DiskNumber size ($($disk.Size))."
    exit 3
}

Write-Log "Target verified: Disk $DiskNumber - $($disk.FriendlyName) ($([math]::Round($disk.Size/1GB,2)) GB, USB)"
Write-Log "Source ISO: $IsoPath ($([math]::Round($isoInfo.Length/1MB,1)) MB)"

Write-Log "Clearing existing partitions on Disk $DiskNumber..."
try {
    Clear-Disk -Number $DiskNumber -RemoveData -RemoveOEM -Confirm:$false -ErrorAction Stop
} catch {
    Write-Log "Clear-Disk note: $($_.Exception.Message)"
}
Start-Sleep -Seconds 2

Add-Type -TypeDefinition @"
using System;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public static class RawDiskWriter {
    private const uint GENERIC_READ = 0x80000000;
    private const uint GENERIC_WRITE = 0x40000000;
    private const uint FILE_SHARE_READ = 0x1;
    private const uint FILE_SHARE_WRITE = 0x2;
    private const uint OPEN_EXISTING = 3;
    private const uint FILE_FLAG_WRITE_THROUGH = 0x80000000;

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern SafeFileHandle CreateFile(
        string lpFileName, uint dwDesiredAccess, uint dwShareMode,
        IntPtr lpSecurityAttributes, uint dwCreationDisposition,
        uint dwFlagsAndAttributes, IntPtr hTemplateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool FlushFileBuffers(SafeFileHandle hFile);

    public static long WriteIso(string isoPath, int diskNumber, Action<long, long> onProgress) {
        string devicePath = @"\\.\PhysicalDrive" + diskNumber;
        using (SafeFileHandle handle = CreateFile(
            devicePath, GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            IntPtr.Zero, OPEN_EXISTING, FILE_FLAG_WRITE_THROUGH, IntPtr.Zero))
        {
            if (handle.IsInvalid) {
                throw new IOException("CreateFile failed for " + devicePath + ", Win32Error=" + Marshal.GetLastWin32Error());
            }
            using (FileStream dst = new FileStream(handle, FileAccess.ReadWrite, 4 * 1024 * 1024, false))
            using (FileStream src = new FileStream(isoPath, FileMode.Open, FileAccess.Read, FileShare.Read, 4 * 1024 * 1024))
            {
                byte[] buf = new byte[4 * 1024 * 1024];
                long total = src.Length;
                long written = 0;
                int read;
                long nextReport = 128L * 1024 * 1024;
                while ((read = src.Read(buf, 0, buf.Length)) > 0) {
                    int writeLen = read;
                    if ((writeLen % 512) != 0) {
                        writeLen = ((writeLen + 511) / 512) * 512;
                        Array.Clear(buf, read, writeLen - read);
                    }
                    dst.Write(buf, 0, writeLen);
                    written += read;
                    if (written >= nextReport || written == total) {
                        onProgress(written, total);
                        nextReport += 128L * 1024 * 1024;
                    }
                }
                dst.Flush();
                FlushFileBuffers(handle);
                return written;
            }
        }
    }
}
"@

Write-Log "Writing raw hybrid ISO to \\.\PhysicalDrive$DiskNumber ..."
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$written = [RawDiskWriter]::WriteIso($IsoPath, $DiskNumber, {
    param($w, $t)
    $pct = [math]::Round(($w * 100.0) / $t, 1)
    Write-Log ("Progress: {0:N1} / {1:N1} MB ({2}%)" -f ($w/1MB), ($t/1MB), $pct)
})
$sw.Stop()
$mbps = [math]::Round(($written / 1MB) / [math]::Max($sw.Elapsed.TotalSeconds, 0.1), 1)
Write-Log "SUCCESS: Wrote $([math]::Round($written/1MB,1)) MB in $([math]::Round($sw.Elapsed.TotalSeconds,1))s ($mbps MB/s)."
Write-Log "Refreshing disk partition table..."
Update-Disk -Number $DiskNumber -ErrorAction SilentlyContinue
Write-Log "DONE."
