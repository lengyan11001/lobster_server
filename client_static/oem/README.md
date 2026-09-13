# OEM branding resources

Online clients can use one generic installer. The factory only sets:

```env
LOBSTER_BRAND_MARK=0400
```

Current factory codes:

- `0100` -> `bihuo` -> 必火AI员工
- `0200` -> `daka` -> 大咖AI员工
- `0300` -> `jinghai` -> 鲸海AI员工（资源待提供）
- `0400` -> `hikong` -> 海康AI智能体

The factory starts `OEM配置启动器.exe` and enters the numeric code. It requests `/api/oem/bootstrap?code=0400`, verifies every asset and the brand-specific lightweight EXE with SHA256, writes the local code, runs `install.bat` to install dependencies and create the branded shortcut, and then starts Online. The resolved brand mark, such as `hikong`, is used for authentication and user isolation.

To add another OEM:

1. Add the brand assets under `client_static/oem/<brand_mark>/`.
2. Move the brand from `pending_brands` into a complete `brands` entry in `manifest.json`.
3. Build a lightweight launcher EXE from `desktop/launcher_stub.cs` using the OEM icon and upload it with the assets.
4. Add every downloaded file's exact byte size and SHA256 to the manifest.
5. Run `python scripts/sync_oem_start_entry.py` so the brand folder also carries the
   renamed autostart shell (see below).
6. Restart the server so the brand is seeded into `brand_configs`.

## Unified autostart entry

Every brand folder carries `start.exe` next to `client_launcher.exe`: a byte-identical
copy of the same lightweight shell. The factory wires boot autostart at that one fixed
file name, so each OEM keeps its own branded desktop EXE while the autostart path stays
identical everywhere.

- `/api/oem/bootstrap` appends the copy to `assets` with the key `start_entry`, but only
  while it still matches this brand's `launcher_exe` byte for byte. A stale copy is
  therefore skipped instead of shipping a different brand icon.
- `python scripts/sync_oem_start_entry.py` refreshes the copies (run it after replacing a
  `client_launcher.exe`); `--check` verifies without writing and the server test suite
  asserts the copies stay in sync.

The brand mark must match `^[a-z][a-z0-9_-]{0,62}$`. The factory code can contain 4 to 12 digits. Asset URLs must use `/client/oem/` and are downloaded only from the configured server origin.

The generic EXE's embedded Explorer icon cannot be changed after it is signed. The runtime window, taskbar, loading screen, Online page, and generated desktop shortcut use the downloaded OEM resources.
