import importlib.util
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "publish_client_code_ota_to_server.py"


def _load_publish_module():
    spec = importlib.util.spec_from_file_location("publish_client_code_ota_to_server", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_manifest_paths_only_include_files_present_in_ota(tmp_path):
    module = _load_publish_module()
    zip_path = tmp_path / "client-ota.zip"
    expected_paths = [path for path in module.DEFAULT_CLIENT_CODE_OTA_PATHS if path != "CLIENT_CODE_VERSION.json"]

    with zipfile.ZipFile(zip_path, "w") as archive:
        for path in expected_paths:
            if "." in Path(path).name:
                archive.writestr(path, "test")
            else:
                archive.writestr(f"{path}/placeholder.txt", "test")
        archive.writestr("CLIENT_CODE_VERSION.json", "{}")

    paths = module.manifest_paths_for_zip(zip_path)

    assert ".env" not in paths
    assert "必火智能AI.exe" not in paths
    with zipfile.ZipFile(zip_path) as archive:
        names = {name.rstrip("/") for name in archive.namelist()}
    for path in paths:
        normalized = path.replace("\\", "/").rstrip("/")
        assert normalized in names or any(name.startswith(normalized + "/") for name in names)
