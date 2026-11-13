param([string]$Action = "release", [string]$Summary = "")
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$code = -join ((1..6) | ForEach-Object { Get-Random -Minimum 0 -Maximum 10 })

$form = New-Object System.Windows.Forms.Form
$form.Text = "Lobster " + $Action + " confirm"
$form.Size = New-Object System.Drawing.Size(780, 620)
$form.StartPosition = "CenterScreen"
$form.TopMost = $true
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false

$summaryBox = New-Object System.Windows.Forms.TextBox
$summaryBox.Left = 14
$summaryBox.Top = 14
$summaryBox.Width = 735
$summaryBox.Height = 370
$summaryBox.Multiline = $true
$summaryBox.ReadOnly = $true
$summaryBox.ScrollBars = "Vertical"
$summaryBox.Font = New-Object System.Drawing.Font("Consolas", 9)
$summaryBox.Text = "即将发布：`r`n" + $Summary
$form.Controls.Add($summaryBox)

$label = New-Object System.Windows.Forms.Label
$label.Left = 14
$label.Top = 395
$label.Width = 735
$label.Height = 90
$label.TextAlign = "MiddleCenter"
$label.Text = ("Action: " + $Action + [Environment]::NewLine + "Code: " + $code + [Environment]::NewLine + "确认上面内容无误后，输入相同验证码继续（120 秒后自动取消）")
$form.Controls.Add($label)

$box = New-Object System.Windows.Forms.TextBox
$box.Top = 495
$box.Left = 200
$box.Width = 360
$form.Controls.Add($box)

$ok = New-Object System.Windows.Forms.Button
$ok.Text = "Confirm"
$ok.Top = 535
$ok.Left = 400
$ok.Width = 150
$ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
$form.Controls.Add($ok)
$form.AcceptButton = $ok

$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = "Cancel"
$cancel.Top = 535
$cancel.Left = 230
$cancel.Width = 150
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
