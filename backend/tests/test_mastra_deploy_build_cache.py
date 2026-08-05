from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_mastra_if_needed.sh"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
def test_mastra_build_is_reused_until_source_changes(tmp_path: Path):
    root = tmp_path / "server"
    mastra = root / "mastra_server"
    runtime_bin = root / ".runtime" / "node" / "bin"
    scripts = root / "scripts"
    mastra.mkdir(parents=True)
    runtime_bin.mkdir(parents=True)
    scripts.mkdir(parents=True)
    (mastra / "src.ts").write_text("export const value = 1;\n", encoding="utf-8")
    (mastra / "package.json").write_text('{"name":"test"}\n', encoding="utf-8")
    shutil.copy2(SCRIPT, scripts / SCRIPT.name)

    log = root / "npm.log"
    installer = scripts / "install_mastra_runtime.sh"
    installer.write_text("#!/usr/bin/env bash\nset -e\nexit 0\n", encoding="utf-8")
    npm = runtime_bin / "npm"
    npm.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> '{log.as_posix()}'\n"
        f"if [ \"${{@: -1}}\" = build ]; then mkdir -p '{(mastra / '.mastra/output').as_posix()}'; echo built > '{(mastra / '.mastra/output/index.mjs').as_posix()}'; fi\n",
        encoding="utf-8",
    )
    node = runtime_bin / "node"
    node.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    for path in (installer, npm, node, scripts / SCRIPT.name):
        path.chmod(path.stat().st_mode | 0o111)

    env = dict(os.environ)
    first = subprocess.run(["bash", str(scripts / SCRIPT.name), str(root)], env=env, text=True, capture_output=True, check=True)
    first_lines = log.read_text(encoding="utf-8").splitlines()
    assert len(first_lines) == 3
    assert "执行完整构建" in first.stdout

    second = subprocess.run(["bash", str(scripts / SCRIPT.name), str(root)], env=env, text=True, capture_output=True, check=True)
    assert log.read_text(encoding="utf-8").splitlines() == first_lines
    assert "复用现有构建产物" in second.stdout

    (mastra / "src.ts").write_text("export const value = 2;\n", encoding="utf-8")
    third = subprocess.run(["bash", str(scripts / SCRIPT.name), str(root)], env=env, text=True, capture_output=True, check=True)
    assert len(log.read_text(encoding="utf-8").splitlines()) == 6
    assert "源码或构建依赖已变化" in third.stdout
