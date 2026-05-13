from __future__ import annotations

import logging
import os
import re
import shutil
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, TextIO


DEFAULT_RETENTION_DAYS = 3
_LOG_LINE_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T]")
_DATE_IN_NAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


class DailyFileHandler(logging.FileHandler):
    """A small date-based file handler using names like app-2026-05-13.log."""

    def __init__(self, project_root: str | Path, stem: str, encoding: str = "utf-8"):
        self.project_root = Path(project_root).resolve()
        self.stem = stem
        self.current_date = datetime.now().date()
        self.project_root.joinpath("logs").mkdir(parents=True, exist_ok=True)
        super().__init__(dated_log_path(self.project_root, self.stem), mode="a", encoding=encoding)
        _ensure_alias(self.project_root / "logs", self.stem, Path(self.baseFilename))

    def emit(self, record: logging.LogRecord) -> None:
        today = datetime.now().date()
        if today != self.current_date:
            self.current_date = today
            self.acquire()
            try:
                if self.stream:
                    self.flush()
                    self.stream.close()
                    self.stream = None
                self.baseFilename = str(dated_log_path(self.project_root, self.stem))
                self.stream = self._open()
                _ensure_alias(self.project_root / "logs", self.stem, Path(self.baseFilename))
                cleanup_log_files(self.project_root)
            finally:
                self.release()
        super().emit(record)


def _int_env(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, value)


def log_retention_days() -> int:
    return _int_env("LOBSTER_LOG_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)


def diagnostics_retention_days() -> int:
    return _int_env("LOBSTER_DIAGNOSTICS_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)


def _retention_cutoff_date(days: int, now: Optional[datetime] = None) -> date:
    today = (now or datetime.now()).date()
    return today - timedelta(days=max(1, days) - 1)


def _retention_cutoff_ts(days: int, now_ts: Optional[float] = None) -> float:
    return (time.time() if now_ts is None else now_ts) - max(1, days) * 86400


def dated_log_path(project_root: str | Path, stem: str, now: Optional[datetime] = None) -> Path:
    current = now or datetime.now()
    return Path(project_root).resolve() / "logs" / f"{stem}-{current.strftime('%Y-%m-%d')}.log"


def _parse_date_prefix(line: str) -> Optional[date]:
    m = _LOG_LINE_DATE_RE.match(line.lstrip("\ufeff"))
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def migrate_legacy_log(project_root: str | Path, stem: str, retention_days: Optional[int] = None) -> None:
    """Split old logs/<stem>.log into dated files, keeping only the retention window."""
    days = retention_days or log_retention_days()
    log_dir = Path(project_root).resolve() / "logs"
    legacy = log_dir / f"{stem}.log"
    if not legacy.exists() or legacy.is_symlink():
        return

    tmp = log_dir / f".{stem}.legacy-migrating-{datetime.now().strftime('%Y%m%d%H%M%S')}-{os.getpid()}.log"
    try:
        legacy.replace(tmp)
    except OSError:
        return

    cutoff = _retention_cutoff_date(days)
    outputs: Dict[date, TextIO] = {}
    current_date: Optional[date] = None
    try:
        with tmp.open("r", encoding="utf-8", errors="replace") as src:
            for line in src:
                line_date = _parse_date_prefix(line)
                if line_date is not None:
                    current_date = line_date if line_date >= cutoff else None
                if current_date is None:
                    continue
                out = outputs.get(current_date)
                if out is None:
                    out_path = log_dir / f"{stem}-{current_date.isoformat()}.log"
                    out = out_path.open("a", encoding="utf-8")
                    outputs[current_date] = out
                out.write(line)
    finally:
        for out in outputs.values():
            try:
                out.close()
            except OSError:
                pass
        try:
            tmp.unlink()
        except OSError:
            pass


def cleanup_log_files(project_root: str | Path, retention_days: Optional[int] = None) -> None:
    days = retention_days or log_retention_days()
    log_dir = Path(project_root).resolve() / "logs"
    if not log_dir.exists():
        return
    cutoff_date = _retention_cutoff_date(days)
    cutoff_ts = _retention_cutoff_ts(days)
    for path in log_dir.iterdir():
        try:
            if not path.is_file() and not path.is_symlink():
                continue
            if path.is_symlink():
                continue
            name = path.name
            m = _DATE_IN_NAME_RE.search(name)
            if m:
                try:
                    file_date = date.fromisoformat(m.group(1))
                except ValueError:
                    file_date = None
                if file_date is not None and file_date < cutoff_date:
                    path.unlink(missing_ok=True)
                continue
            if name.endswith(".log") and path.stat().st_mtime < cutoff_ts:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def _ensure_alias(log_dir: Path, stem: str, current_path: Path) -> None:
    alias = log_dir / f"{stem}.log"
    try:
        if alias.is_symlink():
            try:
                if os.readlink(alias) == current_path.name:
                    return
            except OSError:
                pass
            alias.unlink(missing_ok=True)
        if alias.exists():
            return
        os.symlink(current_path.name, alias)
    except OSError:
        # Windows without symlink privilege still writes the dated file; /api/logs
        # reads the dated path directly.
        return


def _has_file_handler(path: Path) -> bool:
    target = str(path.resolve())
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.FileHandler):
            try:
                if str(Path(handler.baseFilename).resolve()) == target:
                    return True
            except OSError:
                continue
    return False


def configure_daily_file_logging(project_root: str | Path, stem: str, level: int) -> Path:
    root = Path(project_root).resolve()
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.getLogger().setLevel(level)
    migrate_legacy_log(root, stem)
    cleanup_log_files(root)
    current_path = dated_log_path(root, stem)
    if not _has_file_handler(current_path):
        handler = DailyFileHandler(root, stem, encoding="utf-8")
        handler.setLevel(level)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logging.getLogger().addHandler(handler)
    _ensure_alias(log_dir, stem, current_path)
    return current_path


def current_log_path(project_root: str | Path, stem: str = "app") -> Path:
    root = Path(project_root).resolve()
    current = dated_log_path(root, stem)
    if current.exists():
        return current
    legacy = root / "logs" / f"{stem}.log"
    return legacy if legacy.exists() else current


def _diagnostic_uploaded_ts(path: Path) -> float:
    meta = path / "metadata.json"
    if meta.exists():
        try:
            import json

            raw = json.loads(meta.read_text(encoding="utf-8"))
            uploaded_at = str(raw.get("uploaded_at") or "").strip()
            if uploaded_at:
                return datetime.fromisoformat(uploaded_at.replace("Z", "+00:00")).timestamp()
        except Exception:
            pass
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def cleanup_diagnostics_uploads(project_root: str | Path, retention_days: Optional[int] = None) -> None:
    days = retention_days or diagnostics_retention_days()
    root = Path(project_root).resolve()
    base = root / "diagnostics_uploads"
    if not base.exists():
        return
    cutoff_ts = _retention_cutoff_ts(days)
    for user_dir in base.glob("user_*"):
        if not user_dir.is_dir():
            continue
        for item in user_dir.iterdir():
            try:
                if item.is_dir():
                    if _diagnostic_uploaded_ts(item) < cutoff_ts:
                        shutil.rmtree(item, ignore_errors=True)
                elif item.stat().st_mtime < cutoff_ts:
                    item.unlink(missing_ok=True)
            except OSError:
                continue
        try:
            user_dir.rmdir()
        except OSError:
            pass
