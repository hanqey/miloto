

import io
import json
import os
import re
import shutil
import sys
import zipfile
from urllib.request import Request, ProxyHandler, build_opener

from core.version import MILOTO_VERSION

BACKUP_DIRNAME = ".miloto_backup"
STAGING_DIRNAME = ".miloto_update_staging"
PENDING_MARKER = ".miloto_update_pending.json"

def running_directory() -> str:

    return os.path.dirname(os.path.abspath(sys.argv[0]))

def manifest_url(settings) -> str:

    repo = (settings.get("updater.repo") or "hanqey/miloto").strip()
    branch = (settings.get("updater.branch") or "main").strip()
    mirror = (settings.get("updater.mirror") or "").strip()
    if mirror:
        return f"{mirror.rstrip('/')}/{repo}/{branch}/upjson/version.json"
    return f"https://raw.githubusercontent.com/{repo}/{branch}/upjson/version.json"

def proxy_address(settings) -> str:

    if settings is not None:
        configured = (settings.get("updater.proxy") or "").strip()
        if configured:
            return configured
    return (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "").strip()

def make_opener(settings):

    proxy = proxy_address(settings)
    if proxy:
        return build_opener(ProxyHandler({"https": proxy, "http": proxy}))
    return build_opener()

def fetch_version_manifest(settings, timeout: int = 15) -> list:

    url = manifest_url(settings)
    request = Request(url, headers={"User-Agent": "Miloto"})
    opener = make_opener(settings)
    with opener.open(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))

    versions = []
    for entry in data.get("versions", []) or []:
        tag = entry.get("tag") or ""
        version = entry.get("version") or tag.lstrip("vV")
        if not version:
            continue
        versions.append({
            "version": version,
            "tag": tag or f"v{version}",
            "name": entry.get("name") or version,
            "updated_at": entry.get("updated_at", ""),
            "notes": entry.get("notes", ""),
            "download_url": entry.get("download_url") or "",
        })
    return versions

def compare_versions(current: str, target: str) -> bool:

    def _tuple(version: str):
        return tuple(int(part) for part in re.findall(r"\d+", version))

    try:
        return _tuple(target) > _tuple(current)
    except Exception:
        return False

def download_archive(download_url: str, dest_path: str, settings=None, timeout: int = 120) -> None:

    request = Request(download_url, headers={"User-Agent": "Miloto"})

    opener = make_opener(settings)
    with opener.open(request, timeout=timeout) as response, open(dest_path, "wb") as out:
        shutil.copyfileobj(response, out)

def _find_content_root(extract_to: str) -> str:

    entries = [name for name in os.listdir(extract_to) if not name.startswith(".")]
    if len(entries) == 1 and os.path.isdir(os.path.join(extract_to, entries[0])):
        return os.path.join(extract_to, entries[0])
    return extract_to

def _write_pending(run_dir: str, content_root: str, target_version: str, old_version: str) -> None:

    marker = {
        "target_version": target_version,
        "content_root": content_root,
        "old_version": old_version,
    }
    marker_path = os.path.join(run_dir, PENDING_MARKER)
    with open(marker_path, "w", encoding="utf-8") as fh:
        json.dump(marker, fh, ensure_ascii=False, indent=2)

def _stage_version(settings, chosen: dict) -> tuple:

    run_dir = running_directory()
    version = chosen["version"]

    staging_root = os.path.join(run_dir, STAGING_DIRNAME)
    if os.path.exists(staging_root):
        shutil.rmtree(staging_root)
    os.makedirs(staging_root, exist_ok=True)

    archive_path = os.path.join(staging_root, "update.zip")
    download_archive(chosen["download_url"], archive_path, settings)

    extract_to = os.path.join(staging_root, "extracted")
    os.makedirs(extract_to, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        archive.extractall(extract_to)

    content_root = _find_content_root(extract_to)
    _write_pending(run_dir, content_root, version, MILOTO_VERSION)
    return True, f"已下载目标版本 v{version}，重启后生效。", version

def prepare_update(settings, target_tag: str):

    versions = fetch_version_manifest(settings)
    normalized = target_tag.lstrip("vV")
    chosen = next(
        (v for v in versions if v["tag"] == target_tag or v["version"] == normalized),
        None,
    )
    if not chosen or not chosen["download_url"]:
        return False, f"找不到版本 {target_tag} 的下载包。", None
    return _stage_version(settings, chosen)

def prepare_rollback(settings, target_tag: str):

    version = target_tag.lstrip("vV")
    run_dir = running_directory()
    backup_dir = os.path.join(run_dir, BACKUP_DIRNAME, f"v{version}")
    if os.path.isdir(backup_dir):
        _write_pending(run_dir, backup_dir, version, MILOTO_VERSION)
        return True, f"已就绪回退到 v{version}（本地备份），重启后生效。", version

    versions = fetch_version_manifest(settings)
    chosen = next(
        (v for v in versions if v["tag"] == target_tag or v["version"] == version),
        None,
    )
    if not chosen or not chosen["download_url"]:
        return False, f"没有 v{version} 的本地备份，且远程清单中也没有该版本。", None
    ok, message, target_version = _stage_version(settings, chosen)
    if ok:
        return True, f"已下载并就绪回退到 v{version}（远程），重启后生效。", target_version
    return ok, message, target_version

def apply_pending_update(run_dir: str):

    marker_path = os.path.join(run_dir, PENDING_MARKER)
    if not os.path.exists(marker_path):
        return False, "没有待应用的更新。"

    try:
        with open(marker_path, "r", encoding="utf-8") as fh:
            marker = json.load(fh)
        content_root = marker["content_root"]
        target_version = marker["target_version"]
        old_version = marker.get("old_version") or "unknown"

        backup_dir = os.path.join(run_dir, BACKUP_DIRNAME, f"v{old_version}")
        _backup_current(run_dir, backup_dir)

        _copy_tree(content_root, run_dir)

        staging_root = os.path.join(run_dir, STAGING_DIRNAME)
        if os.path.exists(staging_root):
            shutil.rmtree(staging_root)
        os.remove(marker_path)
        return True, f"已更新到目标版本 v{target_version}"
    except Exception as error:
        return False, f"应用更新失败：{error}"

def list_backups(run_dir: str = None) -> list:

    run_dir = run_dir or running_directory()
    backup_root = os.path.join(run_dir, BACKUP_DIRNAME)
    if not os.path.isdir(backup_root):
        return []
    return sorted(
        name[1:] for name in os.listdir(backup_root)
        if name.startswith("v") and os.path.isdir(os.path.join(backup_root, name))
    )

def _backup_current(run_dir: str, backup_dir: str) -> None:

    skip = {
        BACKUP_DIRNAME, STAGING_DIRNAME, "attachments", "files",
        "__pycache__", ".git", "venv", ".venv", "node_modules", "build", "dist",
    }
    if os.path.exists(backup_dir):
        shutil.rmtree(backup_dir)
    os.makedirs(backup_dir, exist_ok=True)
    for name in os.listdir(run_dir):
        if name in skip or name.startswith("."):
            continue
        source = os.path.join(run_dir, name)
        dest = os.path.join(backup_dir, name)
        try:
            if os.path.isdir(source):
                shutil.copytree(source, dest)
            else:
                shutil.copy2(source, dest)
        except Exception:

            pass

def _copy_tree(source: str, dest: str) -> None:

    for name in os.listdir(source):
        source_path = os.path.join(source, name)
        dest_path = os.path.join(dest, name)
        if os.path.isdir(source_path):
            if not os.path.exists(dest_path):
                os.makedirs(dest_path, exist_ok=True)
            _copy_tree(source_path, dest_path)
        else:
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(source_path, dest_path)
