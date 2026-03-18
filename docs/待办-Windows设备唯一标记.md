# 待办：Windows 设备出厂唯一标记

**需求**：出厂时给设备一个唯一标记，我们的系统能读取并做匹配（设备绑定/鉴权等）。

**可选方案**（后续实现时选用）：

1. **SMBIOS UUID**：主板 UUID，部分厂商出厂写入。读取：WMI `Win32_ComputerSystemProduct.UUID`、`wmic csproduct get uuid`。
2. **序列号 / Service Tag**：Dell/HP 等序列号，WMI `Win32_BIOS.SerialNumber`、`Win32_SystemEnclosure`。
3. **TPM**：芯片级唯一，TPM 2.0 API 读取。
4. **产线写入**：SMBIOS OEM 字符串、EFI 变量或预置文件（如 `C:\ProgramData\YourProduct\device_id`），我们的客户端读取后上报。

**我们侧**：客户端读取上述之一（或组合）→ 哈希得到 `device_id` → 请求时带 `device_id`；后端存库并与账号/许可绑定，后续请求校验匹配。

（本文档仅作记录，具体实现后续再做。）
