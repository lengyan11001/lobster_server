<#
发布/部署人工确认闸门（GUI）。只有人在弹窗里输入同一串随机码才会返回 0。

为什么不用环境变量：环境变量是我（AI）自己就能设的，等于没有闸门。
这个弹窗必须由坐在电脑前的人点击/输入，AI 无法代替，所以才是真闸门。
#>
param([string]$Action = "发布")

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$code = -join ((1..6) | ForEach-Object { Get-Random -Minimum 0 -Maximum 10 })

$form = New-Object System.Windows.Forms.Form
$form.Text = "Lobster $Action 确认"
$form.Size = New-Object System.Drawing.Size(460, 220)
$form.StartPosition = "CenterScreen"
$form.TopMost = $true
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false

$label = New-Object System.Windows.Forms.Label
$label.Text = "即将执行：$Action`n`n确认码：$code`n`n请在下面输入同一串数字（120 秒内不操作视为取消）"
$label.AutoSize = $false
$label.Dock = "Top"
$label.Height = 110
$label.TextAlign = "MiddleCenter"
$form.Controls.Add($label)

$box = New-Object System.Windows.Forms.TextBox
$box.Top = 115
$box.Left = 40
$box.Width = 360
$form.Controls.Add($box)

$ok = New-Object System.Windows.Forms.Button
$ok.Text = "确认$Action"
$ok.Top = 150
$ok.Left = 220
$ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
$form.Controls.Add($ok)
$form.AcceptButton = $ok

$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = "取消"
$cancel.Top = 150
$cancel.Left = 120
$cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$form.Controls.Add($cancel)
$form.CancelButton = $cancel

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 120000
$timer.Add_Tick({ $form.DialogResult = [System.Windows.Forms.DialogResult]::Cancel; $form.Close() })
$timer.Start()

$result = $form.ShowDialog()
$timer.Stop()

if ($result -ne [System.Windows.Forms.DialogResult]::OK) { Write-Host "已取消"; exit 2 }
if ($box.Text.Trim() -ne "$code") { Write-Host "确认码不匹配"; exit 3 }
Write-Host "确认通过"
exit 0