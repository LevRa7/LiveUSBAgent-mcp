# ==============================================================================
#  LiveUSBAgent-mcp - Bootable LiveUSB Creator (Pure Windows GUI)
#  Downloads prebuilt LiveUSB ISOs from GitHub Releases or uses a local .iso
#  and writes it raw to a removable USB flash drive. Zero WSL / external deps.
# ==============================================================================
[CmdletBinding()]
param(
    [string]$RepoRoot = ""
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

if (-not $RepoRoot) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $RepoRoot = Split-Path -Parent $scriptDir
}
$WriterScript = Join-Path $RepoRoot "ops\live-usb\write-usb-windows.ps1"
$ArtifactsDir = Join-Path $RepoRoot "artifacts"
if (-not (Test-Path -LiteralPath $ArtifactsDir)) {
    New-Item -ItemType Directory -Path $ArtifactsDir -Force | Out-Null
}
$LogFile = Join-Path $ArtifactsDir "usb-write.log"
$DownloadsDir = Join-Path $env:USERPROFILE "Downloads"
if (-not (Test-Path -LiteralPath $DownloadsDir)) {
    $DownloadsDir = $ArtifactsDir
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "LiveUSBAgent-mcp - Bootable LiveUSB Creator"
$form.Size = New-Object System.Drawing.Size(720, 640)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.Font = New-Object System.Drawing.Font("Segoe UI", 9.5)

# --- Group 1: ISO Download / Select ---
$grpIso = New-Object System.Windows.Forms.GroupBox
$grpIso.Text = "1. LiveUSB ISO Image (Download from GitHub Releases or Select Local .iso)"
$grpIso.Location = New-Object System.Drawing.Point(14, 12)
$grpIso.Size = New-Object System.Drawing.Size(676, 190)
$form.Controls.Add($grpIso)

$lblArch = New-Object System.Windows.Forms.Label
$lblArch.Text = "Target PC:"
$lblArch.Location = New-Object System.Drawing.Point(14, 28)
$lblArch.AutoSize = $true
$grpIso.Controls.Add($lblArch)

$cmbArch = New-Object System.Windows.Forms.ComboBox
$cmbArch.Location = New-Object System.Drawing.Point(88, 24)
$cmbArch.Size = New-Object System.Drawing.Size(375, 26)
$cmbArch.DropDownStyle = "DropDownList"
[void]$cmbArch.Items.AddRange(@(
    "amd64 - Standard 64-bit PC / Laptop (UEFI + BIOS)",
    "arm64 - ARM64 PC / Mac / Raspberry Pi (UEFI)",
    "i386  - Old 32-bit PC (BIOS, console TUI)"
))
$cmbArch.SelectedIndex = 0
$grpIso.Controls.Add($cmbArch)

$btnDownload = New-Object System.Windows.Forms.Button
$btnDownload.Text = "Download Latest ISO"
$btnDownload.Location = New-Object System.Drawing.Point(475, 22)
$btnDownload.Size = New-Object System.Drawing.Size(186, 30)
$grpIso.Controls.Add($btnDownload)

$lblIsoPath = New-Object System.Windows.Forms.Label
$lblIsoPath.Text = "ISO File:"
$lblIsoPath.Location = New-Object System.Drawing.Point(14, 70)
$lblIsoPath.AutoSize = $true
$grpIso.Controls.Add($lblIsoPath)

$txtIsoPath = New-Object System.Windows.Forms.TextBox
$txtIsoPath.Location = New-Object System.Drawing.Point(88, 66)
$txtIsoPath.Size = New-Object System.Drawing.Size(467, 26)
foreach ($searchDir in @($DownloadsDir, $ArtifactsDir)) {
    if ($txtIsoPath.Text) { break }
    if (Test-Path -LiteralPath $searchDir) {
        $latestIso = Get-ChildItem -Path $searchDir -Filter "LiveUSBAgent-*.iso" -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($latestIso) { $txtIsoPath.Text = $latestIso.FullName }
    }
}
$grpIso.Controls.Add($txtIsoPath)

$btnBrowse = New-Object System.Windows.Forms.Button
$btnBrowse.Text = "Browse..."
$btnBrowse.Location = New-Object System.Drawing.Point(565, 64)
$btnBrowse.Size = New-Object System.Drawing.Size(96, 30)
$grpIso.Controls.Add($btnBrowse)

$progressBar = New-Object System.Windows.Forms.ProgressBar
$progressBar.Location = New-Object System.Drawing.Point(14, 108)
$progressBar.Size = New-Object System.Drawing.Size(647, 20)
$progressBar.Minimum = 0
$progressBar.Maximum = 100
$progressBar.Value = 0
$grpIso.Controls.Add($progressBar)

$lblIsoHint = New-Object System.Windows.Forms.Label
$lblIsoHint.Text = "Click 'Download Latest ISO' to fetch the official prebuilt image from GitHub Releases into Downloads, or select an existing .iso file."
$lblIsoHint.Location = New-Object System.Drawing.Point(14, 136)
$lblIsoHint.Size = New-Object System.Drawing.Size(647, 42)
$lblIsoHint.ForeColor = [System.Drawing.Color]::DimGray
$grpIso.Controls.Add($lblIsoHint)

# --- Group 2: Target USB Flash Drive ---
$grpUsb = New-Object System.Windows.Forms.GroupBox
$grpUsb.Text = "2. Target Removable USB Flash Drive"
$grpUsb.Location = New-Object System.Drawing.Point(14, 212)
$grpUsb.Size = New-Object System.Drawing.Size(676, 170)
$form.Controls.Add($grpUsb)

$cmbUsb = New-Object System.Windows.Forms.ComboBox
$cmbUsb.Location = New-Object System.Drawing.Point(14, 28)
$cmbUsb.Size = New-Object System.Drawing.Size(540, 26)
$cmbUsb.DropDownStyle = "DropDownList"
$grpUsb.Controls.Add($cmbUsb)

$btnRefreshUsb = New-Object System.Windows.Forms.Button
$btnRefreshUsb.Text = "Refresh USB"
$btnRefreshUsb.Location = New-Object System.Drawing.Point(564, 26)
$btnRefreshUsb.Size = New-Object System.Drawing.Size(98, 30)
$grpUsb.Controls.Add($btnRefreshUsb)

$lblWarn = New-Object System.Windows.Forms.Label
$lblWarn.Text = "WARNING: Writing the LiveUSB image will FORMAT and PERMANENTLY ERASE ALL DATA on the selected USB flash drive! Double-check the drive model and size before continuing."
$lblWarn.Location = New-Object System.Drawing.Point(14, 66)
$lblWarn.Size = New-Object System.Drawing.Size(648, 48)
$lblWarn.ForeColor = [System.Drawing.Color]::DarkRed
$lblWarn.Font = New-Object System.Drawing.Font("Segoe UI", 9.5, [System.Drawing.FontStyle]::Bold)
$grpUsb.Controls.Add($lblWarn)

$chkConfirm = New-Object System.Windows.Forms.CheckBox
$chkConfirm.Text = "I understand that the selected USB drive will be formatted and all existing data will be erased."
$chkConfirm.Location = New-Object System.Drawing.Point(14, 124)
$chkConfirm.Size = New-Object System.Drawing.Size(648, 28)
$grpUsb.Controls.Add($chkConfirm)

# --- Action & Log ---
$btnFlash = New-Object System.Windows.Forms.Button
$btnFlash.Text = "Write Bootable LiveUSB Flash Drive"
$btnFlash.Location = New-Object System.Drawing.Point(14, 394)
$btnFlash.Size = New-Object System.Drawing.Size(676, 40)
$btnFlash.Enabled = $false
$btnFlash.Font = New-Object System.Drawing.Font("Segoe UI", 10.5, [System.Drawing.FontStyle]::Bold)
$form.Controls.Add($btnFlash)

$txtLog = New-Object System.Windows.Forms.TextBox
$txtLog.Location = New-Object System.Drawing.Point(14, 444)
$txtLog.Size = New-Object System.Drawing.Size(676, 144)
$txtLog.Multiline = $true
$txtLog.ScrollBars = "Vertical"
$txtLog.ReadOnly = $true
$txtLog.Font = New-Object System.Drawing.Font("Consolas", 9)
$form.Controls.Add($txtLog)

$script:UsbDisks = @()
$script:IsDownloading = $false
$script:WebClient = $null

function Append-Log([string]$msg) {
    $txtLog.AppendText("[" + (Get-Date -Format "HH:mm:ss") + "] " + $msg + "`r`n")
}

function Refresh-UsbList {
    $cmbUsb.Items.Clear()
    $script:UsbDisks = @(Get-Disk | Where-Object { $_.BusType -eq "USB" } | Sort-Object Number)
    foreach ($d in $script:UsbDisks) {
        $gb = [math]::Round($d.Size / 1GB, 2)
        [void]$cmbUsb.Items.Add("Disk $($d.Number) - $($d.FriendlyName) ($gb GB, USB)")
    }
    if ($cmbUsb.Items.Count -gt 0) {
        $cmbUsb.SelectedIndex = 0
        Append-Log "Detected $($cmbUsb.Items.Count) removable USB drive(s)."
    } else {
        [void]$cmbUsb.Items.Add("(No removable USB drives detected)")
        $cmbUsb.SelectedIndex = 0
        Append-Log "No removable USB drives detected. Insert a USB stick and click Refresh USB."
    }
    Update-FlashEnabled
}

function Update-FlashEnabled {
    $hasUsb = ($script:UsbDisks.Count -gt 0)
    $hasIso = ($txtIsoPath.Text.Trim() -ne "") -and (Test-Path -LiteralPath $txtIsoPath.Text.Trim())
    $btnFlash.Enabled = ($hasUsb -and $hasIso -and $chkConfirm.Checked -and (-not $script:IsDownloading))
}

$btnRefreshUsb.Add_Click({ Refresh-UsbList })
$chkConfirm.Add_CheckedChanged({ Update-FlashEnabled })
$txtIsoPath.Add_TextChanged({ Update-FlashEnabled })

$btnBrowse.Add_Click({
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Filter = "ISO Images (*.iso)|*.iso|All Files (*.*)|*.*"
    if (Test-Path -LiteralPath $DownloadsDir) { $dlg.InitialDirectory = $DownloadsDir }
    if ($dlg.ShowDialog() -eq "OK") {
        $txtIsoPath.Text = $dlg.FileName
        Append-Log "Selected ISO: $($dlg.FileName)"
    }
})

$btnDownload.Add_Click({
    if ($script:IsDownloading) {
        if ($script:WebClient) {
            $script:WebClient.CancelAsync()
            Append-Log "Cancelling download..."
        }
        return
    }
    $arch = ([string]$cmbArch.SelectedItem).Split(" ")[0].Trim()
    Append-Log "Checking latest GitHub Release for $arch ISO..."
    $btnDownload.Enabled = $false
    [System.Windows.Forms.Application]::DoEvents()
    try {
        $headers = @{ "User-Agent" = "LiveUSBAgent-Creator" }
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/LevRa7/LiveUSBAgent-mcp/releases/latest" -Headers $headers -TimeoutSec 20
        $asset = $release.assets | Where-Object { $_.name -like "LiveUSBAgent-*-$arch-*.iso" } | Select-Object -First 1
        if (-not $asset) {
            Append-Log "ERROR: No $arch ISO found in release $($release.tag_name)."
            [System.Windows.Forms.MessageBox]::Show(
                "No $arch ISO asset was found in the latest GitHub Release ($($release.tag_name)).",
                "ISO Not Found", "OK", "Warning"
            )
            $btnDownload.Enabled = $true
            return
        }
        $destPath = Join-Path $DownloadsDir $asset.name
        $sizeMb = [math]::Round($asset.size / 1MB, 1)
        Append-Log "Downloading $($asset.name) ($sizeMb MB) from $($release.tag_name)..."
        $progressBar.Value = 0
        $script:IsDownloading = $true
        $btnDownload.Text = "Cancel Download"
        $btnDownload.Enabled = $true
        $btnBrowse.Enabled = $false
        $cmbArch.Enabled = $false
        Update-FlashEnabled

        $wc = New-Object System.Net.WebClient
        $wc.Headers.Add("User-Agent", "LiveUSBAgent-Creator")
        $script:WebClient = $wc

        $wc.Add_DownloadProgressChanged({
            param($sender, $e)
            $pct = [math]::Min(100, [math]::Max(0, $e.ProgressPercentage))
            $progressBar.Value = $pct
            $mbDone = [math]::Round($e.BytesReceived / 1MB, 1)
            $mbTotal = [math]::Round($e.TotalBytesToReceive / 1MB, 1)
            $lblIsoHint.Text = "Downloading $($asset.name): $mbDone MB / $mbTotal MB ($pct%)"
        })

        $wc.Add_DownloadFileCompleted({
            param($sender, $e)
            $script:IsDownloading = $false
            $btnDownload.Text = "Download Latest ISO"
            $btnDownload.Enabled = $true
            $btnBrowse.Enabled = $true
            $cmbArch.Enabled = $true
            if ($e.Cancelled) {
                Append-Log "Download cancelled."
                $lblIsoHint.Text = "Download cancelled. Click 'Download Latest ISO' to try again."
                $progressBar.Value = 0
            } elseif ($e.Error) {
                Append-Log "Download failed: $($e.Error.Message)"
                $lblIsoHint.Text = "Download failed. Check your internet connection or browse to a local .iso."
                $progressBar.Value = 0
            } else {
                $progressBar.Value = 100
                $txtIsoPath.Text = $destPath
                $lblIsoHint.Text = "Downloaded: $destPath"
                Append-Log "Download complete: $destPath"
            }
            Update-FlashEnabled
        })

        $wc.DownloadFileAsync([Uri]$asset.browser_download_url, $destPath)
    } catch {
        Append-Log "ERROR querying GitHub Releases: $($_.Exception.Message)"
        $btnDownload.Enabled = $true
        $script:IsDownloading = $false
        Update-FlashEnabled
    }
})

$btnFlash.Add_Click({
    $idx = $cmbUsb.SelectedIndex
    if ($idx -lt 0 -or $idx -ge $script:UsbDisks.Count) { return }
    $disk = $script:UsbDisks[$idx]
    $gb = [math]::Round($disk.Size / 1GB, 2)
    $iso = $txtIsoPath.Text.Trim()

    $confirmMsg = "FINAL WARNING:`n`nALL DATA on Disk $($disk.Number) ($($disk.FriendlyName), $gb GB) will be FORMATTED and PERMANENTLY ERASED!`n`nISO: $iso`n`nProceed with writing the bootable LiveUSB?"
    $res = [System.Windows.Forms.MessageBox]::Show(
        $confirmMsg,
        "Confirm USB Format & Erase",
        [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Warning
    )
    if ($res -ne [System.Windows.Forms.DialogResult]::Yes) {
        Append-Log "USB write cancelled by user."
        return
    }
    if (-not (Test-Path -LiteralPath $WriterScript)) {
        [System.Windows.Forms.MessageBox]::Show("Writer script not found: $WriterScript", "Error", "OK", "Error")
        return
    }
    Append-Log "Launching elevated USB writer for Disk $($disk.Number) ($($disk.FriendlyName))..."
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit",
        "-File", $WriterScript,
        "-IsoPath", $iso,
        "-DiskNumber", "$($disk.Number)",
        "-LogPath", $LogFile
    )
})

$form.Add_FormClosing({
    if ($script:IsDownloading -and $script:WebClient) {
        $script:WebClient.CancelAsync()
    }
})

Refresh-UsbList
[void]$form.ShowDialog()
