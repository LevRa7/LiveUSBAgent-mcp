# ==============================================================================
#  LiveUSBAgent-mcp - Visual ISO Builder & Bootable USB Flasher (Windows GUI)
#  Uses native Windows Forms (.NET Framework 4.x) - zero external dependencies.
# ==============================================================================
[CmdletBinding()]
param(
    [string]$RepoRoot = ""
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

if (-not $RepoRoot) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $RepoRoot = Split-Path -Parent $scriptDir
}
$WriterScript = Join-Path $RepoRoot "ops\live-usb\write-usb-windows.ps1"
$ArtifactsDir = Join-Path $RepoRoot "artifacts"
$LogFile = Join-Path $ArtifactsDir "usb-write.log"

$form = New-Object System.Windows.Forms.Form
$form.Text = "LiveUSBAgent-mcp - Visual ISO Builder & USB Flasher"
$form.Size = New-Object System.Drawing.Size(720, 620)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.Font = New-Object System.Drawing.Font("Segoe UI", 9.5)

# --- Group 1: ISO Build / Select ---
$grpIso = New-Object System.Windows.Forms.GroupBox
$grpIso.Text = "1. ISO Image (Build via WSL Debian or Select Existing)"
$grpIso.Location = New-Object System.Drawing.Point(14, 12)
$grpIso.Size = New-Object System.Drawing.Size(676, 175)
$form.Controls.Add($grpIso)

$lblArch = New-Object System.Windows.Forms.Label
$lblArch.Text = "Arch:"
$lblArch.Location = New-Object System.Drawing.Point(14, 28)
$lblArch.AutoSize = $true
$grpIso.Controls.Add($lblArch)

$cmbArch = New-Object System.Windows.Forms.ComboBox
$cmbArch.Location = New-Object System.Drawing.Point(60, 24)
$cmbArch.Size = New-Object System.Drawing.Size(100, 26)
$cmbArch.DropDownStyle = "DropDownList"
[void]$cmbArch.Items.AddRange(@("amd64", "arm64", "i386"))
$cmbArch.SelectedIndex = 0
$grpIso.Controls.Add($cmbArch)

$lblProf = New-Object System.Windows.Forms.Label
$lblProf.Text = "Profile:"
$lblProf.Location = New-Object System.Drawing.Point(175, 28)
$lblProf.AutoSize = $true
$grpIso.Controls.Add($lblProf)

$cmbProf = New-Object System.Windows.Forms.ComboBox
$cmbProf.Location = New-Object System.Drawing.Point(230, 24)
$cmbProf.Size = New-Object System.Drawing.Size(110, 26)
$cmbProf.DropDownStyle = "DropDownList"
[void]$cmbProf.Items.AddRange(@("full", "compact", "lite"))
$cmbProf.SelectedIndex = 0
$grpIso.Controls.Add($cmbProf)

$lblWsl = New-Object System.Windows.Forms.Label
$lblWsl.Text = "WSL Distro:"
$lblWsl.Location = New-Object System.Drawing.Point(355, 28)
$lblWsl.AutoSize = $true
$grpIso.Controls.Add($lblWsl)

$txtWsl = New-Object System.Windows.Forms.TextBox
$txtWsl.Location = New-Object System.Drawing.Point(435, 24)
$txtWsl.Size = New-Object System.Drawing.Size(120, 26)
$txtWsl.Text = "Debian13Store"
$grpIso.Controls.Add($txtWsl)

$btnBuild = New-Object System.Windows.Forms.Button
$btnBuild.Text = "Build ISO"
$btnBuild.Location = New-Object System.Drawing.Point(565, 22)
$btnBuild.Size = New-Object System.Drawing.Size(96, 30)
$grpIso.Controls.Add($btnBuild)

$lblIsoPath = New-Object System.Windows.Forms.Label
$lblIsoPath.Text = "ISO File:"
$lblIsoPath.Location = New-Object System.Drawing.Point(14, 72)
$lblIsoPath.AutoSize = $true
$grpIso.Controls.Add($lblIsoPath)

$txtIsoPath = New-Object System.Windows.Forms.TextBox
$txtIsoPath.Location = New-Object System.Drawing.Point(75, 68)
$txtIsoPath.Size = New-Object System.Drawing.Size(480, 26)
if (Test-Path $ArtifactsDir) {
    $latestIso = Get-ChildItem -Path $ArtifactsDir -Filter "LiveUSBAgent-*.iso" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latestIso) { $txtIsoPath.Text = $latestIso.FullName }
}
$grpIso.Controls.Add($txtIsoPath)

$btnBrowse = New-Object System.Windows.Forms.Button
$btnBrowse.Text = "Browse..."
$btnBrowse.Location = New-Object System.Drawing.Point(565, 66)
$btnBrowse.Size = New-Object System.Drawing.Size(96, 30)
$grpIso.Controls.Add($btnBrowse)

$lblIsoHint = New-Object System.Windows.Forms.Label
$lblIsoHint.Text = "You can build a fresh Debian 13 LiveUSB ISO via WSL or browse to any prebuilt .iso from GitHub Releases."
$lblIsoHint.Location = New-Object System.Drawing.Point(14, 110)
$lblIsoHint.Size = New-Object System.Drawing.Size(646, 48)
$lblIsoHint.ForeColor = [System.Drawing.Color]::DimGray
$grpIso.Controls.Add($lblIsoHint)

# --- Group 2: Target USB Flash Drive ---
$grpUsb = New-Object System.Windows.Forms.GroupBox
$grpUsb.Text = "2. Target Removable USB Flash Drive"
$grpUsb.Location = New-Object System.Drawing.Point(14, 198)
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
$btnFlash.Location = New-Object System.Drawing.Point(14, 380)
$btnFlash.Size = New-Object System.Drawing.Size(676, 40)
$btnFlash.Enabled = $false
$btnFlash.Font = New-Object System.Drawing.Font("Segoe UI", 10.5, [System.Drawing.FontStyle]::Bold)
$form.Controls.Add($btnFlash)

$txtLog = New-Object System.Windows.Forms.TextBox
$txtLog.Location = New-Object System.Drawing.Point(14, 430)
$txtLog.Size = New-Object System.Drawing.Size(676, 138)
$txtLog.Multiline = $true
$txtLog.ScrollBars = "Vertical"
$txtLog.ReadOnly = $true
$txtLog.Font = New-Object System.Drawing.Font("Consolas", 9)
$form.Controls.Add($txtLog)

$script:UsbDisks = @()

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
    $btnFlash.Enabled = ($hasUsb -and $hasIso -and $chkConfirm.Checked)
}

$btnRefreshUsb.Add_Click({ Refresh-UsbList })
$chkConfirm.Add_CheckedChanged({ Update-FlashEnabled })
$txtIsoPath.Add_TextChanged({ Update-FlashEnabled })

$btnBrowse.Add_Click({
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Filter = "ISO Images (*.iso)|*.iso|All Files (*.*)|*.*"
    if (Test-Path $ArtifactsDir) { $dlg.InitialDirectory = $ArtifactsDir }
    if ($dlg.ShowDialog() -eq "OK") {
        $txtIsoPath.Text = $dlg.FileName
        Append-Log "Selected ISO: $($dlg.FileName)"
    }
})

$btnBuild.Add_Click({
    $arch = [string]$cmbArch.SelectedItem
    $prof = [string]$cmbProf.SelectedItem
    $distro = $txtWsl.Text.Trim()
    if (-not $distro) { $distro = "Debian13Store" }
    $wslRepo = $RepoRoot -replace '\\', '/'
    if ($wslRepo -match '^([A-Za-z]):/(.*)$') {
        $wslRepo = "/mnt/" + $matches[1].ToLower() + "/" + $matches[2]
    }
    $outIso = Join-Path $ArtifactsDir ("LiveUSBAgent-" + $arch + "-" + $prof + ".iso")
    $wslOutIso = $outIso -replace '\\', '/'
    if ($wslOutIso -match '^([A-Za-z]):/(.*)$') {
        $wslOutIso = "/mnt/" + $matches[1].ToLower() + "/" + $matches[2]
    }
    Append-Log "Starting WSL ISO build ($arch / $prof) in distro '$distro'..."
    $bashCmd = "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; rm -rf /root/rhm-live-build; cd '$wslRepo' && bash ops/live-usb/build-iso.sh --work-dir /root/rhm-live-build --arch $arch --profile $prof --tunnel cloudflare --build && cp /root/rhm-live-build/live-image-*.iso '$wslOutIso'"
    Start-Process wsl.exe -ArgumentList @("-d", $distro, "-u", "root", "--", "bash", "-c", $bashCmd)
    $txtIsoPath.Text = $outIso
    Append-Log "WSL build window opened. Target ISO path set to: $outIso"
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

Refresh-UsbList
[void]$form.ShowDialog()
