"""与界面无关的更新信任边界、版本选择和可恢复会话。"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from urllib.parse import unquote, urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from packaging.version import InvalidVersion, Version

from core.app_version import APP_VERSION, REPOSITORY, UPDATE_CLIENT_VERSION
from core.storage_backup import write_json_atomic

TRUSTED_KEYS: dict[str, str] = {
    "b5d5c93383dc6eaa": "LQn7FVU+93EmCcGbqRT0YTZd2+eT0FYFve4iS0phkyI=",
}
MAX_MANIFEST_BYTES = 64 * 1024
MAX_INSTALLER_BYTES = 2 * 1024**3 - 1


class UpdateError(RuntimeError):
    """用户可见、可重试的更新错误。"""


class UnsupportedUpdatePlatform(UpdateError):
    """清单签名及协议有效，但面向其他平台；在线检查可继续下一候选。"""


class UpdateState(str, Enum):
    IDLE = "idle"
    CHECKING = "checking"
    AVAILABLE = "available"
    DOWNLOADING = "downloading"
    READY = "ready"
    PREPARING = "preparing"
    FAILED = "failed"


def version(value: str) -> Version:
    if not isinstance(value, str) or not re.fullmatch(r"v?\d+\.\d+\.\d+(?:-(?:rc|alpha|beta)\d+)?", value):
        raise UpdateError("版本号格式不受支持")
    try:
        return Version(value.removeprefix("v"))
    except InvalidVersion as exc:
        raise UpdateError("版本号无效") from exc


def validate_url(url: str, *, api: bool = False) -> str:
    """每次重定向都重新校验，不允许用户名、任意仓库或 HTTP。"""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise UpdateError("更新地址必须是官方 HTTPS 地址")
    host = (parsed.hostname or "").lower()
    path = unquote(parsed.path)
    if any(segment in ("..", ".") for segment in path.split("/")) or "\\" in path:
        raise UpdateError("更新地址路径无效")
    if host == "api.github.com" and api and path.startswith(f"/repos/{REPOSITORY}/releases"):
        return url
    if host == "github.com" and path.startswith(f"/{REPOSITORY}/releases/download/"):
        return url
    if host in {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}:
        return url
    raise UpdateError("更新地址不属于官方下载源")


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    channel: str
    published_at: str
    notes: str
    url: str
    filename: str
    size: int
    sha256: str
    min_client_version: str
    database_min: int
    database_max: int

    @classmethod
    def from_signed(cls, raw: bytes, signature: bytes, *, keys=None) -> "ReleaseInfo":
        if len(raw) > MAX_MANIFEST_BYTES or len(signature) != 64:
            raise UpdateError("更新清单或签名大小无效")
        # 签名先于 JSON 解析验证；禁止根据不可信 JSON 指定密钥或下载地址。
        valid = False
        for encoded in (TRUSTED_KEYS if keys is None else keys).values():
            try:
                Ed25519PublicKey.from_public_bytes(base64.b64decode(encoded, validate=True)).verify(signature, raw)
                valid = True
                break
            except (InvalidSignature, ValueError):
                continue
        if not valid:
            raise UpdateError("更新清单签名验证失败，请重新下载官方更新包")
        try:
            data = json.loads(raw)
            if data["format"] != "video-organizer-update" or data["schema"] != 1:
                raise ValueError("格式不匹配")
            if not isinstance(data["platform"], str) or not data["platform"]:
                raise ValueError("平台格式无效")
            if data["platform"] != "windows-x64":
                raise UnsupportedUpdatePlatform("更新清单面向其他平台，不适用于 Windows x64")
            release = cls(**{field: data[field] for field in cls.__dataclass_fields__})
            parsed_version = version(release.version)
            if release.channel not in ("stable", "preview") or (release.channel == "stable" and parsed_version.is_prerelease):
                raise ValueError("渠道与版本不匹配")
            expected = f"VideoOrganizer-v{release.version}-setup-win-x64.exe"
            if release.filename != expected:
                raise ValueError("安装包名称不匹配")
            if release.url != f"https://github.com/{REPOSITORY}/releases/download/v{release.version}/{expected}":
                raise ValueError("安装包地址与版本不一致")
            validate_url(release.url)
            if type(release.size) is not int or not 0 < release.size <= MAX_INSTALLER_BYTES:
                raise ValueError("安装包大小无效")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", release.sha256):
                raise ValueError("摘要格式无效")
            if not isinstance(release.notes, str) or not isinstance(release.published_at, str):
                raise ValueError("更新说明格式无效")
            if Version(release.min_client_version) > Version(UPDATE_CLIENT_VERSION):
                raise ValueError("请先从官网下载新版更新客户端")
            if type(release.database_min) is not int or type(release.database_max) is not int or not 0 <= release.database_min <= release.database_max:
                raise ValueError("数据兼容范围无效")
            return release
        except (KeyError, TypeError, ValueError, InvalidVersion) as exc:
            raise UpdateError(f"更新清单不可用：{exc}") from exc


def release_candidates(releases: list, *, preview=False, current=APP_VERSION) -> list[dict]:
    result = []
    for release in releases:
        if not isinstance(release, dict):
            continue
        try:
            candidate = version(release["tag_name"])
            if release.get("draft") or candidate <= version(current):
                continue
            if not preview and (release.get("prerelease") or candidate.is_prerelease):
                continue
            assets = {item["name"]: item["browser_download_url"] for item in release.get("assets", [])}
            expected = f"VideoOrganizer-v{release['tag_name'].removeprefix('v')}-setup-win-x64.exe"
            if not {"update.json", "update.json.sig", expected} <= assets.keys():
                continue
            prefix = f"https://github.com/{REPOSITORY}/releases/download/{release['tag_name']}/"
            if any(assets[name] != prefix + name for name in ("update.json", "update.json.sig", expected)):
                continue
            result.append(release)
        except (KeyError, TypeError, UpdateError):
            continue
    return sorted(result, key=lambda item: version(item["tag_name"]), reverse=True)


def read_signed_release(directory: Path):
    manifest = directory / "update.json"
    signature = directory / "update.json.sig"
    if manifest.stat().st_size > MAX_MANIFEST_BYTES or signature.stat().st_size != 64:
        raise UpdateError("更新清单或签名大小无效")
    raw, sig = manifest.read_bytes(), signature.read_bytes()
    return ReleaseInfo.from_signed(raw, sig), raw, sig


def verify_installer(path: Path, release: ReleaseInfo) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != release.size:
        raise UpdateError("安装包缺失或大小不符，请重新下载")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest().lower() != release.sha256.lower():
        raise UpdateError("安装包 SHA-256 不匹配，已禁止安装")


def require_space(directory: Path, size: int) -> None:
    if shutil.disk_usage(directory).free < size:
        raise UpdateError("磁盘可用空间不足，请释放空间后重试")


def application_directory() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


def update_cache(app_dir: Path | None = None) -> Path:
    app_dir = app_dir or application_directory()
    key = hashlib.sha256(os.path.normcase(str(app_dir.resolve())).encode()).hexdigest()[:16]
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".cache"))) / "VideoOrganizerPro" / key / "updates"
    return root


@dataclass
class UpdateSession:
    target_version: str
    previous_version: str
    application_directory: str
    data_root: str
    database_path: str
    backup_path: str
    locator_files: dict
    phase: str = "prepared"
    message: str = ""

    @property
    def is_pending(self) -> bool:
        # incomplete 表示旧版已经成功打开原库并报告了未完成结果，属于终态。
        # 不能让历史记录继续限制用户之后主动选择、搬迁或恢复数据目录。
        return self.phase in {"prepared", "launched"}

    def save(self, cache: Path) -> None:
        write_json_atomic(str(cache / "session.json"), asdict(self))

    @classmethod
    def load(cls, cache: Path):
        path = cache / "session.json"
        if not path.exists():
            return None
        try:
            session = cls(**json.loads(path.read_text(encoding="utf-8")))
            if session.phase not in {"prepared", "launched", "incomplete", "completed"}:
                raise ValueError("未知更新阶段")
            return session
        except (ValueError, TypeError) as exc:
            raise UpdateError("更新记录损坏；请保留数据目录并查看恢复指引") from exc

    def validate_data_root(self, actual_root: str) -> None:
        same = os.path.normcase(os.path.realpath(actual_root)) == os.path.normcase(os.path.realpath(self.data_root))
        if not same or not Path(self.database_path).is_file():
            raise UpdateError(f"更新前的数据目录不可用，已阻止创建空素材库。\n原目录：{self.data_root}\n备份：{self.backup_path}")

    def reconcile(self, current: str, actual_root: str) -> str:
        self.validate_data_root(actual_root)
        if self.phase == "completed":
            return ""
        if version(current) == version(self.target_version):
            self.phase = "completed"
            self.message = f"更新完成：{current}，已确认使用原素材库。"
        else:
            self.phase = "incomplete"
            self.message = "上次更新未完成或已取消，原数据和更新前备份已保留。"
        return self.message
