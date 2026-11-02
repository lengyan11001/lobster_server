param([string]$Action = "release")
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$code = -join ((1..6) | ForEach-Object { Get-Random -Minimum 0 -Maximum 10 })

$form = New-Object System.Windows.Forms.Form
$form.Text = "Lobster " + $Action + " confirm"
$form.Size = New-Object System.Drawing.Size(460, 230)
$form.StartPosition = "CenterScreen"
$form.TopMost = $true
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false

$label = New-Object System.Windows.Forms.Label
$label.Dock = "Top"
$label.Height = 120
$label.TextAlign = "MiddleCenter"
$label.Text = ("Action: " + $Action + [Environment]::NewLine + [Environment]::NewLine + "Code: " + $code + [Environment]::NewLine + [Environment]::NewLine + "Type the same code to continue (auto-cancel in 120s)")
$form.Controls.Add($label)

$box = New-Object System.Windows.Forms.TextBox
$box.Top = 125
$box.Left = 60
$box.Width = 330
$form.Controls.Add($box)

$ok = New-Object System.Windows.Forms.Button
$ok.Text = "Confirm"
$ok.Top = 160
$ok.Left = 230
$ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
$form.Controls.Add($ok)
$form.AcceptButton = $ok

$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = "Cancel"
$cancel.Top = 160
$cancel.Left = 130
$cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.Controls.Add($cancel)
$form.CancelButton = $cancel

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 120000
$timer.Add_Tick({ $form.DialogResult = [System.Windows.Forms.DialogResult]::Cancel; $form.Close() })
$timer.Start()

$result = $form.ShowDialog()
$timer.Stop()

if ($result -ne [System.Windows.Forms.DialogResult]::OK) { Write-Host "cancelled"; exit 2 }
if ($box.Text.Trim() -ne $code) { Write-Host "code mismatch"; exit 3 }
Write-Host "confirmed"
exit 0