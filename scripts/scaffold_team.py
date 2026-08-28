#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为项目创建多会话协作层(按部门组织,三层框架:管理 / 执行 / 审核)。"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

if os.name == "nt":
    import msvcrt
else:
    import fcntl


UTF8_BOOTSTRAP_MARKER = "AGENT_TEAM_UTF8_BOOTSTRAPPED"
PROTOCOL_VERSION = "1.5.1"
PROTOCOL_FILE = "协议版本.json"
ADD_TRANSACTION_FILE = ".add-roles-transaction.json"
DEACTIVATE_TRANSACTION_FILE = ".deactivate-roles-transaction.json"
UPGRADE_TRANSACTION_FILE = ".upgrade-transaction.json"
LEGACY_ARCHIVE_RECOVERY_FILE = "legacy-archive-recovery.json"
LEGACY_CLOSEOUT_INDEX_FILE = "legacy-closeout-index.json"
ADD_ROLE_SENTINEL = ".agent-team-add-role-owner.json"
ROLE_POLICY_OVERRIDE_FIELD = "role_policy_overlays"
THREAD_ID_MAX_CHARS = 300
ACTOR_MAX_CHARS = 400
ROLLBACK_HANDOFF_HOT_BLOCK_START = "<!-- agent-team current-slice:start -->"
ROLLBACK_HANDOFF_HOT_BLOCK_END = "<!-- agent-team current-slice:end -->"
ROLE_CONTRACT_VERSIONS = {"product": "product-dev-2", "dev": "product-dev-2"}
ROLE_OVERLAY_SECTIONS = {
    "mission": "负责什么", "not_responsible": "不负责什么", "inputs": "输入",
    "outputs": "输出", "can_write": "可写范围", "cannot_write": "禁止写入", "confirm": "确认节点",
}


def ensure_utf8_filesystem_runtime() -> None:
    """Restart once in UTF-8 mode when the process filesystem codec is ASCII."""
    encoding = (sys.getfilesystemencoding() or "").lower().replace("_", "-")
    if encoding not in {"ascii", "us-ascii", "ansi-x3.4-1968"}:
        return
    if os.environ.get(UTF8_BOOTSTRAP_MARKER) == "1":
        raise SystemExit("无法启用 UTF-8 文件系统编码,已停止以避免生成不完整协作层。")

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env[UTF8_BOOTSTRAP_MARKER] = "1"
    try:
        os.execve(sys.executable, [sys.executable, *sys.argv], env)
    except OSError as exc:
        raise SystemExit(f"无法以 UTF-8 模式重启脚手架: {exc}") from exc


ensure_utf8_filesystem_runtime()


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def reject_duplicate_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject ambiguous JSON instead of silently accepting the last duplicate key."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 含重复键: {key}")
        result[key] = value
    return result


def strict_json_loads(text: str, *, source: str) -> object:
    try:
        return json.loads(text, object_pairs_hook=reject_duplicate_json_pairs)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source} JSON 无效: {exc}") from exc


def archive_receipt_fields(evidence: str) -> dict[str, set[str]]:
    fields: dict[str, set[str]] = {}
    for token in evidence.split():
        key, separator, value = token.partition("=")
        if separator and key and value and not value.startswith("="):
            fields.setdefault(key.casefold(), set()).add(value)
    return fields


def valid_archive_receipt(evidence: str, thread_id: str) -> bool:
    fields = archive_receipt_fields(evidence)
    return (
        fields.get("thread_id") == {thread_id}
        and {value.casefold() for value in fields.get("archived", set())} == {"true"}
        and bool(fields.get("host") or fields.get("user_confirmation"))
    )


class CollaborationBusyError(RuntimeError):
    pass


@contextmanager
def project_lock(target: Path):
    """Use an OS-managed lock so concurrent scaffold transactions cannot overwrite each other."""
    try:
        temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
    except OSError as exc:
        raise CollaborationBusyError(f"无法解析当前可写临时目录: {exc}") from exc
    if not temporary_root.is_dir():
        raise CollaborationBusyError("当前临时目录不可用。")
    if hasattr(os, "getuid"):
        owner_suffix = f"uid-{os.getuid()}"
    else:
        owner_hint = os.environ.get("USERNAME", "current").encode("utf-8", errors="surrogatepass")
        owner_suffix = "user-" + hashlib.sha256(owner_hint).hexdigest()[:16]
    lock_root = temporary_root / f"agent-team-scaffold-locks-{owner_suffix}"
    if lock_root.is_symlink():
        raise CollaborationBusyError("agent-team 锁目录不能是符号链接。")
    try:
        lock_root.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise CollaborationBusyError(f"无法创建 agent-team 私有锁目录: {exc}") from exc
    try:
        lock_root.resolve(strict=True).relative_to(temporary_root)
    except (OSError, ValueError) as exc:
        raise CollaborationBusyError("agent-team 私有锁目录越出当前临时目录。") from exc
    if lock_root.is_symlink() or not lock_root.is_dir():
        raise CollaborationBusyError("agent-team 私有锁目录不安全。")
    root_stat = lock_root.stat()
    if hasattr(os, "getuid") and root_stat.st_uid != os.getuid():
        raise CollaborationBusyError("agent-team 私有锁目录不属于当前用户。")
    try:
        os.chmod(lock_root, 0o700)
    except OSError as exc:
        raise CollaborationBusyError(f"无法收紧 agent-team 锁目录权限: {exc}") from exc
    collaboration_locks = target / "docs" / "collaboration" / ".locks"
    if collaboration_locks.exists():
        if collaboration_locks.is_symlink() or not collaboration_locks.is_dir():
            raise CollaborationBusyError("项目协作锁目录不安全。")
        try:
            collaboration_locks.resolve(strict=True).relative_to(target.resolve(strict=True))
        except (OSError, ValueError) as exc:
            raise CollaborationBusyError("项目协作锁目录越出项目根目录。") from exc
        lock_path = collaboration_locks / "project-control.lock"
    else:
        lock_key = hashlib.sha256(str(target).encode("utf-8")).hexdigest()
        lock_path = lock_root / f"{lock_key}.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = -1
    try:
        fd = os.open(lock_path, flags, 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise CollaborationBusyError("agent-team 锁文件不是单链接普通文件。")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise CollaborationBusyError("agent-team 锁文件不属于当前用户。")
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        handle = os.fdopen(fd, "a+b", buffering=0)
        fd = -1
    except CollaborationBusyError:
        if fd >= 0:
            os.close(fd)
        raise
    except OSError as exc:
        if fd >= 0:
            os.close(fd)
        raise CollaborationBusyError(f"无法安全打开 agent-team 锁: {exc}") from exc
    try:
        if os.name == "nt":
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise CollaborationBusyError("另一个 agent-team 脚手架进程正在修改该项目。") from exc
        else:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise CollaborationBusyError("另一个 agent-team 脚手架进程正在修改该项目。") from exc
        yield
    finally:
        try:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def validate_target_layout(target: Path) -> tuple[bool, str]:
    """Validate every pre-existing path that the scaffold may read or replace."""
    docs = target / "docs"
    if docs.exists() and (docs.is_symlink() or not docs.is_dir()):
        return False, "docs 必须是项目内普通目录，不能是文件或符号链接。"
    if docs.exists():
        try:
            docs.resolve().relative_to(target)
        except (OSError, ValueError):
            return False, "docs 解析后超出项目根目录。"
    for path in (docs / "spec.md", docs / "overview.md", docs / "progress.md", docs / "agent-guide.md"):
        if path.exists() and (path.is_symlink() or not path.is_file()):
            return False, f"{path.relative_to(target)} 必须是普通文件，不能是目录或符号链接。"
    collab = docs / "collaboration"
    if collab.exists() and collab.is_symlink():
        return False, "docs/collaboration 不能是符号链接。"
    return True, ""


def plain_path_within(path: Path, root: Path, *, kind: str) -> bool:
    """Require an existing non-symlink path whose complete resolved chain stays inside root."""
    try:
        root_resolved = root.resolve(strict=True)
        if root.is_symlink() or not root_resolved.is_dir():
            return False
        lexical = Path(os.path.abspath(str(path)))
        relative = lexical.relative_to(Path(os.path.abspath(str(root))))
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return False
        resolved = path.resolve(strict=True)
        resolved.relative_to(root_resolved)
    except (OSError, ValueError):
        return False
    if kind == "dir":
        return path.is_dir()
    if kind == "file":
        return path.is_file()
    raise ValueError(f"unknown path kind: {kind}")


MANAGED_BASE_FILES = (
    "README.md",
    "路由表.md",
    "会话启动清单.md",
    "任务交接模板.md",
    "模板/工作报告.md",
    "模板/审核报告.md",
    "模板/专项结论.md",
    "scripts/agent_team_log.py",
    "scripts/agent_team_task.py",
    "scripts/agent_team_session.py",
    "scripts/agent_team_temporary.py",
    ".locks/legacy-closeout-index.json",
)


def managed_relative_paths(roles: list[str]) -> list[str]:
    paths = list(MANAGED_BASE_FILES)
    for key in roles:
        department = ROLE_DEFS[key]["name"]
        paths.extend((f"部门/{department}/岗位说明.md", f"部门/{department}/上岗引导.md"))
    return sorted(paths)


CURRENT_SESSION_ITEM_FIELDS = {
    "role_id", "active", "notification_mode", "step", "thread_id", "previous_thread_id",
    "failed_from", "evidence", "operation_id", "note", "updated_at", "lifecycle_history",
}
CURRENT_SESSION_STEPS = {"pending", "created", "onboarded", "registered", "failed"}


def validate_session_item_lifecycle(item: dict, department: str) -> None:
    current_thread = item["thread_id"]
    previous_thread = item["previous_thread_id"]
    operation_id = item["operation_id"]
    failed_from = item["failed_from"]
    if not item.get("active", False) and (
        item["step"] != "pending" or current_thread or previous_thread
    ):
        raise ValueError(f"已停用部门仍保留活动会话事实: {department}")
    for label, thread_id in (("thread_id", current_thread), ("previous_thread_id", previous_thread)):
        if thread_id and (
            len(thread_id) > THREAD_ID_MAX_CHARS
            or thread_id.startswith("=")
            or any(char.isspace() for char in thread_id)
        ):
            raise ValueError(f"会话启动状态 {label} 不可表示为归档回执: {department}")
    if not operation_id:
        raise ValueError(f"会话启动状态 operation_id 无效: {department}")
    if previous_thread and not operation_id.startswith("SWITCH-"):
        raise ValueError(f"待收口旧会话缺少 SWITCH 事务: {department}")
    if operation_id.startswith("SWITCH-") and not previous_thread:
        raise ValueError(f"SWITCH 事务缺少 previous_thread_id: {department}")
    if item["step"] == "pending" and current_thread:
        raise ValueError(f"pending 会话不得预先占用 thread_id: {department}")
    if item["step"] in {"created", "onboarded", "registered"} and not current_thread:
        raise ValueError(f"会话启动状态 {item['step']} 缺少 thread_id: {department}")
    if item["step"] == "failed":
        if failed_from not in {"pending", "created", "onboarded"}:
            raise ValueError(f"failed 会话缺少可恢复的 failed_from: {department}")
        if (failed_from == "pending") == bool(current_thread):
            raise ValueError(f"failed 会话的 thread_id 与 failed_from 矛盾: {department}")
    elif failed_from:
        raise ValueError(f"非 failed 会话不得保留 failed_from: {department}")


def validate_current_session_payload(payload: object) -> list[str]:
    """Validate the exact current session-state schema and return configured role IDs."""
    root_fields = {
        "schema_version", "protocol_version", "created_at", "updated_at", "profile",
        "session_mode", "role_order", "departments",
    }
    if not isinstance(payload, dict) or set(payload) != root_fields:
        raise ValueError("会话启动状态根结构无效")
    if payload["schema_version"] != 1 or payload["protocol_version"] != PROTOCOL_VERSION:
        raise ValueError("会话启动状态版本无效")
    for field in ("created_at", "updated_at", "profile"):
        if not isinstance(payload[field], str) or not payload[field]:
            raise ValueError(f"会话启动状态 {field} 无效")
    if payload["session_mode"] not in {"auto", "manual"}:
        raise ValueError("会话启动状态 session_mode 无效")
    departments = payload["departments"]
    if not isinstance(departments, dict) or not departments:
        raise ValueError("会话启动状态未登记部门")
    roles: list[str] = []
    for department, item in departments.items():
        if not isinstance(department, str) or not isinstance(item, dict) or set(item) != CURRENT_SESSION_ITEM_FIELDS:
            raise ValueError(f"会话启动状态部门条目无效: {department}")
        role_id = item["role_id"]
        if role_id not in ROLE_DEFS or ROLE_DEFS[role_id]["name"] != department:
            raise ValueError(f"会话启动状态角色无效: {department}")
        if item["notification_mode"] not in {"auto", "manual"}:
            raise ValueError(f"会话通知模式无效: {department}")
        if item["step"] not in CURRENT_SESSION_STEPS:
            raise ValueError(f"会话启动状态 step 无效: {department}")
        if not isinstance(item["active"], bool):
            raise ValueError(f"会话启动状态 active 类型无效: {department}")
        for field in CURRENT_SESSION_ITEM_FIELDS - {
            "role_id", "active", "notification_mode", "step", "lifecycle_history",
        }:
            if not isinstance(item[field], str):
                raise ValueError(f"会话启动状态 {field} 类型无效: {department}")
        history = item["lifecycle_history"]
        if not isinstance(history, list):
            raise ValueError(f"会话启动状态 lifecycle_history 无效: {department}")
        for event in history:
            if (
                not isinstance(event, dict)
                or set(event) != {"event", "at", "actor", "evidence", "thread_id"}
                or event.get("event") not in {"retired", "deactivated", "reactivated"}
                or any(not isinstance(event.get(field), str) or not event.get(field) for field in ("at", "actor", "evidence"))
                or not isinstance(event.get("thread_id"), str)
            ):
                raise ValueError(f"会话启动状态 lifecycle_history 条目无效: {department}")
        validate_session_item_lifecycle(item, department)
        roles.append(role_id)
    if len(roles) != len(set(roles)):
        raise ValueError("会话启动状态含重复 role_id")
    if (
        not isinstance(payload["role_order"], list)
        or any(not isinstance(role, str) for role in payload["role_order"])
        or set(payload["role_order"]) != set(roles)
        or len(payload["role_order"]) != len(set(payload["role_order"]))
    ):
        raise ValueError("会话启动状态 role_order 与部门顺序不一致")
    active_layers = {
        ROLE_DEFS[item["role_id"]]["layer"] for item in departments.values() if item["active"]
    }
    if active_layers != {"management", "execution", "audit"}:
        raise ValueError("活跃部门未保持管理、执行、审核三层最小结构")
    return list(payload["role_order"])


def protocol_payload(
    date: str,
    managed_files: dict[str, dict[str, str]] | None = None,
    role_policy_overlays: dict[str, object] | None = None,
    upgrade_identity: dict[str, str] | None = None,
) -> str:
    return json.dumps(
        {
            "protocol_version": PROTOCOL_VERSION,
            "generator": "agent-team",
            "generated_on": date,
            "managed_files": managed_files or {},
            ROLE_POLICY_OVERRIDE_FIELD: role_policy_overlays or {},
            "upgrade_identity": upgrade_identity,
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def slice_control_payload() -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "protocol_version": PROTOCOL_VERSION,
            "active_slice": None,
            "history": [],
            "candidate_ids": [],
            "host_runtime": {"mode": "manual-degraded", "capabilities": []},
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def legacy_closeout_index_payload(
    task_ids: list[str] | None = None,
    archive_recovery_task_ids: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "protocol_version": PROTOCOL_VERSION,
            "task_ids": sorted(task_ids or []),
            "archive_recovery_task_ids": sorted(archive_recovery_task_ids or []),
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def managed_manifest_from_disk(collab: Path, roles: list[str]) -> dict[str, dict[str, str]]:
    manifest: dict[str, dict[str, str]] = {}
    for relative in managed_relative_paths(roles):
        path = collab / relative
        if not plain_path_within(path, collab, kind="file"):
            raise ValueError(f"受管文件缺失或不安全: {relative}")
        info = path.stat()
        manifest[relative] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "mode": f"{stat.S_IMODE(info.st_mode):04o}",
        }
    return manifest


def rollback_surface_state_sha256(project: Path, manifest: dict) -> str:
    """Digest every path an explicit rollback could overwrite, except disposable indexes."""
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list) or not isinstance(
        manifest.get("directories"), list,
    ):
        raise ValueError("升级回滚清单缺少可绑定的状态表面")
    excluded = {
        "docs/collaboration/协议版本.json",
        "docs/collaboration/.locks/hot-state.json",
        "docs/collaboration/.upgrade-transaction.json",
    }
    def rollback_file_state(path: Path, relative: str) -> dict[str, object]:
        data = path.read_bytes()
        scope = "complete-file"
        if relative.endswith("/交接班文档.md"):
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise ValueError(f"升级回滚交接班文档不是 UTF-8: {relative}") from exc
            start_count = text.count(ROLLBACK_HANDOFF_HOT_BLOCK_START)
            end_count = text.count(ROLLBACK_HANDOFF_HOT_BLOCK_END)
            if start_count or end_count:
                if start_count != 1 or end_count != 1:
                    raise ValueError(f"升级回滚交接班文档机器区块无效: {relative}")
                start = text.index(ROLLBACK_HANDOFF_HOT_BLOCK_START)
                end = text.index(ROLLBACK_HANDOFF_HOT_BLOCK_END, start)
                if end < start:
                    raise ValueError(f"升级回滚交接班文档机器区块顺序无效: {relative}")
                end += len(ROLLBACK_HANDOFF_HOT_BLOCK_END)
                text = text[:start] + text[end:]
                scope = "manual-content-outside-generated-current-slice"
            text = re.sub(r"\n{3,}", "\n\n", text)
            data = text.encode("utf-8")
        return {
            "kind": "file", "path": relative, "state": "present", "scope": scope,
            "sha256": hashlib.sha256(data).hexdigest(),
            "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}",
        }

    def absent_directory_tree(path: Path) -> list[dict[str, object]]:
        tree: list[dict[str, object]] = []
        for child in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()):
            child_relative = child.relative_to(project).as_posix()
            if child_relative in excluded or child_relative.endswith("/收件箱.md"):
                continue
            if child.is_symlink():
                raise ValueError(f"升级回滚新建目录含符号链接: {child_relative}")
            if child.is_dir() and plain_path_within(child, project, kind="dir"):
                tree.append({
                    "kind": "directory", "path": child_relative, "state": "present",
                    "mode": f"{stat.S_IMODE(child.stat().st_mode):04o}",
                })
            elif child.is_file() and plain_path_within(child, project, kind="file"):
                tree.append(rollback_file_state(child, child_relative))
            else:
                raise ValueError(f"升级回滚新建目录含无效路径: {child_relative}")
        return tree

    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for entry in manifest["files"]:
        relative = entry.get("target") if isinstance(entry, dict) else None
        if (
            not isinstance(relative, str)
            or relative in seen
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise ValueError("升级回滚文件状态表面无效")
        seen.add(relative)
        if (
            relative in excluded
            or relative.endswith("/收件箱.md")
        ):
            continue
        path = project / relative
        if path.is_symlink():
            raise ValueError(f"升级回滚状态表面出现符号链接: {relative}")
        if not path.exists():
            rows.append({"kind": "file", "path": relative, "state": "absent"})
        elif path.is_file() and plain_path_within(path, project, kind="file"):
            rows.append(rollback_file_state(path, relative))
        else:
            raise ValueError(f"升级回滚文件状态表面类型无效: {relative}")
    for entry in manifest["directories"]:
        relative = entry.get("target") if isinstance(entry, dict) else None
        if (
            not isinstance(relative, str)
            or relative in seen
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise ValueError("升级回滚目录状态表面无效")
        seen.add(relative)
        path = project / relative
        if path.is_symlink():
            raise ValueError(f"升级回滚状态表面出现符号链接: {relative}")
        if not path.exists():
            rows.append({"kind": "directory", "path": relative, "state": "absent"})
        elif path.is_dir() and plain_path_within(path, project, kind="dir"):
            row: dict[str, object] = {
                "kind": "directory", "path": relative, "state": "present",
                "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}",
            }
            if isinstance(entry, dict) and entry.get("existed") is False:
                row["tree"] = absent_directory_tree(path)
            rows.append(row)
        else:
            raise ValueError(f"升级回滚目录状态表面类型无效: {relative}")
    tasks_root = project / "docs" / "collaboration" / "tasks"
    if tasks_root.is_symlink():
        raise ValueError("升级回滚 TASK 状态表面不能是符号链接")
    if tasks_root.exists():
        if not plain_path_within(tasks_root, project, kind="dir"):
            raise ValueError("升级回滚 TASK 状态表面不安全")
        task_rows = []
        for task_path in sorted(tasks_root.glob("TASK-*.json")):
            relative = task_path.relative_to(project).as_posix()
            if task_path.is_symlink() or not plain_path_within(task_path, project, kind="file"):
                raise ValueError(f"升级回滚 TASK 状态表面不安全: {relative}")
            task_rows.append({
                "path": relative,
                "sha256": hashlib.sha256(task_path.read_bytes()).hexdigest(),
                "mode": f"{stat.S_IMODE(task_path.stat().st_mode):04o}",
            })
        rows.append({"kind": "task-state-guard", "tasks": task_rows})
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def rollback_identity_state_sha256(collab: Path, identity: dict[str, str]) -> str:
    operation_id = identity["operation_id"]
    backup_root = collab / "升级备份" / operation_id
    manifest_path = backup_root / "rollback-manifest.json"
    if not plain_path_within(manifest_path, backup_root, kind="file"):
        raise ValueError("当前升级代次的 rollback manifest 缺失或不安全")
    manifest_bytes = manifest_path.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != identity["rollback_manifest_sha256"]:
        raise ValueError("当前升级代次的 rollback manifest 已漂移")
    manifest = strict_json_loads(manifest_bytes.decode("utf-8"), source=str(manifest_path))
    if (
        not isinstance(manifest, dict)
        or manifest.get("operation_id") != operation_id
        or manifest.get("source_protocol") != identity["source_protocol"]
        or manifest.get("target_protocol") != identity["target_protocol"]
    ):
        raise ValueError("当前升级代次与 rollback manifest 身份不一致")
    return rollback_surface_state_sha256(collab.parents[1], manifest)


def read_protocol_version(collab: Path) -> str | None:
    path = collab / PROTOCOL_FILE
    if not plain_path_within(path, collab, kind="file"):
        return None
    try:
        payload = strict_json_loads(read_utf8(path), source=str(path))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("protocol_version")
    return value if isinstance(value, str) else None


def read_protocol_version_for_upgrade(collab: Path) -> str | None:
    path = collab / PROTOCOL_FILE
    if path.is_symlink():
        raise ValueError("协议版本文件不能是符号链接")
    if not path.exists():
        return None
    if not plain_path_within(path, collab, kind="file"):
        raise ValueError("协议版本文件路径不安全")
    payload = strict_json_loads(read_utf8(path), source=str(path))
    if not isinstance(payload, dict):
        raise ValueError("协议版本 JSON 根节点必须是对象")
    value = payload.get("protocol_version")
    if not isinstance(value, str) or not value:
        raise ValueError("协议版本字段缺失或无效")
    return value


def collaboration_work_mode(collab: Path) -> str:
    control = collab / ".locks" / "dispatch-control.json"
    if not control.exists():
        raise ValueError("派单控制缺失；为避免失控派单，已拒绝新工作")
    if not plain_path_within(control, collab, kind="file"):
        raise ValueError("派单控制路径不安全")
    payload = strict_json_loads(read_utf8(control), source=str(control))
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "mode", "updated_at", "history"}:
        raise ValueError("派单控制结构无效")
    if payload.get("schema_version") != 1 or payload.get("mode") not in {"normal", "frozen"}:
        raise ValueError("派单控制版本或模式无效")
    history = payload.get("history")
    if history == []:
        if payload.get("mode") != "normal" or payload.get("updated_at") != "":
            raise ValueError("初始派单控制状态无效")
        return "normal"
    if not isinstance(history, list) or len(history) > 1000:
        raise ValueError("派单控制历史无效")
    expected_action = "freeze"
    timestamps: list[dt.datetime] = []
    for event in history:
        if not isinstance(event, dict) or set(event) != {"at", "action", "actor", "evidence"}:
            raise ValueError("派单控制历史条目无效")
        if event["action"] != expected_action:
            raise ValueError("派单控制历史顺序无效")
        expected_action = "unfreeze" if expected_action == "freeze" else "freeze"
        if (
            not isinstance(event["actor"], str) or not event["actor"].startswith("统筹部/")
            or len(event["actor"]) > ACTOR_MAX_CHARS or any(char.isspace() for char in event["actor"])
            or not isinstance(event["evidence"], str) or not event["evidence"].strip()
            or event["evidence"] != event["evidence"].strip() or len(event["evidence"]) > 1000
        ):
            raise ValueError("派单控制 actor 或 evidence 无效")
        try:
            timestamp = dt.datetime.fromisoformat(event["at"])
        except (TypeError, ValueError) as exc:
            raise ValueError("派单控制时间无效") from exc
        if timestamp.tzinfo is None:
            raise ValueError("派单控制时间缺失时区")
        timestamps.append(timestamp)
    if timestamps != sorted(timestamps):
        raise ValueError("派单控制时间顺序无效")
    expected_mode = "frozen" if history[-1]["action"] == "freeze" else "normal"
    if payload["mode"] != expected_mode or payload.get("updated_at") != history[-1].get("at"):
        raise ValueError("派单控制当前模式与历史不一致")
    return payload["mode"]


def read_protocol_payload_for_upgrade(collab: Path) -> dict[str, object]:
    path = collab / PROTOCOL_FILE
    if path.is_symlink() or not plain_path_within(path, collab, kind="file"):
        raise ValueError("协议版本文件路径不安全")
    payload = strict_json_loads(read_utf8(path), source=str(path))
    if not isinstance(payload, dict):
        raise ValueError("协议版本 JSON 根节点必须是对象")
    return payload


def validate_role_policy_overlays(raw: object, roles: list[str]) -> dict[str, dict[str, object]]:
    if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
        raise ValueError("岗位职责项目追加层登记无效")
    if not set(raw) <= set(roles):
        raise ValueError("岗位职责项目追加层包含未登记角色")
    validated: dict[str, dict[str, object]] = {}
    for role_id, value in raw.items():
        if not isinstance(value, dict) or set(value) != {
            "schema_version", "reviewed_base_contract_version", "authorization_evidence", "additions",
        }:
            raise ValueError(f"岗位职责项目追加层结构无效: {role_id}")
        expected_contract = ROLE_CONTRACT_VERSIONS.get(role_id, "role-1")
        if value.get("schema_version") != 1 or value.get("reviewed_base_contract_version") != expected_contract:
            raise ValueError(
                f"岗位职责项目追加层尚未复核当前标准职责: {role_id}; "
                f"required={expected_contract}"
            )
        evidence = value.get("authorization_evidence")
        additions = value.get("additions")
        if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 1000:
            raise ValueError(f"岗位职责项目追加层缺用户确认依据: {role_id}")
        if not isinstance(additions, list) or not additions:
            raise ValueError(f"岗位职责项目追加层内容为空: {role_id}")
        ids: set[str] = set()
        for addition in additions:
            if not isinstance(addition, dict) or set(addition) != {"id", "section", "text"}:
                raise ValueError(f"岗位职责追加项结构无效: {role_id}")
            addition_id, section, text = addition["id"], addition["section"], addition["text"]
            if (
                not isinstance(addition_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", addition_id)
                or addition_id in ids or section not in ROLE_OVERLAY_SECTIONS
                or not isinstance(text, str) or not text.strip() or len(text) > 1000
                or any(ord(char) < 32 and char not in "\n\t" for char in text)
            ):
                raise ValueError(f"岗位职责追加项内容无效: {role_id}")
            ids.add(addition_id)
        validated[role_id] = value
    return validated


def role_policy_overlays_for_upgrade(
    collab: Path, roles: list[str], protocol: dict[str, object],
) -> tuple[dict[str, dict[str, object]], list[str]]:
    """Return validated additive overlays and directly edited managed role policies."""
    legacy_overrides = protocol.get("role_policy_overrides", [])
    if legacy_overrides not in (None, []):
        raise ValueError(
            "发现旧版整文件岗位覆盖登记，不能静默冻结或丢弃；"
            "请先将经用户确认的新增职责显式迁移为 role_policy_overlays"
        )
    overlays = validate_role_policy_overlays(protocol.get(ROLE_POLICY_OVERRIDE_FIELD, {}), roles)
    manifest = protocol.get("managed_files")
    if not isinstance(manifest, dict):
        raise ValueError("缺少受管文件清单,无法判断岗位职责是否经过项目定制")
    changed: list[str] = []
    for role_id in roles:
        relative = f"部门/{ROLE_DEFS[role_id]['name']}/岗位说明.md"
        path = collab / relative
        if path.is_symlink():
            raise ValueError(f"岗位说明缺失或不安全:{relative}")
        if not path.exists():
            continue
        if not plain_path_within(path, collab, kind="file"):
            raise ValueError(f"岗位说明缺失或不安全:{relative}")
        record = manifest.get(relative)
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
        ):
            raise ValueError(f"缺少岗位职责受管基线:{role_id}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            changed.append(role_id)
    return overlays, changed


def current_runtime_complete(collab: Path) -> bool:
    directories = ("部门", ".locks", "tasks", "scripts", "模板", "专项结论")
    project = collab.parents[1]
    guide = collab.parent / "agent-guide.md"
    try:
        protocol_path = collab / PROTOCOL_FILE
        if not plain_path_within(protocol_path, collab, kind="file"):
            return False
        protocol = strict_json_loads(read_utf8(protocol_path), source=str(protocol_path))
        if not isinstance(protocol, dict) or protocol.get("protocol_version") != PROTOCOL_VERSION:
            return False
        state_path = collab / "会话启动状态.json"
        if not plain_path_within(state_path, collab, kind="file"):
            return False
        state_payload = strict_json_loads(read_utf8(state_path), source=str(state_path))
        roles = validate_current_session_payload(state_payload)
        validate_role_policy_overlays(protocol.get(ROLE_POLICY_OVERRIDE_FIELD), roles)
        registry_path = collab / "部门表.md"
        for variable_file in ("部门表.md", "错题集.md"):
            if not plain_path_within(collab / variable_file, collab, kind="file"):
                return False
        registry_text = read_utf8(registry_path)
        registry_roles = registered_role_ids(registry_text)
        if len(registry_roles) != len(set(registry_roles)) or set(registry_roles) != set(roles):
            return False
        if registry_session_data(registry_text) != registry_data_from_session_state(state_payload):
            return False
        preflight_upgrade_tasks(collab, roles)
        tasks_root = collab / "tasks"
        if any(entry.is_dir() for entry in tasks_root.iterdir()):
            return False
        task_payloads = iter_upgrade_task_payloads(collab)
        validate_session_thread_identities(state_payload, task_payloads)
        _, archive_recovery = load_legacy_archive_recovery(collab, task_payloads, PROTOCOL_VERSION)
        load_legacy_closeout_index(
            collab, task_payloads, archive_recovery["entries"], PROTOCOL_VERSION,
        )
        expected_paths = managed_relative_paths(roles)
        manifest = protocol.get("managed_files")
        if not isinstance(manifest, dict) or sorted(manifest) != expected_paths:
            return False
        for relative in expected_paths:
            record = manifest.get(relative)
            path = collab / relative
            if (
                not isinstance(record, dict)
                or set(record) != {"sha256", "mode"}
                or not isinstance(record.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
                or not isinstance(record.get("mode"), str)
                or not re.fullmatch(r"[0-7]{4}", record["mode"])
                or not plain_path_within(path, collab, kind="file")
                or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]
                or (os.name != "nt" and f"{stat.S_IMODE(path.stat().st_mode):04o}" != record["mode"])
            ):
                return False
        upgrade_identity = protocol.get("upgrade_identity")
        if upgrade_identity is not None:
            if (
                not isinstance(upgrade_identity, dict)
                or set(upgrade_identity) != {
                    "operation_id", "source_protocol", "target_protocol",
                    "target_state_sha256", "rollback_manifest_sha256",
                }
                or not re.fullmatch(
                    r"UPGRADE-[0-9]{8}T[0-9]{6}-[A-F0-9]{8}",
                    upgrade_identity.get("operation_id", ""),
                )
                or not isinstance(upgrade_identity.get("source_protocol"), str)
                or not upgrade_identity["source_protocol"]
                or upgrade_identity.get("target_protocol") != PROTOCOL_VERSION
                or not re.fullmatch(r"[0-9a-f]{64}", upgrade_identity.get("target_state_sha256", ""))
                or not re.fullmatch(r"[0-9a-f]{64}", upgrade_identity.get("rollback_manifest_sha256", ""))
                or upgrade_identity["target_state_sha256"]
                != rollback_identity_state_sha256(collab, upgrade_identity)
            ):
                return False
        for department in state_payload["departments"]:
            root = collab / "部门" / department
            if not plain_path_within(root, collab, kind="dir"):
                return False
            for name in ("上岗引导.md", "岗位说明.md", "交接班文档.md", "收件箱.md"):
                if not plain_path_within(root / name, collab, kind="file"):
                    return False
        guide_current = (
            plain_path_within(guide, project, kind="file")
            and f"受管协议版本:{PROTOCOL_VERSION}" in read_utf8(guide)
        )
    except (OSError, UnicodeError, ValueError):
        guide_current = False
    return guide_current and all(
        plain_path_within(collab / name, collab, kind="dir") for name in directories
    )


# 三层框架:management(管理层) / execution(执行层) / audit(审核层)
LAYER_CN = {
    "management": "管理层",
    "execution": "执行层",
    "audit": "审核层",
}


# 角色库:每个角色带 layer 字段。软件默认 lead,dev,test;其他场景可显式用 lead,do,review。
ROLE_DEFS = {
    # ============ 管理层 ============
    "lead": {
        "name": "统筹部",
        "layer": "management",
        "mission": "判断阶段、拆分端到端切片、用任务工具派单、维护项目总进度与跨部门沟通;每个切片只设一个执行 owner,证据就绪后最多开两个审核 gate。派单写清验收出口和失败路径,核验任务状态、唯一候选、真实用户出口与错题自检。宿主有 heartbeat/lease/状态查询/等待/恢复/归档才持续;否则按 manual-degraded 交接,不得轮询或声称无人值守。不得自动开启下一切片。用户冻结、任务堆积、上下文/存储压力、同一 gate 跨两代连续 FAIL 或无真实用户出口时立即冻结新工作。最终体验、范围、成本、安全、发布或重大方案由用户决定。",
        "not_responsible": "不亲自替执行层产出;不替审核层做独立验证;不自动对外放行;不把建议下一步当成用户已同意;不在重大边界替用户拍板;不在止血条件下继续派单;不为同一范围的返工新建 TASK;默认不吞入无关部门正文、长日志或完整证据;不按部门复制同一构建物;派单缺关键验收信息时不要求接收部门脑补。",
        "inputs": "项目目标, 任务状态与完成收据, 可选 LOG_OK 事件收据, 验收出口, 失败路径, 必要的项目总进度",
        "outputs": "通过任务工具派发的任务, 按用户闸门触发的稳定短报, 项目总进度汇总, 三关汇总后的放行建议",
        "can_write": "项目总进度文档, 部门表.md;通过任务工具派单和核收,不手工编辑收件箱",
        "cannot_write": "各部门的产出物, 其他部门岗位边界, 不替审核层改把关结论",
        "confirm": "最终体验与用户感知、范围和路线、视觉或交互方向、用户明确要求的设计预览、上线发布、外发交付、明显成本增加、隐私/安全/授权风险、大阶段收口或对外放行",
    },
    # ============ 执行层(产出层,≥1) ============
    "do": {
        "name": "执行部",
        "layer": "execution",
        "mission": "根据统筹部派的任务和已确认方案,产出实际成果,交付可验证的结果。",
        "not_responsible": "不擅自改需求/范围;不跳过验证;不替审核层做最终把关。",
        "inputs": "任务要求, 已确认的方案/标准, 相关材料",
        "outputs": "实际产出物(放项目产出区), 产出说明, 自检结果",
        "can_write": "项目产出区(本部门负责的部分), 本部门记忆文件",
        "cannot_write": "未经确认的范围/方案大改, 其他部门岗位边界",
        "confirm": "高风险动作(删数据/对外发布/付费/授权/改密钥等), 大改方向",
    },
    "research": {
        "name": "研究部",
        "layer": "execution",
        "mission": "在项目早期或信息不确定时,负责资料收集、用户/市场/竞品/案例研究、事实核验和证据整理,为后续方案与执行提供可靠输入。",
        "not_responsible": "不替统筹部裁决方向;不把未核实信息当事实;不直接承诺执行方案;不擅自扩大调研范围。",
        "inputs": "项目目标, 用户已知材料, 待验证问题, 信息来源范围, 证据标准",
        "outputs": "调研摘要, 证据清单, 不确定项, 可供策划/执行使用的结论",
        "can_write": "docs/overview.md 背景/证据小节, research/ 或材料目录, 本部门记忆文件",
        "cannot_write": "未经确认的最终方案, 其他部门产出物, 未标来源的事实判断",
        "confirm": "扩大调研范围, 采用高风险/付费/受限来源, 把关键不确定项转为结论",
    },
    "planning": {
        "name": "策划部",
        "layer": "execution",
        "mission": "把目标和研究输入转成可执行方案、流程、排期、资源配置、验收节点和交付路径;适用于课程、内容、运营、线下交付、咨询方案等非软件或混合项目。",
        "not_responsible": "不替执行部完成产出;不替审核层验收;不在用户确认前改变目标、范围、预算或交付标准。",
        "inputs": "项目目标, 研究结论, 资源约束, 时间要求, 用户验收标准, 统筹部提供的项目进度摘要",
        "outputs": "执行方案, 节点拆解, 排期, 资源清单, 验收标准草案",
        "can_write": "docs/overview.md 方案/流程小节, deliverables/ 或业务计划目录, 本部门记忆文件;阶段计划建议通过任务回报交给统筹部",
        "cannot_write": "未经确认的最终范围, 其他部门产出物, 与业务无关的软件目录",
        "confirm": "方案定稿, 范围变化, 排期/预算变化, 新增关键交付物或删减核心交付物",
    },
    "product": {
        "name": "产品部",
        "layer": "execution",
        "mission": "负责整个产品规划:完成产品调研,理解用户需求,定义功能、用户流程、优先级、验收目标与 MVP 边界;同时负责系统级技术实现路径、整体架构、模块/数据/接口边界、技术选型方案与必要实验、依赖约束、迁移/回滚方案、实施阶段和架构类 ADR/决策合同。系统级 ADR 至少使用 draft→proposed→accepted→superseded 状态;产品部起草和修订,开发参与可行性评审,用户确认和证据齐全后才进入 accepted。accepted 正文不可原地修改;实质变化必须新建 draft、重新评审确认,再把旧 ADR 标为 superseded 并保留替代指针。AI 产品的使用场景、行为验收目标、质量/成本/延迟目标与系统级 AI 路线也归产品部。把已确认规划交给开发部落地,并把上线反馈转化为下一轮迭代需求。",
        "not_responsible": "不画最终视觉;不写正式业务代码;不决定函数、类、算法和代码组织等代码级细节;不替开发部实现、自测或集成;不替测试部或安全部做独立放行。开发提交可行性证据或技术变更建议时,经统筹评估并由产品部修订系统级合同。",
        "inputs": "用户需求, 产品/用户/市场调研, docs/overview.md, docs/roadmap.md, 资源与依赖约束, 开发部可行性评审和技术变更建议, 统筹部提供的项目进度摘要, 上线反馈",
        "outputs": "docs/spec.md, docs/mvp.md, 产品方案, 用户流程与验收目标, 系统级技术实现路径, 整体架构与模块/数据/接口边界, 技术选型实验结论, 架构类 ADR/决策合同, 依赖与迁移/回滚约束, 实施阶段, 迭代需求",
        "can_write": "docs/spec.md, docs/mvp.md, docs/overview.md, docs/roadmap.md, docs/architecture/, docs/decisions/system/ 中的系统级架构合同, scratch/product-experiments/ 中不可直接合并或发布的 disposable spike;实验结论被采纳后由开发部重新实现和测试",
        "cannot_write": "app/ 正式业务代码, design/ 定稿视觉, 开发部负责的代码级实现与代码组织, 测试部/安全部审核结论",
        "confirm": "Spec 或系统级架构合同定稿, MVP/路线/核心依赖变化, 技术栈或基础模型选择, 迁移/回滚边界, 删除核心功能, 涉及隐私/付款/授权",
    },
    "design": {
        "name": "设计部",
        "layer": "execution",
        "mission": "把 spec 转成设计规范、界面布局、交互流程、视觉规范,打磨体验。设计意图预览默认可选;只有用户明确要看预览/原型/设计稿/视觉方案/先看效果,或任务已把预览列为交付物时,才使用 OpenDesign、本地 HTML + PNG、Figma 或可打开图片制作。用户未要求时,不为满足流程额外制作或排障,直接输出必要设计规范并以真实 App / 真实路由 / 构建或打包态验收 UI。",
        "not_responsible": "不定义需求;不写代码;不擅自增删功能;发现需求问题→经统筹转产品部;不得因为能使用 OpenDesign 就扩大需求范围、做完整 UI 重设计、品牌升级或开发实现;不得把可选预览当成必经流程或真实 UI 验收。",
        "inputs": "docs/spec.md, design/references/, 用户审美偏好",
        "outputs": "design/ui/, design/references/, 设计规范, 页面状态清单; 用户或任务明确要求时再交付设计意图预览路径与工具状态说明",
        "can_write": "design/, docs/spec.md 中明确的设计小节(需说明)",
        "cannot_write": "app/ 业务逻辑, docs/decisions/ 技术决策, docs/spec.md 的 MVP 范围",
        "confirm": "视觉方向定稿, 交互方向定稿, 页面流程改变, 增加新页面或新主流程;用户明确要求预览时,预览方向未确认前不视为该预览节点通过",
    },
    "dev": {
        "name": "开发部",
        "layer": "execution",
        "mission": "开工前复核已确认 Spec、架构类 ADR、设计和实施规划的可行性,再依据这些合同完成正式业务代码、整体集成与自测。函数、类、算法、代码组织和局部性能取舍等代码级细节由开发部负责。互联网产品和 AI 产品的代码实现均由开发部承担;涉及 AI 时,包括模型/API 接入、Prompt、RAG、Agent 链路、评测集、质量基线、推理成本与延迟、降级/重试/拒答、输出安全和可观测性。",
        "not_responsible": "不静默修改需求、用户流程、系统级技术实现路径、整体架构合同或产品路线;不得无授权重写系统级 ADR 的正文、状态或路线;不把可行性评审变成对产品规划权的接管;不做最终质量、安全或发布背书。发现合同不合理时提交证据、影响与优化方案：有产品部时经统筹退回产品部修订；未配置产品部时由统筹请用户指定规划责任人或直接确认新合同，再按新合同实现。",
        "inputs": "已确认的 docs/spec.md、docs/architecture/、架构类 docs/decisions/、实施阶段、docs/conventions.md 和 design/ 材料;产品部定义的依赖/迁移/回滚约束，未配置产品部时使用用户已确认的项目地基与合同;AI 功能另读使用场景、数据样例、模型/API 文档和质量/成本/延迟目标",
        "outputs": "开工可行性评审, app/ 与测试代码, 自测和集成结果, 技术实现说明, commit, 技术变更建议, 代码级决策记录;AI 功能另含评测集与基线、Prompt/RAG/Agent 配置、成本与延迟证据和降级策略",
        "can_write": "app/, tests/, evals/, prompts/, docs/conventions.md, scratch/ 实现实验, docs/decisions/code/ 中不重写系统级合同的代码级决策记录",
        "cannot_write": "未经规划责任人修订与用户确认的 docs/spec.md、docs/architecture/、系统级架构 ADR 或产品路线, design/ 定稿, 其他部门岗位边界, 测试部/安全部审核结论, .env 真值, 未脱敏数据或生产密钥/账号凭证",
        "confirm": "实现中发现需要改变系统级架构、路线、核心依赖或迁移/回滚合同,改认证/权限/支付/密钥,删除数据,大重构;AI 功能还包括更换基础模型、引入付费模型、上传用户数据、保存对话/向量或启用自动执行型 Agent",
    },
    "data": {
        "name": "数据部",
        "layer": "execution",
        "mission": "处理数据来源、采集、清洗、字段定义、导入导出和数据质量;涉及系统级数据边界时只提交证据与方案,经统筹交产品部写入系统合同。",
        "not_responsible": "不写 UI;不绕过安全部评估的平台风险采集;不擅自处理敏感数据。",
        "inputs": "docs/overview.md 或 docs/spec.md, 数据样例, 平台规则, 用户提供的数据文件",
        "outputs": "数据字段说明, 数据质量检查, 导入导出方案",
        "can_write": "docs/overview.md 数据小节, research/ 或 data/ 数据说明, scratch/ 数据实验",
        "cannot_write": "正式实现代码(除非作为开发任务), 未脱敏敏感数据",
        "confirm": "采集个人信息, 使用外部数据源, 保存敏感字段, 变更核心数据结构时经统筹请用户确认",
    },
    "auto": {
        "name": "自动化部",
        "layer": "execution",
        "mission": "设计批处理、定时任务、跨平台操作和流程自动化方案。",
        "not_responsible": "不绕过安全部的平台风险评估;不直接执行高风险自动化;不替用户发布内容。",
        "inputs": "docs/overview.md 或 docs/spec.md, 操作流程, 平台限制, 失败重试要求",
        "outputs": "自动化流程图, 触发条件, 异常处理方案",
        "can_write": "docs/overview.md 自动化小节, operations/ 或 scratch/ 实验脚本",
        "cannot_write": "生产自动化脚本(未经确认), 账号凭证, 发布/发送类动作",
        "confirm": "定时执行, 批量操作, 发消息/发内容/调用付费 API, 使用账号登录态",
    },
    "content": {
        "name": "内容部",
        "layer": "execution",
        "mission": "负责文案、素材、报告、视频脚本等内容生产链路。",
        "not_responsible": "不直接发布;不编造事实;不越过审核。",
        "inputs": "docs/overview.md 或 docs/spec.md, 参考资料, 用户风格要求",
        "outputs": "内容草稿, 素材清单, 报告结构",
        "can_write": "deliverables/ 内容草稿, materials/ 素材清单, docs/overview.md 内容小节",
        "cannot_write": "与业务无关的软件代码, 对外定稿内容(未经确认)",
        "confirm": "对外发布, 使用个人信息/联系方式, 引用未核实事实",
    },
    "growth": {
        "name": "增长运营部",
        "layer": "execution",
        "mission": "关注用户获取、转化、留存、商业化和运营指标。",
        "not_responsible": "不改变 MVP 技术实现;不夸大商业判断;不直接发布内容。",
        "inputs": "docs/overview.md 或 docs/spec.md, 目标用户, 业务目标, 反馈数据, 统筹部提供的项目进度摘要",
        "outputs": "运营假设, 指标设计, 反馈闭环建议",
        "can_write": "docs/overview.md 指标/运营小节, operations/ 运营方案;运营进度建议通过任务回报交给统筹部",
        "cannot_write": "与业务无关的软件代码, 未确认的对外发布材料",
        "confirm": "商业化方案, 对外承诺, 增长实验上线",
    },
    # ============ 审核层(把关层,三维度:质量 / 风险 / 成本,≥1) ============
    "review": {
        "name": "检验部",
        "layer": "audit",
        "mission": "独立把关执行层成果:依据任务领域选择合适方法,亲自取得可复验的证据,覆盖验收出口、指定失败路径和关键风险的反向探针,再判断是否符合要求。涉及用户可见结果时验证真实用户出口;没有用户界面时不制造软件层级。团队未单独拆出安全部或财务部时,兼做风险与成本的轻量把关。",
        "not_responsible": "不替执行部做事;不继承执行部的长上下文;不把执行部的完成陈述当作证据;不只沿执行部门提供的顺利路径复查;不自动返工或放行。",
        "inputs": "验收标准, 验收出口, 必测失败路径, 成果, 复现/检验方式, 变更摘要(仅用于定位,不作为通过依据)",
        "outputs": "报告/ 下的审核报告,包含审核对象与标准、独立证据、失败路径、反向探针、用户出口(如适用)、未覆盖项、问题清单和是否通过建议",
        "can_write": "报告/",
        "cannot_write": "执行部的产出物(除非用户明确授权), 验收标准本身",
        "confirm": "是否允许直接修复;涉及体验取舍、范围、成本、安全、发布或重大方案选择时由统筹部请用户确认",
    },
    "test": {
        "name": "测试部",
        "layer": "audit",
        "mission": "质量关分两段介入。用户体验前先做最小独立冒烟与安全探针，确认候选可运行、不会在基本路径直接崩溃；用户确认体验方向后再做完整回归、异常场景、打包、日志和边界检查。没有用户界面的任务直接按验收出口进入完整质量验证。测试亲自运行并出报告，结论回统筹部，不直接触发返工或放行。正式测试必须覆盖派单验收出口和必测失败路径；凡涉及用户看到的提示、错误、进度、状态、弹窗、结果摘要、导出文件名或打包态窗口，必须测到用户最终出口，不能只测 engine/API/helper 层。每个关键风险至少自设计一个反向探针。",
        "not_responsible": "不代替用户体验功能;不判断是否顺手、是否符合用户预期;只判专业质量这一关,不碰安全合规与成本;不改代码;不采信开发部转述的“已通过”;不只沿开发部 happy path 重跑一遍;不因底层 engine/API 通过就判定用户可见出口通过。",
        "inputs": "docs/spec.md, 验收标准, 验收出口, 必测失败路径, 可运行的产出, 复现方式, 变更摘要(仅用于定位)",
        "outputs": "报告/ 测试报告(附自己跑出的证据:实际输出/测试结果/截图/复现步骤,并写明验证层级、用户可见出口、自设计反向探针、未覆盖层级、是否触发子 Agent 盲审/抽检), bug 清单, 是否通过建议",
        "can_write": "报告/",
        "cannot_write": "app/ 代码, 验收标准本身",
        "confirm": "低影响的体验前最小冒烟无需用户先确认；测试会明显妨碍用户正常使用设备时遵守高影响测试例外，前台独占或难以自行退出时取得当次明确确认。完整体验方向由用户确认后再做正式回归。无用户界面的任务按验收出口直接完整验证。测试结论只回统筹部，不得直接返工或放行；涉及体验取舍、范围变化、成本、安全、发布、方案选择或重大事项时由统筹部请用户确认",
    },
    "security": {
        "name": "安全部",
        "layer": "audit",
        "mission": "风险关:在大阶段完成、上线或外发前,或涉及隐私、上传、权限、密钥、授权、第三方平台、生产配置等风险时介入,评估数据/法务/合规/第三方平台(封号、授权、费用、频率)/认证权限/密钥/隐私/生产配置等风险,出风险报告与合规清单;结论回统筹部,不自动触发返工或放行。",
        "not_responsible": "不判功能 bug;不评估成本是否划算(平台费用是否值归财务);不实现业务功能;不保存密钥;不替用户授权;不降低安全要求换速度。",
        "inputs": "docs/spec.md, docs/decisions/, 第三方平台文档, 环境变量示例, 权限/授权设计",
        "outputs": "报告/ 风险报告 + 合规清单, 可做/不可做边界, 替代方案",
        "can_write": "报告/;安全或平台合同修改只作为风险建议经统筹交产品部吸收",
        "cannot_write": "docs/spec.md, docs/decisions/, .env 真值, 生产配置, app/ 代码, 账号凭证",
        "confirm": "处理敏感数据, 上线生产, 改权限/认证/密钥/支付, 涉及登录态/授权/爬取/批量操作",
    },
    "finance": {
        "name": "财务部",
        "layer": "audit",
        "mission": "成本关:在成本核算、成本影响中大的功能规划、MVP 或第二版上线前、大功能板块完成时介入,评估和计算各环节成本;超支或成本过高时主动预警、给降本建议,并经统筹上报用户。成本只监控,不自动卡死发布。",
        "not_responsible": "不碰技术质量与安全;不替用户做最终花钱决定;不自动阻断发布(只预警+上报,花钱由用户拍板)。",
        "inputs": "docs/spec.md, 方案/技术选型, 第三方费用与计费规则, 预算上限",
        "outputs": "报告/ 成本测算与预算追踪, 超支预警与降本建议",
        "can_write": "报告/;成本合同修改只作为建议经统筹交产品部吸收",
        "cannot_write": "app/ 代码, 账号凭证, 未经用户确认的付费动作",
        "confirm": "超出预算阈值(预警上报), 引入付费项, 重大成本结构变化",
    },
}

# 旧协议中曾存在的角色。只用于识别并安全阻止静默迁移,不能新建或增量添加。
DEPRECATED_ROLE_IDS = {"ai": "AI工程部"}


def md_escape(text: str) -> str:
    return text.replace("\n", " ").strip()


def read_utf8(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def write_utf8_atomic(path: Path, text: str, *, mode: int | None = None) -> None:
    """以 UTF-8 写入同目录临时文件，成功后原子替换，避免留下半文件/0 字节文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temp_name, mode)
        os.replace(temp_name, path)
        temp_name = None
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def write_bytes_atomic(path: Path, data: bytes, *, mode: int) -> None:
    """Atomically restore exact bytes and permissions; used by verified rollback."""
    if path.parent.is_symlink():
        raise ValueError(f"恢复目标父目录不能是符号链接: {path.parent}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=path.parent, prefix=f".{path.name}.", suffix=".restore.tmp", delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
        temp_name = None
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def fsync_dir(path: Path) -> None:
    try:
        directory_fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        pass


def log_writer_script() -> str:
    return r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent-team 事实事件追加器。

只做确定性机械工作:校验字段、生成带时区时间和唯一事件 ID、
在部门周日志末尾原子追加一条事实记录，并只返回短收据。
不读取或输出历史日志，不总结经验，不替代 Agent 判断事件事实。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl


UTF8_BOOTSTRAP_MARKER = "AGENT_TEAM_LOG_UTF8_BOOTSTRAPPED"
EVENT_TYPES = {"MILESTONE", "CHANGE", "CORRECTION", "DECISION", "INCIDENT"}
INITIATORS = {"user", "agent", "review", "external"}
PREFIXES = {
    "MILESTONE": "MIL",
    "CHANGE": "CHG",
    "CORRECTION": "COR",
    "DECISION": "DEC",
    "INCIDENT": "INC",
}
MAX_FIELD_CHARS = 500
FORMAL_START = "<!-- agent-team:formal-log:start -->"
FORMAL_END = "<!-- agent-team:formal-log:end -->"
TEMP_START = "<!-- agent-team:temporary-log:start -->"
TEMP_END = "<!-- agent-team:temporary-log:end -->"


def ensure_utf8_filesystem_runtime() -> None:
    encoding = (sys.getfilesystemencoding() or "").lower().replace("_", "-")
    if encoding not in {"ascii", "us-ascii", "ansi-x3.4-1968"}:
        return
    if os.environ.get(UTF8_BOOTSTRAP_MARKER) == "1":
        raise SystemExit("无法启用 UTF-8 文件系统编码。")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env[UTF8_BOOTSTRAP_MARKER] = "1"
    os.execve(sys.executable, [sys.executable, *sys.argv], env)


ensure_utf8_filesystem_runtime()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def reject_duplicate_json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 含重复键: {key}")
        result[key] = value
    return result


def strict_json_loads(text, source):
    try:
        return json.loads(text, object_pairs_hook=reject_duplicate_json_pairs)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source} JSON 无效: {exc}") from exc


def discover_control_root() -> Path:
    local = Path(__file__).resolve().parents[1]
    project = local.parents[1]
    inside = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--is-inside-work-tree"],
        text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    result = subprocess.run(
        ["git", "-C", str(project), "worktree", "list", "--porcelain"],
        text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode == 0:
        first = next((line[9:] for line in result.stdout.splitlines() if line.startswith("worktree ")), "")
        candidate = Path(first) / "docs" / "collaboration" if first else None
        if candidate is not None and candidate.is_dir() and not candidate.is_symlink():
            return candidate.resolve(strict=True)
    if (project / ".git").exists() or (inside.returncode == 0 and inside.stdout.strip() == "true"):
        raise SystemExit("CONTROL_ROOT_ERROR | Git worktree 中无法验证主 docs/collaboration，拒绝写本地副本")
    return local


COLLAB = discover_control_root()
PROJECT = COLLAB.parents[1]
DEPARTMENTS = COLLAB / "部门"
TASKS = COLLAB / "tasks"
LOCKS = COLLAB / ".locks"


def clean_field(name: str, value: str, *, required: bool = True) -> str:
    cleaned = value.strip()
    if required and not cleaned:
        raise ValueError(f"{name} 不能为空")
    if any(ch in cleaned for ch in ("\n", "\r", "|")):
        raise ValueError(f"{name} 不能包含换行或竖线")
    if any(ord(ch) < 32 for ch in cleaned):
        raise ValueError(f"{name} 不能包含控制字符")
    if len(cleaned) > MAX_FIELD_CHARS:
        raise ValueError(f"{name} 不能超过 {MAX_FIELD_CHARS} 个字符")
    return cleaned or "-"


def safe_department(name: str) -> Path:
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError("部门名非法")
    if DEPARTMENTS.is_symlink() or not DEPARTMENTS.is_dir():
        raise ValueError("部门目录不存在或为符号链接")
    department = DEPARTMENTS / name
    if department.is_symlink() or not department.is_dir():
        raise ValueError(f"部门不存在或为符号链接: {name}")
    logs = department / "日志"
    if logs.is_symlink() or not logs.is_dir():
        raise ValueError("日志路径不是已存在的普通目录")
    return logs


def use_dir_fd() -> bool:
    return os.name != "nt" and os.open in os.supports_dir_fd


def open_directory(name_or_path, *, dir_fd=None) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(name_or_path, flags, dir_fd=dir_fd) if dir_fd is not None else os.open(name_or_path, flags)
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError("路径不是普通目录")
    return fd


@contextmanager
def logs_directory(department: str):
    logs = safe_department(department)
    if not use_dir_fd():
        yield logs, None
        return
    collab_fd = departments_fd = department_fd = logs_fd = -1
    try:
        collab_fd = open_directory(COLLAB)
        departments_fd = open_directory("部门", dir_fd=collab_fd)
        department_fd = open_directory(department, dir_fd=departments_fd)
        logs_fd = open_directory("日志", dir_fd=department_fd)
        yield logs, logs_fd
    finally:
        for fd in (logs_fd, department_fd, departments_fd, collab_fd):
            if fd >= 0:
                os.close(fd)


def safe_pointer(raw: str) -> str:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = PROJECT / candidate
    try:
        lexical = Path(os.path.abspath(str(candidate)))
        relative = lexical.relative_to(PROJECT)
        current = PROJECT
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("pointer 不能经过符号链接")
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(PROJECT)
    except (OSError, ValueError) as exc:
        raise ValueError("pointer 必须指向项目内已存在的非链接路径") from exc
    return str(resolved.relative_to(PROJECT))


@contextmanager
def log_lock(department: str):
    if LOCKS.exists() and (LOCKS.is_symlink() or not LOCKS.is_dir()):
        raise ValueError("锁目录不安全")
    LOCKS.mkdir(mode=0o700, exist_ok=True)
    lock_name = "log-" + department + ".lock"
    lock_path = LOCKS / lock_name
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    if use_dir_fd():
        collab_fd = locks_fd = -1
        try:
            collab_fd = open_directory(COLLAB)
            locks_fd = open_directory(".locks", dir_fd=collab_fd)
            fd = os.open(lock_name, flags, 0o600, dir_fd=locks_fd)
        finally:
            if locks_fd >= 0:
                os.close(locks_fd)
            if collab_fd >= 0:
                os.close(collab_fd)
    else:
        fd = os.open(lock_path, flags, 0o600)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ValueError("日志锁不是普通文件")
    handle = os.fdopen(fd, "a+b", buffering=0)
    try:
        if os.name == "nt":
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("写入失败")
        view = view[written:]


def week_info(now: dt.datetime) -> tuple[str, str, str]:
    iso_year, iso_week, _ = now.date().isocalendar()
    monday = now.date() - dt.timedelta(days=now.date().weekday())
    sunday = monday + dt.timedelta(days=6)
    return f"{iso_year}-W{iso_week:02d}", monday.isoformat(), sunday.isoformat()


def create_week_file(path: Path, department: str, week: str, start: str, end: str, logs_fd: int | None) -> None:
    header = (
        f"---\n部门: {department}\n覆盖: {start} ~ {end}\n---\n\n"
        f"# {department} · 日志 · {week}\n\n"
        "> 冷历史，默认不读。正式部门与临时外包物理分区；只记录改变项目轨迹的事实。\n\n"
        "## 正式部门日志\n\n"
        f"{FORMAL_START}\n{FORMAL_END}\n\n"
        "## 临时外包日志\n\n"
        "> 临时任务局部判断不代表父部门或项目正式结论。\n\n"
        f"{TEMP_START}\n{TEMP_END}\n"
    ).encode("utf-8")
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path.name, flags, 0o600, dir_fd=logs_fd) if logs_fd is not None else os.open(path, flags, 0o600)
    except FileExistsError:
        return
    try:
        write_all(fd, header)
        os.fsync(fd)
    finally:
        os.close(fd)


def section_layout(text: str) -> str:
    markers = (FORMAL_START, FORMAL_END, TEMP_START, TEMP_END)
    counts = [text.count(marker) for marker in markers]
    if counts == [1, 1, 1, 1]:
        if not (
            text.index(FORMAL_START) < text.index(FORMAL_END)
            < text.index(TEMP_START) < text.index(TEMP_END)
        ):
            raise ValueError("周日志分区标记顺序损坏")
        return text
    if any(counts):
        raise ValueError("周日志分区标记不完整，拒绝猜测修复")

    # 兼容旧平铺日志：保留头部，把既有事实事件原样迁入正式板块。
    lines = text.splitlines(keepends=True)
    first_event = next((index for index, line in enumerate(lines) if line.startswith("- ") and " | " in line), len(lines))
    prefix = "".join(lines[:first_event]).rstrip() + "\n\n"
    events = "".join(lines[first_event:]).strip()
    formal_body = (events + "\n") if events else ""
    return (
        prefix
        + "## 正式部门日志\n\n"
        + FORMAL_START + "\n" + formal_body + FORMAL_END + "\n\n"
        + "## 临时外包日志\n\n"
        + "> 临时任务局部判断不代表父部门或项目正式结论。\n\n"
        + TEMP_START + "\n" + TEMP_END + "\n"
    )


def replace_file(path: Path, data: bytes, logs_fd: int | None) -> None:
    temp_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temp_name, flags, 0o600, dir_fd=logs_fd) if logs_fd is not None else os.open(path.parent / temp_name, flags, 0o600)
    try:
        write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        if logs_fd is not None:
            os.replace(temp_name, path.name, src_dir_fd=logs_fd, dst_dir_fd=logs_fd)
            os.fsync(logs_fd)
        else:
            os.replace(path.parent / temp_name, path)
    finally:
        if logs_fd is not None:
            try:
                os.unlink(temp_name, dir_fd=logs_fd)
            except FileNotFoundError:
                pass
        else:
            (path.parent / temp_name).unlink(missing_ok=True)


def insert_event(text: str, *, event_line: str, task_id: str, executor_type: str, executor_id: str) -> str:
    text = section_layout(text)
    if executor_type == "formal":
        position = text.index(FORMAL_END)
        return text[:position] + event_line + text[position:]

    position = text.index(TEMP_END)
    section_start = text.index(TEMP_START) + len(TEMP_START)
    temporary_body = text[section_start:position]
    task_marker = f"<!-- agent-team:temporary-task:{task_id} -->"
    if task_marker in temporary_body:
        group_start = text.index(task_marker, section_start)
        next_group = text.find("\n### TASK-", group_start)
        insert_at = position if next_group < 0 or next_group > position else next_group
        return text[:insert_at].rstrip() + "\n" + event_line + "\n" + text[insert_at:].lstrip("\n")
    group = (
        f"\n### {task_id}\n\n"
        f"{task_marker}\n"
        f"> executor_type:temporary · executor_id:{executor_id}\n\n"
        f"{event_line}"
    )
    return text[:position] + group + text[position:]


def append_event(args: argparse.Namespace) -> int:
    event_type = args.type.upper()
    if event_type not in EVENT_TYPES:
        raise ValueError("type 只允许: " + ", ".join(sorted(EVENT_TYPES)))
    if args.initiator not in INITIATORS:
        raise ValueError("initiator 只允许: " + ", ".join(sorted(INITIATORS)))

    department = clean_field("department", args.department)
    task_id = clean_field("task-id", args.task_id)
    if task_id != "PROJECT" and not re.fullmatch(r"TASK-[0-9]{8}-[A-Z0-9]{6}", task_id):
        raise ValueError("task-id 必须是 TASK-YYYYMMDD-XXXXXX 或 PROJECT")
    executor_type = args.executor_type
    if executor_type not in {"formal", "temporary"}:
        raise ValueError("executor-type 只允许 formal 或 temporary")
    executor_id = clean_field("executor-id", args.executor_id or "", required=executor_type == "temporary")
    parent_department = clean_field(
        "parent-department", args.parent_department or department, required=executor_type == "temporary"
    )
    if executor_type == "temporary":
        if task_id == "PROJECT":
            raise ValueError("临时外包日志必须绑定具体 TASK")
        if parent_department != department:
            raise ValueError("临时外包日志必须写入父部门周日志")
        if TASKS.is_symlink() or not TASKS.is_dir():
            raise ValueError("tasks 目录缺失或为符号链接")
        task_path = TASKS / f"{task_id}.json"
        if task_path.is_symlink() or not task_path.is_file():
            raise ValueError("临时外包日志绑定的权威 TASK 不存在或不安全")
        task_payload = strict_json_loads(task_path.read_text(encoding="utf-8"), str(task_path))
        temporary = task_payload.get("temporary_executor")
        if not isinstance(temporary, dict):
            raise ValueError("TASK 未绑定临时执行者，拒绝写临时日志")
        if task_payload.get("department") != department or temporary.get("parent_department") != department:
            raise ValueError("日志父部门与 TASK 真值不一致")
        if temporary.get("executor_type") != "temporary" or temporary.get("executor_id") != executor_id:
            raise ValueError("日志执行者与 TASK 真值不一致")
    else:
        executor_id = "-"
        parent_department = department
    fact = clean_field("fact", args.fact)
    result = clean_field("result", args.result)
    pointer = safe_pointer(clean_field("pointer", args.pointer))
    needs_context = event_type != "MILESTONE"
    trigger = clean_field("trigger", args.trigger or "", required=needs_context)
    impact = clean_field("impact", args.impact or "", required=needs_context)

    now = dt.datetime.now().astimezone()
    timestamp = now.isoformat(timespec="minutes")
    week, start, end = week_info(now)
    with log_lock(department):
        with logs_directory(department) as (logs, logs_fd):
            log_path = logs / f"{week}.md"
            create_week_file(log_path, department, week, start, end, logs_fd)
            event_id = f"{PREFIXES[event_type]}-{now:%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6].upper()}"
            line = (
                f"- {timestamp} | {event_id} | {event_type} | task:{task_id} | initiator:{args.initiator} | "
                f"executor_type:{executor_type} | executor_id:{executor_id} | parent_department:{parent_department} | "
                f"fact:{fact} | trigger:{trigger} | impact:{impact} | result:{result} | -> {pointer}\n"
            )
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(log_path.name, flags, dir_fd=logs_fd) if logs_fd is not None else os.open(log_path, flags)
            try:
                file_stat = os.fstat(fd)
                if not stat.S_ISREG(file_stat.st_mode):
                    raise ValueError("周日志不是普通文件")
                if file_stat.st_nlink != 1:
                    raise ValueError("周日志存在硬链接，拒绝更新")
                with os.fdopen(fd, "r", encoding="utf-8", newline="") as handle:
                    fd = -1
                    current = handle.read()
            finally:
                if fd >= 0:
                    os.close(fd)
            updated = insert_event(
                current, event_line=line, task_id=task_id,
                executor_type=executor_type, executor_id=executor_id,
            )
            replace_file(log_path, updated.encode("utf-8"), logs_fd)

    relative = log_path.relative_to(PROJECT)
    print(f"LOG_OK | {timestamp} | {task_id} | {event_id} | {relative}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="agent-team 事实事件追加器")
    sub = parser.add_subparsers(dest="cmd", required=True)
    append = sub.add_parser("append", help="向部门周日志末尾追加一条事实事件")
    append.add_argument("--department", required=True)
    append.add_argument("--task-id", default="PROJECT")
    append.add_argument("--type", required=True)
    append.add_argument("--initiator", required=True)
    append.add_argument("--fact", required=True)
    append.add_argument("--trigger", default="")
    append.add_argument("--impact", default="")
    append.add_argument("--result", required=True)
    append.add_argument("--pointer", required=True)
    append.add_argument("--executor-type", choices=("formal", "temporary"), default="formal")
    append.add_argument("--executor-id", default="")
    append.add_argument("--parent-department", default="")
    append.set_defaults(func=append_event)
    args = parser.parse_args()
    try:
        return args.func(args)
    except (OSError, ValueError) as exc:
        print(f"LOG_ERROR | {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
'''


def temporary_executor_script() -> str:
    source = Path(__file__).with_name("temporary_executor_runtime.py")
    if source.is_symlink() or not source.is_file():
        raise ValueError("临时外包运行时模板缺失或不安全")
    return source.read_text(encoding="utf-8")


def task_writer_script() -> str:
    return r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Atomic task queue for agent-team.

Canonical task state lives in one JSON file per task. 收件箱.md is a generated
index and must never be used as the write transaction surface.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

if os.name == "nt":
    import msvcrt
else:
    import fcntl


UTF8_BOOTSTRAP_MARKER = "AGENT_TEAM_TASK_UTF8_BOOTSTRAPPED"


def ensure_utf8_filesystem_runtime() -> None:
    encoding = (sys.getfilesystemencoding() or "").lower().replace("_", "-")
    if encoding not in {"ascii", "us-ascii", "ansi-x3.4-1968"}:
        return
    if os.environ.get(UTF8_BOOTSTRAP_MARKER) == "1":
        raise SystemExit("无法启用 UTF-8 文件系统编码。")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env[UTF8_BOOTSTRAP_MARKER] = "1"
    os.execve(sys.executable, [sys.executable, *sys.argv], env)


ensure_utf8_filesystem_runtime()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def reject_duplicate_json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 含重复键: {key}")
        result[key] = value
    return result


def strict_json_loads(text, source):
    try:
        return json.loads(text, object_pairs_hook=reject_duplicate_json_pairs)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source} JSON 无效: {exc}") from exc


def discover_control_root() -> Path:
    local = Path(__file__).resolve().parents[1]
    project = local.parents[1]
    inside = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--is-inside-work-tree"],
        text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    result = subprocess.run(
        ["git", "-C", str(project), "worktree", "list", "--porcelain"],
        text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode == 0:
        first = next((line[9:] for line in result.stdout.splitlines() if line.startswith("worktree ")), "")
        candidate = Path(first) / "docs" / "collaboration" if first else None
        if candidate is not None and candidate.is_dir() and not candidate.is_symlink():
            return candidate.resolve(strict=True)
    if (project / ".git").exists() or (inside.returncode == 0 and inside.stdout.strip() == "true"):
        raise SystemExit("CONTROL_ROOT_ERROR | Git worktree 中无法验证主 docs/collaboration，拒绝写本地副本")
    return local


COLLAB = discover_control_root()
PROJECT = COLLAB.parents[1]
DEPARTMENTS = COLLAB / "部门"
TASKS = COLLAB / "tasks"
LOCKS = COLLAB / ".locks"
SESSION_STATE = COLLAB / "会话启动状态.json"
INDEX_MARKER = "<!-- agent-team task index; use scripts/agent_team_task.py -->"
HANDOFF_FRESHNESS_RE = re.compile(r"^<!-- agent-team hot-state:([0-9a-f]{64}|pending-rebuild) -->$", re.MULTILINE)
HANDOFF_HOT_BLOCK_START = "<!-- agent-team current-slice:start -->"
HANDOFF_HOT_BLOCK_END = "<!-- agent-team current-slice:end -->"
HOT_STATE = LOCKS / "hot-state.json"
INDEX_TRANSACTION = LOCKS / "task-index-transaction.json"
DISPATCH_CONTROL = LOCKS / "dispatch-control.json"
SLICE_CONTROL = LOCKS / "slice-control.json"
GATE_TRANSACTION = LOCKS / "gate-verdict-transaction.json"
SLICE_ENQUEUE_TRANSACTION = LOCKS / "slice-enqueue-transaction.json"
SLICE_CLOSE_TRANSACTION = LOCKS / "slice-close-transaction.json"
LEGACY_ARCHIVE_RECOVERY = LOCKS / "legacy-archive-recovery.json"
LEGACY_CLOSEOUT_INDEX = LOCKS / "legacy-closeout-index.json"
SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1
PROTOCOL_VERSION = "1.5.1"
STATES = ("queued", "claimed", "blocked", "waiting_input", "completed", "acknowledged")
THREAD_ID_MAX_CHARS = 300
ACTOR_MAX_CHARS = 400
HOT_CONTEXT_WARNING_BYTES = 24_000
BUSY_STATES = {"claimed"}
VISIBLE_ACTIVE_STATES = {"claimed", "blocked", "waiting_input"}
STATE_CN = {
    "queued": "待领取",
    "claimed": "进行中",
    "blocked": "阻断",
    "waiting_input": "等待输入",
    "completed": "待统筹核收",
    "acknowledged": "统筹已核收",
}
AUTH_STATES = {"none", "user_required", "user_confirmed", "user_rejected"}
COMPLETION_CLASSES = {"standard", "audit"}
TASK_FIELDS = {
    "schema_version", "task_id", "department", "from_department", "title", "node", "details",
    "acceptance_exit", "failure_paths", "confirmation", "domain_stage", "authorization_state",
    "authorization_evidence", "authorization_history", "execution_state", "completion_class", "pointers",
    "created_at", "updated_at", "revision", "claimed_by", "block_reason", "artifacts", "external_artifacts",
    "verified", "unverified", "mistake_check", "report", "event_receipts",
}
OPTIONAL_TASK_FIELDS = {
    "acknowledged_by", "impact_declaration", "temporary_executor", "temporary_operation_history", "resolution",
    "ownership_history", "state_action_receipt", "completion_history",
    "slice_id", "task_kind", "gate_type", "gate_attempts",
}
TRANSITIONS = {
    "claim": {"queued": "claimed"},
    "block": {"claimed": "blocked"},
    "wait": {"claimed": "waiting_input", "blocked": "waiting_input"},
    "resume": {
        "blocked": "claimed", "waiting_input": "claimed",
        "completed": "claimed", "acknowledged": "claimed",
    },
    "complete": {"claimed": "completed"},
    "ack": {"completed": "acknowledged"},
}
TASK_ID_RE = re.compile(r"^TASK-[0-9]{8}-[A-Z0-9]{6}$")
SLICE_ID_RE = re.compile(r"^SLICE-[0-9]{8}-[A-Z0-9]{6}$")
CANDIDATE_ID_RE = re.compile(r"^CAND-[0-9]{8}-[A-Z0-9]{6}$")
ROLE_DEPARTMENT_NAMES = {
    "lead": "统筹部",
    "do": "执行部",
    "research": "研究部",
    "planning": "策划部",
    "product": "产品部",
    "design": "设计部",
    "dev": "开发部",
    "data": "数据部",
    "auto": "自动化部",
    "content": "内容部",
    "growth": "增长运营部",
    "review": "检验部",
    "test": "测试部",
    "security": "安全部",
    "finance": "财务部",
}
AUDIT_ROLE_IDS = {"review", "test", "security", "finance"}
_TASK_CACHE = None


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="minutes")


def clean(name: str, value: str, *, max_chars: int = 2000) -> str:
    result = value.strip()
    if not result:
        raise ValueError(f"{name} 不能为空")
    if any(ord(ch) < 32 and ch not in "\t" for ch in result):
        raise ValueError(f"{name} 不能包含控制字符")
    if len(result) > max_chars:
        raise ValueError(f"{name} 不能超过 {max_chars} 个字符")
    return result


def ensure_plain_dir(path: Path, root: Path) -> None:
    try:
        root_resolved = root.resolve(strict=True)
        lexical = Path(os.path.abspath(str(path)))
        relative = lexical.relative_to(Path(os.path.abspath(str(root))))
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError
        resolved = path.resolve(strict=True)
        resolved.relative_to(root_resolved)
    except (OSError, ValueError) as exc:
        raise ValueError(f"不安全目录: {path}") from exc
    if not path.is_dir():
        raise ValueError(f"目录不存在: {path}")


def ensure_plain_file(path: Path, root: Path, *, label: str) -> Path:
    try:
        root_resolved = root.resolve(strict=True)
        lexical = Path(os.path.abspath(str(path)))
        relative = lexical.relative_to(Path(os.path.abspath(str(root))))
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError
        resolved = path.resolve(strict=True)
        resolved.relative_to(root_resolved)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} 路径不安全") from exc
    if not path.is_file():
        raise ValueError(f"{label} 必须是普通文件")
    return lexical


def init_layout() -> None:
    ensure_plain_dir(COLLAB, PROJECT)
    ensure_plain_dir(DEPARTMENTS, COLLAB)
    ensure_plain_dir(TASKS, COLLAB)
    ensure_plain_dir(LOCKS, COLLAB)


@contextmanager
def task_lock():
    lock_path = LOCKS / "project-control.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(lock_path, flags, 0o600)
    handle = os.fdopen(fd, "a+b", buffering=0)
    try:
        if os.name == "nt":
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def write_atomic(path: Path, data: bytes, mode: int) -> None:
    if path.parent.is_symlink():
        raise ValueError(f"父目录不能是符号链接: {path.parent}")
    temp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(temp, flags, mode)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("写入失败")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(temp, path)
        fsync_dir(path.parent)
    finally:
        temp.unlink(missing_ok=True)


def task_path(task_id: str) -> Path:
    return TASKS / f"{task_id}.json"


def require_task_text(payload: dict, field: str, *, allow_empty: bool = False, max_chars: int = 2000) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"任务字段缺失或类型无效: {field}")
    if (value and value != value.strip()) or len(value) > max_chars or any(ord(char) < 32 and char != "\t" for char in value):
        raise ValueError(f"任务文本字段超长或含非法控制字符: {field}")
    return value


def require_task_text_list(
    payload: dict,
    field: str,
    *,
    min_items: int = 0,
    max_items: int | None = None,
    max_chars: int = 2000,
) -> list[str]:
    value = payload.get(field)
    if not isinstance(value, list) or any(
        not isinstance(item, str)
        or not item.strip()
        or item != item.strip()
        or len(item) > max_chars
        or any(ord(char) < 32 and char != "\t" for char in item)
        for item in value
    ):
        raise ValueError(f"任务列表字段无效: {field}")
    if len(value) < min_items or (max_items is not None and len(value) > max_items):
        raise ValueError(f"任务列表字段数量无效: {field}")
    return value


def parse_task_timestamp(payload: dict, field: str) -> dt.datetime:
    value = require_task_text(payload, field, max_chars=64)
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"任务时间戳无效: {field}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"任务时间戳缺失时区: {field}")
    return parsed


def impact_path(raw: str) -> str:
    value = clean("write-path", raw, max_chars=500).replace("\\", "/")
    path = Path(value)
    if path.is_absolute() or value in {".", ".."} or ".." in path.parts:
        raise ValueError("write-path 必须是项目内相对路径")
    forbidden = (".git", ".agent-team", "docs/collaboration")
    if any(value == prefix or value.startswith(prefix + "/") for prefix in forbidden):
        raise ValueError(f"write-path 禁止授权控制根、Git 元数据或临时系统目录: {value}")
    return path.as_posix().rstrip("/")


def validate_impact_declaration(value: object, source: Path, *, task_id: str | None = None) -> dict:
    required = {"write_paths", "shared_contracts", "external_effects", "base_revision", "owner_task", "admission"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError(f"任务影响声明结构无效: {source.name}")
    for field in ("write_paths", "shared_contracts", "external_effects"):
        items = value.get(field)
        if not isinstance(items, list) or any(
            not isinstance(item, str) or not item.strip() or item != item.strip() or len(item) > 500
            or any(ord(char) < 32 for char in item)
            for item in items
        ) or len(items) != len(set(items)):
            raise ValueError(f"任务影响声明列表无效: {field}")
    if not value["write_paths"] or not value["external_effects"]:
        raise ValueError("任务影响声明必须包含 write_paths 和 external_effects")
    normalized_paths = [impact_path(item) for item in value["write_paths"]]
    if normalized_paths != value["write_paths"]:
        raise ValueError("任务影响声明 write_paths 未规范化")
    if "none" in value["external_effects"] and value["external_effects"] != ["none"]:
        raise ValueError("external_effects 中 none 不能与其他副作用并存")
    for field in ("base_revision", "owner_task"):
        field_value = value.get(field)
        if not isinstance(field_value, str) or not field_value.strip() or field_value != field_value.strip():
            raise ValueError(f"任务影响声明 {field} 无效")
    if task_id is not None and value["owner_task"] != task_id:
        raise ValueError(f"任务影响声明 owner_task 与 TASK 不一致: {source.name}")
    if value.get("admission") not in {"safe", "manual", "unsafe", "waiting_base"}:
        raise ValueError(f"任务影响声明 admission 无效: {source.name}")
    return value


def validate_task_payload(payload: object, path: Path) -> dict:
    if not isinstance(payload, dict):
        raise ValueError(f"任务根节点必须是 JSON 对象: {path.name}")
    missing_fields = sorted(TASK_FIELDS - set(payload))
    unknown_fields = sorted(set(payload) - TASK_FIELDS - OPTIONAL_TASK_FIELDS)
    if missing_fields:
        raise ValueError("任务字段缺失: " + ", ".join(missing_fields))
    if unknown_fields:
        raise ValueError("任务含当前 schema 未定义字段: " + ", ".join(unknown_fields))
    impact = payload.get("impact_declaration")
    if impact is not None:
        validate_impact_declaration(impact, path)
    temporary = payload.get("temporary_executor")
    if temporary is not None:
        if not isinstance(temporary, dict) or temporary.get("executor_type") != "temporary":
            raise ValueError(f"temporary_executor 结构无效: {path.name}")
        if temporary.get("parent_department") != payload.get("department"):
            raise ValueError(f"temporary_executor 父部门不匹配: {path.name}")
        nested_impact = temporary.get("impact")
        validate_impact_declaration(nested_impact, path, task_id=payload.get("task_id"))
        if impact is None or nested_impact != impact:
            raise ValueError(f"temporary_executor 的 impact 与 TASK impact_declaration 不一致: {path.name}")
        if temporary.get("promotion_state") not in {
            "not_submitted", "submitted", "reviewing", "waiting_base", "ready", "integrated",
            "archived", "cancelled", "abandoned",
        }:
            raise ValueError(f"temporary_executor 晋升状态无效: {path.name}")
    schema_version = payload.get("schema_version")
    if isinstance(schema_version, bool) or schema_version not in {LEGACY_SCHEMA_VERSION, SCHEMA_VERSION}:
        raise ValueError(f"不支持的任务版本: {path.name}")
    if schema_version == SCHEMA_VERSION:
        if not {"slice_id", "task_kind", "gate_type", "ownership_history"}.issubset(payload):
            raise ValueError(f"1.5 TASK 缺少切片字段: {path.name}")
        slice_id = require_task_text(payload, "slice_id", max_chars=32)
        if not SLICE_ID_RE.fullmatch(slice_id):
            raise ValueError(f"TASK slice_id 无效: {path.name}")
        if payload.get("task_kind") not in {"owner", "gate"}:
            raise ValueError(f"TASK task_kind 无效: {path.name}")
        gate_type = require_task_text(payload, "gate_type", allow_empty=True, max_chars=32)
        if (payload["task_kind"] == "owner") != (gate_type == ""):
            raise ValueError(f"TASK gate_type 与 task_kind 冲突: {path.name}")
        attempts = payload.get("gate_attempts", [])
        if payload["task_kind"] == "gate":
            if gate_type not in AUDIT_ROLE_IDS or not isinstance(attempts, list) or len(attempts) > 100:
                raise ValueError(f"gate TASK 尝试记录无效: {path.name}")
            generations: list[int] = []
            for attempt in attempts:
                if not isinstance(attempt, dict) or set(attempt) != {
                    "at", "decision", "candidate_id", "candidate_manifest", "candidate_sha256",
                    "generation", "evidence", "actor", "report", "report_sha256",
                }:
                    raise ValueError(f"gate TASK 尝试条目无效: {path.name}")
                if attempt.get("decision") not in {"pass", "fail"}:
                    raise ValueError(f"gate TASK decision 无效: {path.name}")
                if not CANDIDATE_ID_RE.fullmatch(attempt.get("candidate_id", "")):
                    raise ValueError(f"gate TASK candidate_id 无效: {path.name}")
                generation = attempt.get("generation")
                if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
                    raise ValueError(f"gate TASK generation 无效: {path.name}")
                generations.append(generation)
                for field, limit in (("evidence", 1000), ("actor", ACTOR_MAX_CHARS), ("report", 500)):
                    require_task_text(attempt, field, max_chars=limit)
                parse_task_timestamp(attempt, "at")
                require_task_text(attempt, "candidate_manifest", max_chars=500)
                if not re.fullmatch(r"[0-9a-f]{64}", attempt.get("candidate_sha256", "")):
                    raise ValueError(f"gate TASK 候选 SHA-256 无效: {path.name}")
                if not re.fullmatch(r"[0-9a-f]{64}", attempt.get("report_sha256", "")):
                    raise ValueError(f"gate TASK 审核报告 SHA-256 无效: {path.name}")
                candidate_manifest(
                    attempt["candidate_manifest"], attempt["candidate_id"], attempt["candidate_sha256"],
                )
                report = audit_report(
                    attempt["report"], payload["department"], payload["task_id"],
                    expected_decision=attempt["decision"], expected_candidate=attempt["candidate_id"],
                )
                if hashlib.sha256((PROJECT / report).read_bytes()).hexdigest() != attempt["report_sha256"]:
                    raise ValueError(f"gate TASK 审核报告内容已漂移: {path.name}")
            if generations != sorted(set(generations)):
                raise ValueError(f"gate TASK generation 必须严格递增: {path.name}")
        elif attempts:
            raise ValueError(f"owner TASK 不得含 gate_attempts: {path.name}")
        if temporary is not None:
            raise ValueError(f"1.5 切片 TASK 不允许绑定 legacy temporary_executor: {path.name}")

    task_id = require_task_text(payload, "task_id")
    if task_id != path.stem or not TASK_ID_RE.fullmatch(task_id):
        raise ValueError(f"任务 ID 与文件名不一致或格式无效: {path.name}")
    if impact is not None:
        validate_impact_declaration(impact, path, task_id=task_id)

    department = require_task_text(payload, "department")
    from_department = require_task_text(payload, "from_department")
    known_departments = set(department_names())
    if department not in known_departments or from_department not in known_departments:
        raise ValueError(f"任务部门不存在: {path.name}")
    if schema_version == SCHEMA_VERSION:
        role_id = configured_departments()[department]
        if payload["task_kind"] == "gate" and role_id != payload["gate_type"]:
            raise ValueError(f"gate TASK 部门与 gate_type 不一致: {path.name}")
        if payload["task_kind"] == "owner" and (role_id == "lead" or role_id in AUDIT_ROLE_IDS):
            raise ValueError(f"owner TASK 必须属于执行层部门: {path.name}")

    for field, max_chars in (
        ("title", 200), ("node", 200), ("details", 2000), ("acceptance_exit", 2000),
        ("confirmation", 2000), ("domain_stage", 200),
    ):
        require_task_text(payload, field, max_chars=max_chars)
    for field, max_chars in (
        ("authorization_evidence", 1000), ("claimed_by", ACTOR_MAX_CHARS), ("block_reason", 2000),
        ("mistake_check", 2000), ("report", 500),
    ):
        require_task_text(payload, field, allow_empty=True, max_chars=max_chars)
    if "acknowledged_by" in payload:
        require_task_text(payload, "acknowledged_by", allow_empty=True)
    ownership_history = payload.get("ownership_history", [])
    if not isinstance(ownership_history, list) or len(ownership_history) > 100:
        raise ValueError(f"任务 ownership_history 无效: {path.name}")
    previous_owner = ""
    for entry in ownership_history:
        if not isinstance(entry, dict) or set(entry) != {"at", "action", "actor", "previous_actor", "evidence"}:
            raise ValueError(f"任务 ownership_history 条目无效: {path.name}")
        if entry.get("action") not in {"claim", "rebind"}:
            raise ValueError(f"任务 ownership_history 动作无效: {path.name}")
        actor = require_task_text(entry, "actor", max_chars=ACTOR_MAX_CHARS)
        prior = require_task_text(entry, "previous_actor", allow_empty=True, max_chars=ACTOR_MAX_CHARS)
        require_task_text(entry, "evidence", max_chars=1000)
        parse_task_timestamp(entry, "at")
        if entry["action"] == "claim" and (prior or previous_owner):
            raise ValueError(f"任务首次 claim 身份历史无效: {path.name}")
        if entry["action"] == "rebind" and (not previous_owner or prior != previous_owner or actor == prior):
            raise ValueError(f"任务 rebind 身份历史不连续: {path.name}")
        previous_owner = actor

    completion_history = payload.get("completion_history", [])
    completion_fields = {
        "from_state", "completed_revision", "completed_at", "reopened_at", "reopened_by", "reason",
        "candidate_id", "candidate_manifest", "candidate_sha256", "generation", "artifacts",
        "external_artifacts", "verified", "unverified", "mistake_check", "report", "event_receipts",
        "acknowledged_by",
    }
    if not isinstance(completion_history, list) or len(completion_history) > 100:
        raise ValueError(f"任务 completion_history 无效: {path.name}")
    for entry in completion_history:
        if not isinstance(entry, dict) or set(entry) != completion_fields:
            raise ValueError(f"任务 completion_history 条目无效: {path.name}")
        if entry.get("from_state") not in {"completed", "acknowledged"}:
            raise ValueError(f"任务 completion_history 原状态无效: {path.name}")
        completed_revision = entry.get("completed_revision")
        generation = entry.get("generation")
        if (
            isinstance(completed_revision, bool) or not isinstance(completed_revision, int) or completed_revision < 1
            or isinstance(generation, bool) or not isinstance(generation, int) or generation < 1
            or not CANDIDATE_ID_RE.fullmatch(entry.get("candidate_id", ""))
            or not re.fullmatch(r"[0-9a-f]{64}", entry.get("candidate_sha256", ""))
        ):
            raise ValueError(f"任务 completion_history 候选或版本无效: {path.name}")
        for field, max_chars, allow_empty in (
            ("candidate_manifest", 500, False), ("reopened_by", ACTOR_MAX_CHARS, False),
            ("reason", 1000, False), ("mistake_check", 2000, False),
            ("report", 500, False), ("acknowledged_by", ACTOR_MAX_CHARS, True),
        ):
            require_task_text(entry, field, allow_empty=allow_empty, max_chars=max_chars)
        parse_task_timestamp(entry, "completed_at")
        parse_task_timestamp(entry, "reopened_at")
        for field, max_chars in (
            ("artifacts", 500), ("external_artifacts", 1000), ("verified", 2000),
            ("unverified", 2000), ("event_receipts", 1000),
        ):
            require_task_text_list(entry, field, max_chars=max_chars)
        if not entry["artifacts"] and not entry["external_artifacts"]:
            raise ValueError(f"任务 completion_history 缺少历史产物: {path.name}")
        if not entry["verified"] or not entry["unverified"]:
            raise ValueError(f"任务 completion_history 缺少历史验证记录: {path.name}")
        if entry["from_state"] == "acknowledged" and not entry["acknowledged_by"]:
            raise ValueError(f"任务 completion_history 缺少历史核收人: {path.name}")
        if entry["from_state"] == "completed" and entry["acknowledged_by"]:
            raise ValueError(f"任务 completion_history 未核收状态不得带核收人: {path.name}")

    require_task_text_list(payload, "failure_paths", min_items=1, max_items=3, max_chars=1000)
    for field, max_chars in (
        ("pointers", 500), ("artifacts", 500), ("external_artifacts", 1000),
        ("verified", 2000), ("unverified", 2000), ("event_receipts", 1000),
    ):
        require_task_text_list(payload, field, max_chars=max_chars)
    for raw in payload["artifacts"]:
        artifact_path = Path(raw)
        if artifact_path.is_absolute() or ".." in artifact_path.parts or raw in {"", "."}:
            raise ValueError(f"任务本地产物路径无效: {path.name}")
    for raw in payload["external_artifacts"]:
        parsed_url = urlparse(raw)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc or parsed_url.username or parsed_url.password:
            raise ValueError(f"任务外部产物 URL 无效: {path.name}")

    authorization = payload["authorization_state"]
    if not isinstance(authorization, str) or authorization not in AUTH_STATES:
        raise ValueError(f"任务授权状态无效: {path.name}")
    if authorization in {"user_confirmed", "user_rejected"} and not payload["authorization_evidence"].strip():
        raise ValueError(f"任务授权证据缺失: {path.name}")
    history = payload.get("authorization_history")
    if not isinstance(history, list):
        raise ValueError(f"任务授权历史无效: {path.name}")
    history_times: list[dt.datetime] = []
    for entry in history:
        if not isinstance(entry, dict) or set(entry) != {"at", "state", "evidence"}:
            raise ValueError(f"任务授权历史条目无效: {path.name}")
        history_times.append(parse_task_timestamp(entry, "at"))
        if not isinstance(entry["state"], str) or entry["state"] not in AUTH_STATES:
            raise ValueError(f"任务授权历史状态无效: {path.name}")
        require_task_text(entry, "evidence", max_chars=1000)
    has_evidence = bool(payload["authorization_evidence"].strip())
    if bool(history) != has_evidence:
        raise ValueError(f"任务当前授权证据与历史有无不一致: {path.name}")
    if history and (history[-1]["state"] != authorization or history[-1]["evidence"] != payload["authorization_evidence"]):
        raise ValueError(f"任务当前授权与历史最新记录不一致: {path.name}")

    state = payload.get("execution_state")
    if not isinstance(state, str) or state not in STATES:
        raise ValueError(f"任务执行状态无效: {path.name}")
    resolution = payload.get("resolution")
    resolution_state = "open" if resolution is None else resolution.get("state") if isinstance(resolution, dict) else None
    if resolution is not None:
        common_fields = {
            "state", "reason", "evidence", "resolved_by", "resolved_at",
            "target_revision_before", "receipt_id",
        }
        superseded_fields = common_fields | {
            "replacement_task_id", "replacement_revision", "replacement_state",
        }
        if not isinstance(resolution, dict) or resolution_state not in {
            "superseded", "rejected_by_user", "abandoned",
        }:
            raise ValueError(f"任务收口轴结构无效: {path.name}")
        expected_fields = superseded_fields if resolution_state == "superseded" else common_fields
        if set(resolution) != expected_fields or payload.get("temporary_executor") is not None:
            raise ValueError(f"任务收口轴字段无效: {path.name}")
        allowed_resolution_states = {"queued", "blocked", "waiting_input"}
        if state not in allowed_resolution_states:
            raise ValueError(f"当前执行状态不允许该收口结论: {path.name}")
        for field in ("reason", "evidence", "resolved_by", "receipt_id"):
            if not isinstance(resolution[field], str):
                raise ValueError(f"任务收口证据类型无效: {path.name}:{field}")
            if not resolution[field].strip() or len(resolution[field]) > 1000:
                raise ValueError(f"任务收口证据缺失: {path.name}:{field}")
        if (
            not resolution["resolved_by"].startswith("统筹部/")
            or any(char.isspace() for char in resolution["resolved_by"])
            or not re.fullmatch(r"RES-[0-9]{8}T[0-9]{6}-[A-F0-9]{8}", resolution["receipt_id"])
            or isinstance(resolution["target_revision_before"], bool)
            or not isinstance(resolution["target_revision_before"], int)
            or resolution["target_revision_before"] < 1
        ):
            raise ValueError(f"任务收口身份或版本无效: {path.name}")
        if resolution_state == "superseded":
            if (
                not TASK_ID_RE.fullmatch(resolution["replacement_task_id"])
                or resolution["replacement_task_id"] == task_id
                or resolution["replacement_state"] == "queued"
                or resolution["replacement_state"] not in STATES
                or isinstance(resolution["replacement_revision"], bool)
                or not isinstance(resolution["replacement_revision"], int)
                or resolution["replacement_revision"] < 1
            ):
                raise ValueError(f"任务替代版本或后续状态无效: {path.name}")
        elif resolution_state == "rejected_by_user" and authorization != "user_rejected":
            raise ValueError(f"用户拒绝收口必须绑定 user_rejected 授权事实: {path.name}")
        parse_task_timestamp(resolution, "resolved_at")
    completion_class = payload.get("completion_class")
    if not isinstance(completion_class, str) or completion_class not in COMPLETION_CLASSES:
        raise ValueError(f"任务完成类型无效: {path.name}")
    expected_completion_class = "audit" if audit_department(department) else "standard"
    if completion_class != expected_completion_class:
        raise ValueError(f"任务完成类型与部门层级不一致: {path.name}")
    revision = payload.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError(f"任务 revision 无效: {path.name}")
    if resolution_state != "open" and resolution["target_revision_before"] + 1 != revision:
        raise ValueError(f"任务收口版本与当前 revision 不连续: {path.name}")
    created_at = parse_task_timestamp(payload, "created_at")
    updated_at = parse_task_timestamp(payload, "updated_at")
    if updated_at < created_at:
        raise ValueError(f"任务 updated_at 早于 created_at: {path.name}")
    if resolution_state != "open":
        resolved_at = parse_task_timestamp(resolution, "resolved_at")
        if resolved_at != updated_at or resolved_at < created_at:
            raise ValueError(f"任务收口时间越出任务时间轴: {path.name}")
    if history_times != sorted(history_times) or any(timestamp < created_at or timestamp > updated_at for timestamp in history_times):
        raise ValueError(f"任务授权历史时间顺序或边界无效: {path.name}")
    if state == "queued" and payload["claimed_by"].strip():
        raise ValueError(f"待领取任务不得预填 claimed_by: {path.name}")
    if state not in {"blocked", "waiting_input"} and payload["block_reason"].strip():
        raise ValueError(f"任务当前状态不得预填 block_reason: {path.name}")
    if state in {"blocked", "waiting_input"} and not payload["block_reason"].strip():
        raise ValueError(f"阻断或等待输入任务缺失 block_reason: {path.name}")
    if state in {"queued", "claimed", "blocked", "waiting_input"} and any((
        payload["artifacts"], payload["external_artifacts"], payload["verified"], payload["unverified"],
        payload["mistake_check"].strip(), payload["report"].strip(), payload["event_receipts"],
    )):
        raise ValueError(f"未完成任务不得预填交付结果: {path.name}")
    if state in {"claimed", "blocked", "waiting_input", "completed", "acknowledged"} and not payload["claimed_by"].strip():
        raise ValueError(f"任务已进入执行生命周期但 claimed_by 缺失: {path.name}")
    if ownership_history and payload["claimed_by"] != ownership_history[-1]["actor"]:
        raise ValueError(f"任务 claimed_by 与 ownership_history 不一致: {path.name}")
    if state in {"claimed", "completed", "acknowledged"} and authorization not in {"none", "user_confirmed"}:
        raise ValueError(f"任务当前执行状态与授权状态冲突: {path.name}")
    if state in {"blocked", "waiting_input"} and authorization in {"user_required", "user_rejected"} and not has_evidence:
        raise ValueError(f"阻断或等待输入任务的授权变更缺失证据: {path.name}")
    if state == "acknowledged" and not payload.get("acknowledged_by", "").strip():
        raise ValueError(f"已核收任务缺失 acknowledged_by: {path.name}")
    if state != "acknowledged" and payload.get("acknowledged_by", "").strip():
        raise ValueError(f"未核收任务不得预填 acknowledged_by: {path.name}")
    state_action_receipt = payload.get("state_action_receipt")
    if state_action_receipt is not None:
        if not isinstance(state_action_receipt, dict) or set(state_action_receipt) != {
            "action", "actor", "reason", "target_state", "candidate_generation",
        }:
            raise ValueError(f"任务 state_action_receipt 结构无效: {path.name}")
        action = state_action_receipt.get("action")
        target_state = state_action_receipt.get("target_state")
        expected_states = {
            "claim": "claimed", "block": "blocked", "wait": "waiting_input", "resume": "claimed",
        }
        if action not in expected_states or target_state != expected_states[action]:
            raise ValueError(f"任务 state_action_receipt 动作或目标状态无效: {path.name}")
        require_task_text(state_action_receipt, "actor", max_chars=ACTOR_MAX_CHARS)
        reason = require_task_text(state_action_receipt, "reason", allow_empty=True, max_chars=2000)
        if (action in {"block", "wait"}) != bool(reason):
            raise ValueError(f"任务 state_action_receipt 原因与动作不一致: {path.name}")
        generation = state_action_receipt.get("candidate_generation")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError(f"任务 state_action_receipt 候选代次无效: {path.name}")
        if state in {"claimed", "blocked", "waiting_input"} and target_state != state:
            raise ValueError(f"任务 state_action_receipt 与当前状态不一致: {path.name}")
    if state in {"completed", "acknowledged"}:
        if not payload["artifacts"] and not payload["external_artifacts"]:
            raise ValueError(f"已完成任务缺失产物: {path.name}")
        if not payload["verified"] or not payload["unverified"] or not payload["mistake_check"].strip():
            raise ValueError(f"已完成任务缺失验证或自检记录: {path.name}")
        if not payload["report"].strip():
            raise ValueError(f"已完成任务缺失 report 记录: {path.name}")
        if completion_class == "audit" and payload["report"] not in payload["artifacts"]:
            raise ValueError(f"审核任务 report 未同时列入本地产物: {path.name}")
    return payload


def load_task_at(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"任务文件不安全: {path}")
    payload = strict_json_loads(path.read_text(encoding="utf-8"), str(path))
    return validate_task_payload(payload, path)


def locate(task_id: str) -> tuple[str, Path, dict]:
    if not TASK_ID_RE.fullmatch(task_id):
        raise ValueError("任务 ID 格式非法")
    path = task_path(task_id)
    if not path.exists():
        raise ValueError(f"任务不存在: {task_id}")
    task = load_task_at(path)
    return task["execution_state"], path, task


def all_tasks() -> list[tuple[str, Path, dict]]:
    global _TASK_CACHE
    if _TASK_CACHE is not None:
        return _TASK_CACHE
    tasks = [locate(path.stem) for path in sorted(TASKS.glob("TASK-*.json"))]
    by_id = {task["task_id"]: task for _, _, task in tasks}
    for _, path, task in tasks:
        resolution = task.get("resolution")
        if resolution is None or resolution.get("state") != "superseded":
            continue
        replacement = by_id.get(resolution["replacement_task_id"])
        if (
            replacement is None
            or replacement.get("resolution") is not None
            or replacement["revision"] < resolution["replacement_revision"]
            or replacement["execution_state"] == "queued"
        ):
            raise ValueError(f"任务替代证据已缺失、倒退或再次收口: {path.name}")
    _TASK_CACHE = tasks
    return tasks


def invalidate_task_cache() -> None:
    global _TASK_CACHE
    _TASK_CACHE = None


def configured_departments() -> dict[str, str]:
    if SESSION_STATE.is_symlink() or not SESSION_STATE.is_file():
        raise ValueError("会话启动状态缺失或不安全")
    payload = strict_json_loads(SESSION_STATE.read_text(encoding="utf-8"), str(SESSION_STATE))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("会话启动状态版本无效")
    departments = payload.get("departments")
    if not isinstance(departments, dict) or not departments:
        raise ValueError("会话启动状态未登记部门")
    result: dict[str, str] = {}
    for name, item in departments.items():
        if not isinstance(name, str) or not isinstance(item, dict):
            raise ValueError("会话启动状态部门条目无效")
        role_id = item.get("role_id")
        if not isinstance(role_id, str) or ROLE_DEPARTMENT_NAMES.get(role_id) != name:
            raise ValueError(f"会话启动状态的部门与固定角色不一致: {name}")
        if not isinstance(item.get("active"), bool):
            raise ValueError(f"会话启动状态 active 无效: {name}")
        department_path = DEPARTMENTS / name
        ensure_plain_dir(department_path, DEPARTMENTS)
        result[name] = role_id
    return result


def department_names() -> list[str]:
    return sorted(configured_departments())


def require_department(name: str) -> None:
    if name not in department_names():
        raise ValueError(f"未知部门: {name}")
    payload = strict_json_loads(SESSION_STATE.read_text(encoding="utf-8"), str(SESSION_STATE))
    if not payload["departments"][name]["active"]:
        raise ValueError(f"部门已停用，不能承接新动作: {name}")


def audit_department(name: str) -> bool:
    role_id = configured_departments().get(name)
    if role_id is None:
        raise ValueError(f"未登记部门: {name}")
    return role_id in AUDIT_ROLE_IDS


def local_artifact(raw: str) -> str:
    value = clean("artifact", raw, max_chars=500)
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT / candidate
    project_lexical = Path(os.path.abspath(str(PROJECT)))
    candidate_lexical = Path(os.path.abspath(str(candidate)))
    try:
        relative = candidate_lexical.relative_to(project_lexical)
        current = PROJECT
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(PROJECT.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ValueError(f"本地产物不存在、越界或经过符号链接: {value}") from exc
    if not (resolved.is_file() or resolved.is_dir()):
        raise ValueError(f"本地产物类型不受支持: {value}")
    if resolved == PROJECT.resolve(strict=True):
        raise ValueError("项目根目录不能作为任务产物")
    return resolved.relative_to(PROJECT.resolve(strict=True)).as_posix()


def external_artifact(raw: str) -> str:
    value = clean("external-artifact", raw, max_chars=1000)
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("external-artifact 必须是无内嵌凭据的 http/https URL")
    return value


def audit_report(
    raw: str, department: str, task_id: str, *, expected_decision: str = "", expected_candidate: str = "",
) -> str:
    if not raw.strip() or raw.strip() == "不适用":
        raise ValueError("审核任务必须提交本部门审核报告")
    relative = local_artifact(raw)
    expected = f"docs/collaboration/部门/{department}/报告/"
    if not relative.startswith(expected) or not relative.endswith(".md"):
        raise ValueError(f"审核报告必须是本部门 报告/ 下的 Markdown 文件: {expected}")
    text = (PROJECT / relative).read_text(encoding="utf-8-sig")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise ValueError("审核报告缺少 YAML frontmatter")
    header = text.split("\n---\n", 1)[0][4:]
    fields = {}
    for line_number, line in enumerate(header.splitlines(), start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line != line.lstrip() or ":" not in line:
            raise ValueError(f"审核报告 YAML 只允许顶层单行字段: line {line_number}")
        key, value = line.split(":", 1)
        normalized_key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", normalized_key):
            raise ValueError(f"审核报告 YAML 字段名无效: line {line_number}")
        if normalized_key in fields:
            raise ValueError(f"审核报告 YAML 含重复字段: {normalized_key}")
        scalar = value.strip()
        if scalar.startswith('"') or scalar.endswith('"'):
            if not (scalar.startswith('"') and scalar.endswith('"')):
                raise ValueError(f"审核报告 YAML 引号无效: {normalized_key}")
            try:
                scalar = json.loads(scalar)
            except json.JSONDecodeError as exc:
                raise ValueError(f"审核报告 YAML 字符串无效: {normalized_key}") from exc
        elif scalar.startswith("'") or scalar.endswith("'"):
            if not (scalar.startswith("'") and scalar.endswith("'")):
                raise ValueError(f"审核报告 YAML 引号无效: {normalized_key}")
            scalar = scalar[1:-1].replace("''", "'")
        if not isinstance(scalar, str):
            raise ValueError(f"审核报告 YAML 字段必须是字符串: {normalized_key}")
        fields[normalized_key] = scalar.strip()
    required = {"type", "department", "target", "status", "date", "related_task", "decision", "tags", "summary"}
    missing = sorted(key for key in required if not fields.get(key))
    if missing:
        raise ValueError("审核报告 YAML 缺少字段: " + ", ".join(missing))
    if fields["type"] != "audit_report" or fields["department"] != department or fields["related_task"] != task_id:
        raise ValueError("审核报告 YAML 的 type / department / related_task 与任务不一致")
    if fields["status"] != "final":
        raise ValueError("审核报告 status 必须为 final；pending 草稿不能完成审核任务")
    if fields["decision"] not in {"pass", "fail"}:
        raise ValueError("审核报告 decision 必须为 pass 或 fail")
    if expected_decision and fields["decision"] != expected_decision:
        raise ValueError(f"审核报告 decision 必须匹配本次 gate verdict: {expected_decision}")
    if expected_candidate and fields.get("candidate_id") != expected_candidate:
        raise ValueError("审核报告 candidate_id 必须匹配当前唯一候选")
    placeholders = {"待定", "待补", "待填", "待填写", "pending", "todo", "tbd", "n/a", "-"}
    normalized_summary = fields["summary"].strip().lower()
    if normalized_summary in placeholders or normalized_summary.startswith(("待填", "待补", "todo", "tbd")):
        raise ValueError("审核报告 summary 仍是占位内容")
    return relative


def registered_department_actor(department: str) -> str:
    if SESSION_STATE.is_symlink() or not SESSION_STATE.is_file():
        raise ValueError("会话状态缺失或不安全，不能执行部门动作")
    payload = strict_json_loads(SESSION_STATE.read_text(encoding="utf-8"), str(SESSION_STATE))
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or payload.get("protocol_version") != PROTOCOL_VERSION
        or not isinstance(payload.get("departments"), dict)
    ):
        raise ValueError("会话启动状态根真值无效")
    role_id = configured_departments().get(department)
    if role_id is None:
        raise ValueError(f"部门未登记: {department}")
    item = payload["departments"].get(department, {})
    thread_id = item.get("thread_id", "")
    if (
        item.get("role_id") != role_id
        or item.get("active") is not True
        or item.get("step") != "registered"
        or not isinstance(thread_id, str)
        or not thread_id
        or len(thread_id) > THREAD_ID_MAX_CHARS
        or thread_id.startswith("=")
        or any(char.isspace() for char in thread_id)
    ):
        raise ValueError(f"{department} 会话尚未登记，不能执行部门动作")
    return f"{department}/{thread_id}"


def registered_lead_actor() -> str:
    return registered_department_actor("统筹部")


def require_task_actor(task: dict, actor_value: str) -> str:
    actor = clean("actor", actor_value, max_chars=ACTOR_MAX_CHARS)
    expected = registered_department_actor(task["department"])
    if actor != expected:
        raise ValueError(f"actor 必须匹配当前已登记部门会话: {expected}")
    if task.get("claimed_by") != actor:
        raise ValueError(
            f"TASK owner 已漂移: task={task.get('claimed_by') or '未领取'} current={actor};"
            "换班后必须先执行 rebind-owner"
        )
    return actor


def require_current_slice_task(task: dict, action: str) -> None:
    if task.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"LEGACY_TASK_COLD_ONLY | {action} 不得重新激活 1.4 历史 TASK；"
            "只允许显式清账、核收或 --include-cold 审计"
        )


def load_dispatch_control() -> dict:
    if not DISPATCH_CONTROL.exists():
        raise ValueError("派单控制缺失；为避免失控派单，已拒绝新工作")
    ensure_plain_file(DISPATCH_CONTROL, COLLAB, label="派单控制")
    payload = strict_json_loads(DISPATCH_CONTROL.read_text(encoding="utf-8"), str(DISPATCH_CONTROL))
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "mode", "updated_at", "history"}:
        raise ValueError("派单控制结构无效")
    if payload["schema_version"] != 1 or payload["mode"] not in {"normal", "frozen"}:
        raise ValueError("派单控制版本或模式无效")
    history = payload["history"]
    if history == []:
        if payload["mode"] != "normal" or payload["updated_at"] != "":
            raise ValueError("初始派单控制状态无效")
        return payload
    if not isinstance(history, list) or len(history) > 1000:
        raise ValueError("派单控制历史无效")
    expected_action = "freeze"
    timestamps: list[dt.datetime] = []
    for event in history:
        if not isinstance(event, dict) or set(event) != {"at", "action", "actor", "evidence"}:
            raise ValueError("派单控制历史条目无效")
        if event["action"] != expected_action:
            raise ValueError("派单控制历史顺序无效")
        expected_action = "unfreeze" if expected_action == "freeze" else "freeze"
        actor = event["actor"]
        evidence = event["evidence"]
        if (
            not isinstance(actor, str) or not actor.startswith("统筹部/") or len(actor) > ACTOR_MAX_CHARS
            or any(char.isspace() for char in actor)
            or not isinstance(evidence, str) or not evidence.strip() or evidence != evidence.strip()
            or len(evidence) > 1000
        ):
            raise ValueError("派单控制 actor 或 evidence 无效")
        try:
            timestamp = dt.datetime.fromisoformat(event["at"])
        except (TypeError, ValueError) as exc:
            raise ValueError("派单控制时间无效") from exc
        if timestamp.tzinfo is None:
            raise ValueError("派单控制时间缺失时区")
        timestamps.append(timestamp)
    if timestamps != sorted(timestamps):
        raise ValueError("派单控制时间顺序无效")
    expected_mode = "frozen" if history[-1]["action"] == "freeze" else "normal"
    if payload["mode"] != expected_mode or payload["updated_at"] != history[-1]["at"]:
        raise ValueError("派单控制当前模式与历史不一致")
    return payload


def store_dispatch_control(payload: dict) -> None:
    write_atomic(
        DISPATCH_CONTROL,
        (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        0o600,
    )


def default_slice_control() -> dict:
    return {
        "schema_version": 1,
        "protocol_version": PROTOCOL_VERSION,
        "active_slice": None,
        "history": [],
        "candidate_ids": [],
        "host_runtime": {"mode": "manual-degraded", "capabilities": []},
    }


def load_slice_control() -> dict:
    if SLICE_CONTROL.is_symlink() or not SLICE_CONTROL.is_file():
        raise ValueError("切片控制缺失或不安全；运行协议升级或重新生成协作层")
    payload = strict_json_loads(SLICE_CONTROL.read_text(encoding="utf-8"), str(SLICE_CONTROL))
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "protocol_version", "active_slice", "history", "candidate_ids", "host_runtime",
    }:
        raise ValueError("切片控制结构无效")
    if payload.get("schema_version") != 1 or payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("切片控制版本无效")
    host = payload.get("host_runtime")
    if host != {"mode": "manual-degraded", "capabilities": []}:
        raise ValueError("当前版本只允许可证明的 manual-degraded 宿主边界")
    history = payload.get("history")
    if not isinstance(history, list) or len(history) > 100:
        raise ValueError("切片冷历史索引无效")
    candidate_ids = payload.get("candidate_ids")
    if (
        not isinstance(candidate_ids, list)
        or len(candidate_ids) > 100000
        or len(candidate_ids) != len(set(candidate_ids))
        or any(not isinstance(item, str) or not CANDIDATE_ID_RE.fullmatch(item) for item in candidate_ids)
    ):
        raise ValueError("候选永久身份账本无效")
    seen_slices: set[str] = set()
    seen_candidates: set[str] = set()
    for entry in history:
        base_fields = {
            "slice_id", "owner_task_id", "candidate_id", "candidate_manifest",
            "candidate_sha256", "closed_at", "metrics",
        }
        evidence_fields = {"user_exit", "revision_requests"}
        accepted_fields = {
            frozenset(base_fields),
            frozenset(base_fields | {"resolution"}),
            frozenset(base_fields | evidence_fields),
            frozenset(base_fields | evidence_fields | {"resolution"}),
        }
        if not isinstance(entry, dict) or frozenset(entry) not in accepted_fields:
            raise ValueError("切片冷历史条目结构无效")
        if (
            not SLICE_ID_RE.fullmatch(entry.get("slice_id", ""))
            or not TASK_ID_RE.fullmatch(entry.get("owner_task_id", ""))
            or entry["slice_id"] in seen_slices
        ):
            raise ValueError("切片冷历史身份重复或无效")
        seen_slices.add(entry["slice_id"])
        candidate_id = entry.get("candidate_id", "")
        manifest = entry.get("candidate_manifest", "")
        digest = entry.get("candidate_sha256", "")
        if candidate_id:
            if (
                not CANDIDATE_ID_RE.fullmatch(candidate_id)
                or candidate_id in seen_candidates
                or not isinstance(manifest, str) or not manifest or len(manifest) > 500
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
            ):
                raise ValueError("切片冷历史候选身份重复或无效")
            if candidate_id not in candidate_ids:
                raise ValueError("切片冷历史候选未登记到永久身份账本")
            seen_candidates.add(candidate_id)
        elif "resolution" not in entry or manifest or digest:
            raise ValueError("无候选切片只允许以 resolution 收口")
        if "resolution" in entry and entry["resolution"] not in {"rejected_by_user", "abandoned"}:
            raise ValueError("切片冷历史 resolution 无效")
        if not isinstance(entry.get("metrics"), list):
            raise ValueError("切片冷历史 metrics 无效")
        if evidence_fields <= set(entry):
            user_exit = entry["user_exit"]
            revision_requests = entry["revision_requests"]
            if (
                not isinstance(user_exit, dict)
                or set(user_exit) != {"status", "evidence", "actor", "at", "candidate_id", "generation"}
                or user_exit.get("status") not in {"pending", "needs_revision", "verified", "not_applicable"}
                or not isinstance(revision_requests, list)
                or len(revision_requests) > 100
            ):
                raise ValueError("切片冷历史用户出口或修订证据无效")
            user_candidate = user_exit.get("candidate_id")
            user_generation = user_exit.get("generation")
            if candidate_id:
                if (
                    user_candidate != candidate_id
                    or isinstance(user_generation, bool)
                    or not isinstance(user_generation, int)
                    or user_generation < 1
                    or not all(isinstance(user_exit.get(field), str) for field in ("evidence", "actor", "at"))
                    or (
                        user_exit["status"] == "pending"
                        and any(user_exit[field] for field in ("evidence", "actor", "at"))
                    )
                    or (
                        user_exit["status"] != "pending"
                        and not all(user_exit[field] for field in ("evidence", "actor", "at"))
                    )
                ):
                    raise ValueError("切片冷历史用户出口与最终候选不一致")
                if user_exit["status"] != "pending":
                    parse_task_timestamp(user_exit, "at")
            elif user_exit != {
                "status": "pending", "evidence": "", "actor": "", "at": "",
                "candidate_id": "", "generation": 0,
            } or revision_requests:
                raise ValueError("无候选切片的冷历史用户出口或修订证据无效")

            expected_revision_fields = {
                "source_candidate_id", "source_generation", "evidence", "actor", "at", "status",
                "consumed_by_candidate_id", "consumed_generation", "consumed_at", "superseded_user_exit",
            }
            seen_revision_sources: set[tuple[str, int]] = set()
            pending_revision = None
            source_generations: list[int] = []
            for request in revision_requests:
                if not isinstance(request, dict) or set(request) != expected_revision_fields:
                    raise ValueError("切片冷历史用户修订记录结构无效")
                source = (request.get("source_candidate_id"), request.get("source_generation"))
                if (
                    not isinstance(source[0], str) or not CANDIDATE_ID_RE.fullmatch(source[0])
                    or isinstance(source[1], bool) or not isinstance(source[1], int) or source[1] < 1
                    or source in seen_revision_sources or source[0] not in candidate_ids
                    or not all(
                        isinstance(request.get(field), str) and request[field]
                        for field in ("evidence", "actor", "at")
                    )
                    or request.get("status") not in {"pending", "consumed"}
                ):
                    raise ValueError("切片冷历史用户修订记录内容无效")
                parse_task_timestamp(request, "at")
                superseded = request["superseded_user_exit"]
                if (
                    not isinstance(superseded, dict)
                    or set(superseded) != {"status", "evidence", "actor", "at", "candidate_id", "generation"}
                    or superseded.get("status") not in {"pending", "verified", "not_applicable"}
                    or superseded.get("candidate_id") != source[0]
                    or superseded.get("generation") != source[1]
                    or not all(isinstance(superseded.get(field), str) for field in ("evidence", "actor", "at"))
                    or (
                        superseded["status"] == "pending"
                        and any(superseded[field] for field in ("evidence", "actor", "at"))
                    )
                    or (
                        superseded["status"] != "pending"
                        and not all(superseded[field] for field in ("evidence", "actor", "at"))
                    )
                ):
                    raise ValueError("切片冷历史未保全被替代的用户出口")
                if superseded["status"] != "pending":
                    parse_task_timestamp(superseded, "at")
                seen_revision_sources.add(source)
                source_generations.append(source[1])
                if request["status"] == "pending":
                    if (
                        request["consumed_by_candidate_id"] != ""
                        or request["consumed_generation"] != 0
                        or request["consumed_at"] != ""
                        or pending_revision is not None
                    ):
                        raise ValueError("切片冷历史待消费用户修订记录无效")
                    pending_revision = request
                else:
                    consumed_id = request.get("consumed_by_candidate_id")
                    consumed_generation = request.get("consumed_generation")
                    if (
                        not isinstance(consumed_id, str) or not CANDIDATE_ID_RE.fullmatch(consumed_id)
                        or consumed_id not in candidate_ids or consumed_id == source[0]
                        or isinstance(consumed_generation, bool)
                        or not isinstance(consumed_generation, int)
                        or consumed_generation != source[1] + 1
                        or consumed_generation > user_generation
                        or not isinstance(request.get("consumed_at"), str)
                        or not request["consumed_at"]
                    ):
                        raise ValueError("切片冷历史已消费用户修订记录无效")
                    parse_task_timestamp(request, "consumed_at")
            if source_generations != sorted(source_generations):
                raise ValueError("切片冷历史用户修订代次顺序无效")
            if pending_revision is not None:
                if (
                    "resolution" not in entry
                    or user_exit["status"] != "needs_revision"
                    or pending_revision["source_candidate_id"] != user_candidate
                    or pending_revision["source_generation"] != user_generation
                ):
                    raise ValueError("切片冷历史待消费修订与最终用户出口不一致")
            elif user_exit["status"] == "needs_revision":
                raise ValueError("切片冷历史 needs_revision 缺少待消费记录")
            if "resolution" not in entry and user_exit["status"] not in {"verified", "not_applicable"}:
                raise ValueError("正常核收的切片冷历史缺少最终用户出口")
    active = payload.get("active_slice")
    if active is None:
        return payload
    required = {
        "slice_id", "owner_task_id", "required_gates", "gate_tasks", "candidate",
        "user_exit", "revision_requests", "created_at", "metrics",
    }
    if not isinstance(active, dict) or set(active) != required:
        raise ValueError("活动切片结构无效")
    if not SLICE_ID_RE.fullmatch(active.get("slice_id", "")) or not TASK_ID_RE.fullmatch(active.get("owner_task_id", "")):
        raise ValueError("活动切片身份无效")
    gates = active.get("required_gates")
    gate_tasks = active.get("gate_tasks")
    if (
        not isinstance(gates, list) or len(gates) > 2 or len(gates) != len(set(gates))
        or any(gate not in AUDIT_ROLE_IDS for gate in gates)
        or not isinstance(gate_tasks, dict) or any(gate not in gates for gate in gate_tasks)
        or any(not TASK_ID_RE.fullmatch(value) for value in gate_tasks.values())
    ):
        raise ValueError("活动切片 gate 结构无效")
    candidate = active.get("candidate")
    if candidate is not None:
        if not isinstance(candidate, dict) or set(candidate) != {
            "candidate_id", "manifest", "sha256", "generation", "bound_by", "bound_at",
        }:
            raise ValueError("候选身份结构无效")
        if (
            not CANDIDATE_ID_RE.fullmatch(candidate.get("candidate_id", ""))
            or not re.fullmatch(r"[0-9a-f]{64}", candidate.get("sha256", ""))
            or isinstance(candidate.get("generation"), bool)
            or not isinstance(candidate.get("generation"), int)
            or candidate["generation"] < 1
        ):
            raise ValueError("候选身份字段无效")
        if candidate["candidate_id"] not in candidate_ids:
            raise ValueError("活动候选未登记到永久身份账本")
    user_exit = active.get("user_exit")
    if not isinstance(user_exit, dict) or set(user_exit) != {
        "status", "evidence", "actor", "at", "candidate_id", "generation",
    }:
        raise ValueError("用户出口结构无效")
    if user_exit.get("status") not in {"pending", "needs_revision", "verified", "not_applicable"}:
        raise ValueError("用户出口状态无效")
    if (
        not isinstance(user_exit.get("candidate_id"), str)
        or isinstance(user_exit.get("generation"), bool)
        or not isinstance(user_exit.get("generation"), int)
        or not all(isinstance(user_exit.get(field), str) for field in ("evidence", "actor", "at"))
    ):
        raise ValueError("用户出口字段无效")
    if candidate is None:
        if user_exit != {
            "status": "pending", "evidence": "", "actor": "", "at": "",
            "candidate_id": "", "generation": 0,
        }:
            raise ValueError("无候选切片的用户出口必须保持初始 pending")
    elif (
        user_exit["candidate_id"] != candidate["candidate_id"]
        or user_exit["generation"] != candidate["generation"]
        or (
            user_exit["status"] == "pending"
            and any(user_exit[field] for field in ("evidence", "actor", "at"))
        )
        or (
            user_exit["status"] != "pending"
            and not all(user_exit[field] for field in ("evidence", "actor", "at"))
        )
    ):
        raise ValueError("用户出口与当前候选代次不一致")
    revision_requests = active.get("revision_requests")
    if not isinstance(revision_requests, list) or len(revision_requests) > 100:
        raise ValueError("用户修订记录无效")
    seen_revision_sources: set[tuple[str, int]] = set()
    pending_revision = None
    for request in revision_requests:
        expected_fields = {
            "source_candidate_id", "source_generation", "evidence", "actor", "at", "status",
            "consumed_by_candidate_id", "consumed_generation", "consumed_at", "superseded_user_exit",
        }
        if not isinstance(request, dict) or set(request) != expected_fields:
            raise ValueError("用户修订记录结构无效")
        source = (request.get("source_candidate_id"), request.get("source_generation"))
        if (
            not isinstance(source[0], str) or not CANDIDATE_ID_RE.fullmatch(source[0])
            or isinstance(source[1], bool) or not isinstance(source[1], int) or source[1] < 1
            or source in seen_revision_sources or source[0] not in candidate_ids
            or not all(isinstance(request.get(field), str) and request[field] for field in ("evidence", "actor", "at"))
            or request.get("status") not in {"pending", "consumed"}
        ):
            raise ValueError("用户修订记录内容无效")
        superseded = request["superseded_user_exit"]
        if (
            not isinstance(superseded, dict)
            or set(superseded) != {"status", "evidence", "actor", "at", "candidate_id", "generation"}
            or superseded.get("status") not in {"pending", "verified", "not_applicable"}
            or superseded.get("candidate_id") != source[0]
            or superseded.get("generation") != source[1]
            or not all(isinstance(superseded.get(field), str) for field in ("evidence", "actor", "at"))
            or (
                superseded["status"] == "pending"
                and any(superseded[field] for field in ("evidence", "actor", "at"))
            )
            or (
                superseded["status"] != "pending"
                and not all(superseded[field] for field in ("evidence", "actor", "at"))
            )
        ):
            raise ValueError("用户修订记录未保全被替代的用户出口")
        seen_revision_sources.add(source)
        if request["status"] == "pending":
            if (
                request["consumed_by_candidate_id"] != ""
                or request["consumed_generation"] != 0
                or request["consumed_at"] != ""
                or pending_revision is not None
            ):
                raise ValueError("待消费用户修订记录无效")
            pending_revision = request
        elif (
            not CANDIDATE_ID_RE.fullmatch(request["consumed_by_candidate_id"])
            or request["consumed_by_candidate_id"] not in candidate_ids
            or isinstance(request["consumed_generation"], bool)
            or request["consumed_generation"] != request["source_generation"] + 1
            or not isinstance(request["consumed_at"], str) or not request["consumed_at"]
        ):
            raise ValueError("已消费用户修订记录无效")
    if user_exit["status"] == "needs_revision":
        if (
            pending_revision is None
            or pending_revision["source_candidate_id"] != candidate["candidate_id"]
            or pending_revision["source_generation"] != candidate["generation"]
            or pending_revision["evidence"] != user_exit["evidence"]
            or pending_revision["actor"] != user_exit["actor"]
            or pending_revision["at"] != user_exit["at"]
        ):
            raise ValueError("用户修订出口缺少同代审计记录")
    elif pending_revision is not None:
        raise ValueError("待消费用户修订记录与用户出口状态冲突")
    if not isinstance(active.get("metrics"), list) or len(active["metrics"]) > 100:
        raise ValueError("切片 metrics 无效")
    for metric in active["metrics"]:
        expected_metric_fields = {
            "at", "actor", "source", "input_tokens", "output_tokens", "max_input_tokens",
            "tool_calls", "polls", "hot_context_bytes",
        }
        if not isinstance(metric, dict) or set(metric) != expected_metric_fields:
            raise ValueError("切片 metric 条目无效")
        if any(
            isinstance(metric[field], bool) or not isinstance(metric[field], int) or metric[field] < 0
            for field in expected_metric_fields - {"at", "actor", "source"}
        ) or any(not isinstance(metric[field], str) or not metric[field] for field in ("at", "actor", "source")):
            raise ValueError("切片 metric 内容无效")
    return payload


def store_slice_control(payload: dict) -> None:
    write_atomic(
        SLICE_CONTROL,
        (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        0o600,
    )


def active_slice_task_ids(control: dict | None = None) -> set[str]:
    control = control or load_slice_control()
    active = control.get("active_slice")
    if not active:
        return set()
    return {active["owner_task_id"], *active["gate_tasks"].values()}


def hot_tasks() -> list[tuple[str, Path, dict]]:
    return [locate(task_id) for task_id in sorted(active_slice_task_ids())]


def department_onboard_tasks(
    department: str,
    tasks: list[tuple[str, Path, dict]] | None = None,
) -> tuple[list[tuple[str, Path, dict]], list[tuple[str, Path, dict]]]:
    """Return only machine-authoritative hot and frozen-recovery TASKs for a department."""
    current: list[tuple[str, Path, dict]] = []
    for state, path, task in tasks if tasks is not None else hot_tasks():
        own_open_task = (
            task["department"] == department
            and task.get("resolution") is None
            and state != "acknowledged"
        )
        lead_review = (
            department == "统筹部"
            and state == "completed"
            and task.get("resolution") is None
        )
        if own_open_task or lead_review:
            current.append((state, path, task))

    recovery: list[tuple[str, Path, dict]] = []
    for task_id in legacy_temporary_closeout_blockers():
        state, path, task = locate(task_id)
        if department == "统筹部" or task["department"] == department:
            recovery.append((state, path, task))
    current.sort(key=lambda item: item[2]["task_id"])
    recovery.sort(key=lambda item: item[2]["task_id"])
    return current, recovery


def append_dispatch_event(control: dict, event: dict) -> None:
    history = list(control["history"])
    history.append(event)
    # freeze / unfreeze 成对交替。超过上限时按完整事件对丢弃最老历史，
    # 避免第 1001 次操作写出一个下一次无法读取的控制文件。
    while len(history) > 1000:
        if len(history) < 2 or history[0].get("action") != "freeze" or history[1].get("action") != "unfreeze":
            raise ValueError("派单控制历史无法安全压缩")
        del history[:2]
    control["history"] = history


def require_new_work_open(action: str) -> None:
    if load_dispatch_control()["mode"] == "frozen":
        raise ValueError(
            f"P0_FREEZE_ACTIVE | {action} 已冻结；只允许已领取任务完成或安全停下、清账、核收、交接、换班和证据保全"
        )


def cmd_work_mode(args) -> int:
    control = load_dispatch_control()
    print(f"WORK_MODE | {control['mode']} | history:{len(control['history'])}")
    return 0


def cmd_freeze_new_work(args) -> int:
    actor = clean("actor", args.actor, max_chars=ACTOR_MAX_CHARS)
    expected_actor = registered_lead_actor()
    if actor != expected_actor:
        raise ValueError(f"actor 必须匹配当前已登记统筹会话: {expected_actor}")
    evidence = clean("evidence", args.evidence, max_chars=1000)
    control = load_dispatch_control()
    if control["mode"] == "frozen":
        print(f"WORK_FREEZE_OK | frozen | idempotent | history:{len(control['history'])}")
        return 0
    timestamp = now_iso()
    control["mode"] = "frozen"
    control["updated_at"] = timestamp
    append_dispatch_event(
        control, {"at": timestamp, "action": "freeze", "actor": actor, "evidence": evidence},
    )
    store_dispatch_control(control)
    refresh_inboxes()
    print(f"WORK_FREEZE_OK | frozen | history:{len(control['history'])}")
    return 0


def cmd_unfreeze_new_work(args) -> int:
    actor = clean("actor", args.actor, max_chars=ACTOR_MAX_CHARS)
    expected_actor = registered_lead_actor()
    if actor != expected_actor:
        raise ValueError(f"actor 必须匹配当前已登记统筹会话: {expected_actor}")
    evidence = clean("user-confirmation", args.user_confirmation, max_chars=1000)
    control = load_dispatch_control()
    require_no_legacy_temporary_owner("unfreeze-new-work")
    if control["mode"] == "normal":
        print(f"WORK_UNFREEZE_OK | normal | idempotent | history:{len(control['history'])}")
        return 0
    timestamp = now_iso()
    control["mode"] = "normal"
    control["updated_at"] = timestamp
    append_dispatch_event(
        control, {"at": timestamp, "action": "unfreeze", "actor": actor, "evidence": evidence},
    )
    store_dispatch_control(control)
    refresh_inboxes()
    print(f"WORK_UNFREEZE_OK | normal | history:{len(control['history'])}")
    return 0


def department_hot_digest(department: str, tasks: list[tuple[str, Path, dict]], mode: str) -> str:
    current, recovery = department_onboard_tasks(department, tasks)
    rows = [
        {
            "kind": kind,
            "task_id": task["task_id"],
            "state": state,
            "revision": task["revision"],
            "department": task["department"],
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for kind, selected in (("current", current), ("legacy-closeout", recovery))
        for state, path, task in selected
    ]
    slice_control = load_slice_control()
    payload = {
        "protocol": PROTOCOL_VERSION, "mode": mode, "department": department,
        "active_slice": slice_control["active_slice"], "tasks": rows,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def render_handoff_hot_block(
    department: str, digest: str, tasks: list[tuple[str, Path, dict]], mode: str,
) -> str:
    active = load_slice_control().get("active_slice")
    current, recovery = department_onboard_tasks(department, tasks)
    visible = [
        f"- `{task['task_id']}` · {STATE_CN[state]} · {task['department']} · "
        f"owner `{task.get('claimed_by') or '未领取'}`"
        for state, _, task in current
    ]
    lines = [
        HANDOFF_HOT_BLOCK_START,
        f"<!-- agent-team hot-state:{digest} -->",
        "## 机器生成的当前切片",
        "",
        "> 本区块由任务工具整段重建，只证明当前机械状态；区块外文字是人工提示，不能充当 TASK 身份或 freshness 证据。",
        "",
        f"- 工作模式：`{mode}`",
        f"- 活动切片：`{active['slice_id'] if active else 'none'}`",
    ]
    if active:
        candidate = active.get("candidate") or {}
        gate_states = current_generation_gate_states(active)
        owner_state, _, owner = locate(active["owner_task_id"])
        decision = next_action_decision(active["owner_task_id"])
        lines.extend([
            f"- 候选：`{candidate.get('candidate_id') or 'none'}` · generation `{candidate.get('generation', 0)}`",
            "- 当前代 gate：`" + ", ".join(
                f"{gate}={gate_states.get(gate, 'pending')}" for gate in active["required_gates"]
            ) + "`",
            f"- 用户出口：`{active['user_exit']['status']}`",
            f"- 阻断原因：`{owner.get('block_reason') or 'none'}`",
            f"- 下一合法动作：`{decision['allowed'][0] if decision['allowed'] else 'none'}`"
            + (f" · 目标 `{decision['target_task_id']}`" if decision.get("target_task_id") else ""),
        ])
    lines.extend(visible or ["- 本部门当前没有活动切片 TASK"])
    if recovery:
        lines.extend(["", "### 冻结恢复任务", ""])
        lines.extend(
            f"- `{task['task_id']}` · {task['department']} · {STATE_CN[state]} · 仅允许 frozen 清账/恢复"
            for state, _, task in recovery
        )
    lines.append(HANDOFF_HOT_BLOCK_END)
    return "\n".join(lines)


def extract_handoff_hot_block(text: str) -> str:
    if text.count(HANDOFF_HOT_BLOCK_START) != 1 or text.count(HANDOFF_HOT_BLOCK_END) != 1:
        raise ValueError("交接班文档机器区块缺失或重复")
    start = text.index(HANDOFF_HOT_BLOCK_START)
    end = text.index(HANDOFF_HOT_BLOCK_END, start) + len(HANDOFF_HOT_BLOCK_END)
    if HANDOFF_HOT_BLOCK_END in text[:start]:
        raise ValueError("交接班文档机器区块顺序无效")
    return text[start:end]


def update_handoff_freshness(
    department: str, digest: str, tasks: list[tuple[str, Path, dict]], mode: str,
) -> bytes:
    handoff = DEPARTMENTS / department / "交接班文档.md"
    ensure_plain_file(handoff, COLLAB, label=f"{department} 交接班文档")
    text = handoff.read_text(encoding="utf-8-sig")
    block = render_handoff_hot_block(department, digest, tasks, mode)
    if HANDOFF_HOT_BLOCK_START in text or HANDOFF_HOT_BLOCK_END in text:
        old_block = extract_handoff_hot_block(text)
        updated = text.replace(old_block, block, 1)
    else:
        without_legacy_marker = HANDOFF_FRESHNESS_RE.sub("", text).strip()
        lines = without_legacy_marker.splitlines()
        insert_at = 1 if lines and lines[0].startswith("#") else 0
        lines.insert(insert_at, "")
        lines.insert(insert_at + 1, block)
        updated = "\n".join(lines).rstrip() + "\n"
    if updated != text:
        write_atomic(handoff, updated.encode("utf-8"), 0o644)
    return block.encode("utf-8")


def render_inboxes(*, force: bool = False) -> None:
    tasks = hot_tasks()
    departments = department_names()
    mode = load_dispatch_control()["mode"]
    visible_task_ids = active_slice_task_ids()
    handoff_digests: dict[str, str] = {}
    handoff_block_hashes: dict[str, str] = {}
    inbox_hashes: dict[str, str] = {}
    for department in departments:
        inbox = DEPARTMENTS / department / "收件箱.md"
        if inbox.exists():
            ensure_plain_file(inbox, COLLAB, label=f"{department} 收件箱")
            existing = inbox.read_text(encoding="utf-8-sig")
            if INDEX_MARKER not in existing and not force:
                raise ValueError(f"收件箱尚未迁移为事务索引: {department}")
        queued = [
            (s, p, t) for s, p, t in tasks
            if t["task_id"] in visible_task_ids and t["department"] == department and s == "queued"
            and t.get("resolution") is None
            and t["authorization_state"] in {"none", "user_confirmed"}
        ]
        gated = [
            (s, p, t) for s, p, t in tasks
            if t["task_id"] in visible_task_ids and t["department"] == department and s == "queued"
            and t.get("resolution") is None
            and t["authorization_state"] in {"user_required", "user_rejected"}
        ]
        active = [
            (s, p, t) for s, p, t in tasks
            if t["task_id"] in visible_task_ids and t["department"] == department
            and s in VISIBLE_ACTIVE_STATES and t.get("resolution") is None
        ]
        review = [
            (s, p, t) for s, p, t in tasks
            if department == "统筹部" and t["task_id"] in visible_task_ids
            and s == "completed" and t.get("resolution") is None
        ]
        _, recovery = department_onboard_tasks(department, tasks)
        lines = [
            f"# {department} · 收件箱",
            "",
            INDEX_MARKER,
            "> 自动索引；任务正文与状态以 `../../tasks/` 中的单任务 JSON 为准，不要手工编辑。",
            "",
            "## 待领取",
            "",
        ]
        if queued:
            for state, path, task in queued:
                lines.append(f"- [`{task['task_id']}`](../../tasks/{path.name}) · {task['title']}")
        else:
            lines.append("_(没有待领取任务)_")
        lines.extend(["", "## 待授权 / 已拒绝", ""])
        if gated:
            for state, path, task in gated:
                label = "待用户确认" if task["authorization_state"] == "user_required" else "用户已拒绝"
                lines.append(f"- [`{task['task_id']}`](../../tasks/{path.name}) · {label} · {task['title']}")
        else:
            lines.append("_(没有授权闸任务)_")
        lines.extend(["", "## 当前在办 / 阻断", ""])
        if active:
            for state, path, task in active:
                lines.append(f"- [`{task['task_id']}`](../../tasks/{path.name}) · {STATE_CN[state]} · {task['title']}")
        else:
            lines.append("_(没有在办任务)_")
        if recovery:
            lines.extend(["", "## 冻结恢复任务", ""])
            for state, path, task in recovery:
                lines.append(
                    f"- [`{task['task_id']}`](../../tasks/{path.name}) · 来自:{task['department']} · "
                    f"{STATE_CN[state]} · 仅允许 frozen 清账/恢复"
                )
        if department == "统筹部":
            lines.extend(["", "## 待核收回报", ""])
            if review:
                for state, path, task in review:
                    lines.append(f"- [`{task['task_id']}`](../../tasks/{path.name}) · 来自:{task['department']} · {task['title']}")
            else:
                lines.append("_(没有待核收回报)_")
        inbox_data = ("\n".join(lines).rstrip() + "\n").encode("utf-8")
        write_atomic(inbox, inbox_data, 0o644)
        digest = department_hot_digest(department, tasks, mode)
        handoff_block = update_handoff_freshness(department, digest, tasks, mode)
        handoff_digests[department] = digest
        handoff_block_hashes[department] = hashlib.sha256(handoff_block).hexdigest()
        inbox_hashes[department] = hashlib.sha256(inbox_data).hexdigest()
    write_atomic(
        HOT_STATE,
        (json.dumps({
            "schema_version": 1, "protocol_version": PROTOCOL_VERSION, "mode": mode,
            "handoff_digests": handoff_digests, "handoff_block_hashes": handoff_block_hashes,
            "inbox_hashes": inbox_hashes,
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        0o600,
    )


def verify_hot_state(department: str | None = None) -> None:
    if HOT_STATE.is_symlink() or not HOT_STATE.is_file():
        raise ValueError("HOT_STATE_STALE | freshness 记录缺失；运行 rebuild-index")
    payload = strict_json_loads(HOT_STATE.read_text(encoding="utf-8"), str(HOT_STATE))
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "protocol_version", "mode", "handoff_digests", "handoff_block_hashes", "inbox_hashes",
    } or payload.get("schema_version") != 1 or payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("HOT_STATE_STALE | freshness 记录结构无效")
    tasks = hot_tasks()
    mode = load_dispatch_control()["mode"]
    selected = [department] if department else department_names()
    if department and department not in department_names():
        raise ValueError(f"未知部门: {department}")
    for name in selected:
        expected = department_hot_digest(name, tasks, mode)
        if payload.get("mode") != mode or payload.get("handoff_digests", {}).get(name) != expected:
            raise ValueError(f"HOT_STATE_STALE | {name} 热状态摘要过期；运行 rebuild-index")
        handoff = DEPARTMENTS / name / "交接班文档.md"
        inbox = DEPARTMENTS / name / "收件箱.md"
        ensure_plain_file(handoff, COLLAB, label=f"{name} 交接班文档")
        ensure_plain_file(inbox, COLLAB, label=f"{name} 收件箱")
        marker = f"<!-- agent-team hot-state:{expected} -->"
        handoff_text = handoff.read_text(encoding="utf-8-sig")
        block = extract_handoff_hot_block(handoff_text)
        if block.count(marker) != 1:
            raise ValueError(f"HOT_STATE_STALE | {name} 交接班文档 freshness 不匹配")
        expected_block = render_handoff_hot_block(name, expected, tasks, mode)
        if block != expected_block:
            raise ValueError(f"HOT_STATE_STALE | {name} 交接班文档机器区块已漂移")
        if payload.get("handoff_block_hashes", {}).get(name) != hashlib.sha256(block.encode("utf-8")).hexdigest():
            raise ValueError(f"HOT_STATE_STALE | {name} 交接班文档机器区块哈希已漂移")
        actual_inbox_hash = hashlib.sha256(inbox.read_bytes()).hexdigest()
        if payload.get("inbox_hashes", {}).get(name) != actual_inbox_hash:
            raise ValueError(f"HOT_STATE_STALE | {name} 收件箱内容已漂移")


def refresh_inboxes() -> None:
    try:
        render_inboxes()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"TASK_INDEX_STALE | {exc} | 任务 JSON 已落盘；修复收件箱后运行 rebuild-index", file=sys.stderr)


def recover_index_transaction() -> bool:
    if not INDEX_TRANSACTION.exists():
        return False
    if INDEX_TRANSACTION.is_symlink() or not INDEX_TRANSACTION.is_file():
        raise ValueError("任务索引事务标记不安全")
    payload = strict_json_loads(INDEX_TRANSACTION.read_text(encoding="utf-8"), str(INDEX_TRANSACTION))
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "kind", "operation_id", "task_id"}
        or payload.get("schema_version") != 1
        or payload.get("kind") != "task-index-refresh"
        or not isinstance(payload.get("operation_id"), str)
        or not re.fullmatch(r"IDX-[0-9]{8}T[0-9]{6}-[A-F0-9]{8}", payload["operation_id"])
        or not isinstance(payload.get("task_id"), str)
        or not TASK_ID_RE.fullmatch(payload["task_id"])
    ):
        raise ValueError("任务索引事务标记无效")
    # marker 不携带任何可写路径；恢复只从 TASK 真值重建索引。
    render_inboxes(force=True)
    INDEX_TRANSACTION.unlink()
    fsync_dir(LOCKS)
    print(f"TASK_INDEX_RECOVERY_OK | {payload['operation_id']} | task_id={payload['task_id']}")
    return True


def save_new(task: dict) -> Path:
    path = task_path(task["task_id"])
    if path.exists():
        raise ValueError(f"任务已存在: {task['task_id']}")
    validate_task_payload(task, path)
    data = json.dumps(task, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    write_atomic(path, data, 0o600)
    invalidate_task_cache()
    return path


def transition(task_id: str, action: str, mutate) -> tuple[dict, Path]:
    state, source, task = locate(task_id)
    allowed = TRANSITIONS[action]
    if state not in allowed:
        raise ValueError(f"非法状态转换: {state} --{action}--> ?")
    if task.get("resolution") is not None:
        raise ValueError("已收口任务不得再进入执行状态机")
    target_state = allowed[state]
    task = dict(task)
    mutate(task)
    task["execution_state"] = target_state
    task["updated_at"] = now_iso()
    task["revision"] = int(task.get("revision", 0)) + 1
    validate_task_payload(task, source)
    data = json.dumps(task, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    write_atomic(source, data, 0o600)
    invalidate_task_cache()
    return task, source


def update_task(
    task_id: str, mutate, *, allowed_states: set[str], timestamp: str | None = None,
) -> tuple[dict, Path]:
    state, path, task = locate(task_id)
    if state not in allowed_states:
        raise ValueError(f"当前状态不允许更新任务记录: {state}")
    if task.get("resolution") is not None:
        raise ValueError("已收口任务不得再更新")
    task = dict(task)
    mutate(task)
    task["updated_at"] = timestamp or now_iso()
    task["revision"] = int(task.get("revision", 0)) + 1
    validate_task_payload(task, path)
    data = json.dumps(task, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    write_atomic(path, data, 0o600)
    invalidate_task_cache()
    return task, path


def busy_for(department: str, *, excluding: str | None = None) -> list[str]:
    result = []
    for state, _, task in all_tasks():
        if (state in BUSY_STATES and task["department"] == department and task["task_id"] != excluding
                and not task.get("temporary_executor")):
            result.append(task["task_id"])
    return result


def paths_overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def active_temporary_impacts(task_id: str) -> list[tuple[str, dict]]:
    active: list[tuple[str, dict]] = []
    for _, path, other in all_tasks():
        if other["task_id"] == task_id:
            continue
        temporary = other.get("temporary_executor")
        if not isinstance(temporary, dict) or temporary.get("promotion_state") in {"archived", "abandoned", "cancelled"}:
            continue
        declared = other.get("impact_declaration")
        nested = temporary.get("impact")
        validate_impact_declaration(declared, path, task_id=other["task_id"])
        validate_impact_declaration(nested, path, task_id=other["task_id"])
        if declared != nested:
            raise ValueError(f"临时外包 {other['task_id']} 的影响声明真值冲突")
        active.append((other["task_id"], declared))
    return active


def legacy_archive_recovery_entries() -> dict[str, dict]:
    if not LEGACY_ARCHIVE_RECOVERY.exists():
        return {}
    ensure_plain_file(LEGACY_ARCHIVE_RECOVERY, COLLAB, label="legacy archive recovery")
    payload = strict_json_loads(
        LEGACY_ARCHIVE_RECOVERY.read_text(encoding="utf-8"), str(LEGACY_ARCHIVE_RECOVERY),
    )
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "protocol_version", "entries"}:
        raise ValueError("legacy archive recovery 结构无效")
    entries = payload.get("entries")
    if payload.get("schema_version") != 1 or payload.get("protocol_version") != PROTOCOL_VERSION or not isinstance(entries, dict):
        raise ValueError("legacy archive recovery 版本无效")
    for task_id, entry in entries.items():
        if (
            not TASK_ID_RE.fullmatch(task_id)
            or not isinstance(entry, dict)
            or set(entry) != {"task_sha256", "thread_id", "state", "receipt", "verified_at"}
            or not re.fullmatch(r"[0-9a-f]{64}", entry.get("task_sha256", ""))
            or not isinstance(entry.get("thread_id"), str)
            or entry.get("state") not in {"pending", "verified"}
            or not isinstance(entry.get("receipt"), str)
            or not isinstance(entry.get("verified_at"), str)
            or (entry["state"] == "pending" and (entry["receipt"] or entry["verified_at"]))
            or (entry["state"] == "verified" and (not entry["receipt"] or not entry["verified_at"]))
        ):
            raise ValueError("legacy archive recovery 条目无效")
        path = TASKS / f"{task_id}.json"
        if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["task_sha256"]:
            raise ValueError(f"legacy archive recovery 与原 TASK 字节不一致: {task_id}")
    return entries


def legacy_closeout_index() -> tuple[list[str], set[str]]:
    ensure_plain_file(LEGACY_CLOSEOUT_INDEX, COLLAB, label="legacy closeout index")
    index_bytes = LEGACY_CLOSEOUT_INDEX.read_bytes()
    protocol_path = COLLAB / "协议版本.json"
    ensure_plain_file(protocol_path, COLLAB, label="协议版本")
    protocol = strict_json_loads(protocol_path.read_text(encoding="utf-8"), str(protocol_path))
    managed = protocol.get("managed_files") if isinstance(protocol, dict) else None
    record = managed.get(".locks/legacy-closeout-index.json") if isinstance(managed, dict) else None
    if (
        not isinstance(record, dict)
        or not re.fullmatch(r"[0-9a-f]{64}", record.get("sha256", ""))
        or hashlib.sha256(index_bytes).hexdigest() != record["sha256"]
    ):
        raise ValueError("LEGACY_CLOSEOUT_INDEX_INTEGRITY | 窄索引与协议受管哈希不一致")
    payload = strict_json_loads(
        index_bytes.decode("utf-8"), str(LEGACY_CLOSEOUT_INDEX),
    )
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "protocol_version", "task_ids", "archive_recovery_task_ids",
    }:
        raise ValueError("legacy closeout index 结构无效")
    task_ids = payload.get("task_ids")
    recovery_ids = payload.get("archive_recovery_task_ids")
    if (
        payload.get("schema_version") != 1
        or payload.get("protocol_version") != PROTOCOL_VERSION
        or not isinstance(task_ids, list)
        or not isinstance(recovery_ids, list)
        or len(task_ids) > 10000
        or task_ids != sorted(set(task_ids))
        or recovery_ids != sorted(set(recovery_ids))
        or not set(recovery_ids) <= set(task_ids)
        or any(not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id) for task_id in task_ids)
        or any(not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id) for task_id in recovery_ids)
    ):
        raise ValueError("legacy closeout index 内容无效")
    return task_ids, set(recovery_ids)


def legacy_temporary_terminal(
    task: dict, recovery: dict[str, dict], *, archive_recovery_required: bool = False,
) -> bool:
    temporary = task.get("temporary_executor")
    if task.get("schema_version") == SCHEMA_VERSION or not isinstance(temporary, dict):
        return True
    workspace = temporary.get("workspace")
    cleanup = temporary.get("cleanup_operation")
    session = temporary.get("temporary_session")
    if (
        task.get("execution_state") != "acknowledged"
        or temporary.get("promotion_state") != "archived"
        or not isinstance(workspace, dict) or workspace.get("state") != "removed"
        or not isinstance(cleanup, dict) or cleanup.get("state") != "verified"
        or not isinstance(session, dict) or session.get("state") not in {"archived", "cancelled"}
    ):
        return False
    entry = recovery.get(task["task_id"])
    if archive_recovery_required:
        return entry is not None and entry.get("state") == "verified"
    return entry is None or entry.get("state") == "verified"


def legacy_temporary_closeout_blockers() -> list[str]:
    recovery = legacy_archive_recovery_entries()
    task_ids, recovery_required = legacy_closeout_index()
    blockers = []
    for task_id in task_ids:
        _, _, task = locate(task_id)
        if task.get("schema_version") == SCHEMA_VERSION or not isinstance(task.get("temporary_executor"), dict):
            raise ValueError(f"legacy closeout index 指向非 legacy temporary TASK: {task_id}")
        if not legacy_temporary_terminal(
            task, recovery, archive_recovery_required=task_id in recovery_required,
        ):
            blockers.append(task_id)
    return blockers


def require_no_legacy_temporary_owner(action: str) -> None:
    blockers = legacy_temporary_closeout_blockers()
    if blockers:
        raise ValueError(
            f"LEGACY_TEMPORARY_CLOSEOUT_REQUIRED | {action} 前必须在 frozen 模式完成 1.4 临时执行者收口: "
            + ", ".join(blockers)
        )


def require_formal_temporary_compatibility(task: dict, path: Path) -> None:
    temporary_impacts = active_temporary_impacts(task["task_id"])
    if not temporary_impacts:
        return
    impact = task.get("impact_declaration")
    if impact is None:
        raise ValueError(
            "存在未收口临时外包，正式任务 claim 前必须先用 declare-impact 明确声明影响范围"
        )
    validate_impact_declaration(impact, path, task_id=task["task_id"])
    if impact["external_effects"] != ["none"]:
        raise ValueError("正式任务存在外部副作用，无法自动证明与临时外包并行安全")
    for other_task_id, other in temporary_impacts:
        if other["external_effects"] != ["none"]:
            raise ValueError(f"临时外包 {other_task_id} 存在外部副作用，拒绝并行 claim")
        if any(paths_overlap(left, right) for left in impact["write_paths"] for right in other["write_paths"]):
            raise ValueError(f"正式任务写路径与临时外包冲突: {other_task_id}")
        shared = sorted(set(impact["shared_contracts"]) & set(other["shared_contracts"]))
        if shared:
            raise ValueError(f"正式任务共享契约与临时外包冲突: {other_task_id}: {', '.join(shared)}")


def cmd_declare_impact(args) -> int:
    state, path, current = locate(args.task_id)
    require_current_slice_task(current, "declare-impact")
    require_new_work_open("declare-impact")
    if current.get("resolution") is not None:
        raise ValueError("已收口任务不得再声明影响")
    if current.get("temporary_executor"):
        raise ValueError("临时外包影响声明只能通过 agent_team_temporary.py 修改")
    if state != "queued":
        raise ValueError("正式任务只能在 queued 状态声明 impact")
    if current["revision"] != args.expected_revision:
        raise ValueError("expected-revision 与 TASK 当前 revision 不一致")
    write_paths = [impact_path(item) for item in args.write_path]
    if len(write_paths) != len(set(write_paths)):
        raise ValueError("write-path 不能重复")
    shared_contracts = [clean("shared-contract", item, max_chars=500) for item in args.shared_contract]
    if len(shared_contracts) != len(set(shared_contracts)):
        raise ValueError("shared-contract 不能重复")
    external_effects = [clean("external-effect", item, max_chars=500) for item in (args.external_effect or ["none"])]
    if len(external_effects) != len(set(external_effects)):
        raise ValueError("external-effect 不能重复")
    if "none" in external_effects and external_effects != ["none"]:
        raise ValueError("external-effect=none 不能与其他副作用并存")
    declaration = {
        "write_paths": write_paths,
        "shared_contracts": shared_contracts,
        "external_effects": external_effects,
        "base_revision": clean("base-revision", args.base_revision, max_chars=300),
        "owner_task": current["task_id"],
        "admission": "manual",
    }
    validate_impact_declaration(declaration, path, task_id=current["task_id"])
    candidate = dict(current)
    candidate["impact_declaration"] = declaration
    active_temporaries = active_temporary_impacts(current["task_id"])
    require_formal_temporary_compatibility(candidate, path)
    if active_temporaries:
        declaration["admission"] = "safe"

    def mutate(item: dict) -> None:
        item["impact_declaration"] = declaration

    task, updated_path = update_task(args.task_id, mutate, allowed_states={"queued"})
    print(f"TASK_IMPACT_OK | {task['task_id']} | revision:{task['revision']} | {updated_path.relative_to(PROJECT)}")
    return 0


def cmd_enqueue(args) -> int:
    require_department(args.department)
    require_department(args.from_department)
    actor = clean("actor", args.actor, max_chars=ACTOR_MAX_CHARS)
    expected_actor = registered_lead_actor()
    if actor != expected_actor:
        raise ValueError(f"actor 必须匹配当前已登记统筹会话: {expected_actor};该字段只作审计声明")
    require_new_work_open("enqueue")
    require_no_legacy_temporary_owner("enqueue")
    slice_control = load_slice_control()
    active_slice = slice_control["active_slice"]
    configured = configured_departments()
    role_id = configured[args.department]
    if args.task_kind == "owner":
        if active_slice is not None:
            raise ValueError(
                f"ACTIVE_SLICE_EXISTS | {active_slice['slice_id']} owner={active_slice['owner_task_id']};"
                "完成或收口当前切片后才能创建下一个 owner"
            )
        if role_id == "lead" or role_id in AUDIT_ROLE_IDS:
            raise ValueError("切片 owner 必须由执行层部门承担")
        if args.slice_id or args.gate_type:
            raise ValueError("owner enqueue 不接受 --slice-id 或 --gate-type")
        required_gates = list(args.required_gate)
        if len(required_gates) > 2 or len(required_gates) != len(set(required_gates)):
            raise ValueError("一个切片最多两个且不得重复的 required-gate")
        active_role_ids = set(configured.values())
        if any(gate not in AUDIT_ROLE_IDS or gate not in active_role_ids for gate in required_gates):
            raise ValueError("required-gate 必须是已配置且活动的审核部门 role ID")
        slice_id = f"SLICE-{dt.datetime.now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
        gate_type = ""
    else:
        if active_slice is None:
            raise ValueError("没有活动切片，不能创建 gate")
        slice_id = clean("slice-id", args.slice_id, max_chars=32)
        if slice_id != active_slice["slice_id"]:
            raise ValueError(f"gate slice-id 必须匹配当前活动切片: {active_slice['slice_id']}")
        gate_type = clean("gate-type", args.gate_type, max_chars=32)
        if args.required_gate:
            raise ValueError("gate enqueue 不接受 --required-gate")
        if gate_type != role_id or gate_type not in active_slice["required_gates"]:
            raise ValueError("gate-type 必须匹配当前审核部门和切片 required_gates")
        if gate_type in active_slice["gate_tasks"]:
            raise ValueError(f"该切片已存在 {gate_type} gate TASK")
        if len(active_slice["gate_tasks"]) >= 2:
            raise ValueError("一个切片最多两个 gate TASK")
        if active_slice["candidate"] is None:
            raise ValueError("先固定唯一候选，再创建 gate TASK")
        require_candidate_integrity(active_slice)
        required_gates = list(active_slice["required_gates"])
    failure_paths = [clean("failure-path", item, max_chars=1000) for item in args.failure_path]
    if not 1 <= len(failure_paths) <= 3:
        raise ValueError("failure-path 必须提供 1-3 项")
    if args.authorization_state not in AUTH_STATES:
        raise ValueError("authorization-state 非法")
    authorization_evidence = args.authorization_evidence.strip()
    if args.authorization_state in {"user_confirmed", "user_rejected"} and not authorization_evidence:
        raise ValueError("已确认或已拒绝的授权记录必须提供 authorization-evidence")
    task_id = f"TASK-{dt.datetime.now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
    timestamp = now_iso()
    task = {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "department": args.department,
        "from_department": args.from_department,
        "title": clean("title", args.title, max_chars=200),
        "node": clean("node", args.node, max_chars=200),
        "details": clean("details", args.details),
        "acceptance_exit": clean("acceptance-exit", args.acceptance_exit),
        "failure_paths": failure_paths,
        "confirmation": clean("confirmation", args.confirmation),
        "domain_stage": clean("domain-stage", args.domain_stage, max_chars=200),
        "authorization_state": args.authorization_state,
        "authorization_evidence": clean("authorization-evidence", authorization_evidence, max_chars=1000) if authorization_evidence else "",
        "authorization_history": ([{
            "at": timestamp,
            "state": args.authorization_state,
            "evidence": clean("authorization-evidence", authorization_evidence, max_chars=1000),
        }] if authorization_evidence else []),
        "execution_state": "queued",
        "completion_class": "audit" if audit_department(args.department) else "standard",
        "pointers": [clean("pointer", item, max_chars=500) for item in args.pointer],
        "created_at": timestamp,
        "updated_at": timestamp,
        "revision": 1,
        "claimed_by": "",
        "block_reason": "",
        "artifacts": [],
        "external_artifacts": [],
        "verified": [],
        "unverified": [],
        "mistake_check": "",
        "report": "",
        "event_receipts": [],
        "resolution": None,
        "ownership_history": [],
        "slice_id": slice_id,
        "task_kind": args.task_kind,
        "gate_type": gate_type,
        "gate_attempts": [],
        "completion_history": [],
    }
    validate_task_payload(task, task_path(task_id))
    target_control = json.loads(json.dumps(slice_control, ensure_ascii=False))
    if args.task_kind == "owner":
        target_control["active_slice"] = {
            "slice_id": slice_id,
            "owner_task_id": task_id,
            "required_gates": required_gates,
            "gate_tasks": {},
            "candidate": None,
            "user_exit": {
                "status": "pending", "evidence": "", "actor": "", "at": "",
                "candidate_id": "", "generation": 0,
            },
            "revision_requests": [],
            "created_at": timestamp,
            "metrics": [],
        }
    else:
        target_control["active_slice"]["gate_tasks"][gate_type] = task_id
    operation_id = "ENQUEUE-" + dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8].upper()
    previous_digest = hashlib.sha256(
        json.dumps(slice_control, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    write_atomic(
        SLICE_ENQUEUE_TRANSACTION,
        (json.dumps({
            "schema_version": 1, "operation_id": operation_id, "task": task,
            "previous_control_sha256": previous_digest, "target_control": target_control,
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        0o600,
    )
    path = save_new(task)
    store_slice_control(target_control)
    SLICE_ENQUEUE_TRANSACTION.unlink()
    fsync_dir(LOCKS)
    refresh_inboxes()
    print(f"TASK_ENQUEUED | {task_id} | slice:{slice_id} | kind:{args.task_kind} | {path.relative_to(PROJECT)}")
    return 0


def recover_slice_enqueue_transaction() -> bool:
    if not SLICE_ENQUEUE_TRANSACTION.exists():
        return False
    ensure_plain_file(SLICE_ENQUEUE_TRANSACTION, COLLAB, label="slice enqueue 事务")
    payload = strict_json_loads(
        SLICE_ENQUEUE_TRANSACTION.read_text(encoding="utf-8"), str(SLICE_ENQUEUE_TRANSACTION),
    )
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "operation_id", "task", "previous_control_sha256", "target_control",
    }:
        raise ValueError("slice enqueue 事务结构无效")
    if (
        payload.get("schema_version") != 1
        or not re.fullmatch(r"ENQUEUE-[0-9]{8}T[0-9]{6}-[A-F0-9]{8}", payload.get("operation_id", ""))
        or not re.fullmatch(r"[0-9a-f]{64}", payload.get("previous_control_sha256", ""))
        or not isinstance(payload.get("task"), dict)
        or not isinstance(payload.get("target_control"), dict)
    ):
        raise ValueError("slice enqueue 事务身份无效")
    task = payload["task"]
    task_id = task.get("task_id", "")
    if not TASK_ID_RE.fullmatch(task_id):
        raise ValueError("slice enqueue 事务 TASK ID 无效")
    path = task_path(task_id)
    validate_task_payload(task, path)
    current_control = load_slice_control()
    current_digest = hashlib.sha256(
        json.dumps(current_control, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    target_control = payload["target_control"]
    if not isinstance(target_control, dict) or set(target_control) != {
        "schema_version", "protocol_version", "active_slice", "history", "candidate_ids", "host_runtime",
    } or target_control.get("schema_version") != 1 or target_control.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("slice enqueue 目标控制结构无效")
    target_active = target_control.get("active_slice")
    if not isinstance(target_active, dict) or target_active.get("slice_id") != task.get("slice_id"):
        raise ValueError("slice enqueue 目标控制与 TASK slice_id 不一致")
    if task.get("task_kind") == "owner":
        if target_active.get("owner_task_id") != task_id or target_active.get("gate_tasks") != {}:
            raise ValueError("slice enqueue owner 映射无效")
    elif task.get("task_kind") == "gate":
        if target_active.get("gate_tasks", {}).get(task.get("gate_type")) != task_id:
            raise ValueError("slice enqueue gate 映射无效")
    else:
        raise ValueError("slice enqueue TASK 类型无效")
    if current_control != target_control and current_digest != payload["previous_control_sha256"]:
        raise ValueError("slice enqueue 事务与当前切片控制冲突")
    if path.exists():
        existing = load_task_at(path)
        if existing != task:
            raise ValueError("slice enqueue 事务与现有 TASK 内容冲突")
    else:
        save_new(task)
    if current_control != target_control:
        store_slice_control(target_control)
    SLICE_ENQUEUE_TRANSACTION.unlink()
    fsync_dir(LOCKS)
    refresh_inboxes()
    print(f"SLICE_ENQUEUE_RECOVERY_OK | {payload['operation_id']} | task:{task_id}")
    return True


def current_candidate_generation(task: dict) -> int:
    control = load_slice_control()
    active = control.get("active_slice")
    if not isinstance(active, dict) or active.get("slice_id") != task.get("slice_id"):
        return 0
    candidate = active.get("candidate")
    if not isinstance(candidate, dict):
        return 0
    generation = candidate.get("generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise ValueError("当前候选代次无效")
    return generation


def state_action_signature(
    action: str, actor: str, reason: str, target_state: str, candidate_generation: int,
) -> dict:
    return {
        "action": action,
        "actor": actor,
        "reason": reason,
        "target_state": target_state,
        "candidate_generation": candidate_generation,
    }


def record_state_action(
    task: dict, action: str, actor: str, reason: str, target_state: str, candidate_generation: int,
) -> None:
    task["state_action_receipt"] = state_action_signature(
        action, actor, reason, target_state, candidate_generation,
    )


def require_identical_state_retry(
    task: dict, action: str, actor: str, reason: str, target_state: str, candidate_generation: int,
) -> None:
    expected = state_action_signature(action, actor, reason, target_state, candidate_generation)
    if task.get("state_action_receipt") != expected:
        raise ValueError(f"{action} 重试与已记录动作签名冲突")


def cmd_claim(args) -> int:
    _, current_path, current = locate(args.task_id)
    require_current_slice_task(current, "claim")
    require_new_work_open("claim")
    require_no_legacy_temporary_owner("claim")
    if current.get("resolution") is not None:
        raise ValueError("已收口任务不得再领取")
    if current.get("temporary_executor"):
        raise ValueError("临时外包生命周期只能通过 agent_team_temporary.py 修改")
    require_department(current["department"])
    authorization = current["authorization_state"]
    if authorization in {"user_required", "user_rejected"}:
        raise ValueError(f"当前授权状态禁止领取: {authorization}")
    if authorization == "user_confirmed" and not current.get("authorization_evidence"):
        raise ValueError("用户确认缺少授权证据记录")
    other = busy_for(current["department"], excluding=args.task_id)
    if other:
        raise ValueError("本部门已有在办任务: " + ", ".join(other))
    require_formal_temporary_compatibility(current, current_path)
    actor = clean("claimed-by", args.claimed_by, max_chars=ACTOR_MAX_CHARS)
    expected_actor = registered_department_actor(current["department"])
    if actor != expected_actor:
        raise ValueError(f"claimed-by 必须匹配当前已登记部门会话: {expected_actor}")

    generation = current_candidate_generation(current)

    def mutate(item: dict) -> None:
        item["claimed_by"] = actor
        history = list(item.get("ownership_history", []))
        history.append({
            "at": now_iso(), "action": "claim", "actor": actor,
            "previous_actor": "", "evidence": "registered-session-claim",
        })
        item["ownership_history"] = history
        record_state_action(item, "claim", actor, "", "claimed", generation)

    task, path = transition(args.task_id, "claim", mutate)
    refresh_inboxes()
    print(f"TASK_CLAIMED | {task['task_id']} | {path.relative_to(PROJECT)}")
    return 0


def cmd_block(args) -> int:
    state, path, current = locate(args.task_id)
    require_current_slice_task(current, "block")
    if current.get("temporary_executor"):
        raise ValueError("临时外包生命周期只能通过 agent_team_temporary.py 修改")
    actor = require_task_actor(current, args.actor)
    reason = clean("reason", args.reason)
    generation = current_candidate_generation(current)
    if state == "blocked":
        require_identical_state_retry(current, "block", actor, reason, "blocked", generation)
        print(f"TASK_STATE_NOOP | blocked | {current['task_id']} | {path.relative_to(PROJECT)}")
        return 0

    def mutate(item: dict) -> None:
        item["block_reason"] = reason
        record_state_action(item, "block", actor, reason, "blocked", generation)

    task, path = transition(args.task_id, "block", mutate)
    refresh_inboxes()
    print(f"TASK_BLOCKED | {task['task_id']} | {path.relative_to(PROJECT)}")
    return 0


def cmd_wait(args) -> int:
    state, path, current = locate(args.task_id)
    require_current_slice_task(current, "wait")
    if current.get("temporary_executor"):
        raise ValueError("临时外包生命周期只能通过 agent_team_temporary.py 修改")
    actor = require_task_actor(current, args.actor)
    reason = clean("reason", args.reason)
    generation = current_candidate_generation(current)
    if state == "waiting_input":
        require_identical_state_retry(current, "wait", actor, reason, "waiting_input", generation)
        print(f"TASK_STATE_NOOP | waiting_input | {current['task_id']} | {path.relative_to(PROJECT)}")
        return 0

    def mutate(item: dict) -> None:
        item["block_reason"] = reason
        record_state_action(item, "wait", actor, reason, "waiting_input", generation)

    task, path = transition(args.task_id, "wait", mutate)
    refresh_inboxes()
    print(f"TASK_WAITING_INPUT | {task['task_id']} | {path.relative_to(PROJECT)}")
    return 0


def cmd_resume(args) -> int:
    state, current_path, current = locate(args.task_id)
    require_current_slice_task(current, "resume")
    if current.get("temporary_executor"):
        raise ValueError("临时外包生命周期只能通过 agent_team_temporary.py 修改")
    actor = require_task_actor(current, args.actor)
    generation = current_candidate_generation(current)
    if state == "claimed":
        require_identical_state_retry(current, "resume", actor, "", "claimed", generation)
        print(f"TASK_STATE_NOOP | claimed | {current['task_id']} | {current_path.relative_to(PROJECT)}")
        return 0
    control = load_slice_control()
    active = control.get("active_slice")
    terminal_reopen = state in {"completed", "acknowledged"}
    if terminal_reopen:
        candidate = require_candidate_integrity(active or {})
        if current["task_kind"] == "owner":
            user_exit = active["user_exit"] if active else {}
            if (
                user_exit.get("status") != "needs_revision"
                or user_exit.get("candidate_id") != candidate["candidate_id"]
                or user_exit.get("generation") != candidate["generation"]
            ):
                raise ValueError("completed owner 仅可因当前候选的用户修订重新打开")
            reopen_reason = user_exit["evidence"]
            completed_candidate = candidate
        else:
            gate_states = current_generation_gate_states(active)
            attempts = current.get("gate_attempts", [])
            previous_attempt = attempts[-1] if attempts else None
            if (
                gate_states.get(current["gate_type"]) != "pending"
                or previous_attempt is None
                or previous_attempt.get("generation", 0) >= candidate["generation"]
            ):
                raise ValueError("terminal gate 仅可为更新一代候选重新打开")
            reopen_reason = (
                f"candidate generation {candidate['generation']} requires a fresh {current['gate_type']} verdict"
            )
            completed_candidate = {
                "candidate_id": previous_attempt["candidate_id"],
                "manifest": previous_attempt["candidate_manifest"],
                "sha256": previous_attempt["candidate_sha256"],
                "generation": previous_attempt["generation"],
            }
    require_new_work_open("resume")
    require_department(current["department"])
    authorization = current["authorization_state"]
    if authorization in {"user_required", "user_rejected"}:
        raise ValueError(f"当前授权状态禁止恢复: {authorization}")
    other = busy_for(current["department"], excluding=args.task_id)
    if other:
        raise ValueError("本部门已有其他在办任务: " + ", ".join(other))
    require_formal_temporary_compatibility(current, current_path)
    def mutate(item: dict) -> None:
        if terminal_reopen:
            history = list(item.get("completion_history", []))
            history.append({
                "from_state": state,
                "completed_revision": item["revision"],
                "completed_at": item["updated_at"],
                "reopened_at": now_iso(),
                "reopened_by": actor,
                "reason": reopen_reason,
                "candidate_id": completed_candidate["candidate_id"],
                "candidate_manifest": completed_candidate["manifest"],
                "candidate_sha256": completed_candidate["sha256"],
                "generation": completed_candidate["generation"],
                "artifacts": list(item["artifacts"]),
                "external_artifacts": list(item["external_artifacts"]),
                "verified": list(item["verified"]),
                "unverified": list(item["unverified"]),
                "mistake_check": item["mistake_check"],
                "report": item["report"],
                "event_receipts": list(item["event_receipts"]),
                "acknowledged_by": item.get("acknowledged_by", ""),
            })
            item["completion_history"] = history[-100:]
            item["artifacts"] = []
            item["external_artifacts"] = []
            item["verified"] = []
            item["unverified"] = []
            item["mistake_check"] = ""
            item["report"] = ""
            item["event_receipts"] = []
            item.pop("acknowledged_by", None)
        item["block_reason"] = ""
        record_state_action(item, "resume", actor, "", "claimed", generation)

    task, path = transition(args.task_id, "resume", mutate)
    refresh_inboxes()
    print(f"TASK_RESUMED | {task['task_id']} | {path.relative_to(PROJECT)}")
    return 0


def cmd_rebind_owner(args) -> int:
    state, _, current = locate(args.task_id)
    require_current_slice_task(current, "rebind-owner")
    if current.get("temporary_executor"):
        raise ValueError("临时外包身份只能通过 agent_team_temporary.py 修改")
    allowed_states = {"claimed", "blocked", "waiting_input", "completed", "acknowledged"}
    if state not in allowed_states:
        raise ValueError("只允许对活动中的普通 TASK 换班绑定")
    if state in {"completed", "acknowledged"}:
        control = load_slice_control()
        active = control.get("active_slice")
        if active is None or terminal_reopen_priority(state, current, active, frozen=False) is None:
            raise ValueError("终态 TASK 仅可在当前切片明确要求 reopen 时换班绑定")
    if current["revision"] != args.expected_revision:
        raise ValueError("expected-revision 与 TASK 当前 revision 不一致")
    actor = clean("actor", args.actor, max_chars=ACTOR_MAX_CHARS)
    expected = registered_department_actor(current["department"])
    if actor != expected:
        raise ValueError(f"actor 必须匹配当前已登记部门会话: {expected}")
    previous = clean("previous-actor", args.previous_actor, max_chars=ACTOR_MAX_CHARS)
    if previous != current["claimed_by"] or previous == actor:
        raise ValueError("previous-actor 与 TASK 当前 owner 不一致，或换班身份未变化")
    evidence = clean("evidence", args.evidence, max_chars=1000)

    def mutate(item: dict) -> None:
        history = list(item.get("ownership_history", []))
        if not history:
            history.append({
                "at": item["created_at"], "action": "claim", "actor": previous,
                "previous_actor": "", "evidence": "legacy-owner-import",
            })
        history.append({
            "at": now_iso(), "action": "rebind", "actor": actor,
            "previous_actor": previous, "evidence": evidence,
        })
        item["claimed_by"] = actor
        item["ownership_history"] = history

    task, path = update_task(args.task_id, mutate, allowed_states=allowed_states)
    refresh_inboxes()
    print(f"TASK_OWNER_REBOUND | {task['task_id']} | {previous} -> {actor} | {path.relative_to(PROJECT)}")
    return 0


def require_active_slice_task(task: dict, kind: str) -> tuple[dict, dict]:
    if task.get("schema_version") != SCHEMA_VERSION or task.get("task_kind") != kind:
        raise ValueError(f"该命令只适用于 1.5 {kind} TASK")
    control = load_slice_control()
    active = control.get("active_slice")
    if not active or active["slice_id"] != task.get("slice_id"):
        raise ValueError("TASK 不属于当前活动切片")
    expected_task_id = active["owner_task_id"] if kind == "owner" else active["gate_tasks"].get(task.get("gate_type"))
    if expected_task_id != task["task_id"]:
        raise ValueError("TASK 与活动切片身份映射不一致")
    return control, active


def candidate_manifest(raw: str, candidate_id: str, expected_sha256: str) -> tuple[str, dict]:
    relative = local_artifact(raw)
    path = PROJECT / relative
    if not path.is_file():
        raise ValueError("候选 manifest 必须是项目内普通 JSON 文件")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or digest != expected_sha256:
        raise ValueError("候选 manifest SHA-256 不匹配")
    payload = strict_json_loads(path.read_text(encoding="utf-8"), str(path))
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "candidate_id", "artifact", "source_revision",
    } or payload.get("schema_version") != 1 or payload.get("candidate_id") != candidate_id:
        raise ValueError("候选 manifest 根结构或 candidate_id 无效")
    artifact = payload.get("artifact")
    if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256", "kind"}:
        raise ValueError("候选 manifest artifact 结构无效")
    if (
        not isinstance(artifact.get("path"), str) or not artifact["path"].strip()
        or Path(artifact["path"]).is_absolute() or ".." in Path(artifact["path"]).parts
        or artifact.get("kind") not in {"file", "directory-manifest", "git-tree"}
        or not re.fullmatch(r"[0-9a-f]{64}", artifact.get("sha256", ""))
        or not isinstance(payload.get("source_revision"), str) or not payload["source_revision"].strip()
    ):
        raise ValueError("候选 manifest artifact 或 source_revision 无效")
    artifact_relative = local_artifact(artifact["path"])
    artifact_path = PROJECT / artifact_relative
    if not artifact_path.is_file():
        raise ValueError("候选 artifact 必须是项目内普通文件")
    if hashlib.sha256(artifact_path.read_bytes()).hexdigest() != artifact["sha256"]:
        raise ValueError("候选 artifact SHA-256 与当前产物不一致")
    return relative, payload


def require_candidate_integrity(active: dict, candidate_id: str = "") -> dict:
    candidate = active.get("candidate")
    if not candidate:
        raise ValueError("当前切片尚未固定候选")
    if candidate_id and candidate["candidate_id"] != candidate_id:
        raise ValueError(f"候选身份不匹配，当前唯一候选为 {candidate['candidate_id']}")
    candidate_manifest(candidate["manifest"], candidate["candidate_id"], candidate["sha256"])
    return candidate


def cmd_bind_candidate(args) -> int:
    owner_state, _, owner = locate(args.task_id)
    require_task_actor(owner, args.actor)
    control, active = require_active_slice_task(owner, "owner")
    require_new_work_open("bind-candidate")
    if owner_state != "claimed":
        raise ValueError(f"bind-candidate 只允许 claimed owner；当前状态: {owner_state}")
    candidate_id = clean("candidate-id", args.candidate_id, max_chars=32)
    if not CANDIDATE_ID_RE.fullmatch(candidate_id):
        raise ValueError("candidate-id 格式应为 CAND-YYYYMMDD-XXXXXX")
    previous = active.get("candidate")
    if previous:
        require_candidate_integrity(active)
        if previous["generation"] >= 100:
            raise ValueError("候选代次已达 100 代上限；停止返工并收口当前切片")
        if candidate_id == previous["candidate_id"]:
            raise ValueError("返工候选必须使用新的 candidate-id")
        failed_current_generation = False
        for gate_task_id in active["gate_tasks"].values():
            _, _, gate_task = locate(gate_task_id)
            failed_current_generation = failed_current_generation or any(
                attempt.get("generation") == previous["generation"] and attempt.get("decision") == "fail"
                for attempt in gate_task.get("gate_attempts", [])
            )
        revision_request = next((
            request for request in active["revision_requests"]
            if request["status"] == "pending"
            and request["source_candidate_id"] == previous["candidate_id"]
            and request["source_generation"] == previous["generation"]
        ), None)
        if not failed_current_generation and revision_request is None:
            raise ValueError("只有当前候选已有 gate FAIL 或有效 needs_revision 时才能绑定下一代候选")
    else:
        revision_request = None
    manifest, _ = candidate_manifest(args.manifest, candidate_id, args.sha256)
    if previous and manifest == previous["manifest"]:
        raise ValueError("返工候选必须使用新的 manifest 路径，不得覆盖上一代证据")
    used_ids = set(control["candidate_ids"])
    used_ids.update(
        entry.get("candidate_id") for entry in control["history"] if isinstance(entry, dict)
    )
    used_ids.update(
        attempt.get("candidate_id")
        for _, _, task in all_tasks()
        for attempt in task.get("gate_attempts", [])
        if isinstance(attempt, dict)
    )
    if candidate_id in used_ids:
        raise ValueError("candidate-id 已在冷历史中使用，必须保持全项目唯一")
    if len(control["candidate_ids"]) >= 100000:
        raise ValueError("候选永久身份账本已达安全上限；停止新建候选并归档项目")
    control["candidate_ids"].append(candidate_id)
    active["candidate"] = {
        "candidate_id": candidate_id,
        "manifest": manifest,
        "sha256": args.sha256,
        "generation": (previous["generation"] + 1 if previous else 1),
        "bound_by": args.actor,
        "bound_at": now_iso(),
    }
    if revision_request is not None:
        revision_request["status"] = "consumed"
        revision_request["consumed_by_candidate_id"] = candidate_id
        revision_request["consumed_generation"] = active["candidate"]["generation"]
        revision_request["consumed_at"] = active["candidate"]["bound_at"]
    active["user_exit"] = {
        "status": "pending", "evidence": "", "actor": "", "at": "",
        "candidate_id": candidate_id, "generation": active["candidate"]["generation"],
    }
    store_slice_control(control)
    refresh_inboxes()
    print(
        f"CANDIDATE_BOUND | {active['slice_id']} | {candidate_id} | "
        f"generation:{active['candidate']['generation']} | manifest:{manifest}"
    )
    return 0


def cmd_gate_verdict(args) -> int:
    gate_state, _, gate = locate(args.task_id)
    if gate_state != "claimed" or gate.get("task_kind") != "gate":
        raise ValueError(f"gate-verdict 只允许 claimed gate；当前状态: {gate_state}")
    actor = require_task_actor(gate, args.actor)
    control, active = require_active_slice_task(gate, "gate")
    candidate = require_candidate_integrity(active, args.candidate_id)
    attempts = list(gate.get("gate_attempts", []))
    if attempts and attempts[-1].get("generation") == candidate["generation"]:
        raise ValueError("同一 gate 对同一代候选只能提交一次 verdict")
    report = audit_report(
        args.report, gate["department"], gate["task_id"],
        expected_decision=args.decision, expected_candidate=candidate["candidate_id"],
    )
    report_sha256 = hashlib.sha256((PROJECT / report).read_bytes()).hexdigest()
    attempt = {
        "at": now_iso(), "decision": args.decision, "candidate_id": candidate["candidate_id"],
        "candidate_manifest": candidate["manifest"], "candidate_sha256": candidate["sha256"],
        "generation": candidate["generation"], "evidence": clean("evidence", args.evidence, max_chars=1000),
        "actor": actor, "report": report, "report_sha256": report_sha256,
    }
    should_freeze = (
        args.decision == "fail" and bool(attempts) and attempts[-1].get("decision") == "fail"
        and attempts[-1].get("generation") != candidate["generation"]
    )
    freeze_event = None
    if should_freeze:
        timestamp = now_iso()
        freeze_event = {
            "at": timestamp, "action": "freeze", "actor": registered_lead_actor(),
            "evidence": f"AUTO_GATE_FAIL_X2 slice={active['slice_id']} gate={gate['gate_type']}",
        }
    operation_id = "GATE-" + dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8].upper()
    transaction = {
        "schema_version": 1,
        "operation_id": operation_id,
        "task_id": gate["task_id"],
        "expected_revision": gate["revision"],
        "attempt": attempt,
        "freeze_event": freeze_event,
    }
    write_atomic(
        GATE_TRANSACTION,
        (json.dumps(transaction, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        0o600,
    )

    def mutate(item: dict) -> None:
        history = list(item.get("gate_attempts", []))
        history.append(attempt)
        item["gate_attempts"] = history

    task, path = update_task(args.task_id, mutate, allowed_states={"claimed"})
    if freeze_event:
        dispatch = load_dispatch_control()
        if dispatch["mode"] == "normal":
            dispatch["mode"] = "frozen"
            dispatch["updated_at"] = freeze_event["at"]
            append_dispatch_event(dispatch, freeze_event)
            store_dispatch_control(dispatch)
    GATE_TRANSACTION.unlink()
    fsync_dir(LOCKS)
    refresh_inboxes()
    suffix = " | WORK_FROZEN" if should_freeze else ""
    print(
        f"GATE_VERDICT_OK | {task['task_id']} | {args.decision} | {candidate['candidate_id']} | "
        f"attempt:{len(task['gate_attempts'])}{suffix} | {path.relative_to(PROJECT)}"
    )
    return 0


def recover_gate_transaction() -> bool:
    if not GATE_TRANSACTION.exists():
        return False
    ensure_plain_file(GATE_TRANSACTION, COLLAB, label="gate verdict 事务")
    payload = strict_json_loads(GATE_TRANSACTION.read_text(encoding="utf-8"), str(GATE_TRANSACTION))
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version", "operation_id", "task_id", "expected_revision", "attempt", "freeze_event",
    }:
        raise ValueError("gate verdict 事务结构无效")
    if (
        payload.get("schema_version") != 1
        or not re.fullmatch(r"GATE-[0-9]{8}T[0-9]{6}-[A-F0-9]{8}", payload.get("operation_id", ""))
        or not TASK_ID_RE.fullmatch(payload.get("task_id", ""))
        or isinstance(payload.get("expected_revision"), bool)
        or not isinstance(payload.get("expected_revision"), int)
    ):
        raise ValueError("gate verdict 事务身份无效")
    state, _, task = locate(payload["task_id"])
    if state != "claimed" or task.get("task_kind") != "gate":
        raise ValueError("gate verdict 恢复目标已离开 claimed gate 状态")
    expected_revision = payload["expected_revision"]
    attempt = payload["attempt"]
    if task["revision"] == expected_revision:
        def mutate(item: dict) -> None:
            history = list(item.get("gate_attempts", []))
            history.append(attempt)
            item["gate_attempts"] = history
        task, _ = update_task(payload["task_id"], mutate, allowed_states={"claimed"})
    elif task["revision"] != expected_revision + 1 or not task.get("gate_attempts") or task["gate_attempts"][-1] != attempt:
        raise ValueError("gate verdict 事务与 TASK revision 冲突")
    freeze_event = payload.get("freeze_event")
    if freeze_event is not None:
        if not isinstance(freeze_event, dict) or set(freeze_event) != {"at", "action", "actor", "evidence"}:
            raise ValueError("gate verdict freeze_event 无效")
        dispatch = load_dispatch_control()
        if dispatch["mode"] == "normal":
            dispatch["mode"] = "frozen"
            dispatch["updated_at"] = freeze_event["at"]
            append_dispatch_event(dispatch, freeze_event)
            store_dispatch_control(dispatch)
        elif not dispatch["history"] or dispatch["history"][-1] != freeze_event:
            raise ValueError("gate verdict 冻结恢复与现有派单控制冲突")
    GATE_TRANSACTION.unlink()
    fsync_dir(LOCKS)
    refresh_inboxes()
    print(f"GATE_VERDICT_RECOVERY_OK | {payload['operation_id']} | task:{payload['task_id']}")
    return True


def latest_gate_passes(active: dict) -> bool:
    candidate = require_candidate_integrity(active)
    if set(active["gate_tasks"]) != set(active["required_gates"]):
        return False
    for task_id in active["gate_tasks"].values():
        _, _, task = locate(task_id)
        attempts = task.get("gate_attempts", [])
        if not attempts or attempts[-1].get("decision") != "pass" or attempts[-1].get("generation") != candidate["generation"]:
            return False
    return True


def current_generation_gate_states(active: dict) -> dict[str, str]:
    candidate = active.get("candidate")
    generation = candidate.get("generation") if isinstance(candidate, dict) else None
    states: dict[str, str] = {}
    for gate in active["required_gates"]:
        task_id = active["gate_tasks"].get(gate)
        if not task_id or generation is None:
            states[gate] = "pending"
            continue
        _, _, task = locate(task_id)
        current = next((
            attempt for attempt in reversed(task.get("gate_attempts", []))
            if attempt.get("generation") == generation
        ), None)
        states[gate] = current.get("decision", "pending") if current else "pending"
    return states


def authorization_priority(state: str, task: dict, *, frozen: bool) -> dict[str, object] | None:
    if state not in {"queued", "blocked", "waiting_input"}:
        return None
    authorization = task["authorization_state"]
    if authorization == "user_required":
        return {
            "first_blocker": "USER_AUTHORIZATION_REQUIRED",
            "allowed": ["authorize", "unfreeze-new-work", "next-action"] if frozen else [
                "authorize", "next-action",
            ],
            "forbidden": ["claim", "resume"],
            "user_decision": True,
        }
    if authorization == "user_rejected":
        return {
            "first_blocker": "USER_REJECTED", "allowed": ["resolve", "next-action"],
            "forbidden": ["claim", "resume", "unfreeze-new-work"] if frozen else ["claim", "resume"],
            "user_decision": False,
        }
    return None


def owner_rebind_priority() -> dict[str, object]:
    return {
        "first_blocker": "OWNER_REBIND_REQUIRED",
        "allowed": ["rebind-owner", "next-action"],
        "forbidden": ["claim", "resume", "bind-candidate", "gate-verdict", "complete"],
        "user_decision": False,
    }


def terminal_reopen_priority(
    state: str, task: dict, active: dict, *, frozen: bool,
) -> dict[str, object] | None:
    if state not in {"completed", "acknowledged"}:
        return None
    reopen_required = False
    blocker = ""
    if task["task_kind"] == "owner":
        candidate = active.get("candidate")
        user_exit = active["user_exit"]
        reopen_required = (
            state in {"completed", "acknowledged"}
            and isinstance(candidate, dict)
            and user_exit["status"] == "needs_revision"
            and user_exit["candidate_id"] == candidate["candidate_id"]
            and user_exit["generation"] == candidate["generation"]
        )
        blocker = "OWNER_REOPEN_REQUIRED"
    else:
        candidate = active.get("candidate")
        attempts = task.get("gate_attempts", [])
        reopen_required = (
            isinstance(candidate, dict)
            and bool(attempts)
            and attempts[-1].get("generation", 0) < candidate["generation"]
            and current_generation_gate_states(active).get(task["gate_type"]) == "pending"
        )
        blocker = "GATE_TASK_REOPEN_REQUIRED"
    if not reopen_required:
        return None
    if not task_owner_is_current(task):
        decision = owner_rebind_priority()
        decision["target_task_id"] = task["task_id"]
        return decision
    return {
        "first_blocker": "P0_FREEZE_ACTIVE" if frozen else blocker,
        "target_task_id": task["task_id"],
        "allowed": ["unfreeze-new-work", "next-action"] if frozen else ["resume", "next-action"],
        "forbidden": ["resume", "bind-candidate", "claim", "enqueue"] if frozen else [
            "complete", "ack", "bind-candidate",
        ],
        "user_decision": frozen,
    }


def task_owner_is_current(task: dict) -> bool:
    return task.get("claimed_by") == registered_department_actor(task["department"])


def gate_task_completion_priority(active: dict, *, frozen: bool) -> dict[str, object] | None:
    for gate in active["required_gates"]:
        gate_task_id = active["gate_tasks"].get(gate)
        if not gate_task_id:
            continue
        gate_state, _, gate_task = locate(gate_task_id)
        if gate_state == "acknowledged":
            continue
        if gate_state == "completed":
            return {
                "first_blocker": "GATE_TASK_ACK_REQUIRED",
                "target_task_id": gate_task_id,
                "allowed": ["ack", "next-action"],
                "forbidden": ["owner-complete", "owner-ack", "bind-candidate"],
                "user_decision": False,
            }
        if not task_owner_is_current(gate_task):
            decision = owner_rebind_priority()
            decision["target_task_id"] = gate_task_id
            return decision
        if gate_state == "claimed":
            return {
                "first_blocker": "GATE_TASK_COMPLETION_REQUIRED",
                "target_task_id": gate_task_id,
                "allowed": ["complete", "next-action"],
                "forbidden": ["owner-complete", "bind-candidate"],
                "user_decision": False,
            }
        if gate_state in {"blocked", "waiting_input"}:
            return {
                "first_blocker": "P0_FREEZE_ACTIVE" if frozen else "GATE_TASK_RESUME_REQUIRED",
                "target_task_id": gate_task_id,
                "allowed": ["unfreeze-new-work", "next-action"] if frozen else ["resume", "next-action"],
                "forbidden": ["resume", "owner-complete", "bind-candidate"] if frozen else [
                    "owner-complete", "bind-candidate",
                ],
                "user_decision": frozen,
            }
        return {
            "first_blocker": "GATE_TASK_STATE_INVALID",
            "target_task_id": gate_task_id,
            "allowed": ["next-action"],
            "forbidden": ["owner-complete", "bind-candidate"],
            "user_decision": False,
        }
    return None


def slice_progress_priority(
    state: str, task: dict, active: dict, *, frozen: bool,
) -> dict[str, object] | None:
    """Return slice facts that must be handled before an owner state action."""
    if state not in {"claimed", "blocked", "waiting_input"}:
        return None
    candidate = active.get("candidate")
    gate_states: dict[str, str] = {}
    if candidate:
        require_candidate_integrity(active)
        gate_states = current_generation_gate_states(active)
    all_gates_pass = bool(candidate) and all(value == "pass" for value in gate_states.values())
    if all_gates_pass and active["user_exit"]["status"] == "pending":
        allowed = ["record-user-exit", "next-action"] if frozen or state == "blocked" else [
            "record-user-exit", "wait", "block", "next-action",
        ]
        if state == "waiting_input":
            allowed = ["record-user-exit", "next-action"]
        forbidden = ["resume", "bind-candidate", "claim", "enqueue"] if frozen else [
            "resume", "complete", "bind-candidate",
        ] if state in {"blocked", "waiting_input"} else ["complete", "bind-candidate"]
        return {
            "first_blocker": "P0_FREEZE_ACTIVE" if frozen else "USER_EXIT_PENDING",
            "allowed": allowed, "forbidden": forbidden, "user_decision": True,
        }
    if not task_owner_is_current(task):
        return owner_rebind_priority()
    if not candidate or any(value == "fail" for value in gate_states.values()):
        return None
    if (
        task["task_kind"] == "owner"
        and all(value == "pass" for value in gate_states.values())
        and active["user_exit"]["status"] in {"verified", "not_applicable"}
    ):
        completion = gate_task_completion_priority(active, frozen=frozen)
        if completion is not None:
            return completion

    pending_gates: list[tuple[str, str, dict]] = []
    for gate, gate_status in gate_states.items():
        gate_task_id = active["gate_tasks"].get(gate)
        if gate_status == "pending" and gate_task_id:
            gate_state, _, gate_task = locate(gate_task_id)
            pending_gates.append((gate_task_id, gate_state, gate_task))

    for gate_task_id, gate_state, gate_task in pending_gates:
        if gate_state in {"completed", "acknowledged"}:
            decision = terminal_reopen_priority(gate_state, gate_task, active, frozen=frozen)
            if decision is not None:
                return decision

    claimed_pending = [item for item in pending_gates if item[1] == "claimed"]
    for gate_task_id, _, gate_task in claimed_pending:
        if not task_owner_is_current(gate_task):
            decision = owner_rebind_priority()
            decision["target_task_id"] = gate_task_id
            return decision
    if task["task_kind"] == "gate" and any(task["task_id"] == item[0] for item in claimed_pending):
        allowed = ["gate-verdict", "wait", "block", "next-action"]
        target_task_id = task["task_id"]
    elif task["task_kind"] == "owner" and claimed_pending:
        allowed = ["gate-verdict", "next-action"] if frozen or state in {"blocked", "waiting_input"} else [
            "gate-verdict", "wait", "block", "next-action",
        ]
        target_task_id = claimed_pending[0][0]
    else:
        allowed = []
        target_task_id = ""
    if target_task_id:
        return {
            "first_blocker": "P0_FREEZE_ACTIVE" if frozen else "GATES_PENDING",
            "target_task_id": target_task_id,
            "allowed": allowed,
            "forbidden": ["resume", "bind-candidate", "claim", "enqueue"] if frozen else [
                "complete", "bind-candidate",
            ],
            "user_decision": False,
        }

    blocked_pending = [item for item in pending_gates if item[1] in {"blocked", "waiting_input"}]
    if blocked_pending:
        gate_task_id, _, gate_task = blocked_pending[0]
        if not task_owner_is_current(gate_task):
            decision = owner_rebind_priority()
            decision["target_task_id"] = gate_task_id
            return decision
        return {
            "first_blocker": "P0_FREEZE_ACTIVE" if frozen else "GATE_TASK_RESUME_REQUIRED",
            "target_task_id": gate_task_id,
            "allowed": ["unfreeze-new-work", "next-action"] if frozen else ["resume", "next-action"],
            "forbidden": ["resume", "bind-candidate", "claim", "enqueue"] if frozen else [
                "complete", "bind-candidate", "gate-verdict",
            ],
            "user_decision": frozen,
        }
    return None


def next_action_decision(task_id: str) -> dict[str, object]:
    state, _, task = locate(task_id)
    mode = load_dispatch_control()["mode"]
    if task.get("schema_version") != SCHEMA_VERSION:
        return {
            "first_blocker": "LEGACY_TASK_COLD_ONLY", "allowed": ["next-action"],
            "forbidden": ["slice-state-action"], "user_decision": False,
        }
    _, active = require_active_slice_task(task, task["task_kind"])
    terminal_reopen = terminal_reopen_priority(state, task, active, frozen=mode == "frozen")
    if terminal_reopen is not None:
        return terminal_reopen
    if task["task_kind"] == "owner" and state in {"completed", "acknowledged"}:
        gate_close = gate_task_completion_priority(active, frozen=mode == "frozen")
        if gate_close is not None:
            return gate_close
    authorization = authorization_priority(state, task, frozen=mode == "frozen")
    if authorization is not None:
        return authorization
    priority = slice_progress_priority(state, task, active, frozen=mode == "frozen")
    if priority is not None:
        return priority
    if mode == "frozen":
        allowed = ["unfreeze-new-work", "next-action"]
        user_decision = True
        candidate = active.get("candidate")
        gate_states: dict[str, str] = {}
        if candidate:
            require_candidate_integrity(active)
            gate_states = current_generation_gate_states(active)
        all_gates_pass = bool(candidate) and all(
            value == "pass" for value in gate_states.values()
        )
        if active["user_exit"]["status"] == "needs_revision":
            pass
        elif state == "completed":
            allowed, user_decision = ["ack", "next-action"], False
        elif state == "claimed" and all_gates_pass and active["user_exit"]["status"] in {"verified", "not_applicable"}:
            allowed, user_decision = ["complete", "wait", "block", "next-action"], False
        return {
            "first_blocker": "P0_FREEZE_ACTIVE", "allowed": allowed,
            "forbidden": ["resume", "bind-candidate", "claim", "enqueue"],
            "user_decision": user_decision,
        }
    if state == "completed":
        return {
            "first_blocker": "ACK_REQUIRED", "allowed": ["ack", "next-action"],
            "forbidden": ["state-mutation"], "user_decision": False,
        }
    if state == "blocked":
        return {
            "first_blocker": "TASK_BLOCKED", "allowed": ["resume", "next-action"],
            "forbidden": ["bind-candidate"], "user_decision": False,
        }
    if state == "waiting_input":
        return {
            "first_blocker": "TASK_WAITING_INPUT", "allowed": ["resume", "next-action"],
            "forbidden": ["bind-candidate"], "user_decision": True,
        }
    if state == "queued":
        return {
            "first_blocker": "TASK_UNCLAIMED", "allowed": ["claim", "next-action"],
            "forbidden": ["bind-candidate"], "user_decision": False,
        }
    if state != "claimed":
        return {
            "first_blocker": "TASK_TERMINAL", "allowed": ["next-action"],
            "forbidden": ["state-mutation"], "user_decision": False,
        }
    candidate = active.get("candidate")
    if not candidate:
        return {
            "first_blocker": "CANDIDATE_REQUIRED", "allowed": ["bind-candidate", "wait", "block", "next-action"],
            "forbidden": ["complete"], "user_decision": False,
        }
    require_candidate_integrity(active)
    gate_states = current_generation_gate_states(active)
    if any(value == "fail" for value in gate_states.values()):
        return {
            "first_blocker": "CURRENT_GATE_FAIL", "allowed": ["bind-candidate", "wait", "block", "next-action"],
            "forbidden": ["complete"], "user_decision": False,
        }
    if any(value != "pass" for value in gate_states.values()):
        return {
            "first_blocker": "GATES_PENDING", "allowed": ["gate-verdict", "wait", "block", "next-action"],
            "forbidden": ["complete", "bind-candidate"], "user_decision": False,
        }
    user_exit = active["user_exit"]["status"]
    if user_exit == "pending":
        return {
            "first_blocker": "USER_EXIT_PENDING", "allowed": ["record-user-exit", "wait", "block", "next-action"],
            "forbidden": ["complete", "bind-candidate"], "user_decision": True,
        }
    if user_exit == "needs_revision":
        return {
            "first_blocker": "USER_REVISION_READY", "allowed": ["bind-candidate", "wait", "block", "next-action"],
            "forbidden": ["complete"], "user_decision": False,
        }
    return {
        "first_blocker": "NONE", "allowed": ["complete", "next-action"],
        "forbidden": [], "user_decision": False,
    }


def cmd_next_action(args) -> int:
    transactions = (
        INDEX_TRANSACTION, GATE_TRANSACTION, SLICE_ENQUEUE_TRANSACTION, SLICE_CLOSE_TRANSACTION,
    )
    if any(path.exists() for path in transactions):
        print(
            f"NEXT_ACTION | task={args.task_id} | state=unknown | first_blocker=HOT_STATE_BUSY | "
            "allowed=next-action | forbidden=state-mutation | user_decision=no"
        )
        return 0
    state, task_path, task = locate(args.task_id)
    control = load_slice_control()
    watched = [DISPATCH_CONTROL, SLICE_CONTROL, SESSION_STATE, task_path]
    active = control.get("active_slice")
    if active:
        watched.extend(task_path_for_id for task_path_for_id in (
            locate(task_id)[1] for task_id in active_slice_task_ids(control)
        ))
    before = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in dict.fromkeys(watched)
    }
    decision = next_action_decision(task["task_id"])
    after = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in dict.fromkeys(watched)
    }
    if after != before:
        raise ValueError("HOT_STATE_BUSY | next-action 查询期间机械真值发生变化")
    allowed = ",".join(decision["allowed"]) or "none"
    forbidden = ",".join(decision["forbidden"]) or "none"
    user_decision = "yes" if decision["user_decision"] else "no"
    target_task = decision.get("target_task_id", task["task_id"])
    print(
        f"NEXT_ACTION | task={task['task_id']} | state={state} | "
        f"first_blocker={decision['first_blocker']} | target_task={target_task} | allowed={allowed} | "
        f"forbidden={forbidden} | user_decision={user_decision}"
    )
    return 0


def cmd_record_user_exit(args) -> int:
    actor = clean("actor", args.actor, max_chars=ACTOR_MAX_CHARS)
    expected = registered_lead_actor()
    if actor != expected:
        raise ValueError(f"actor 必须匹配当前已登记统筹会话: {expected}")
    _, _, owner = locate(args.task_id)
    control, active = require_active_slice_task(owner, "owner")
    candidate = require_candidate_integrity(active, args.candidate_id)
    status = args.status
    evidence = clean("evidence", args.evidence, max_chars=1000)
    requested = {
        "status": status, "evidence": evidence, "actor": actor,
        "candidate_id": candidate["candidate_id"], "generation": candidate["generation"],
    }
    current = active["user_exit"]
    if all(current.get(field) == value for field, value in requested.items()):
        print(f"USER_EXIT_NOOP | {active['slice_id']} | {candidate['candidate_id']} | {status}")
        return 0
    superseding_revision = (
        status == "needs_revision" and current["status"] in {"verified", "not_applicable"}
    )
    if current["status"] != "pending" and not superseding_revision:
        raise ValueError("用户出口已记录；不同状态、证据、actor 或候选代次构成冲突")
    timestamp = now_iso()
    if status == "needs_revision":
        if not latest_gate_passes(active):
            raise ValueError("needs_revision 只用于当前候选全部 gate PASS 后的用户修订")
        active["revision_requests"].append({
            "source_candidate_id": candidate["candidate_id"],
            "source_generation": candidate["generation"],
            "evidence": evidence, "actor": actor, "at": timestamp, "status": "pending",
            "consumed_by_candidate_id": "", "consumed_generation": 0, "consumed_at": "",
            "superseded_user_exit": dict(current),
        })
    active["user_exit"] = {**requested, "at": timestamp}
    store_slice_control(control)
    refresh_inboxes()
    print(f"USER_EXIT_RECORDED | {active['slice_id']} | {candidate['candidate_id']} | {status}")
    return 0


def cmd_record_metrics(args) -> int:
    actor = clean("actor", args.actor, max_chars=ACTOR_MAX_CHARS)
    expected = registered_lead_actor()
    if actor != expected:
        raise ValueError(f"actor 必须匹配当前已登记统筹会话: {expected}")
    control = load_slice_control()
    active = control.get("active_slice")
    if not active or active["slice_id"] != args.slice_id:
        raise ValueError("slice-id 必须匹配当前活动切片")
    values = {
        "input_tokens": args.input_tokens, "output_tokens": args.output_tokens,
        "max_input_tokens": args.max_input_tokens, "tool_calls": args.tool_calls,
        "polls": args.polls, "hot_context_bytes": args.hot_context_bytes,
    }
    if any(isinstance(value, bool) or value < 0 for value in values.values()):
        raise ValueError("metrics 必须是非负整数")
    metrics = list(active["metrics"])
    metrics.append({
        "at": now_iso(), "actor": actor, "source": clean("source", args.source, max_chars=200),
        **values,
    })
    active["metrics"] = metrics[-100:]
    store_slice_control(control)
    refresh_inboxes()
    print(f"SLICE_METRICS_OK | {active['slice_id']} | mode:manual-degraded | claims:none")
    return 0


def cmd_authorize(args) -> int:
    actor = clean("actor", args.actor, max_chars=ACTOR_MAX_CHARS)
    expected_actor = registered_lead_actor()
    if actor != expected_actor:
        raise ValueError(f"actor 必须匹配当前已登记统筹会话: {expected_actor};该字段只作审计声明")
    _, _, current = locate(args.task_id)
    if current.get("resolution") is not None:
        raise ValueError("已收口任务不得再修改授权")
    if current.get("temporary_executor"):
        raise ValueError("临时外包生命周期只能通过 agent_team_temporary.py 修改")
    evidence = clean("evidence", args.evidence, max_chars=1000)
    state = args.state
    if state not in {"user_required", "user_confirmed", "user_rejected"}:
        raise ValueError("授权状态非法")
    if current.get("schema_version") != SCHEMA_VERSION and state != "user_rejected":
        raise ValueError(
            "LEGACY_TASK_COLD_ONLY | 1.4 历史 TASK 只允许记录 user_rejected 后清账"
        )
    if state != "user_rejected":
        require_new_work_open(f"authorize:{state}")

    def mutate(item: dict) -> None:
        item["authorization_state"] = state
        item["authorization_evidence"] = evidence
        history = list(item.get("authorization_history", []))
        history.append({"at": now_iso(), "state": state, "evidence": evidence})
        item["authorization_history"] = history

    task, path = update_task(
        args.task_id,
        mutate,
        allowed_states={"queued", "blocked", "waiting_input"},
    )
    refresh_inboxes()
    print(f"TASK_AUTH_RECORDED | {task['task_id']} | {state} | {path.relative_to(PROJECT)}")
    return 0


def cmd_supersede(args) -> int:
    state, path, current = locate(args.task_id)
    if state not in {"queued", "blocked", "waiting_input"} or current.get("resolution") is not None:
        raise ValueError("只允许收口 open queued / blocked / waiting_input 任务；claimed 任务须先 block")
    if current.get("temporary_executor") is not None:
        raise ValueError("临时外包任务必须按专用生命周期收口")
    if current.get("schema_version") == SCHEMA_VERSION:
        raise ValueError("1.5 切片返工必须绑定下一代候选，不得创建 replacement TASK")
    if current["revision"] != args.expected_revision:
        raise ValueError("expected-revision 与 TASK 当前 revision 不一致")
    actor = clean("actor", args.actor, max_chars=ACTOR_MAX_CHARS)
    expected_actor = registered_lead_actor()
    if actor != expected_actor:
        raise ValueError(f"actor 必须匹配当前已登记统筹会话: {expected_actor}")
    replacement_state, _, replacement = locate(args.replacement_task)
    if replacement["task_id"] == current["task_id"]:
        raise ValueError("replacement-task 不能指向原任务自身")
    if replacement.get("resolution") is not None:
        raise ValueError("replacement-task 已收口，不能作为有效替代事实")
    if replacement["revision"] != args.expected_replacement_revision:
        raise ValueError("expected-replacement-revision 与替代 TASK 当前 revision 不一致")
    source_created = parse_task_timestamp(current, "created_at")
    replacement_updated = parse_task_timestamp(replacement, "updated_at")
    later_fact = replacement_updated >= source_created and replacement_state != "queued"
    if not later_fact:
        raise ValueError(
            "replacement-task 未形成可审计后续事实：必须不早于原任务，且已进入执行生命周期"
        )
    reason = clean("reason", args.reason, max_chars=1000)
    evidence = clean("evidence", args.evidence, max_chars=1000)
    timestamp = now_iso()
    candidate = dict(current)
    receipt_id = "RES-" + dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8].upper()
    candidate["resolution"] = {
        "state": "superseded",
        "replacement_task_id": replacement["task_id"],
        "reason": reason,
        "evidence": evidence,
        "resolved_by": actor,
        "resolved_at": timestamp,
        "target_revision_before": current["revision"],
        "replacement_revision": replacement["revision"],
        "replacement_state": replacement_state,
        "receipt_id": receipt_id,
    }
    candidate["updated_at"] = timestamp
    candidate["revision"] = current["revision"] + 1
    validate_task_payload(candidate, path)
    original_task = path.read_bytes()
    inboxes = {
        ensure_plain_file(inbox, COLLAB, label="收件箱"): inbox.read_bytes()
        for inbox in (DEPARTMENTS / name / "收件箱.md" for name in department_names())
    }
    index_operation = "IDX-" + dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8].upper()
    index_marker = {
        "schema_version": 1, "kind": "task-index-refresh",
        "operation_id": index_operation, "task_id": candidate["task_id"],
    }
    try:
        write_atomic(
            INDEX_TRANSACTION,
            json.dumps(index_marker, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n",
            0o600,
        )
        encoded = json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        write_atomic(path, encoded, 0o600)
        invalidate_task_cache()
        render_inboxes()
        INDEX_TRANSACTION.unlink()
        fsync_dir(LOCKS)
    except Exception:
        write_atomic(path, original_task, 0o600)
        invalidate_task_cache()
        for inbox, data in inboxes.items():
            write_atomic(inbox, data, 0o644)
        if INDEX_TRANSACTION.exists() and not INDEX_TRANSACTION.is_symlink() and INDEX_TRANSACTION.is_file():
            INDEX_TRANSACTION.unlink()
            fsync_dir(LOCKS)
        raise
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    print(
        f"TASK_RESOLUTION_OK | state=superseded | task_id={candidate['task_id']} | "
        f"replacement={replacement['task_id']} | revision={candidate['revision']} | "
        f"receipt_id={receipt_id} | digest={digest} | path={path.relative_to(PROJECT)}"
    )
    return 0


def slice_close_entry(active: dict, timestamp: str, resolution_state: str | None) -> dict:
    candidate = active.get("candidate") or {}
    entry = {
        "slice_id": active["slice_id"], "owner_task_id": active["owner_task_id"],
        "candidate_id": candidate.get("candidate_id", ""),
        "candidate_manifest": candidate.get("manifest", ""),
        "candidate_sha256": candidate.get("sha256", ""),
        "closed_at": timestamp, "metrics": active["metrics"],
        "user_exit": active["user_exit"],
        "revision_requests": active["revision_requests"],
    }
    if resolution_state is not None:
        entry["resolution"] = resolution_state
    return entry


def prepare_slice_close_transaction(
    *, action: str, task: dict, actor: str, timestamp: str, resolution: dict | None = None,
) -> dict | None:
    if task.get("schema_version") != SCHEMA_VERSION:
        return None
    control = load_slice_control()
    active = control.get("active_slice")
    if not active or active["slice_id"] != task.get("slice_id"):
        return None
    task_ids = active_slice_task_ids(control)
    if action == "ack":
        closes = all(
            task_id == task["task_id"] or locate(task_id)[0] == "acknowledged"
            for task_id in task_ids
        )
        resolution_state = None
    elif action == "resolve":
        if not isinstance(resolution, dict):
            raise ValueError("slice close resolve 缺少 resolution")
        closes = all(
            task_id == task["task_id"] or locate(task_id)[2].get("resolution") is not None
            for task_id in task_ids
        )
        resolution_state = resolution["state"]
    else:
        raise ValueError("slice close action 无效")
    if not closes:
        return None
    entry = slice_close_entry(active, timestamp, resolution_state)
    if entry["candidate_id"]:
        require_candidate_integrity(active)
    payload = {
        "schema_version": 1,
        "operation_id": "CLOSE-" + dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8].upper(),
        "action": action,
        "task_id": task["task_id"],
        "expected_revision": task["revision"],
        "actor": actor,
        "timestamp": timestamp,
        "resolution": resolution,
        "close_entry": entry,
    }
    write_atomic(
        SLICE_CLOSE_TRANSACTION,
        (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        0o600,
    )
    return payload


def validate_slice_close_transaction(payload: object) -> dict:
    fields = {
        "schema_version", "operation_id", "action", "task_id", "expected_revision",
        "actor", "timestamp", "resolution", "close_entry",
    }
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ValueError("slice close 事务结构无效")
    if (
        payload.get("schema_version") != 1
        or not re.fullmatch(r"CLOSE-[0-9]{8}T[0-9]{6}-[A-F0-9]{8}", payload.get("operation_id", ""))
        or payload.get("action") not in {"ack", "resolve"}
        or not TASK_ID_RE.fullmatch(payload.get("task_id", ""))
        or isinstance(payload.get("expected_revision"), bool)
        or not isinstance(payload.get("expected_revision"), int)
        or payload["expected_revision"] < 1
        or not isinstance(payload.get("actor"), str)
        or not payload["actor"].startswith("统筹部/")
    ):
        raise ValueError("slice close 事务身份无效")
    try:
        timestamp = dt.datetime.fromisoformat(payload["timestamp"])
    except (TypeError, ValueError) as exc:
        raise ValueError("slice close 事务时间无效") from exc
    if timestamp.tzinfo is None:
        raise ValueError("slice close 事务时间缺少时区")
    resolution = payload["resolution"]
    if payload["action"] == "ack" and resolution is not None:
        raise ValueError("ack slice close 不得携带 resolution")
    if payload["action"] == "resolve" and (
        not isinstance(resolution, dict)
        or resolution.get("state") not in {"rejected_by_user", "abandoned"}
    ):
        raise ValueError("resolve slice close 的 resolution 无效")
    entry = payload["close_entry"]
    base_fields = {
        "slice_id", "owner_task_id", "candidate_id", "candidate_manifest",
        "candidate_sha256", "closed_at", "metrics", "user_exit", "revision_requests",
    }
    expected_entry_fields = base_fields if payload["action"] == "ack" else base_fields | {"resolution"}
    if not isinstance(entry, dict) or set(entry) != expected_entry_fields:
        raise ValueError("slice close 冷历史条目无效")
    if (
        not SLICE_ID_RE.fullmatch(entry.get("slice_id", ""))
        or not TASK_ID_RE.fullmatch(entry.get("owner_task_id", ""))
        or entry.get("closed_at") != payload["timestamp"]
        or not isinstance(entry.get("metrics"), list)
    ):
        raise ValueError("slice close 冷历史身份无效")
    return payload


def apply_slice_close_transaction(payload: dict, *, announce: bool) -> tuple[dict, Path]:
    payload = validate_slice_close_transaction(payload)
    state, path, current = locate(payload["task_id"])
    expected_revision = payload["expected_revision"]
    action = payload["action"]
    if current["revision"] == expected_revision:
        target = dict(current)
        if action == "ack":
            if state != "completed" or current.get("resolution") is not None:
                raise ValueError("slice close ack 目标不再是 completed")
            target["execution_state"] = "acknowledged"
            target["acknowledged_by"] = payload["actor"]
        else:
            if state not in {"queued", "blocked", "waiting_input"} or current.get("resolution") is not None:
                raise ValueError("slice close resolve 目标状态已漂移")
            resolution = payload["resolution"]
            if resolution["state"] == "rejected_by_user" and target["authorization_state"] != "user_rejected":
                target["authorization_state"] = "user_rejected"
                target["authorization_evidence"] = resolution["evidence"]
                history = list(target.get("authorization_history", []))
                history.append({
                    "at": payload["timestamp"], "state": "user_rejected", "evidence": resolution["evidence"],
                })
                target["authorization_history"] = history
            target["resolution"] = resolution
        target["updated_at"] = payload["timestamp"]
        target["revision"] = expected_revision + 1
        validate_task_payload(target, path)
        write_atomic(
            path,
            json.dumps(target, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n",
            0o600,
        )
        invalidate_task_cache()
        current = target
    elif current["revision"] == expected_revision + 1:
        if action == "ack" and (
            state != "acknowledged" or current.get("acknowledged_by") != payload["actor"]
        ):
            raise ValueError("slice close ack 已写 TASK 与事务不一致")
        if action == "resolve" and current.get("resolution") != payload["resolution"]:
            raise ValueError("slice close resolve 已写 TASK 与事务不一致")
    else:
        raise ValueError("slice close TASK revision 与事务冲突")

    control = load_slice_control()
    active = control.get("active_slice")
    entry = payload["close_entry"]
    if active is not None:
        if active["slice_id"] != entry["slice_id"] or active["owner_task_id"] != entry["owner_task_id"]:
            raise ValueError("slice close 事务目标与活动切片冲突")
        task_ids = active_slice_task_ids(control)
        if action == "ack" and any(locate(task_id)[0] != "acknowledged" for task_id in task_ids):
            raise ValueError("slice close ack 尚有未核收 TASK")
        if action == "resolve" and any(locate(task_id)[2].get("resolution") is None for task_id in task_ids):
            raise ValueError("slice close resolve 尚有未收口 TASK")
        control["history"].append(entry)
        control["history"] = control["history"][-100:]
        control["active_slice"] = None
        store_slice_control(control)
    elif not any(item == entry for item in control["history"]):
        raise ValueError("slice close 活动切片已消失但冷历史收据缺失")
    SLICE_CLOSE_TRANSACTION.unlink(missing_ok=True)
    fsync_dir(LOCKS)
    if announce:
        refresh_inboxes()
        print(f"SLICE_CLOSE_RECOVERY_OK | {payload['operation_id']} | slice:{entry['slice_id']}")
    return current, path


def recover_slice_close_transaction() -> bool:
    if not SLICE_CLOSE_TRANSACTION.exists():
        return False
    ensure_plain_file(SLICE_CLOSE_TRANSACTION, COLLAB, label="slice close 事务")
    payload = strict_json_loads(
        SLICE_CLOSE_TRANSACTION.read_text(encoding="utf-8"), str(SLICE_CLOSE_TRANSACTION),
    )
    apply_slice_close_transaction(payload, announce=True)
    return True


def cmd_resolve(args) -> int:
    state, _, current = locate(args.task_id)
    if state not in {"queued", "blocked", "waiting_input"} or current.get("resolution") is not None:
        raise ValueError("只允许收口 open queued / blocked / waiting_input 任务；claimed 任务须先 block")
    if current.get("temporary_executor") is not None:
        raise ValueError("临时外包任务必须按专用生命周期收口")
    if current["revision"] != args.expected_revision:
        raise ValueError("expected-revision 与 TASK 当前 revision 不一致")
    actor = clean("actor", args.actor, max_chars=ACTOR_MAX_CHARS)
    expected_actor = registered_lead_actor()
    if actor != expected_actor:
        raise ValueError(f"actor 必须匹配当前已登记统筹会话: {expected_actor};该字段只作审计声明")
    resolution_state = args.state
    timestamp = now_iso()
    receipt_id = "RES-" + dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8].upper()
    resolution = {
        "state": resolution_state,
        "reason": clean("reason", args.reason, max_chars=1000),
        "evidence": clean("evidence", args.evidence, max_chars=1000),
        "resolved_by": actor,
        "resolved_at": timestamp,
        "target_revision_before": current["revision"],
        "receipt_id": receipt_id,
    }

    close_transaction = prepare_slice_close_transaction(
        action="resolve", task=current, actor=actor, timestamp=timestamp, resolution=resolution,
    )

    if close_transaction is not None:
        task, path = apply_slice_close_transaction(close_transaction, announce=False)
        refresh_inboxes()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        print(
            f"TASK_RESOLUTION_OK | state={resolution_state} | task_id={task['task_id']} | "
            f"revision={task['revision']} | receipt_id={receipt_id} | digest={digest} | "
            f"path={path.relative_to(PROJECT)}"
        )
        return 0

    def mutate(item: dict) -> None:
        if resolution_state == "rejected_by_user" and item["authorization_state"] != "user_rejected":
            item["authorization_state"] = "user_rejected"
            item["authorization_evidence"] = resolution["evidence"]
            history = list(item.get("authorization_history", []))
            history.append({
                "at": timestamp, "state": "user_rejected", "evidence": resolution["evidence"],
            })
            item["authorization_history"] = history
        item["resolution"] = resolution

    task, path = update_task(
        args.task_id, mutate, allowed_states={"queued", "blocked", "waiting_input"}, timestamp=timestamp,
    )
    refresh_inboxes()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    print(
        f"TASK_RESOLUTION_OK | state={resolution_state} | task_id={task['task_id']} | "
        f"revision={task['revision']} | receipt_id={receipt_id} | digest={digest} | "
        f"path={path.relative_to(PROJECT)}"
    )
    return 0


def cmd_complete(args) -> int:
    _, _, current = locate(args.task_id)
    require_current_slice_task(current, "complete")
    if current.get("temporary_executor"):
        raise ValueError("临时外包必须通过 agent_team_temporary.py 固定候选并 submit")
    require_task_actor(current, args.actor)
    authorization = current["authorization_state"]
    if authorization in {"user_required", "user_rejected"}:
        raise ValueError(f"当前授权状态禁止完成: {authorization}")
    local_paths = [local_artifact(value) for value in args.artifact]
    external_urls = [external_artifact(value) for value in args.external_artifact]
    if not local_paths and not external_urls:
        raise ValueError("complete 必须提供至少一个已验证的本地产物或显式外部产物")
    report_path = clean("report", args.report, max_chars=500)
    active = None
    candidate = None
    if current.get("schema_version") == SCHEMA_VERSION:
        _, active = require_active_slice_task(current, current["task_kind"])
        candidate = require_candidate_integrity(active)
        if not latest_gate_passes(active):
            raise ValueError("当前候选尚未通过切片要求的全部 gate")
        if active["user_exit"]["status"] not in {"verified", "not_applicable"}:
            raise ValueError("用户最终出口尚未记录，不能完成切片 TASK")
        if current["task_kind"] == "owner":
            if candidate["manifest"] not in local_paths:
                raise ValueError("owner 完成必须提交当前候选 manifest 作为本地产物")
            incomplete_gates = [
                task_id for task_id in active["gate_tasks"].values()
                if locate(task_id)[0] != "acknowledged"
            ]
            if incomplete_gates:
                raise ValueError("gate TASK 尚未核收: " + ", ".join(incomplete_gates))
    if current.get("completion_class") == "audit" or audit_department(current["department"]):
        report_path = audit_report(
            args.report, current["department"], current["task_id"],
            expected_decision="pass" if candidate else "",
            expected_candidate=candidate["candidate_id"] if candidate else "",
        )
        if report_path not in local_paths:
            raise ValueError("审核报告必须同时通过 --artifact 提交")

    def mutate(item: dict) -> None:
        item["artifacts"] = local_paths
        item["external_artifacts"] = external_urls
        item["verified"] = [clean("verified", value) for value in args.verified]
        item["unverified"] = [clean("unverified", value) for value in args.unverified]
        item["mistake_check"] = clean("mistake-check", args.mistake_check)
        item["report"] = report_path
        item["event_receipts"] = [clean("event-receipt", value, max_chars=1000) for value in args.event_receipt]
        item["block_reason"] = ""
    if not args.verified or not args.unverified:
        raise ValueError("complete 必须提供 verified 和 unverified；无未验证项时传入“无”")
    task, path = transition(args.task_id, "complete", mutate)
    refresh_inboxes()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    print(
        f"TASK_STATE_OK | state_persisted | local_paths_checked={len(local_paths)} | "
        f"external_declared={len(external_urls)} | {task['updated_at']} | "
        f"{task['task_id']} | {digest} | {path.relative_to(PROJECT)}"
    )
    return 0


def cmd_ack(args) -> int:
    state, _, current = locate(args.task_id)
    if current.get("temporary_executor"):
        raise ValueError("临时外包必须通过 agent_team_temporary.py acknowledge")
    actor = clean("acknowledged-by", args.acknowledged_by, max_chars=ACTOR_MAX_CHARS)
    expected = registered_lead_actor()
    if actor != expected:
        raise ValueError(f"acknowledged-by 必须匹配当前已登记统筹会话: {expected};该字段仍只作审计声明")
    if state != "completed" or current.get("resolution") is not None:
        raise ValueError(f"非法状态转换: {state} --ack--> ?")
    if current.get("schema_version") == SCHEMA_VERSION:
        _, active = require_active_slice_task(current, current["task_kind"])
        if active["user_exit"]["status"] == "needs_revision":
            raise ValueError("用户修订尚未完成，不能核收或关闭当前切片")
        if current["task_kind"] == "owner":
            unacknowledged_gates = [
                task_id for task_id in active["gate_tasks"].values()
                if locate(task_id)[0] != "acknowledged"
            ]
            if unacknowledged_gates:
                raise ValueError("gate TASK 尚未核收，不能先核收 owner: " + ", ".join(unacknowledged_gates))
    timestamp = now_iso()
    close_transaction = prepare_slice_close_transaction(
        action="ack", task=current, actor=actor, timestamp=timestamp,
    )
    if close_transaction is not None:
        task, path = apply_slice_close_transaction(close_transaction, announce=False)
    else:
        task, path = transition(args.task_id, "ack", lambda item: item.update(acknowledged_by=actor))
    refresh_inboxes()
    print(f"TASK_ACK | {task['updated_at']} | {task['task_id']} | {path.relative_to(PROJECT)}")
    return 0


def cmd_list(args) -> int:
    rows = []
    tasks = all_tasks() if args.include_cold else hot_tasks()
    for state, path, task in tasks:
        if args.department and task["department"] != args.department:
            continue
        if args.state and state != args.state:
            continue
        resolution_labels = {
            "superseded": "已取代", "rejected_by_user": "用户拒绝后收口", "abandoned": "已放弃",
        }
        if task.get("resolution"):
            label = resolution_labels.get(task["resolution"].get("state"), STATE_CN[state])
        elif state == "queued" and task["authorization_state"] == "user_required":
            label = "待用户确认"
        elif state == "queued" and task["authorization_state"] == "user_rejected":
            label = "用户已拒绝，待收口"
        else:
            label = STATE_CN[state]
        rows.append(f"{task['task_id']} | {label} | {task['department']} | {task['title']} | {path.relative_to(PROJECT)}")
    if not args.include_cold:
        blockers = legacy_temporary_closeout_blockers()
        if blockers:
            rows.insert(0, "LEGACY_TEMPORARY_CLOSEOUT_REQUIRED | " + ", ".join(blockers))
    print("\n".join(rows) if rows else "NO_TASKS")
    return 0


def cmd_rebuild_index(args) -> int:
    actor = clean("actor", args.actor, max_chars=ACTOR_MAX_CHARS)
    try:
        expected = registered_lead_actor()
    except ValueError as registration_error:
        state = strict_json_loads(SESSION_STATE.read_text(encoding="utf-8"), str(SESSION_STATE))
        departments = state.get("departments", {}) if isinstance(state, dict) else {}
        pristine_sessions = bool(departments) and all(
            isinstance(item, dict)
            and item.get("step") == "pending"
            and item.get("thread_id", "") == ""
            and item.get("previous_thread_id", "") == ""
            for item in departments.values()
        )
        if (
            actor != "统筹部/bootstrap"
            or not pristine_sessions
            or all_tasks()
            or load_slice_control().get("active_slice") is not None
            or load_dispatch_control()["mode"] != "normal"
        ):
            raise ValueError(
                "rebuild-index 必须匹配当前已登记统筹会话；"
                "仅全新空协作层允许 统筹部/bootstrap"
            ) from registration_error
    else:
        if actor != expected:
            raise ValueError(f"actor 必须匹配当前已登记统筹会话: {expected}")
    render_inboxes(force=True)
    print("TASK_INDEX_OK")
    return 0


def cmd_onboard_check(args) -> int:
    verify_hot_state(args.department)
    print(f"ONBOARD_FRESH_OK | {args.department}")
    return 0


def cmd_onboard_bundle(args) -> int:
    department = clean("department", args.department, max_chars=80)
    if department not in department_names():
        raise ValueError(f"未知部门: {department}")
    transactions = (
        INDEX_TRANSACTION, GATE_TRANSACTION, SLICE_ENQUEUE_TRANSACTION, SLICE_CLOSE_TRANSACTION,
    )
    if any(path.exists() for path in transactions):
        raise ValueError("HOT_STATE_BUSY | 当前有未恢复事务；请统筹先运行任一任务工具完成恢复")
    verify_hot_state(department)
    department_root = DEPARTMENTS / department
    documents = [
        ("岗位说明.md", department_root / "岗位说明.md"),
        ("交接班文档.md", department_root / "交接班文档.md"),
        ("收件箱.md", department_root / "收件箱.md"),
    ]
    snapshots: list[tuple[str, Path, bytes]] = []
    source_snapshots: list[tuple[str, Path, bytes]] = []
    for label, path in documents:
        ensure_plain_file(path, COLLAB, label=f"{department} {label}")
        source_data = path.read_bytes()
        source_snapshots.append((label, path, source_data))
        data = (
            (extract_handoff_hot_block(source_data.decode("utf-8-sig")) + "\n").encode("utf-8")
            if label == "交接班文档.md" else source_data
        )
        snapshots.append((label, path, data))
    current, recovery = department_onboard_tasks(department)

    def identity_rows(
        current_items: list[tuple[str, Path, dict]],
        recovery_items: list[tuple[str, Path, dict]],
    ) -> list[tuple[str, str, str, str]]:
        return [
            (kind, task["task_id"], state, hashlib.sha256(path.read_bytes()).hexdigest())
            for kind, selected in (("current", current_items), ("legacy-closeout", recovery_items))
            for state, path, task in selected
        ]

    selected_identity = identity_rows(current, recovery)
    for kind, selected in (("current", current), ("legacy-closeout", recovery)):
        for _, path, task in selected:
            ensure_plain_file(path, COLLAB, label=f"{kind} TASK {task['task_id']}")
            snapshots.append((f"tasks/{path.name}", path, path.read_bytes()))
    verify_hot_state(department)
    current_after, recovery_after = department_onboard_tasks(department)
    if identity_rows(current_after, recovery_after) != selected_identity:
        raise ValueError("HOT_STATE_BUSY | 接班快照读取期间 TASK 身份集合发生变化")
    if any(path.exists() for path in transactions):
        raise ValueError("HOT_STATE_BUSY | 接班快照读取期间出现未恢复事务")
    for label, path, data in source_snapshots:
        if path.read_bytes() != data:
            raise ValueError(f"HOT_STATE_BUSY | 接班快照读取期间发生变化: {label}")
    for label, path, data in snapshots:
        if label.startswith("tasks/") and path.read_bytes() != data:
            raise ValueError(f"HOT_STATE_BUSY | 接班快照读取期间发生变化: {label}")
    hot_context_bytes = sum(len(data) for _, _, data in snapshots)
    warning = "review_required" if hot_context_bytes > HOT_CONTEXT_WARNING_BYTES else "none"
    print(
        f"ONBOARD_BUNDLE_OK | {department} | current_tasks={len(current)} | "
        f"recovery_tasks={len(recovery)} | cold_history=not_loaded | "
        f"hot_context_bytes={hot_context_bytes} | hot_context_warning={warning}"
    )
    if warning != "none":
        print(
            f"HOT_CONTEXT_WARNING | {department} | bytes={hot_context_bytes} | "
            f"threshold={HOT_CONTEXT_WARNING_BYTES} | 内容未截断，请先收紧岗位说明或热交接"
        )
    for label, _, data in snapshots:
        print(f"===== BEGIN {label} =====")
        sys.stdout.write(data.decode("utf-8-sig"))
        if not data.endswith(b"\n"):
            print()
        print(f"===== END {label} =====")
    return 0


def cmd_doctor(args) -> int:
    tasks = all_tasks()
    mode = load_dispatch_control()["mode"]
    slice_control = load_slice_control()
    active = slice_control.get("active_slice")
    active_ids = active_slice_task_ids(slice_control)
    indexed_task_ids, recovery_required = legacy_closeout_index()
    indexed_legacy = set(indexed_task_ids)
    recovery = legacy_archive_recovery_entries()
    missing_legacy_index = [
        task["task_id"] for _, _, task in tasks
        if task.get("schema_version") != SCHEMA_VERSION
        and isinstance(task.get("temporary_executor"), dict)
        and not legacy_temporary_terminal(
            task, recovery, archive_recovery_required=task["task_id"] in recovery_required,
        )
        and task["task_id"] not in indexed_legacy
    ]
    if missing_legacy_index:
        raise ValueError("LEGACY_CLOSEOUT_INDEX_INCOMPLETE | " + ", ".join(missing_legacy_index))
    legacy_blockers = legacy_temporary_closeout_blockers()
    if legacy_blockers and (mode != "frozen" or active is not None):
        raise ValueError(
            "DUAL_OWNER_RISK | 未收口 legacy temporary 只能在 frozen 且无活动切片时存在: "
            + ", ".join(legacy_blockers)
        )
    by_id = {task["task_id"]: (state, task) for state, _, task in tasks}
    for entry in slice_control["history"]:
        if entry.get("candidate_id"):
            candidate_manifest(
                entry["candidate_manifest"], entry["candidate_id"], entry["candidate_sha256"],
            )
    if active:
        if any(task_id not in by_id for task_id in active_ids):
            raise ValueError("SLICE_DRIFT | 活动切片引用的 TASK 缺失")
        owner = by_id[active["owner_task_id"]][1]
        if owner.get("task_kind") != "owner" or owner.get("slice_id") != active["slice_id"]:
            raise ValueError("SLICE_DRIFT | owner TASK 身份不一致")
        for gate, task_id in active["gate_tasks"].items():
            gate_task = by_id[task_id][1]
            if gate_task.get("task_kind") != "gate" or gate_task.get("gate_type") != gate:
                raise ValueError("SLICE_DRIFT | gate TASK 身份不一致")
        if active.get("candidate"):
            require_candidate_integrity(active)
        active_states = [by_id[task_id][0] for task_id in active_ids]
        active_resolved = [by_id[task_id][1].get("resolution") is not None for task_id in active_ids]
        if active_states and all(state == "acknowledged" for state in active_states):
            raise ValueError("SLICE_CLOSE_INCOMPLETE | 活动切片 TASK 已全部 acknowledged 但控制未收口")
        if active_resolved and all(active_resolved):
            raise ValueError("SLICE_CLOSE_INCOMPLETE | 活动切片 TASK 已全部 resolved 但控制未收口")
    orphaned = []
    for state, _, task in tasks:
        if (
            task.get("schema_version") == SCHEMA_VERSION and task["task_id"] not in active_ids
            and task.get("resolution") is None and state != "acknowledged"
        ):
            orphaned.append(task["task_id"])
    if orphaned:
        raise ValueError("SLICE_DRIFT | 活动切片之外仍有未收口 1.5 TASK: " + ", ".join(orphaned))
    verify_hot_state()
    drift = []
    for state, _, task in tasks:
        if (
            task.get("schema_version") != SCHEMA_VERSION
            or task.get("temporary_executor")
            or task.get("resolution") is not None
        ):
            continue
        if state in {"claimed", "blocked", "waiting_input"}:
            expected = registered_department_actor(task["department"])
            if task.get("claimed_by") != expected:
                drift.append(f"{task['task_id']}:{task.get('claimed_by')}!={expected}")
    if drift:
        raise ValueError("TASK_OWNER_DRIFT | " + ", ".join(drift))
    print(
        f"TASK_DOCTOR_OK | tasks={len(tasks)} | full_history_validated=true | work_mode={mode} | "
        f"active_slice={active['slice_id'] if active else 'none'} | "
        f"legacy_closeout={len(legacy_blockers)} | host_runtime=manual-degraded"
    )
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="agent-team 原子任务队列")
    sub = root.add_subparsers(dest="cmd", required=True)
    enqueue = sub.add_parser("enqueue")
    enqueue.add_argument("--actor", required=True, help="当前已登记统筹会话，格式：统筹部/会话ID")
    enqueue.add_argument("--department", required=True)
    enqueue.add_argument("--from-department", required=True)
    enqueue.add_argument("--title", required=True)
    enqueue.add_argument("--node", required=True)
    enqueue.add_argument("--details", required=True)
    enqueue.add_argument("--acceptance-exit", required=True)
    enqueue.add_argument("--failure-path", action="append", required=True)
    enqueue.add_argument("--confirmation", default="无需额外确认")
    enqueue.add_argument("--domain-stage", default="通用执行")
    enqueue.add_argument("--authorization-state", choices=sorted(AUTH_STATES), default="none")
    enqueue.add_argument("--authorization-evidence", default="")
    enqueue.add_argument("--pointer", action="append", default=[])
    enqueue.add_argument("--task-kind", choices=("owner", "gate"), default="owner")
    enqueue.add_argument("--slice-id", default="")
    enqueue.add_argument("--gate-type", choices=sorted(AUDIT_ROLE_IDS), default="")
    enqueue.add_argument("--required-gate", action="append", choices=sorted(AUDIT_ROLE_IDS), default=[])
    enqueue.set_defaults(func=cmd_enqueue)
    freeze = sub.add_parser("freeze-new-work")
    freeze.add_argument("--actor", required=True, help="当前已登记统筹会话，格式：统筹部/会话ID")
    freeze.add_argument("--evidence", required=True, help="用户冻结要求或可复核止血触发证据")
    freeze.set_defaults(func=cmd_freeze_new_work)
    unfreeze = sub.add_parser("unfreeze-new-work")
    unfreeze.add_argument("--actor", required=True, help="当前已登记统筹会话，格式：统筹部/会话ID")
    unfreeze.add_argument("--user-confirmation", required=True, help="用户明确同意恢复新工作的证据")
    unfreeze.set_defaults(func=cmd_unfreeze_new_work)
    work_mode = sub.add_parser("work-mode")
    work_mode.set_defaults(func=cmd_work_mode)
    declare = sub.add_parser("declare-impact")
    declare.add_argument("--task-id", required=True)
    declare.add_argument("--expected-revision", required=True, type=int)
    declare.add_argument("--write-path", action="append", required=True)
    declare.add_argument("--shared-contract", action="append", default=[])
    declare.add_argument("--external-effect", action="append", default=[])
    declare.add_argument("--base-revision", required=True)
    declare.set_defaults(func=cmd_declare_impact)
    claim = sub.add_parser("claim")
    claim.add_argument("--task-id", required=True)
    claim.add_argument("--claimed-by", required=True)
    claim.set_defaults(func=cmd_claim)
    for name, func in (("block", cmd_block), ("wait", cmd_wait)):
        command = sub.add_parser(name)
        command.add_argument("--task-id", required=True)
        command.add_argument("--reason", required=True)
        command.add_argument("--actor", required=True, help="当前已登记任务部门会话，格式：部门/会话ID")
        command.set_defaults(func=func)
    resume = sub.add_parser("resume")
    resume.add_argument("--task-id", required=True)
    resume.add_argument("--actor", required=True, help="当前已登记任务部门会话，格式：部门/会话ID")
    resume.set_defaults(func=cmd_resume)
    rebind = sub.add_parser("rebind-owner")
    rebind.add_argument("--task-id", required=True)
    rebind.add_argument("--expected-revision", required=True, type=int)
    rebind.add_argument("--actor", required=True, help="换班后的当前已登记部门会话，格式：部门/会话ID")
    rebind.add_argument("--previous-actor", required=True)
    rebind.add_argument("--evidence", required=True)
    rebind.set_defaults(func=cmd_rebind_owner)
    bind_candidate = sub.add_parser("bind-candidate")
    bind_candidate.add_argument("--task-id", required=True)
    bind_candidate.add_argument("--candidate-id", required=True)
    bind_candidate.add_argument("--manifest", required=True)
    bind_candidate.add_argument("--sha256", required=True)
    bind_candidate.add_argument("--actor", required=True, help="当前已登记 owner 部门会话")
    bind_candidate.set_defaults(func=cmd_bind_candidate)
    gate_verdict = sub.add_parser("gate-verdict")
    gate_verdict.add_argument("--task-id", required=True)
    gate_verdict.add_argument("--candidate-id", required=True)
    gate_verdict.add_argument("--decision", choices=("pass", "fail"), required=True)
    gate_verdict.add_argument("--report", required=True)
    gate_verdict.add_argument("--evidence", required=True)
    gate_verdict.add_argument("--actor", required=True, help="当前已登记 gate 部门会话")
    gate_verdict.set_defaults(func=cmd_gate_verdict)
    user_exit = sub.add_parser("record-user-exit")
    user_exit.add_argument("--task-id", required=True)
    user_exit.add_argument("--candidate-id", required=True)
    user_exit.add_argument("--status", choices=("needs_revision", "verified", "not_applicable"), required=True)
    user_exit.add_argument("--evidence", required=True)
    user_exit.add_argument("--actor", required=True, help="当前已登记统筹会话，格式：统筹部/会话ID")
    user_exit.set_defaults(func=cmd_record_user_exit)
    metrics = sub.add_parser("record-metrics")
    metrics.add_argument("--slice-id", required=True)
    metrics.add_argument("--source", required=True)
    metrics.add_argument("--input-tokens", type=int, required=True)
    metrics.add_argument("--output-tokens", type=int, required=True)
    metrics.add_argument("--max-input-tokens", type=int, required=True)
    metrics.add_argument("--tool-calls", type=int, required=True)
    metrics.add_argument("--polls", type=int, required=True)
    metrics.add_argument("--hot-context-bytes", type=int, required=True)
    metrics.add_argument("--actor", required=True, help="当前已登记统筹会话，格式：统筹部/会话ID")
    metrics.set_defaults(func=cmd_record_metrics)
    authorize = sub.add_parser("authorize")
    authorize.add_argument("--task-id", required=True)
    authorize.add_argument("--state", choices=("user_required", "user_confirmed", "user_rejected"), required=True)
    authorize.add_argument("--evidence", required=True)
    authorize.add_argument("--actor", required=True, help="当前已登记统筹会话，格式：统筹部/会话ID")
    authorize.set_defaults(func=cmd_authorize)
    supersede = sub.add_parser("supersede")
    supersede.add_argument("--task-id", required=True)
    supersede.add_argument("--replacement-task", required=True)
    supersede.add_argument("--expected-revision", required=True, type=int)
    supersede.add_argument("--expected-replacement-revision", required=True, type=int)
    supersede.add_argument("--actor", required=True, help="当前已登记统筹会话，格式：统筹部/会话ID")
    supersede.add_argument("--reason", required=True)
    supersede.add_argument("--evidence", required=True)
    supersede.set_defaults(func=cmd_supersede)
    resolve = sub.add_parser("resolve")
    resolve.add_argument("--task-id", required=True)
    resolve.add_argument("--state", choices=("rejected_by_user", "abandoned"), required=True)
    resolve.add_argument("--expected-revision", required=True, type=int)
    resolve.add_argument("--actor", required=True, help="当前已登记统筹会话，格式：统筹部/会话ID")
    resolve.add_argument("--reason", required=True)
    resolve.add_argument("--evidence", required=True)
    resolve.set_defaults(func=cmd_resolve)
    complete = sub.add_parser("complete")
    complete.add_argument("--task-id", required=True)
    complete.add_argument("--actor", required=True, help="当前已登记任务部门会话，格式：部门/会话ID")
    complete.add_argument("--artifact", action="append", default=[])
    complete.add_argument("--external-artifact", action="append", default=[])
    complete.add_argument("--verified", action="append", required=True)
    complete.add_argument("--unverified", action="append", required=True)
    complete.add_argument("--mistake-check", required=True)
    complete.add_argument("--report", default="不适用")
    complete.add_argument("--event-receipt", action="append", default=[])
    complete.set_defaults(func=cmd_complete)
    ack = sub.add_parser("ack")
    ack.add_argument("--task-id", required=True)
    ack.add_argument(
        "--acknowledged-by", required=True,
        help="当前已登记统筹会话，格式：统筹部/会话ID",
    )
    ack.set_defaults(func=cmd_ack)
    listing = sub.add_parser("list")
    listing.add_argument("--department")
    listing.add_argument("--state", choices=STATES)
    listing.add_argument("--include-cold", action="store_true", help="显式审计全部历史 TASK；默认只显示当前切片")
    listing.set_defaults(func=cmd_list)
    rebuild = sub.add_parser("rebuild-index")
    rebuild.add_argument("--actor", required=True, help="当前已登记统筹会话；仅全新空协作层用 统筹部/bootstrap")
    rebuild.set_defaults(func=cmd_rebuild_index)
    onboard = sub.add_parser("onboard-check")
    onboard.add_argument("--department", required=True)
    onboard.set_defaults(func=cmd_onboard_check)
    onboard_bundle = sub.add_parser("onboard-bundle")
    onboard_bundle.add_argument("--department", required=True)
    onboard_bundle.set_defaults(func=cmd_onboard_bundle)
    next_action = sub.add_parser("next-action")
    next_action.add_argument("--task-id", required=True)
    next_action.set_defaults(func=cmd_next_action)
    doctor = sub.add_parser("doctor")
    doctor.set_defaults(func=cmd_doctor)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.cmd == "next-action":
            return args.func(args)
        init_layout()
        if args.cmd == "onboard-bundle":
            return args.func(args)
        with task_lock():
            recover_slice_enqueue_transaction()
            recover_gate_transaction()
            recover_slice_close_transaction()
            recover_index_transaction()
            return args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"TASK_ERROR | {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
'''


def session_state_script() -> str:
    return r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Durable resume state for external thread creation and same-department switches."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import stat
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl


UTF8_BOOTSTRAP_MARKER = "AGENT_TEAM_SESSION_UTF8_BOOTSTRAPPED"
PROTOCOL_VERSION = "1.5.1"
THREAD_ID_MAX_CHARS = 300
ACTOR_MAX_CHARS = 400
ROOT_FIELDS = {
    "schema_version", "protocol_version", "created_at", "updated_at", "profile",
    "session_mode", "role_order", "departments",
}
ITEM_FIELDS = {
    "role_id", "active", "notification_mode", "step", "thread_id", "previous_thread_id",
    "failed_from", "evidence", "operation_id", "note", "updated_at", "lifecycle_history",
}


def ensure_utf8_filesystem_runtime() -> None:
    encoding = (sys.getfilesystemencoding() or "").lower().replace("_", "-")
    if encoding not in {"ascii", "us-ascii", "ansi-x3.4-1968"}:
        return
    if os.environ.get(UTF8_BOOTSTRAP_MARKER) == "1":
        raise SystemExit("无法启用 UTF-8 文件系统编码。")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env[UTF8_BOOTSTRAP_MARKER] = "1"
    os.execve(sys.executable, [sys.executable, *sys.argv], env)


ensure_utf8_filesystem_runtime()

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def reject_duplicate_json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 含重复键: {key}")
        result[key] = value
    return result


def strict_json_loads(text, source):
    try:
        return json.loads(text, object_pairs_hook=reject_duplicate_json_pairs)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source} JSON 无效: {exc}") from exc


def archive_receipt_fields(evidence: str) -> dict[str, set[str]]:
    fields: dict[str, set[str]] = {}
    for token in evidence.split():
        key, separator, value = token.partition("=")
        if separator and key and value and not value.startswith("="):
            fields.setdefault(key.casefold(), set()).add(value)
    return fields


def valid_archive_receipt(evidence: str, thread_id: str) -> bool:
    fields = archive_receipt_fields(evidence)
    return (
        fields.get("thread_id") == {thread_id}
        and {value.casefold() for value in fields.get("archived", set())} == {"true"}
        and bool(fields.get("host") or fields.get("user_confirmation"))
    )


def discover_control_root() -> Path:
    local = Path(__file__).resolve().parents[1]
    project = local.parents[1]
    inside = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--is-inside-work-tree"],
        text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    result = subprocess.run(
        ["git", "-C", str(project), "worktree", "list", "--porcelain"],
        text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode == 0:
        first = next((line[9:] for line in result.stdout.splitlines() if line.startswith("worktree ")), "")
        candidate = Path(first) / "docs" / "collaboration" if first else None
        if candidate is not None and candidate.is_dir() and not candidate.is_symlink():
            return candidate.resolve(strict=True)
    if (project / ".git").exists() or (inside.returncode == 0 and inside.stdout.strip() == "true"):
        raise SystemExit("CONTROL_ROOT_ERROR | Git worktree 中无法验证主 docs/collaboration，拒绝写本地副本")
    return local


COLLAB = discover_control_root()
STATE_FILE = COLLAB / "会话启动状态.json"
REGISTRY_FILE = COLLAB / "部门表.md"
LOCKS = COLLAB / ".locks"
TASKS = COLLAB / "tasks"
STEPS = {"pending", "created", "onboarded", "registered", "failed"}
ALLOWED = {
    "pending": {"created", "failed"},
    "created": {"onboarded", "failed"},
    "onboarded": {"registered", "failed"},
    "registered": set(),
}


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="minutes")


def clean(name: str, value: str, max_chars: int = 500) -> str:
    result = value.strip()
    if not result:
        raise ValueError(f"{name} 不能为空")
    if any(ord(ch) < 32 for ch in result):
        raise ValueError(f"{name} 含控制字符")
    if len(result) > max_chars:
        raise ValueError(f"{name} 过长")
    return result


def clean_thread_id(value: str) -> str:
    result = clean("thread-id", value, max_chars=THREAD_ID_MAX_CHARS)
    if any(char.isspace() for char in result):
        raise ValueError("thread-id 不能包含空格、换行或制表符")
    if result.startswith("="):
        raise ValueError("thread-id 不能以等号开头")
    return result


@contextmanager
def state_lock():
    if LOCKS.exists() and (LOCKS.is_symlink() or not LOCKS.is_dir()):
        raise ValueError("锁目录不安全")
    LOCKS.mkdir(mode=0o700, exist_ok=True)
    lock_info = LOCKS.stat()
    if not stat.S_ISDIR(lock_info.st_mode) or (hasattr(os, "getuid") and lock_info.st_uid != os.getuid()):
        raise ValueError("锁目录不安全或不属于当前用户")
    if hasattr(os, "chmod"):
        os.chmod(LOCKS, 0o700)
    path = LOCKS / "project-control.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    file_info = os.fstat(fd)
    if not stat.S_ISREG(file_info.st_mode) or file_info.st_nlink != 1:
        os.close(fd)
        raise ValueError("身份锁不是单链接普通文件")
    if hasattr(os, "getuid") and file_info.st_uid != os.getuid():
        os.close(fd)
        raise ValueError("身份锁不属于当前用户")
    if hasattr(os, "fchmod"):
        os.fchmod(fd, 0o600)
    handle = os.fdopen(fd, "a+b", buffering=0)
    try:
        if os.name == "nt":
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def load() -> dict:
    if STATE_FILE.is_symlink() or not STATE_FILE.is_file():
        raise ValueError("会话启动状态文件缺失或不安全")
    payload = strict_json_loads(STATE_FILE.read_text(encoding="utf-8"), str(STATE_FILE))
    if (
        not isinstance(payload, dict)
        or set(payload) != ROOT_FIELDS
        or payload.get("schema_version") != 1
        or payload.get("protocol_version") != PROTOCOL_VERSION
        or any(not isinstance(payload.get(field), str) or not payload.get(field) for field in ("created_at", "updated_at", "profile"))
        or payload.get("session_mode") not in {"auto", "manual"}
        or not isinstance(payload.get("role_order"), list)
        or not isinstance(payload.get("departments"), dict)
        or not payload["departments"]
    ):
        raise ValueError("会话启动状态版本无效")
    for department, item in payload["departments"].items():
        if not isinstance(department, str) or not isinstance(item, dict) or set(item) != ITEM_FIELDS:
            raise ValueError(f"会话启动状态部门条目无效: {department}")
        if (
            not isinstance(item.get("active"), bool)
            or item.get("notification_mode") not in {"auto", "manual"}
            or item.get("step") not in STEPS
        ):
            raise ValueError(f"会话启动状态部门值无效: {department}")
        if any(not isinstance(item[field], str) for field in ITEM_FIELDS - {"active", "lifecycle_history"}):
            raise ValueError(f"会话启动状态部门类型无效: {department}")
        history = item["lifecycle_history"]
        if not isinstance(history, list) or any(
            not isinstance(event, dict)
            or set(event) != {"event", "at", "actor", "evidence", "thread_id"}
            or event.get("event") not in {"retired", "deactivated", "reactivated"}
            or any(not isinstance(event.get(field), str) or not event.get(field) for field in ("at", "actor", "evidence"))
            or not isinstance(event.get("thread_id"), str)
            for event in history
        ):
            raise ValueError(f"会话启动状态 lifecycle_history 无效: {department}")
    role_ids = [item["role_id"] for item in payload["departments"].values()]
    if (
        any(not isinstance(role, str) for role in payload["role_order"])
        or len(payload["role_order"]) != len(set(payload["role_order"]))
        or set(payload["role_order"]) != set(role_ids)
    ):
        raise ValueError("会话启动状态 role_order 无效")
    audit_roles = {"review", "test", "security", "finance"}
    active_layers = set()
    for item in payload["departments"].values():
        if not item["active"]:
            continue
        role_id = item["role_id"]
        active_layers.add("management" if role_id == "lead" else "audit" if role_id in audit_roles else "execution")
    if active_layers != {"management", "execution", "audit"}:
        raise ValueError("活跃部门未保持管理、执行、审核三层最小结构")
    validate_identity_registry(payload)
    return payload


def validate_identity_registry(payload: dict) -> None:
    departments = payload.get("departments")
    if not isinstance(departments, dict) or not departments:
        raise ValueError("会话启动状态未登记部门")
    owners: dict[str, str] = {}

    def register(raw: object, owner: str) -> None:
        if not isinstance(raw, str):
            raise ValueError(f"thread_id 类型无效: {owner}")
        if not raw:
            return
        if len(raw) > THREAD_ID_MAX_CHARS or raw.startswith("=") or any(char.isspace() for char in raw):
            raise ValueError(f"thread_id 不可表示为归档回执: {owner}")
        previous = owners.get(raw)
        if previous is not None:
            raise ValueError(f"thread_id 全局冲突: {raw}: {previous} / {owner}")
        owners[raw] = owner

    for department, item in departments.items():
        if not isinstance(department, str) or not isinstance(item, dict) or item.get("step") not in STEPS:
            raise ValueError(f"会话启动状态部门条目无效: {department}")
        current_thread = item.get("thread_id", "")
        previous_thread = item.get("previous_thread_id", "")
        operation_id = item.get("operation_id", "")
        failed_from = item.get("failed_from", "")
        if not item.get("active", False) and (
            item.get("step") != "pending" or current_thread or previous_thread
        ):
            raise ValueError(f"已停用部门仍保留活动会话事实: {department}")
        for label, thread_id in (("thread_id", current_thread), ("previous_thread_id", previous_thread)):
            if isinstance(thread_id, str) and thread_id and (
                len(thread_id) > THREAD_ID_MAX_CHARS
                or thread_id.startswith("=")
                or any(char.isspace() for char in thread_id)
            ):
                raise ValueError(f"会话 {label} 不可表示为归档回执: {department}")
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError(f"会话 operation_id 无效: {department}")
        if previous_thread and not operation_id.startswith("SWITCH-"):
            raise ValueError(f"待收口旧会话缺少 SWITCH 事务: {department}")
        if operation_id.startswith("SWITCH-") and not previous_thread:
            raise ValueError(f"SWITCH 事务缺少 previous_thread_id: {department}")
        if item["step"] == "pending" and current_thread:
            raise ValueError(f"pending 会话不得预先占用 thread_id: {department}")
        if item["step"] in {"created", "onboarded", "registered"} and not current_thread:
            raise ValueError(f"{item['step']} 会话缺少 thread_id: {department}")
        if item["step"] == "failed":
            if failed_from not in {"pending", "created", "onboarded"}:
                raise ValueError(f"failed 会话缺少可恢复的 failed_from: {department}")
            if (failed_from == "pending") == bool(current_thread):
                raise ValueError(f"failed 会话的 thread_id 与 failed_from 矛盾: {department}")
        elif failed_from:
            raise ValueError(f"非 failed 会话不得保留 failed_from: {department}")
        register(current_thread, f"正式部门:{department}:current")
        register(previous_thread, f"正式部门:{department}:previous")

    if TASKS.is_symlink():
        raise ValueError("tasks 目录不能是符号链接")
    if not TASKS.exists():
        return
    if not TASKS.is_dir():
        raise ValueError("tasks 路径不是普通目录")
    for path in sorted(TASKS.glob("TASK-*.json")):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"TASK 路径不安全: {path.name}")
        task = strict_json_loads(path.read_text(encoding="utf-8"), str(path))
        if not isinstance(task, dict):
            raise ValueError(f"TASK 根节点无效: {path.name}")
        temporary = task.get("temporary_executor")
        if not isinstance(temporary, dict):
            continue
        session = temporary.get("temporary_session")
        if not isinstance(session, dict):
            raise ValueError(f"临时会话登记结构无效: {path.name}")
        register(session.get("thread_id", ""), f"临时外包:{task.get('task_id', path.stem)}")


def write_atomic(path: Path, data: bytes, mode: int) -> None:
    temp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("写入失败")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def refresh_registry(payload: dict) -> None:
    if REGISTRY_FILE.is_symlink() or not REGISTRY_FILE.is_file():
        raise ValueError("部门表缺失或不安全")
    lines = REGISTRY_FILE.read_text(encoding="utf-8").splitlines()
    seen = set()
    for index, line in enumerate(lines):
        if not line.startswith("|") or "---" in line or "角色 ID" in line:
            continue
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(parts) < 6:
            continue
        department = parts[1]
        item = payload["departments"].get(department)
        if item is None:
            continue
        parts[3] = item.get("thread_id") or "待登记"
        parts[4] = item.get("notification_mode") or "待登记"
        parts[5] = "已停用" if not item.get("active", False) else {
            "pending": "待启用", "created": "上岗中", "onboarded": "上岗中",
            "registered": "已启用", "failed": "失败",
        }.get(item.get("step"), "待启用")
        lines[index] = "| " + " | ".join(parts) + " |"
        seen.add(department)
    if seen != set(payload["departments"]):
        raise ValueError("部门表与会话状态中的部门不一致")
    write_atomic(REGISTRY_FILE, ("\n".join(lines).rstrip() + "\n").encode("utf-8"), 0o644)


def save(payload: dict) -> None:
    validate_identity_registry(payload)
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    write_atomic(STATE_FILE, data, 0o600)
    try:
        refresh_registry(payload)
    except (OSError, ValueError) as exc:
        print(f"SESSION_INDEX_STALE | {exc} | 会话状态 JSON 已落盘", file=sys.stderr)


def entry(payload: dict, department: str) -> dict:
    try:
        item = payload["departments"][department]
        if not item.get("active", False):
            raise ValueError(f"部门已停用: {department}")
        return item
    except KeyError as exc:
        raise ValueError(f"未知部门: {department}") from exc


def registered_lead_actor(payload: dict) -> str:
    item = payload["departments"].get("统筹部", {})
    thread_id = item.get("thread_id", "")
    if (
        item.get("role_id") != "lead"
        or not item.get("active", False)
        or item.get("step") != "registered"
        or not isinstance(thread_id, str)
        or not thread_id
    ):
        raise ValueError("统筹部会话尚未登记")
    return f"统筹部/{thread_id}"


def cmd_rebuild_registry(args) -> int:
    payload = load()
    rows = []
    audit_roles = {"review", "test", "security", "finance"}
    by_role = {item["role_id"]: (department, item) for department, item in payload["departments"].items()}
    for role_id in payload["role_order"]:
        department, item = by_role[role_id]
        layer = "管理层" if role_id == "lead" else "审核层" if role_id in audit_roles else "执行层"
        status = "已停用" if not item["active"] else {
            "pending": "待启用", "created": "上岗中", "onboarded": "上岗中",
            "registered": "已启用", "failed": "失败",
        }[item["step"]]
        rows.append(
            f"| {layer} | {department} | `{role_id}` | {item['thread_id'] or '待登记'} | "
            f"{item['notification_mode']} | {status} |"
        )
    content = """# 部门表

> 把岗位绑定到具体会话;没有真实会话工具收据时,会话 ID 保持待登记。

## 团队摘要

- 项目类型:{profile}
- 创建日期:{created_at}
- 协议版本:{protocol}
- 会话创建模式:{session_mode}

## 部门列表

| 层 | 部门 | 角色 ID | 会话 ID | 通知模式 | 状态 |
|----|------|---------|---------|----------|------|
{rows}

## 使用规则

- 管理层、执行层、审核层必须齐全;新增、删除或替换部门前先获用户确认。
- `manual` 只生成文件;`auto` 也必须以真实会话工具收据和状态登记为准,不得把配置写成已创建。
- 每个新会话读取上岗引导后只运行一次 `onboard-bundle --department ...`；命令校验 freshness 并原样输出其余热入口和当前 TASK。
- 任务正文和状态只认 `tasks/TASK-*.json`;收件箱是活动任务索引,通知只发任务 ID 和短状态。
- 产品体验、范围取舍、设计方向、发布/外发、明显成本和隐私安全风险由用户确认;设计预览仅在用户提出或任务明确要求时制作。
- 审核部门亲自验证并只回结论和证据,不直接返工、放行或修改产物。
- 同部门换班须由用户授权;新会话登记成功后才归档旧会话,不 fork 旧历史。
- 会话 ID 和通知模式以会话状态 JSON 为准,由会话工具刷新本表;不要手工改行。通知模式变更须经用户确认后运行 `set-notification`。
""".format(
        profile=payload["profile"], created_at=payload["created_at"], protocol=PROTOCOL_VERSION,
        session_mode=payload["session_mode"], rows="\n".join(rows),
    )
    encoded = content.encode("utf-8")
    write_atomic(REGISTRY_FILE, encoded, 0o644)
    print(f"SESSION_REGISTRY_OK | departments={len(rows)}")
    return 0


def cmd_show(args) -> int:
    payload = load()
    rows = []
    for department, item in sorted(payload["departments"].items()):
        rows.append(
            f"{department} | {item['step']} | thread:{item.get('thread_id') or '-'} | "
            f"previous:{item.get('previous_thread_id') or '-'} | op:{item.get('operation_id') or '-'}"
        )
    print("\n".join(rows))
    return 0


def cmd_mark(args) -> int:
    if args.step not in STEPS - {"pending"}:
        raise ValueError("mark step 非法")
    payload = load()
    item = entry(payload, args.department)
    current = item["step"]
    if current == "failed":
        failed_from = item.get("failed_from")
        expected = {"pending": "created", "created": "onboarded", "onboarded": "registered"}.get(failed_from)
        if args.step != expected:
            raise ValueError(f"失败重试必须从上次成功点继续: {failed_from} -> {expected}")
    elif args.step not in ALLOWED.get(current, set()):
        raise ValueError(f"非法会话状态转换: {current} -> {args.step}")
    evidence = clean("evidence", args.evidence)
    if args.step == "created":
        if item.get("thread_id"):
            raise ValueError("created 不能覆盖已登记的 thread-id")
        item["thread_id"] = clean_thread_id(args.thread_id)
    elif args.step in {"onboarded", "registered"}:
        if not args.thread_id or args.thread_id != item.get("thread_id"):
            raise ValueError("onboarded / registered 必须提供与已记录值一致的 thread-id")
    if args.step in {"onboarded", "registered"} and not item.get("thread_id"):
        raise ValueError("尚未记录 thread-id")
    if args.step == "failed":
        item["failed_from"] = current
    else:
        item["failed_from"] = ""
    item["step"] = args.step
    item["note"] = args.note.strip()
    item["evidence"] = evidence
    item["updated_at"] = now_iso()
    save(payload)
    print(f"SESSION_OK | {args.department} | {args.step} | {item.get('thread_id') or '-'} | {item.get('operation_id') or '-'}")
    return 0


def cmd_begin_switch(args) -> int:
    payload = load()
    item = entry(payload, args.department)
    if item["step"] != "registered" or item.get("thread_id") != args.old_thread_id:
        raise ValueError("只能从已登记且 ID 匹配的旧会话开始换班")
    if item.get("previous_thread_id") or item.get("operation_id", "").startswith("SWITCH-"):
        raise ValueError("上一次换班尚未收口，不能覆盖 previous_thread_id")
    item["previous_thread_id"] = item["thread_id"]
    item["thread_id"] = ""
    item["step"] = "pending"
    item["operation_id"] = "SWITCH-" + uuid.uuid4().hex[:10].upper()
    item["note"] = clean("reason", args.reason)
    item["updated_at"] = now_iso()
    save(payload)
    print(f"SESSION_SWITCH_READY | {args.department} | {item['operation_id']} | {item['previous_thread_id']}")
    return 0


def cmd_restore_old(args) -> int:
    payload = load()
    item = entry(payload, args.department)
    if not item.get("operation_id", "").startswith("SWITCH-"):
        raise ValueError("当前不是换班操作")
    if not item.get("previous_thread_id") or item["step"] not in {"pending", "failed", "created", "onboarded", "registered"}:
        raise ValueError("没有可恢复的旧会话")
    note = clean("note", args.note)
    new_thread_id = item.get("thread_id", "")
    archive_evidence = args.evidence.strip()
    if new_thread_id:
        if not archive_evidence or not valid_archive_receipt(archive_evidence, new_thread_id):
            raise ValueError(
                "restore-old 在新会话已创建后，必须提供精确绑定新 thread_id 的归档回执"
            )
        evidence = clean("evidence", archive_evidence)
    else:
        if item["step"] not in {"pending", "failed"}:
            raise ValueError("无新 thread_id 时只能从创建前失败恢复旧会话")
        if item["step"] == "failed" and item.get("failed_from") != "pending":
            raise ValueError("当前失败阶段已应存在新 thread_id，拒绝猜测恢复")
        evidence = note
    item["thread_id"] = item["previous_thread_id"]
    item["previous_thread_id"] = ""
    item["step"] = "registered"
    item["failed_from"] = ""
    item["operation_id"] = "ACTIVE-" + uuid.uuid4().hex[:10].upper()
    item["note"] = note
    item["evidence"] = evidence
    item["updated_at"] = now_iso()
    save(payload)
    print(f"SESSION_RESTORED | {args.department} | {item['thread_id']}")
    return 0


def cmd_finish_switch(args) -> int:
    payload = load()
    item = entry(payload, args.department)
    if not item.get("operation_id", "").startswith("SWITCH-"):
        raise ValueError("当前不是换班操作")
    if item["step"] != "registered" or item.get("thread_id") != args.new_thread_id or not item.get("previous_thread_id"):
        raise ValueError("新会话尚未登记或换班状态不完整")
    evidence = clean("evidence", args.evidence)
    if not valid_archive_receipt(evidence, item["previous_thread_id"]):
        raise ValueError(
            "finish-switch 归档回执必须精确绑定 previous_thread_id、包含 archived=true，"
            "并注明 host 或 user_confirmation"
        )
    item["previous_thread_id"] = ""
    item["operation_id"] = "ACTIVE-" + uuid.uuid4().hex[:10].upper()
    item["note"] = evidence
    item["evidence"] = evidence
    item["updated_at"] = now_iso()
    save(payload)
    print(f"SESSION_SWITCH_DONE | {args.department} | {item['thread_id']}")
    return 0


def cmd_set_notification(args) -> int:
    payload = load()
    actor = clean("actor", args.actor, max_chars=ACTOR_MAX_CHARS)
    expected_actor = registered_lead_actor(payload)
    if actor != expected_actor:
        raise ValueError(f"actor 必须匹配当前已登记统筹会话: {expected_actor};该字段只作审计声明")
    item = entry(payload, args.department)
    item["notification_mode"] = args.mode
    item["note"] = clean("evidence", args.evidence)
    item["updated_at"] = now_iso()
    save(payload)
    print(f"SESSION_NOTIFICATION_OK | {args.department} | {args.mode}")
    return 0


def cmd_retire(args) -> int:
    payload = load()
    actor = clean("actor", args.actor, max_chars=ACTOR_MAX_CHARS)
    expected_actor = registered_lead_actor(payload)
    if actor != expected_actor:
        raise ValueError(f"actor 必须匹配当前已登记统筹会话: {expected_actor};该字段只作审计声明")
    item = entry(payload, args.department)
    if item.get("role_id") == "lead":
        raise ValueError("统筹部会话不能通过 retire 退役")
    if item["step"] != "registered" or not item.get("thread_id") or item.get("previous_thread_id"):
        raise ValueError("只允许退役无换班事务的已登记部门会话")
    thread_id = item["thread_id"]
    evidence = clean("evidence", args.evidence, max_chars=1000)
    if not valid_archive_receipt(evidence, thread_id):
        raise ValueError("retire 必须提供精确绑定当前 thread_id 的真实归档回执")
    history = list(item["lifecycle_history"])
    history.append({
        "event": "retired", "at": now_iso(), "actor": actor,
        "evidence": evidence, "thread_id": thread_id,
    })
    item["lifecycle_history"] = history
    item["step"] = "pending"
    item["thread_id"] = ""
    item["previous_thread_id"] = ""
    item["failed_from"] = ""
    item["operation_id"] = "RETIRED-" + uuid.uuid4().hex[:10].upper()
    item["note"] = evidence
    item["evidence"] = evidence
    item["updated_at"] = now_iso()
    save(payload)
    print(f"SESSION_RETIRED | {args.department} | thread_id={thread_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="agent-team 会话创建/换班恢复状态")
    sub = parser.add_subparsers(dest="cmd", required=True)
    show = sub.add_parser("show")
    show.set_defaults(func=cmd_show)
    rebuild = sub.add_parser("rebuild-registry")
    rebuild.set_defaults(func=cmd_rebuild_registry)
    mark = sub.add_parser("mark")
    mark.add_argument("--department", required=True)
    mark.add_argument("--step", required=True, choices=sorted(STEPS - {"pending"}))
    mark.add_argument("--thread-id", default="")
    mark.add_argument("--note", default="")
    mark.add_argument("--evidence", required=True)
    mark.set_defaults(func=cmd_mark)
    switch = sub.add_parser("begin-switch")
    switch.add_argument("--department", required=True)
    switch.add_argument("--old-thread-id", required=True)
    switch.add_argument("--reason", required=True)
    switch.set_defaults(func=cmd_begin_switch)
    restore = sub.add_parser("restore-old")
    restore.add_argument("--department", required=True)
    restore.add_argument("--note", required=True)
    restore.add_argument("--evidence", default="")
    restore.set_defaults(func=cmd_restore_old)
    finish = sub.add_parser("finish-switch")
    finish.add_argument("--department", required=True)
    finish.add_argument("--new-thread-id", required=True)
    finish.add_argument("--evidence", required=True)
    finish.set_defaults(func=cmd_finish_switch)
    notification = sub.add_parser("set-notification")
    notification.add_argument("--department", required=True)
    notification.add_argument("--mode", choices=("auto", "manual"), required=True)
    notification.add_argument("--evidence", required=True)
    notification.add_argument("--actor", required=True, help="当前已登记统筹会话，格式：统筹部/会话ID")
    notification.set_defaults(func=cmd_set_notification)
    retire = sub.add_parser("retire")
    retire.add_argument("--department", required=True)
    retire.add_argument("--actor", required=True, help="当前已登记统筹会话，格式：统筹部/会话ID")
    retire.add_argument("--evidence", required=True)
    retire.set_defaults(func=cmd_retire)
    args = parser.parse_args()
    try:
        with state_lock():
            return args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"SESSION_ERROR | {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
'''


# ---- 每部门文件(在 部门/<部门名>/ 下) -------------------------------------

def role_markdown(
    key: str, role: dict[str, str], date: str, overlay: dict[str, object] | None = None,
) -> str:
    layer_cn = LAYER_CN.get(role.get("layer", ""), "")
    audit_rule = (
        "- 审核层必须亲自验证、覆盖验收出口与失败路径,不采信执行部门转述;结论只回统筹部,不直接改产物或放行。"
        if role.get("layer") == "audit"
        else "- 执行/管理层先自检再回报,不得替审核层给出最终质量、风险或成本放行结论。"
    )
    management_rule = (
        "- 正常模式一次只推进一个切片和一个执行 owner;最多两个 gate 且绑定同一候选。宿主有 heartbeat/lease/查询/等待/恢复/归档才自动等待;否则 manual-degraded 人工交接。不得自动开启下一切片。同一 gate 跨两代连续 FAIL 或出现冻结条件时运行 `freeze-new-work`;仅凭用户明确恢复证据运行 `unfreeze-new-work`。\n"
        "- 用户互动按意图、TASK 影响和是否需用户信息/体验判断/授权分流:需则 user_exit pending 停下,否则本切片验证后 not_applicable 继续。临时问题直接答并保留 TASK;正式汇报用“结果 / 需要你做什么 / 还需注意”,第三段按需,普通问答不套模板。"
        if key == "lead"
        else "- 项目总进度由统筹部维护;本部门用收件箱、交接班文档和产出路径完成闭环。"
    )
    confirmation_heading = (
        "## 必须请用户确认的节点" if key == "lead" else "## 必须经统筹请用户确认的节点"
    )
    base = f"""# {role['name']}岗位说明

> 角色 ID:`{key}` ·所在层:{layer_cn} ·创建日期:{date}
> 本文件只放本岗位长期职责与边界;通用流程细则按需查 `../../README.md` 和 `../../任务交接模板.md`。

## 负责什么

{role['mission']}

## 不负责什么

{role['not_responsible']}

## 输入

{role['inputs']}

## 输出

{role['outputs']}

## 可写文件 / 目录

{role['can_write']}

## 禁止写入

{role['cannot_write']}

{confirmation_heading}

{role['confirm']}

## 核心协作纪律

- 手上只领取一件;任务正文、所有权和状态只认 `../../tasks/TASK-*.json`,并用 `../../scripts/agent_team_task.py` 流转。收件箱只是自动索引;缺验收出口或失败路径时回统筹部补齐。
- 完成回报必须带真实产出、已验证/未验证项、`TASK_STATE_OK` 和错题自检;用户门禁由统筹部统一办理,本部门不得把建议写成授权。
- 后台及普通低影响测试照常自动执行,不增加用户汇报。测试可能明显妨碍用户正常使用设备时,执行前说明影响、预计时长、期间能否继续工作和退出方式;前台独占或难以自行退出时还须取得当次明确确认,阶段性 Kickoff / 开发授权不能替代。
{audit_rule}
{management_rule}
"""
    if overlay is None:
        return base
    grouped: dict[str, list[str]] = {}
    for addition in overlay["additions"]:
        grouped.setdefault(addition["section"], []).append(addition["text"].strip())
    appendix = [
        "## 项目定制职责",
        "",
        "> 本节由协议登记的只追加项目层生成；标准职责仍按当前 Skill 升级。",
        f"> 复核基线：`{overlay['reviewed_base_contract_version']}`；确认依据已记录在协议真值。",
        "",
    ]
    for section in ROLE_OVERLAY_SECTIONS:
        if section in grouped:
            appendix.extend((f"### {ROLE_OVERLAY_SECTIONS[section]} · 项目补充", "", "\n\n".join(grouped[section]), ""))
    return base + "\n".join(appendix) + "\n"



def bootstrap_markdown(key: str, role: dict[str, str]) -> str:
    layer_cn = LAYER_CN.get(role.get("layer", ""), "")
    return f"""# {role['name']} 上岗引导

> 定位:本部门新会话首次接班与换班的唯一入口。同一会话后续不重复读取。
> 自动模式由会话工具发送本段;人工模式由用户粘贴本段。

```
你现在是【{role['name']}】(角色 ID:{key} ·所在层:{layer_cn})。

当前消息/文件就是第 1 份上岗入口,不要重复读取。接着按顺序读取:

2. 运行 `python3 docs/collaboration/scripts/agent_team_task.py onboard-bundle --department "{role['name']}"`。
3. 它校验 freshness 并原样输出岗位说明、交接班、收件箱和当前 TASK；不生成摘要、不读冷历史。失败时停止并请统筹重建；成功后不重复读取。

- 交接或收件箱指向任务时只读对应 TASK JSON。已有授权清楚且无冲突的 `claimed` 任务,短报后同一轮续做;无任务、授权不清、存在冲突或用户只要求接班时停下。
- 只按当前任务需要查项目正文、错题、报告或日志;默认不读冷历史、其他部门正文、完整 diff 或测试全文。处理任务期间不刷新收件箱。
- 需求变化、用户纠偏、关键决策和重大事故直接用日志工具写事实,不先读日志。通知沿用已登记模式,只发短状态。
- “交班”只更新交接和必要日志;“换会话 / 换班”才按会话启动清单创建全新同部门会话。新会话登记成功后再归档旧会话,不 fork 旧历史,失败时保留旧会话。
```
"""


def state_markdown(key: str, role: dict[str, str], date: str) -> str:
    return f"""# {role['name']} · 交接班文档
<!-- agent-team current-slice:start -->
<!-- agent-team hot-state:pending-rebuild -->

## 机器生成的当前切片

> 等待任务工具首次重建；本区块外文字是人工提示，不能充当 TASK 身份或 freshness 证据。
<!-- agent-team current-slice:end -->

> 角色 ID:`{key}` ·最近更新:{date}
> 这是本部门的**语义交接**(给接班的人看),不是任务状态真值或流水账。任务所有权、状态和产物只认 `../../tasks/TASK-*.json`;若两者冲突,以任务 JSON 为准并修正本文件。
> 铁律:从这里删掉的档案级事实,必须先用 `../../scripts/agent_team_log.py append` 追加到本周日志末尾,绝不直接丢;普通过程不记。

## 当前任务补充说明

> 只记录任务 JSON 不适合承载的恢复信息。没有时写“无”;干活时不刷收件箱。

- 已做到:无
- 关键中间结论:无
- 相关产出路径:无

## 已定、不再回退的决策

- _(决策 + 一句原因;后续会话不该再重新纠结)_

## 下一步

- _(做完在办的之后、或下个会话接手应先做什么)_

## 已知坑 / 未决问题

- _(踩过的坑怎么绕、还没解决的问题)_

## 关键文件指针

- _(本部门常碰的文件 / 产出路径)_
"""


def inbox_markdown(key: str, role: dict[str, str], date: str) -> str:
    return f"""# {role['name']} · 收件箱

<!-- agent-team task index; use scripts/agent_team_task.py -->
> 自动索引;任务正文与状态以 `../../tasks/` 中的单任务 JSON 为准,不要手工编辑。

## 待领取

_(没有待领取任务)_

## 当前在办 / 阻断

_(没有在办任务)_
"""


def reports_readme_markdown(role: dict[str, str], date: str) -> str:
    return f"""# 审核报告

> 创建日期:{date}
> {role['name']}使用。每份必须附本部门亲自取得的独立证据,不得把执行部门的完成陈述当成验证结果。
> 证据结构由任务领域决定;涉及用户可见结果时必须验证真实用户出口。审核结论只回统筹部,不自动返工、放行或推进下一节点。
> 文件命名:`YYYY-MM-DD-对象-审核报告.md`。正文使用 YAML frontmatter,每个标量字段保持单行,便于定位、归档和跨部门引用。

<!-- 一份审核报告的格式:
---
type: audit_report
department: {role['name']}
target: 待填
status: pending
date: {date}
related_task: 待填
decision: 待定
tags: []
summary: 待填一句话结论
---

## 审核对象与标准
## 独立证据
## 用户可见出口（如适用）
## 必测失败路径
- (至少 1-3 个打破 happy path 的失败 / 异常 / 边界场景)
## 自设计反向探针
- (每个关键风险至少一个;说明探针如何证明不是只跑 happy path)
## 检查方法
## 实际结果
## 问题清单
## 未覆盖项
## 结论:通过 / 不通过 + 理由
## 需要用户决定
-->
> 完成审核任务前，必须把 `status` 改为 `final`，把 `decision` 改为 `pass` 或 `fail`，并写出真实 summary；草稿不能通过任务完成闸门。
"""


def work_reports_readme_markdown(role: dict[str, str], date: str) -> str:
    return f"""# 报告

> 创建日期:{date}
> 不是所有任务都需要正式报告。默认用任务完成记录 + 交接班闭环;只有复杂研究、设计、方案、架构、数据分析、阶段总结或用户决策材料才写工作报告。
> 文件命名:`YYYY-MM-DD-对象-报告类型.md`。正文使用 YAML frontmatter,每个标量字段保持单行,便于定位、归档和跨部门引用。

<!-- 工作报告模板:
---
type: work_report
department: {role['name']}
target: 待填
status: draft
date: {date}
related_task: 待填
decision: 待定
tags: []
summary: 待填一句话摘要
---

## 背景
## 结论
## 证据 / 过程
## 风险 / 未覆盖项
## 建议下一步
-->
"""


def special_conclusion_readme(date: str) -> str:
    return f"""# 专项结论

> 创建日期:{date}
> 只放会被多个部门复用的结论。只影响一个任务、一个部门的结论放在对应报告正文里;长期改变项目规则、架构、依赖、安全或发布方式的结论升级到 `docs/decisions/`。
> 文件命名:`YYYY-MM-DD-对象-专项结论.md`。正文使用 YAML frontmatter,每个标量字段保持单行,便于定位、归档和跨部门引用。

<!-- 专项结论模板:
---
type: special_conclusion
department: 统筹部
target: 待填
status: active
date: {date}
related_task: 待填
decision: 待填
tags: []
summary: 待填一句话结论
---

## 结论
## 适用范围
## 不适用范围
## 证据 / 来源
## 后续引用方式
-->
"""


def cuoti_markdown(date: str) -> str:
    return f"""# 错题集

> 创建日期:{date}
> AI 犯过的错 + 正确做法。只在当前任务相关时读取,避免把历史错误变成固定上下文。
> 用户纠正了 AI、或审核层发现可复发流程错误时,沉淀一条到这里。最新在最上方。
> 节点回报必须包含“错题自检”:说明已检查哪些相关错题、是否命中、如何处理。

## 写入标准

- 只记录未来可能复发、且能写出明确正确做法的错误。
- 普通一次性 bug 不进错题集;除非它暴露了可复发的流程问题。
- 新错题写入后,相关部门后续回报必须在“错题自检”中检查它。

## 写入格式

```
## YYYY-MM-DD · [部门] · 场景标题
- 错误:[AI 具体做错了什么]
- 正确:[正确做法]
- 关联:[相关物料/决策,便于倒查]
```

<!-- 示例:
## 2026-06-15 · [内容部] · 把未确认选题当定稿写了
- 错误:选题还在待审,就当定稿写完了整篇,白写一篇。
- 正确:动笔前先确认选题状态为“定稿”。
- 关联:视频007
-->
"""


def session_state_payload(roles: list[str], date: str, notification_mode: str, profile: str) -> str:
    payload = {
        "schema_version": 1,
        "protocol_version": PROTOCOL_VERSION,
        "created_at": date,
        "updated_at": date,
        "profile": profile,
        "session_mode": notification_mode,
        "role_order": roles,
        "departments": {
            ROLE_DEFS[key]["name"]: {
                "role_id": key,
                "active": True,
                "notification_mode": notification_mode,
                "step": "pending",
                "thread_id": "",
                "previous_thread_id": "",
                "failed_from": "",
                "evidence": "",
                "operation_id": "INIT-" + hashlib.sha256(f"{date}:{key}".encode("utf-8")).hexdigest()[:10].upper(),
                "note": "",
                "updated_at": date,
                "lifecycle_history": [],
            }
            for key in roles
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def route_table_markdown(roles: list[str], date: str) -> str:
    rows = []
    for key in roles:
        role = ROLE_DEFS[key]
        if key == "lead":
            route = "接收所有裁决、返工、阻断、审核结论、状态升级与放行回报"
        elif role["layer"] == "audit":
            route = "由统筹部派审核任务;审核结论只回统筹部"
        else:
            route = "由统筹部派执行任务;纯澄清可直连;状态变化回统筹部"
        rows.append(f"| {role['name']} | `{key}` | {LAYER_CN[role['layer']]} | {route} |")
    return f"""# 路由表

> 协议版本:{PROTOCOL_VERSION} · 更新日期:{date}
> 本表由脚手架确定性生成,新增部门时同步更新,不留“之后手工补路由”的半完成状态。

| 部门 | 角色 ID | 层 | 默认路由 |
|---|---|---|---|
{chr(10).join(rows)}

## 统一路由规则

- 只问一句、不改产物、不改状态的澄清:可直连目标部门。
- 派单、返工、阻断、裁决、需求/范围变更、审核结论、放行、状态升级、增删部门:经统筹部。
- 通知只带任务 ID 和“有新任务 / 已完成 / 遇到阻断”;任务真值在 `tasks/` 中。
"""


def registry_session_data(registry_text: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for line in registry_text.splitlines():
        if not line.startswith("|") or "---" in line or "角色 ID" in line:
            continue
        parts = [part.strip().strip("`") for part in line.strip().strip("|").split("|")]
        if len(parts) >= 6 and parts[2] in ROLE_DEFS:
            result[parts[2]] = {
                "session_id": parts[3],
                "notification": parts[4],
                "status": parts[5],
            }
    return result


def registry_rows(roles: list[str], existing: dict[str, dict[str, str]] | None = None) -> list[str]:
    rows = []
    existing = existing or {}
    for key in roles:
        role = ROLE_DEFS[key]
        layer_cn = LAYER_CN.get(role.get("layer", ""), "")
        saved = existing.get(key, {})
        session_id = saved.get("session_id", "待登记")
        notification = saved.get("notification", "待登记")
        status = saved.get("status", "待启用")
        rows.append(
            f"| {layer_cn} | {role['name']} | `{key}` | {md_escape(session_id)} | {md_escape(notification)} | {md_escape(status)} |"
        )
    return rows


REGISTRY_RULES_MARKER = "\n\n## 使用规则"


def registry_markdown(roles: list[str], profile: str, date: str, session_mode: str,
                      existing: dict[str, dict[str, str]] | None = None) -> str:
    normalized = {key: dict(value) for key, value in (existing or {}).items()}
    for key in roles:
        item = normalized.setdefault(key, {})
        if item.get("notification") in {None, "", "待登记", "-"}:
            item["notification"] = session_mode
    rows = registry_rows(roles, normalized)
    return f"""# 部门表

> 把岗位绑定到具体会话;没有真实会话工具收据时,会话 ID 保持待登记。

## 团队摘要

- 项目类型:{profile}
- 创建日期:{date}
- 协议版本:{PROTOCOL_VERSION}
- 会话创建模式:{session_mode}

## 部门列表

| 层 | 部门 | 角色 ID | 会话 ID | 通知模式 | 状态 |
|----|------|---------|---------|----------|------|
{chr(10).join(rows)}

## 使用规则

- 管理层、执行层、审核层必须齐全;新增、删除或替换部门前先获用户确认。
- `manual` 只生成文件;`auto` 也必须以真实会话工具收据和状态登记为准,不得把配置写成已创建。
- 新会话读取上岗引导后只运行一次 `onboard-bundle --department ...`；命令机械校验 freshness 并原样输出其余热入口和当前 TASK，不生成摘要或读取冷历史。
- 任务正文和状态只认 `tasks/TASK-*.json`;收件箱是活动任务索引,通知只发任务 ID 和短状态。
- 产品体验、范围取舍、设计方向、发布/外发、明显成本和隐私安全风险由用户确认;设计预览仅在用户提出或任务明确要求时制作。
- 审核部门亲自验证并只回结论和证据,不直接返工、放行或修改产物。
- 同部门换班须由用户授权;新会话登记成功后才归档旧会话,不 fork 旧历史。
- 会话 ID 和通知模式以会话状态 JSON 为准,由会话工具刷新本表;不要手工改行。通知模式变更须经用户确认后运行 `set-notification`。
"""



def handoff_template_markdown() -> str:
    return """# 任务交接协议

> 任务真值是 `tasks/TASK-*.json`;各部门 `收件箱.md` 只是工具重建的可读索引。禁止手工改收件箱来创建、领取或完成任务。

## 一个任务怎么流转

1. 统筹部用 `enqueue` 创建唯一活动切片 owner，并用零到两个 `--required-gate` 声明专业 gate；存在活动切片时拒绝第二 owner。
2. `user_required / user_rejected` 禁止领取;用户决定变化时用 `authorize` 记录状态与证据。
3. `claim / block / wait / resume / complete` 必须绑定所属部门当前登记会话；换班后先 `rebind-owner`。
4. owner 用 `bind-candidate` 固定 manifest 和 SHA-256；随后按同一 slice ID 创建最多两个 gate TASK。
5. gate 用 `gate-verdict` 把 PASS/FAIL 追加到同一 TASK 并绑定当前 candidate ID；跨两代连续 FAIL 自动冻结。
6. 全部 gate PASS 后由统筹记录用户出口。gate 先完成和核收，owner 再以同一 manifest 完成和核收；活动切片随后进入冷历史。

```bash
python3 docs/collaboration/scripts/agent_team_task.py enqueue \
  --actor "统筹部/已登记会话ID" \
  --department 开发部 --from-department 统筹部 --title "节点名" \
  --node "本次只做的节点" --details "必要背景与输出" \
  --acceptance-exit "用户最终在哪里看到什么" \
  --failure-path "输入缺失时的用户出口" --failure-path "执行失败时的用户出口" \
  --confirmation "完成后谁决定什么" --domain-stage "开发实现" \
  --authorization-state user_confirmed \
  --authorization-evidence "用户确认消息或会话指针" \
  --required-gate test

python3 docs/collaboration/scripts/agent_team_task.py claim --task-id TASK-YYYYMMDD-XXXXXX --claimed-by "开发部/已登记会话ID"
python3 docs/collaboration/scripts/agent_team_task.py authorize --task-id TASK-YYYYMMDD-XXXXXX \
  --state user_confirmed --evidence "用户确认消息或会话指针" --actor "统筹部/已登记会话ID"
python3 docs/collaboration/scripts/agent_team_task.py complete --task-id TASK-YYYYMMDD-XXXXXX \
  --actor "开发部/已登记会话ID" \
  --artifact "产出路径" --verified "已验证内容" --unverified "未验证项;没有则写无" \
  --mistake-check "已检查相关错题,无命中"
python3 docs/collaboration/scripts/agent_team_task.py ack --task-id TASK-YYYYMMDD-XXXXXX --acknowledged-by "统筹部/已登记会话ID"
```

## 三轴状态

- `execution_state`:工具管理的并发真值,`queued / claimed / blocked / waiting_input / completed / acknowledged`。
- `domain_stage`:项目领域自己的阶段语义,例如“资料收集”、“开发实现”、“财务复核”。
- `authorization_state`:用户授权状态,`none / user_required / user_confirmed / user_rejected`。

## 收据与日志

- `TASK_STATE_OK` 只证明状态已持久化、本地产物路径已校验和外部产物已显式声明;不证明业务质量。
- `LOG_OK` 只证明一条真实轨迹事件已写入。只在 `MILESTONE / CHANGE / CORRECTION / DECISION / INCIDENT` 真实发生时记录,并带上任务 ID。
- 完成四件套:产出路径、验证结果(含未验证项)、`TASK_STATE_OK`、错题自检。

## 短唤醒

```text
【统筹部→开发部】TASK-YYYYMMDD-XXXXXX 有新任务,请查看收件箱索引。
【开发部→统筹部】TASK-YYYYMMDD-XXXXXX 已完成,请核收。
【开发部→统筹部】TASK-YYYYMMDD-XXXXXX 遇到阻断,请查看任务状态。
【发现者→统筹部】TASK-YYYYMMDD-XXXXXX 会话异常或状态不一致,请对账并恢复。
```

通知不复制任务全文。涉及体验、范围、成本、安全、发布或必须用户确认的节点,未获明确授权不继续。
"""


def session_startup_markdown(roles: list[str], session_mode: str, date: str) -> str:
    rows = []
    for index, key in enumerate(roles, start=1):
        role = ROLE_DEFS[key]
        rows.append(
            f"| {index:02d} | {role['name']} | `{key}` | 部门/{role['name']}/上岗引导.md | 待登记 | {session_mode} |"
        )
    return f"""# 会话启动清单

> 创建日期:{date}
> 会话创建模式:{session_mode}
> 用途:把“部门文件已创建”和“部门会话已创建”分清楚。没有实际调用会话管理工具创建窗口时,不得声称会话已创建。
> 持久化真值:`会话启动状态.json`;状态只通过 `scripts/agent_team_session.py` 修改。

## 启动前硬闸

- 明确是 App/Web/SaaS/AI 工具/Vibe Coding 时统一走互联网产品主分支;AI 只改变产品规划内容和开发任务,不额外拆部门。只有最终交付物 / 目标不明时才追问。
- 先确认会话创建模式,再搭建协作层:
  - `自动`:Codex 等有会话管理工具的 Agent 负责创建部门会话。
  - `手动`:用户先手动创建各部门会话窗口,Agent 只生成文件和上岗引导。
- 创建 `docs/collaboration/`、新增/删除/替换部门、首次创建部门会话、改变跨会话路由或通知模式前,必须先让用户确认;已登记为自动/人工的短唤醒按 `部门表.md` 执行,不每次重复确认。

## 自动模式(Codex / 有会话管理工具)

执行顺序:

1. 新建空协作层后，统筹先运行 `python3 docs/collaboration/scripts/agent_team_task.py rebuild-index --actor "统筹部/bootstrap"`；升级时改传当前已登记统筹 actor。
2. 运行 `python3 docs/collaboration/scripts/agent_team_session.py show`,只继续 `pending / failed` 项;已有 thread ID 的 `created / onboarded / registered` 禁止重复创建。
3. 用环境可用的会话工具创建部门会话。成功后立即用 `mark --department ... --step created --thread-id ... --evidence "外部工具收据"` 持久化。
4. 把对应 `上岗引导.md` 发给该会话;成功后用相同 thread ID 标记 `onboarded`,并记录外部收据。
5. 用相同 thread ID 标记 `registered` 并记录登记证据;会话工具随后刷新 `部门表.md` 派生索引,不要手工改表。
6. 任一步失败都用 `mark ... --step failed --evidence "真实错误"` 记录。重试只能从失败前最后成功点继续,不重复创建已有 thread ID 的会话。
7. 宿主确有 heartbeat、lease、状态查询、等待、恢复和归档适配器时才自动接收与恢复；缺任一能力时记录 `manual-degraded`，只做人工短交接，不轮询、不承诺无人值守。

初始通知模式继承协作层的 `auto / manual`。用户确认改变通知模式后运行 `agent_team_session.py set-notification --department ... --mode auto|manual --evidence "用户确认指针"`;不要手工改部门表。

## 手动模式(其他 Agent / 无会话管理工具)

执行顺序:

1. 全新空协作层先运行 `agent_team_task.py rebuild-index --actor "统筹部/bootstrap"`，生成第一份可校验的热索引。
2. 用户按下表手动创建各部门会话窗口。
3. 给每个窗口粘贴对应 `上岗引导.md`。
4. 读取上岗引导后运行一次 `agent_team_task.py onboard-bundle --department ...`；它校验 freshness 并原样输出岗位说明、交接班、收件箱和当前 TASK。
5. 取得每个窗口可稳定引用的会话 ID 后，依次运行 `mark --step created / onboarded / registered --thread-id <同一ID> --evidence "人工创建、发送和接班记录"`。统筹部必须先完成 `registered`，否则不能派第一张任务。
6. 部门会话先短报职责、当前任务和待确认问题;已有授权清楚的 claimed 任务且无冲突时同一轮续做。
7. 后续任务通过任务工具流转;收件箱由工具重建,会话消息只做短唤醒。

## 同部门换班(需用户授权)

- 会话出现反复遗忘边界、与项目文件矛盾、偏离当前任务或质量明显下降时,先说明具体原因、继续使用旧会话的风险和当前在办事项,然后询问用户是否换班。未获明确同意时保留当前会话,不自动创建、登记或归档。
- 用户在部门会话说“换会话 / 切换会话 / 换班”,即明确授权本次创建同部门新会话并在接班成功后归档旧会话。
- 用户授权后先执行 `agent_team_session.py begin-switch --department ... --old-thread-id ...`;旧会话再更新 `交接班文档.md` 和必要日志。没有已登记旧 ID 时不得自动归档。
- 使用当前宿主提供的会话管理能力创建同项目新会话并发送接班消息;具体工具由 Agent 按当前环境选择。不要用复制旧聊天历史的 fork。
- 新会话接班消息必须带部门名、新旧会话 ID 和四文档路径;读取成功后依次用 `mark --step created / onboarded / registered --evidence ...` 登记同一新 thread ID。
- 旧会话归档必须最后执行。归档成功后运行 `finish-switch --department ... --new-thread-id ... --evidence "归档收据"` 清理旧 ID。新会话尚未创建时，失败可直接执行 `restore-old --department ... --note "真实错误"`。一旦已登记新 thread ID，不得直接覆盖；若确定放弃新会话并恢复旧会话，先归档新会话，再用 `restore-old ... --evidence "host=<真实工具> thread_id=<新ID> archived=true"` 登记收据；无归档能力时保留新旧两个 ID 并提醒用户，不得让任一会话从真值中消失。新会话已 registered 而只是旧会话归档失败时，保持当前换班状态并重试 `finish-switch`。

## 停用部门前退役会话

已登记部门要先真实归档当前会话，再运行 `agent_team_session.py retire --department ... --actor "统筹部/已登记会话ID" --evidence "host=<工具> thread_id=<当前ID> archived=true"`。只有取得精确归档回执并恢复为 pending 后，脚手架才允许停用；统筹部不能 retire。

## 部门会话清单

| 顺序 | 部门 | 角色 ID | 上岗引导 | 会话 ID | 通知模式 |
|------|------|---------|----------|---------|----------|
{chr(10).join(rows)}

## 手动上岗提醒模板

```text
请打开本项目的 docs/collaboration/部门/【部门名】/上岗引导.md,按里面的顺序接班。先短报职责、当前任务和待确认问题;已有授权清楚的 claimed 任务且无冲突时同一轮续做。
```

## 手动换班回退模板

```text
请在同一项目中手动新建一个全新会话,标题建议沿用“【序号】 【部门名】”,不要 fork 旧聊天。

你是【部门名】的新接班会话。
项目根目录:【绝对路径】
旧会话 ID:【旧 ID;没有则写待登记】
新会话 ID:【取得后填写】

请按以下顺序直接读取,不要运行接班总结脚本:
1. docs/collaboration/部门/【部门名】/上岗引导.md
2. 运行 agent_team_task.py onboard-bundle --department "【部门名】"；失败时停止并请统筹 rebuild-index
3. 校验通过后直接使用命令原样输出的岗位说明、交接班、收件箱和当前 TASK，不再重复读取

读取成功后先短报职责、当前任务和待确认问题;已有授权清楚的 claimed 任务且无冲突时同一轮续做。
确认职责一致后,用会话工具依次登记 created / onboarded / registered;工具会刷新部门表索引。登记成功后才归档旧会话;旧 ID 无效、无法登记或无法归档时,明确报告切换未完成并保留旧会话。
```
"""


def readme_markdown(date: str) -> str:
    return f"""# 多会话协作层

> 创建日期:{date}
> 本目录保存跨会话协作的持久真值。详细命令见 `任务交接模板.md` 和 `会话启动清单.md`。

## 权威来源

- `tasks/TASK-*.json`:任务正文、所有者、执行状态、业务阶段与授权声明的唯一真值。文件路径不随状态变化。
- `会话启动状态.json`:部门会话状态真值；`部门表.md` 是由工具刷新的可读索引。
- 部门 `交接班文档.md`:只记录做到哪里、下一步、临时证据和已知坑，不保存任务状态真值。
- 部门 `收件箱.md`:由任务工具重建的活动索引，禁止手工派单、领取或完成。
- 部门 `日志/`:五类轨迹事件的冷历史，首次写入时才创建周文件。

## 结构

```text
docs/collaboration/
├── 协议版本.json
├── README.md
├── 部门表.md
├── 路由表.md
├── 会话启动清单.md
├── 会话启动状态.json
├── 任务交接模板.md
├── 错题集.md
├── tasks/TASK-*.json
├── .locks/
│   ├── project-control.lock
│   ├── dispatch-control.json
│   ├── slice-control.json
│   ├── legacy-closeout-index.json
│   └── hot-state.json
├── 模板/
│   ├── 工作报告.md
│   ├── 审核报告.md
│   └── 专项结论.md
├── scripts/
│   ├── agent_team_task.py
│   ├── agent_team_session.py
│   ├── agent_team_log.py
│   └── agent_team_temporary.py (1.4 legacy 维护，1.5 新切片禁用)
└── 部门/<部门>/
    ├── 上岗引导.md
    ├── 岗位说明.md
    ├── 交接班文档.md
    ├── 收件箱.md
    ├── 报告/
    └── 日志/
```

## 运行原则

1. 团队至少包含管理、执行、审核三层；先用最小团队，有明确职责差异再加部门。
2. 新会话读取上岗引导后运行一次 `onboard-bundle`；它校验 freshness 并原样输出岗位说明、交接班、收件箱及当前 TASK。`list` 默认只显示当前切片，`--include-cold` 才审计全历史。
3. Agent 按任务、权威性、遗漏风险和当前能力自主决定读取哪些项目正文；不限制篇数，也不规定全文或局部读取。只看索引或元数据时，不得声称覆盖正文。
4. 所有任务状态变化通过 `agent_team_task.py`；`TASK_STATE_OK` 只证明状态持久化、本地路径校验和外部产物声明，不证明业务质量。
5. 普通 TASK 动作必须匹配当前登记会话；该绑定用于防漂移和审计，不构成操作系统认证。体验、范围、发布、成本或安全风险仍以用户决定为准。
6. 后台及普通低影响测试照常自动执行，不增加用户汇报。测试可能明显妨碍用户正常使用设备时，执行前说明影响、预计时长、期间能否继续工作和退出方式；前台独占或难以自行退出时还须取得当次明确确认，阶段性 Kickoff / 开发授权不能替代。
7. 正常模式一次只推进一个切片、一个 owner、最多两个 gate 和一个当前候选；返工增加候选代次，不新建 replacement owner TASK，不自动开启下一切片。
8. 用户要求冻结、开放任务堆积、上下文或存储压力、同一 gate 跨两代连续 FAIL，或节点仍无真实用户出口时，立即用任务工具 `freeze-new-work`。冻结后拒绝新派单、影响声明、领取、恢复和临时外包推进，只保留已领取任务完成或安全停下、清账、核收、交接、换班和证据；只有用户明确同意并留下证据才 `unfreeze-new-work`。
9. 审核部门必须亲自取得独立证据，覆盖任务指定的失败路径，并写清未覆盖项；证据结构由领域决定。
10. 设计意图预览仅在用户明确提出或任务列为交付物时制作。触发后必须让用户直接看到，并说明与最终实现的保真差距。
11. 会话变重时只说明具体症状、风险和当前任务，询问用户是否换班；用户未授权时不自动创建、登记或归档。
12. 已有授权清楚的 `claimed` 任务且无冲突时，新会话短报接班状态后同一轮续做。
13. 换班中一旦已登记新 thread ID，`restore-old` 必须带精确绑定新 ID 的归档回执；否则保留新旧两个 ID。执行中的 SWITCH 未收口前不得再次 `begin-switch`。
14. 协议 1.5 暂不允许临时外包另开第二 owner TASK；工具返回 `TEMPORARY_EXECUTOR_P2_REQUIRED`，不得绕过。
15. `LOG_OK` 只用于 `MILESTONE / CHANGE / CORRECTION / DECISION / INCIDENT`，普通任务不凑日志。

## 报告

普通任务默认用 TASK 完成记录与真实产物闭环。复杂研究、方案、阶段总结或用户决策材料才写工作报告；审核任务写审核报告。共享格式只在 `模板/` 保留一份，各部门统一写入自己的 `报告/`。

部门完成四件套只供统筹核收。用户互动不得依赖穷举场景；统筹先理解用户意图和它对当前 TASK 的影响，再判断是否需要用户独有信息、亲自操作或主观判断、方向或风险授权。需要时保持用户出口 `pending`，说明所需动作并停下；无上述依赖的纯代码或内部检查，在当前已确认切片通过自检和所需 gate 后记录 `not_applicable` 并继续内部步骤，不强制汇报，也不得自动开启下一切片。临时提问、状态追问直接回答并保留当前 TASK；清楚的同范围反馈沿用原 owner，只有含义不清或实质改变范围、优先级、方向、成本、安全、隐私、发布时再请求必要决定。

正式体验、选择、风险和重要收口使用“结果 / 需要你做什么 / 还需注意”的稳定信息骨架，篇幅按情况伸缩，无真实注意项时省略第三段。体验时给出入口、操作顺序、预期结果、重点判断和已知限制。普通问答不套模板，不为格式制造空话；内部任务号、状态词、哈希、命令、日志和协议默认不展开。
"""



def append_agent_guide(target: Path) -> None:
    guide = target / "docs" / "agent-guide.md"
    text = read_utf8(guide) if guide.exists() else "# Agent 协作入口\n"
    start = "<!-- agent-team-guide:start -->"
    end = "<!-- agent-team-guide:end -->"
    block = f"""{start}
## 多会话协作(三层框架)

> 受管协议版本:{PROTOCOL_VERSION}。本节只提供入口,不要在这里复制协议正文。

- 协作总则:`docs/collaboration/README.md`
- 任务流转:`docs/collaboration/任务交接模板.md`;任务真值:`docs/collaboration/tasks/TASK-*.json`
- 会话创建与换班:`docs/collaboration/会话启动清单.md`;会话真值:`docs/collaboration/会话启动状态.json`
- 部门首次接班:读取本部门 `上岗引导.md`
{end}"""
    if start in text or end in text:
        if text.count(start) != 1 or text.count(end) != 1 or text.index(start) > text.index(end):
            raise ValueError("agent-guide 的 agent-team 受管区块标记损坏")
        updated = text[:text.index(start)] + block + text[text.index(end) + len(end):]
    elif "## 多会话协作(三层框架)" in text:
        legacy_heading = "## 多会话协作(旧版参考;以前置受管区块为准)"
        updated = text.replace("## 多会话协作(三层框架)", block + "\n\n" + legacy_heading, 1)
    else:
        updated = text.rstrip() + "\n\n" + block + "\n"
    write_utf8_atomic(guide, updated.rstrip() + "\n")



# ---- 最小业务地基 ------------------------------------------------------------

MAX_FOUNDATION_BYTES = 256 * 1024
MAX_FOUNDATION_FIELD_CHARS = 2000


def validate_foundation_file(target: Path, raw_path: str) -> Path:
    """Mechanically validate the foundation file explicitly reviewed by the Agent."""
    value = raw_path.strip()
    if not value:
        raise ValueError("未指定已由 Agent 复核的地基文件; 请传 --foundation-file")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("foundation-file 必须是项目内的相对路径")
    if relative.suffix.lower() != ".md" or "collaboration" in relative.parts:
        raise ValueError("foundation-file 必须是 docs/collaboration 之外的 Markdown 文件")
    candidate = target / relative
    try:
        target_resolved = target.resolve(strict=True)
        current = target
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(target_resolved)
        if not resolved.is_file():
            raise ValueError
        with resolved.open("rb") as handle:
            data = handle.read(MAX_FOUNDATION_BYTES + 1)
        if len(data) > MAX_FOUNDATION_BYTES:
            raise ValueError
        if not data.decode("utf-8-sig").strip():
            raise ValueError
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("foundation-file 不存在、越界、经过符号链接、非 UTF-8、为空或超过 256 KiB") from exc
    return resolved


def ensure_core_docs(target: Path, date: str) -> list[Path]:
    created: list[Path] = []
    docs = target / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    progress = docs / "progress.md"
    if not progress.exists():
        write_utf8_atomic(progress, f"""# 项目进度

> 创建日期:{date}

## 当前阶段

- 已有项目地基,正在搭建多会话协作层。

## 已完成

- 已识别现有项目目标、交付范围和验收地基。

## 进行中

- 搭建多会话协作层。

## 下一步

- 由统筹部根据团队配置派发第一个验收节点。
""")
        created.append(progress)
    guide = docs / "agent-guide.md"
    if not guide.exists():
        write_utf8_atomic(guide, f"""# Agent 协作指南

> 创建日期:{date}

## 文件分工

- `docs/spec.md` 或 `docs/overview.md`:项目目标、交付物、边界、验收标准。
- `docs/progress.md`:项目级进度摘要,启用协作层后由统筹部维护。
- `docs/collaboration/`:多会话部门协作层。
""")
        created.append(guide)
    return created


def create_minimal_foundation(target: Path, profile: str, date: str, *, goal: str, deliverable: str,
                              audience: str, acceptance: str, resources: str, risks: str) -> list[Path]:
    """Create a small, domain-neutral foundation when no dedicated business foundation exists."""
    created: list[Path] = []
    docs = target / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    overview = docs / "overview.md"
    if not overview.exists():
        write_utf8_atomic(overview, f"""# 项目概览

> 创建日期:{date}
> 地基类型:通用最小业务地基
> 团队画像:{profile}

## 目标业务

{goal}

## 最终交付物

{deliverable}

## 服务对象 / 使用对象

{audience}

## 边界

- 做:围绕上述最终交付物和验收标准推进。
- 不做:未经用户确认不扩大交付范围或改变验收口径。

## 验收标准

{acceptance}

## 过程资源

{resources}

## 风险与复核方式

{risks}
""")
        created.append(overview)
    progress = docs / "progress.md"
    if not progress.exists():
        write_utf8_atomic(progress, f"""# 项目进度

> 创建日期:{date}

## 当前阶段

- 目标、交付物、服务对象、验收、资源与风险已记录,正在搭建部门协作层。

## 已完成

- 创建通用最小业务地基。

## 进行中

- 搭建多会话协作层。

## 下一步

- 由统筹部根据团队配置派发第一个验收节点。
""")
        created.append(progress)
    guide = docs / "agent-guide.md"
    if not guide.exists():
        write_utf8_atomic(guide, f"""# Agent 协作指南

> 创建日期:{date}

## 地基说明

本项目使用通用最小业务地基。若后续发现更适合的行业/业务专用地基,先向用户确认迁移范围,再调整目录与部门权限。

## 文件分工

- `docs/overview.md`:目标、交付物、对象、边界、验收标准。
- `docs/progress.md`:项目级进度摘要,启用协作层后由统筹部维护。
- `docs/collaboration/`:多会话部门协作层。
""")
        created.append(guide)
    return created


# ---- 建部门 ----------------------------------------------------------------

def create_department(depts_root: Path, key: str, role: dict[str, str], date: str) -> None:
    d = depts_root / role["name"]
    d.mkdir(parents=True)
    write_utf8_atomic(d / "岗位说明.md", role_markdown(key, role, date))
    write_utf8_atomic(d / "上岗引导.md", bootstrap_markdown(key, role))
    write_utf8_atomic(d / "交接班文档.md", state_markdown(key, role, date))
    write_utf8_atomic(d / "收件箱.md", inbox_markdown(key, role, date))
    reports_dir = d / "报告"
    reports_dir.mkdir(parents=True)
    log_dir = d / "日志"
    log_dir.mkdir(parents=True)


# ---- 命令行 ----------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="为项目创建/扩展多会话协作层(三层框架,按部门组织)。")
    parser.add_argument("target", help="项目根目录")
    parser.add_argument("--profile", default="待确认", help="团队诊断摘要")
    parser.add_argument(
        "--session-mode",
        choices=["auto", "manual"],
        default=None,
        help="部门会话创建模式:auto=工具自动创建, manual=用户手动创建窗口",
    )
    parser.add_argument(
        "--roles",
        default="lead,dev,test",
        help="逗号分隔的角色 ID(软件默认最小盘:lead,dev,test;其他场景显式传 lead,do,review)",
    )
    parser.add_argument(
        "--add-roles",
        default="",
        help="增量模式:在已有 docs/collaboration/ 上追加这些角色 ID(只建缺失部门并更新部门表)",
    )
    parser.add_argument(
        "--deactivate-roles", default="",
        help="停用已无在办任务和会话的部门；保留历史目录、TASK 与角色身份",
    )
    parser.add_argument(
        "--deactivation-evidence", default="",
        help="停用部门所依据的用户确认记录或会话指针",
    )
    parser.add_argument(
        "--upgrade-collaboration",
        action="store_true",
        help="将已有协作层升级到当前协议;保留交接、日志和报告,有旧待办时安全停止",
    )
    parser.add_argument(
        "--rollback-collaboration",
        default="",
        metavar="ROLLBACK_MANIFEST",
        help="从升级备份显式回滚；仅限冻结、无活动切片且尚未创建 1.5 TASK",
    )
    parser.add_argument(
        "--role-policy-overlay-file",
        default="",
        help="升级时显式迁移/更新岗位职责只追加层的 JSON 文件；必须含用户确认依据",
    )
    parser.add_argument("--foundation-file", default="", help="已由 Agent 阅读复核的项目内 Markdown 地基文件")
    parser.add_argument("--allow-without-foundation", action="store_true", help="允许使用 docs/spec.md 之外的已复核地基,或创建通用最小地基")
    parser.add_argument(
        "--create-minimal-foundation",
        action="store_true",
        help="在没有专用业务地基时创建通用最小业务地基(docs/overview.md, docs/progress.md, docs/agent-guide.md)",
    )
    parser.add_argument("--foundation-goal", default="", help="最小地基:目标业务与用户需求")
    parser.add_argument("--foundation-deliverable", default="", help="最小地基:最终交付物与范围")
    parser.add_argument("--foundation-audience", default="", help="最小地基:服务对象/验收对象")
    parser.add_argument("--foundation-acceptance", default="", help="最小地基:可验证的验收标准")
    parser.add_argument("--foundation-resources", default="", help="最小地基:可用材料、系统、人员和过程资源")
    parser.add_argument("--foundation-risks", default="", help="最小地基:风险与复核方式")
    return parser.parse_args()


def validate_minimal_foundation_args(args: argparse.Namespace) -> tuple[bool, str]:
    required = {
        "--foundation-goal": args.foundation_goal,
        "--foundation-deliverable": args.foundation_deliverable,
        "--foundation-audience": args.foundation_audience,
        "--foundation-acceptance": args.foundation_acceptance,
        "--foundation-resources": args.foundation_resources,
        "--foundation-risks": args.foundation_risks,
    }
    invalid = []
    for name, value in required.items():
        stripped = value.strip()
        if (
            not stripped
            or len(stripped) > MAX_FOUNDATION_FIELD_CHARS
            or any(ord(char) < 32 and char not in "\t\n\r" for char in stripped)
        ):
            invalid.append(name)
    if invalid:
        return False, "创建最小地基前六项参数必须非空、不含控制字符且每项不超过 2000 字: " + ", ".join(invalid)
    return True, ""


def validate_roles(roles: list[str], *, require_layers: bool = False) -> int | None:
    duplicates = sorted({role for role in roles if roles.count(role) > 1})
    if duplicates:
        print(f"重复角色: {', '.join(duplicates)}。请每个角色只传一次。", file=sys.stderr)
        return 4
    deprecated = [role for role in roles if role in DEPRECATED_ROLE_IDS]
    if deprecated:
        labels = ", ".join(f"{role}({DEPRECATED_ROLE_IDS[role]})" for role in deprecated)
        print(
            f"已取消独立角色: {labels}。AI 产品与系统级技术规划归产品部,代码与集成实现归开发部。",
            file=sys.stderr,
        )
        return 4
    unknown = [role for role in roles if role not in ROLE_DEFS]
    if unknown:
        print(f"未知角色: {', '.join(unknown)}", file=sys.stderr)
        print(f"可选角色: {', '.join(ROLE_DEFS)}", file=sys.stderr)
        return 4
    if require_layers:
        present = {ROLE_DEFS[role]["layer"] for role in roles}
        missing = [LAYER_CN[layer] for layer in ("management", "execution", "audit") if layer not in present]
        if missing:
            print(f"缺少三层框架: {', '.join(missing)}。全新团队必须至少包含管理层、执行层、审核层各一个角色。", file=sys.stderr)
            return 4
    return None


def registered_role_ids(registry_text: str) -> list[str]:
    roles: list[str] = []
    for line in registry_text.splitlines():
        if not line.startswith("|") or "---" in line or "角色 ID" in line:
            continue
        parts = [part.strip().strip("`") for part in line.strip().strip("|").split("|")]
        if len(parts) >= 3 and (parts[2] in ROLE_DEFS or parts[2] in DEPRECATED_ROLE_IDS):
            roles.append(parts[2])
    return roles


def validate_existing_collaboration(collab: Path, *, require_current: bool = True) -> tuple[str, list[str]] | None:
    if not collab.is_dir() or collab.is_symlink():
        print("docs/collaboration/ 不是可用的普通目录,无法增量。", file=sys.stderr)
        return None
    registry = collab / "部门表.md"
    startup = collab / "会话启动清单.md"
    depts_root = collab / "部门"
    if not plain_path_within(registry, collab, kind="file") or not plain_path_within(startup, collab, kind="file"):
        print("现有协作层的部门表/启动清单缺失、越界或经过符号链接,已拒绝修改。", file=sys.stderr)
        return None
    if not plain_path_within(depts_root, collab, kind="dir"):
        print("现有 docs/collaboration/部门 缺失、越界或为符号链接,已拒绝修改。", file=sys.stderr)
        return None
    try:
        text = read_utf8(registry)
    except (OSError, UnicodeError) as exc:
        print(f"无法以 UTF-8 读取部门表: {exc}", file=sys.stderr)
        return None
    existing = registered_role_ids(text)
    deprecated = [role for role in existing if role in DEPRECATED_ROLE_IDS]
    if deprecated:
        labels = ", ".join(f"{role}({DEPRECATED_ROLE_IDS[role]})" for role in deprecated)
        print(
            f"现有协作层仍包含已取消的独立角色: {labels}。"
            "为避免丢失任务、交接、日志和报告,脚本不会自动删除或合并;"
            "请先获得用户确认,把在办事项和历史指针交给开发部后再调整部门表。",
            file=sys.stderr,
        )
        return None
    if not existing or validate_roles(existing, require_layers=True):
        print("现有部门表无法证明管理层/执行层/审核层齐全,已拒绝增量修改。", file=sys.stderr)
        return None
    if REGISTRY_RULES_MARKER not in text:
        print("部门表缺少标准使用规则标记,已拒绝增量修改。", file=sys.stderr)
        return None
    if require_current:
        version = read_protocol_version(collab)
        if version != PROTOCOL_VERSION:
            print(
                f"协作层协议版本为 {version or '未登记'},当前需要 {PROTOCOL_VERSION}。"
                "请先在用户确认后运行 --upgrade-collaboration,不允许混用新旧部门模板。",
                file=sys.stderr,
            )
            return None
        if not current_runtime_complete(collab):
            print(
                "协作层协议号虽然匹配,但运行文件或安全目录不完整。"
                "请先运行 --upgrade-collaboration 修复,不能把缺件状态当成当前协议。",
                file=sys.stderr,
            )
            return None
    return text, existing


def registry_value(text: str, label: str, default: str) -> str:
    prefix = f"- {label}:"
    for line in text.splitlines():
        if line.startswith(prefix):
            value = line[len(prefix):].strip()
            return value or default
    return default


def legacy_inbox_has_tasks(text: str) -> bool:
    if "<!-- agent-team task index; use scripts/agent_team_task.py -->" in text:
        return False
    body = text.split("## 待办", 1)[-1]
    ignored = {"_(没有待办)_", "_(没有待领取任务)_"}
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith(">") or line in ignored:
            continue
        if line.startswith("## "):
            continue
        return True
    return False


def recover_add_roles_transaction(collab: Path, *, announce: bool = True) -> bool:
    """Rollback an add-role transaction that ended before its commit marker was removed."""
    marker = collab / ADD_TRANSACTION_FILE
    if not marker.exists():
        return False
    if marker.is_symlink() or not marker.is_file():
        raise ValueError(f"{ADD_TRANSACTION_FILE} 不是安全的普通文件")
    payload = strict_json_loads(read_utf8(marker), source=str(marker))
    expected_fields = {
        "schema_version", "kind", "operation_id", "phase", "created_roles",
        "originals", "generated_files",
    }
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise ValueError("新增部门事务标记结构无效")
    if payload.get("schema_version") != 2 or payload.get("kind") != "add_roles":
        raise ValueError("新增部门事务标记版本无效")
    operation_id = payload.get("operation_id")
    if not isinstance(operation_id, str) or not re.fullmatch(r"ADD-[0-9]{8}T[0-9]{6}-[A-F0-9]{8}", operation_id):
        raise ValueError("新增部门事务 operation_id 无效")
    phase = payload.get("phase")
    if phase not in {"applying", "committed"}:
        raise ValueError("新增部门事务 phase 无效")
    originals = payload.get("originals")
    created_roles = payload.get("created_roles")
    allowed_files = {
        PROTOCOL_FILE, "部门表.md", "会话启动清单.md", "路由表.md", "会话启动状态.json",
    }
    if not isinstance(originals, dict) or set(originals) != allowed_files:
        raise ValueError("新增部门事务备份不完整")
    if not isinstance(created_roles, list) or any(role not in ROLE_DEFS for role in created_roles):
        raise ValueError("新增部门事务角色无效")
    if len(created_roles) != len(set(created_roles)):
        raise ValueError("新增部门事务角色重复")
    generated_files = payload.get("generated_files")
    if not isinstance(generated_files, dict) or set(generated_files) != set(created_roles):
        raise ValueError("新增部门事务生成清单无效")
    depts_root = collab / "部门"
    if not plain_path_within(depts_root, collab, kind="dir"):
        raise ValueError("恢复时发现部门目录不安全")

    # 必须先完成所有所有权校验，再写回任何真值。伪造标记绝不能删现有部门。
    owned_departments: list[Path] = []
    for role_id in created_roles:
        department = depts_root / ROLE_DEFS[role_id]["name"]
        if not department.exists():
            continue
        if department.is_symlink() or not plain_path_within(department, collab, kind="dir"):
            raise ValueError(f"恢复时发现部门路径不安全: {department}")
        sentinel = department / ADD_ROLE_SENTINEL
        if phase == "committed":
            if sentinel.exists():
                if sentinel.is_symlink() or not plain_path_within(sentinel, collab, kind="file"):
                    raise ValueError(f"新增部门所有权标记不安全: {department}")
                sentinel_payload = strict_json_loads(read_utf8(sentinel), source=str(sentinel))
                if sentinel_payload != {
                    "schema_version": 1, "kind": "agent-team-add-role-owned",
                    "operation_id": operation_id, "role_id": role_id,
                }:
                    raise ValueError(f"新增部门所有权标记不匹配: {department}")
                owned_departments.append(department)
            continue
        expected = generated_files[role_id]
        if (
            not isinstance(expected, dict)
            or any(not isinstance(name, str) or not isinstance(digest, str) for name, digest in expected.items())
            or any("/" in name or name in {"", ".", ".."} for name in expected)
            or any(not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in expected.values())
        ):
            raise ValueError("新增部门事务生成文件清单无效")
        actual_files: dict[str, str] = {}
        actual_dirs: set[str] = set()
        for entry in department.iterdir():
            if entry.is_symlink():
                raise ValueError(f"事务所属部门含符号链接: {department}")
            if entry.is_file():
                actual_files[entry.name] = hashlib.sha256(entry.read_bytes()).hexdigest()
            elif entry.is_dir():
                actual_dirs.add(entry.name)
                if any(entry.iterdir()):
                    raise ValueError(f"事务所属部门已产生用户资料: {entry}")
            else:
                raise ValueError(f"事务所属部门含未知路径: {entry}")
        if actual_files != expected or actual_dirs != {"报告", "日志"}:
            raise ValueError(f"事务所属部门已漂移，拒绝删除: {department}")
        sentinel_payload = strict_json_loads(
            read_utf8(sentinel), source=str(sentinel),
        )
        if sentinel_payload != {
            "schema_version": 1, "kind": "agent-team-add-role-owned",
            "operation_id": operation_id, "role_id": role_id,
        }:
            raise ValueError(f"新增部门所有权标记不匹配: {department}")
        owned_departments.append(department)

    if phase == "committed":
        for department in owned_departments:
            (department / ADD_ROLE_SENTINEL).unlink(missing_ok=True)
        marker.unlink()
        fsync_dir(collab)
        if announce:
            print(f"RECOVERY_OK | 已收口完成的新增部门事务: {operation_id}")
        return True

    for name, content in originals.items():
        if not isinstance(content, str):
            raise ValueError("新增部门事务备份内容无效")
        write_utf8_atomic(
            collab / name,
            content,
            mode=0o600 if name in {"会话启动状态.json", PROTOCOL_FILE} else None,
        )
    for department in owned_departments:
        shutil.rmtree(department)
    marker.unlink()
    fsync_dir(collab)
    if announce:
        print(f"RECOVERY_OK | 已回滚未提交的新增部门事务: {payload.get('operation_id', '-')}")
    return True


def recover_deactivate_roles_transaction(collab: Path, *, announce: bool = True) -> bool:
    marker = collab / DEACTIVATE_TRANSACTION_FILE
    if not marker.exists():
        return False
    if marker.is_symlink() or not marker.is_file():
        raise ValueError("停用部门事务标记不安全")
    payload = strict_json_loads(read_utf8(marker), source=str(marker))
    expected = {"schema_version", "kind", "operation_id", "phase", "roles", "originals"}
    allowed_files = {PROTOCOL_FILE, "部门表.md", "会话启动清单.md", "路由表.md", "会话启动状态.json"}
    if (
        not isinstance(payload, dict)
        or set(payload) != expected
        or payload.get("schema_version") != 1
        or payload.get("kind") != "deactivate_roles"
        or payload.get("phase") not in {"applying", "committed"}
        or not isinstance(payload.get("roles"), list)
        or any(role not in ROLE_DEFS for role in payload["roles"])
        or not isinstance(payload.get("originals"), dict)
        or set(payload["originals"]) != allowed_files
    ):
        raise ValueError("停用部门事务标记结构无效")
    if payload["phase"] == "applying":
        for name, content in payload["originals"].items():
            if not isinstance(content, str):
                raise ValueError("停用部门事务备份内容无效")
            write_utf8_atomic(
                collab / name, content,
                mode=0o600 if name in {"会话启动状态.json", PROTOCOL_FILE} else None,
            )
    marker.unlink()
    fsync_dir(collab)
    if announce:
        action = "回滚未提交" if payload["phase"] == "applying" else "收口已提交"
        print(f"RECOVERY_OK | 已{action}的停用部门事务: {payload['operation_id']}")
    return True


UPGRADE_TASK_STATES = {"queued", "claimed", "blocked", "waiting_input", "completed", "acknowledged"}
UPGRADE_TASK_AUTH_STATES = {"none", "user_required", "user_confirmed", "user_rejected"}
UPGRADE_TASK_REQUIRED_FIELDS = {
    "schema_version", "task_id", "department", "from_department", "title", "node", "details",
    "acceptance_exit", "failure_paths", "confirmation", "domain_stage", "authorization_state",
    "authorization_evidence", "authorization_history", "execution_state", "completion_class", "pointers",
    "created_at", "updated_at", "revision", "claimed_by", "block_reason", "artifacts", "external_artifacts",
    "verified", "unverified", "mistake_check", "report", "event_receipts",
}
UPGRADE_TASK_OPTIONAL_FIELDS = {
    "acknowledged_by", "impact_declaration", "temporary_executor", "temporary_operation_history", "resolution",
    "ownership_history", "state_action_receipt", "completion_history",
    "slice_id", "task_kind", "gate_type", "gate_attempts",
}
UPGRADE_TASK_ID_RE = re.compile(r"^TASK-[0-9]{8}-[A-Z0-9]{6}$")


def validate_upgrade_task_text(value: object, field: str, *, allow_empty: bool = False, max_chars: int = 2000) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValueError(f"任务字段缺失或类型无效: {field}")
    if (value and value != value.strip()) or len(value) > max_chars or any(ord(char) < 32 and char != "\t" for char in value):
        raise ValueError(f"任务文本字段超长或含非法控制字符: {field}")
    return value


def validate_upgrade_task_list(
    value: object,
    field: str,
    *,
    min_items: int = 0,
    max_items: int | None = None,
    max_chars: int = 2000,
) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() or item != item.strip() or len(item) > max_chars
        or any(ord(char) < 32 and char != "\t" for char in item)
        for item in value
    ):
        raise ValueError(f"任务列表字段无效: {field}")
    if len(value) < min_items or (max_items is not None and len(value) > max_items):
        raise ValueError(f"任务列表字段数量无效: {field}")
    return value


def validate_upgrade_task_timestamp(value: object, field: str) -> dt.datetime:
    text = validate_upgrade_task_text(value, field)
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"任务时间戳无效: {field}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"任务时间戳缺失时区: {field}")
    return parsed


def validate_upgrade_impact(value: object, source: Path) -> None:
    required = {"write_paths", "shared_contracts", "external_effects", "base_revision", "owner_task", "admission"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError(f"任务影响声明结构无效: {source}")
    for field in ("write_paths", "shared_contracts", "external_effects"):
        validate_upgrade_task_list(value[field], f"impact.{field}", min_items=1 if field != "shared_contracts" else 0, max_chars=500)
    for raw in value["write_paths"]:
        normalized = raw.replace("\\", "/")
        path = Path(normalized)
        forbidden = (".git", ".agent-team", "docs/collaboration")
        if (
            path.is_absolute() or normalized in {".", ".."} or ".." in path.parts
            or path.as_posix().rstrip("/") != raw
            or any(raw == prefix or raw.startswith(prefix + "/") for prefix in forbidden)
        ):
            raise ValueError(f"任务影响声明 write_paths 无效: {source}")
    if "none" in value["external_effects"] and value["external_effects"] != ["none"]:
        raise ValueError(f"任务影响声明 external_effects 无效: {source}")
    validate_upgrade_task_text(value["base_revision"], "impact.base_revision", max_chars=300)
    validate_upgrade_task_text(value["owner_task"], "impact.owner_task", max_chars=100)
    if value["admission"] not in {"safe", "manual", "unsafe", "waiting_base"}:
        raise ValueError(f"任务影响声明 admission 无效: {source}")


def validate_upgrade_operation_history(history: object, current_state: str, source: Path, label: str) -> None:
    allowed_states = {"planned", "started", "succeeded", "verified", "failed"}
    if not isinstance(history, list) or not history:
        raise ValueError(f"temporary_executor {label} history 无效: {source}")
    for event in history:
        if not isinstance(event, dict) or set(event) - {"state", "at", "reason", "via"} or not {"state", "at"}.issubset(event):
            raise ValueError(f"temporary_executor {label} history 事件结构无效: {source}")
        if event["state"] not in allowed_states or not isinstance(event["at"], str) or not event["at"]:
            raise ValueError(f"temporary_executor {label} history 事件内容无效: {source}")
        for key in ("reason", "via"):
            if key in event and (not isinstance(event[key], str) or not event[key]):
                raise ValueError(f"temporary_executor {label} history {key} 无效: {source}")
    if history[-1]["state"] != current_state:
        raise ValueError(f"temporary_executor {label} 当前状态与 history 末项不一致: {source}")


def validate_upgrade_temporary(value: object, source: Path, department: str) -> None:
    required = {
        "schema_version", "executor_type", "executor_id", "display_name", "parent_department",
        "current_brief", "brief_revision", "impact", "workspace", "rule", "user_acceptance",
        "promotion_state", "temporary_session", "attempt", "candidate_revision", "candidate",
        "review", "delivery", "integration", "absorption", "operation",
    }
    optional = {"promotion_operation", "cleanup_operation"}
    if not isinstance(value, dict) or required - set(value) or set(value) - required - optional:
        raise ValueError(f"temporary_executor 结构无效: {source}")
    if value["schema_version"] != 1 or value["executor_type"] != "temporary":
        raise ValueError(f"temporary_executor 版本或类型无效: {source}")
    if value["parent_department"] != department:
        raise ValueError(f"temporary_executor 父部门与 TASK 不一致: {source}")
    for field, limit in (("executor_id", 200), ("display_name", 100), ("current_brief", 2000)):
        validate_upgrade_task_text(value[field], f"temporary_executor.{field}", max_chars=limit)
    for field in ("brief_revision", "attempt", "candidate_revision"):
        item = value[field]
        if isinstance(item, bool) or not isinstance(item, int) or item < (0 if field == "candidate_revision" else 1):
            raise ValueError(f"temporary_executor {field} 无效: {source}")
    validate_upgrade_impact(value["impact"], source)
    if value["promotion_state"] not in {
        "not_submitted", "submitted", "reviewing", "waiting_base", "ready", "integrated",
        "archived", "cancelled", "abandoned",
    }:
        raise ValueError(f"temporary_executor 晋升状态无效: {source}")
    session = value["temporary_session"]
    if not isinstance(session, dict) or set(session) != {"state", "thread_id", "evidence"} or any(
        not isinstance(session[key], str) for key in session
    ):
        raise ValueError(f"temporary_executor 会话结构无效: {source}")
    if session["state"] not in {
        "provisioning", "awaiting_rule_confirmation", "active", "standby", "archived", "failed", "cancelled",
    }:
        raise ValueError(f"temporary_executor 会话状态无效: {source}")
    if session["thread_id"] and (
        len(session["thread_id"]) > 300
        or session["thread_id"].startswith("=")
        or any(char.isspace() for char in session["thread_id"])
    ):
        raise ValueError(f"temporary_executor thread_id 不可表示为归档回执: {source}")
    workspace = value["workspace"]
    if not isinstance(workspace, dict) or not all(isinstance(workspace.get(key), str) for key in ("path", "branch", "base_revision", "state")):
        raise ValueError(f"temporary_executor workspace 无效: {source}")
    rule = value["rule"]
    if not isinstance(rule, dict) or not all(isinstance(rule.get(key), str) for key in ("version", "digest", "source_protocol", "confirmed_at")):
        raise ValueError(f"temporary_executor rule 无效: {source}")
    acceptance = value["user_acceptance"]
    if not isinstance(acceptance, dict) or acceptance.get("state") not in {
        "pending", "confirmed", "rejected", "delegated", "not_applicable",
    } or not isinstance(acceptance.get("candidate_revision"), int) or set(acceptance) - {
        "state", "evidence", "candidate_revision", "candidate_digest",
    } or not {"state", "evidence", "candidate_revision"}.issubset(acceptance):
        raise ValueError(f"temporary_executor 用户验收无效: {source}")
    if "candidate_digest" in acceptance and not isinstance(acceptance["candidate_digest"], str):
        raise ValueError(f"temporary_executor 用户验收 digest 无效: {source}")
    absorption = value["absorption"]
    if not isinstance(absorption, dict) or any(
        absorption.get(key) not in {"pending", "completed", "not_applicable"}
        for key in ("preflight", "parent_department", "project_global", "final")
    ) or not isinstance(absorption.get("receipts"), list):
        raise ValueError(f"temporary_executor 知识吸收状态无效: {source}")
    operation = value["operation"]
    if not isinstance(operation, dict) or not all(isinstance(operation.get(key), str) and operation.get(key) for key in ("id", "client_key", "state")):
        raise ValueError(f"temporary_executor operation 无效: {source}")
    if operation["state"] not in {"planned", "started", "succeeded", "verified", "failed"}:
        raise ValueError(f"temporary_executor operation state 无效: {source}")
    if not isinstance(operation.get("resources"), list) or any(not isinstance(item, str) or not item for item in operation["resources"]):
        raise ValueError(f"temporary_executor operation resources 无效: {source}")
    for field in ("request_digest", "scan_boundary_evidence", "cleanup_evidence"):
        if field in operation and (not isinstance(operation[field], str) or not operation[field]):
            raise ValueError(f"temporary_executor operation {field} 无效: {source}")
    if "history" in operation:
        validate_upgrade_operation_history(operation["history"], operation["state"], source, "operation")
    candidate = value["candidate"]
    if candidate is not None:
        required_candidate = {"revision", "kind", "locator", "digest", "brief_revision", "created_at"}
        if not isinstance(candidate, dict) or set(candidate) != required_candidate:
            raise ValueError(f"temporary_executor candidate 结构无效: {source}")
        if not isinstance(candidate["revision"], int) or candidate["revision"] != value["candidate_revision"]:
            raise ValueError(f"temporary_executor candidate revision 无效: {source}")
        if not isinstance(candidate["brief_revision"], int) or candidate["brief_revision"] < 1:
            raise ValueError(f"temporary_executor candidate brief_revision 无效: {source}")
        for field in ("kind", "locator", "digest", "created_at"):
            validate_upgrade_task_text(candidate[field], f"candidate.{field}", max_chars=500)
    review = value["review"]
    if review is not None:
        if not isinstance(review, dict) or set(review) != {"candidate_revision", "decision", "evidence", "reviewed_at"}:
            raise ValueError(f"temporary_executor review 结构无效: {source}")
        if not isinstance(review["candidate_revision"], int) or review["decision"] not in {"pass", "fail"}:
            raise ValueError(f"temporary_executor review 内容无效: {source}")
    delivery = value["delivery"]
    if delivery is not None:
        required_delivery = {"candidate_revision", "locator", "digest", "protected_ref", "evidence", "submitted_at"}
        if not isinstance(delivery, dict) or set(delivery) != required_delivery or not isinstance(delivery["candidate_revision"], int):
            raise ValueError(f"temporary_executor delivery 结构无效: {source}")
        for field in required_delivery - {"candidate_revision"}:
            validate_upgrade_task_text(delivery[field], f"delivery.{field}", max_chars=1000)
        if candidate is not None and (
            delivery["candidate_revision"] != candidate["revision"]
            or delivery["locator"] != candidate["locator"] or delivery["digest"] != candidate["digest"]
        ):
            raise ValueError(f"temporary_executor delivery 与 candidate 不一致: {source}")
    if review is not None and candidate is not None and review["candidate_revision"] != candidate["revision"]:
        raise ValueError(f"temporary_executor review 与 candidate revision 不一致: {source}")
    if candidate is not None and acceptance.get("candidate_digest") and acceptance["state"] in {"confirmed", "delegated", "not_applicable"}:
        if acceptance["candidate_revision"] != candidate["revision"] or acceptance["candidate_digest"] != candidate["digest"]:
            raise ValueError(f"temporary_executor 用户验收与 candidate 不一致: {source}")
    integration = value["integration"]
    if integration is not None:
        legacy_required = {
            "candidate_commit", "tested_base", "tested_commit", "tree_oid", "test_definition",
            "environment", "evidence", "unverified", "result", "tested_at",
        }
        current_extra = {"test_task_id", "report"}
        optional_integration = {"promoted_at", "main_branch"}
        if not isinstance(integration, dict) or legacy_required - set(integration) or set(integration) - legacy_required - current_extra - optional_integration:
            raise ValueError(f"temporary_executor integration 结构无效: {source}")
        if integration["result"] not in {"pass", "fail"}:
            raise ValueError(f"temporary_executor integration result 无效: {source}")
        for field in legacy_required - {"result"}:
            validate_upgrade_task_text(integration[field], f"integration.{field}", max_chars=2000)
        for field in current_extra & set(integration):
            validate_upgrade_task_text(integration[field], f"integration.{field}", max_chars=1000)
    for field, required_record in (
        ("promotion_operation", {"id", "state", "main_branch", "expected_old", "candidate_commit", "tree_oid", "history"}),
        ("cleanup_operation", {"id", "state", "workspace", "branch", "workspace_head", "evidence", "history"}),
    ):
        record = value.get(field)
        if record is not None:
            if not isinstance(record, dict) or set(record) != required_record or not isinstance(record["history"], list):
                raise ValueError(f"temporary_executor {field} 结构无效: {source}")
            for key in required_record - {"history"}:
                validate_upgrade_task_text(record[key], f"{field}.{key}", max_chars=1000)
            if record["state"] not in {"planned", "started", "succeeded", "verified", "failed"}:
                raise ValueError(f"temporary_executor {field} state 无效: {source}")
            validate_upgrade_operation_history(record["history"], record["state"], source, field)
    if session.get("state") == "archived":
        cleanup = value.get("cleanup_operation")
        if (
            not session.get("thread_id")
            or not session.get("evidence")
            or value["promotion_state"] != "archived"
            or workspace.get("state") != "removed"
            or not isinstance(cleanup, dict)
            or cleanup.get("state") != "verified"
        ):
            raise ValueError(f"temporary_executor archived 会话缺少真实收据或清理证据: {source}")
    cleanup = value.get("cleanup_operation")
    cleanup_state = cleanup.get("state") if isinstance(cleanup, dict) else None
    cleanup_terminal = (
        value["promotion_state"] == "archived"
        and workspace.get("state") == "removed"
        and cleanup_state == "verified"
    )
    if any((value["promotion_state"] == "archived", workspace.get("state") == "removed", cleanup_state == "verified")) and not cleanup_terminal:
        raise ValueError(f"temporary_executor 资源终态不一致: {source}")
    promotion_operation = value.get("promotion_operation")
    if (
        isinstance(promotion_operation, dict)
        and promotion_operation.get("state") == "verified"
        and value["promotion_state"] not in {"integrated", "archived"}
    ):
        raise ValueError(f"temporary_executor 已验证晋升事务与晋升状态不一致: {source}")
    if cleanup_terminal:
        if session.get("thread_id") and session.get("state") not in {"standby", "archived"}:
            raise ValueError(f"temporary_executor 清理后真实会话状态无效: {source}")
        if not session.get("thread_id") and session.get("state") != "cancelled":
            raise ValueError(f"temporary_executor 清理后缺少 thread_id 且未标记 cancelled: {source}")
    if session.get("state") == "cancelled" and (session.get("thread_id") or not cleanup_terminal):
        raise ValueError(f"temporary_executor cancelled 只允许无真实会话且资源已清理: {source}")


def validate_upgrade_resolution(
    value: object, source: Path, *, task_id: str, execution_state: str, revision: int,
    created_at: dt.datetime, updated_at: dt.datetime, has_temporary: bool,
) -> None:
    if value is None:
        return
    common_fields = {
        "state", "reason", "evidence", "resolved_by", "resolved_at", "target_revision_before", "receipt_id",
    }
    superseded_fields = common_fields | {"replacement_task_id", "replacement_revision", "replacement_state"}
    resolution_state = value.get("state") if isinstance(value, dict) else None
    expected_fields = superseded_fields if resolution_state == "superseded" else common_fields
    if (
        not isinstance(value, dict)
        or resolution_state not in {"superseded", "rejected_by_user", "abandoned"}
        or set(value) != expected_fields
    ):
        raise ValueError(f"任务收口轴结构无效: {source}")
    if execution_state not in {"queued", "blocked", "waiting_input"} or has_temporary:
        raise ValueError(f"只允许普通 open queued / blocked / waiting_input 任务收口: {source}")
    for field in ("reason", "evidence", "resolved_by", "receipt_id"):
        validate_upgrade_task_text(value.get(field), f"resolution.{field}", max_chars=1000)
    if (
        not value["resolved_by"].startswith("统筹部/")
        or any(char.isspace() for char in value["resolved_by"])
        or not re.fullmatch(r"RES-[0-9]{8}T[0-9]{6}-[A-F0-9]{8}", value["receipt_id"])
        or isinstance(value.get("target_revision_before"), bool)
        or not isinstance(value.get("target_revision_before"), int)
        or value["target_revision_before"] + 1 != revision
    ):
        raise ValueError(f"任务收口身份或版本无效: {source}")
    if resolution_state == "superseded":
        replacement = value.get("replacement_task_id")
        validate_upgrade_task_text(value.get("replacement_state"), "resolution.replacement_state", max_chars=1000)
        if (
            not isinstance(replacement, str)
            or not UPGRADE_TASK_ID_RE.fullmatch(replacement)
            or replacement == task_id
            or value["replacement_state"] == "queued"
            or value["replacement_state"] not in UPGRADE_TASK_STATES
            or isinstance(value.get("replacement_revision"), bool)
            or not isinstance(value.get("replacement_revision"), int)
            or value["replacement_revision"] < 1
        ):
            raise ValueError(f"任务替代版本或后续状态无效: {source}")
    resolved_at = validate_upgrade_task_timestamp(value["resolved_at"], "resolution.resolved_at")
    if resolved_at != updated_at or resolved_at < created_at:
        raise ValueError(f"任务收口时间越出任务时间轴: {source}")


def validate_upgrade_task_payload(payload: object, source: Path, departments: set[str], expected_state: str | None) -> None:
    if not isinstance(payload, dict):
        raise ValueError(f"任务根节点必须是 JSON 对象: {source}")
    missing = sorted(UPGRADE_TASK_REQUIRED_FIELDS - set(payload))
    unknown = sorted(set(payload) - UPGRADE_TASK_REQUIRED_FIELDS - UPGRADE_TASK_OPTIONAL_FIELDS)
    if missing:
        raise ValueError("任务字段缺失: " + ", ".join(missing))
    if unknown:
        raise ValueError("任务含当前 schema 未定义字段: " + ", ".join(unknown))
    if "impact_declaration" in payload:
        validate_upgrade_impact(payload["impact_declaration"], source)
    if "temporary_executor" in payload:
        validate_upgrade_temporary(payload["temporary_executor"], source, payload.get("department", ""))
    schema_version = payload["schema_version"]
    if isinstance(schema_version, bool) or schema_version not in {1, 2}:
        raise ValueError(f"不支持的任务版本: {source}")
    if schema_version == 2:
        required_slice_fields = {"slice_id", "task_kind", "gate_type", "ownership_history"}
        if not required_slice_fields <= set(payload):
            raise ValueError(f"1.5 TASK 缺少切片字段: {source}")
        if not isinstance(payload["slice_id"], str) or not re.fullmatch(
            r"SLICE-[0-9]{8}-[A-Z0-9]{6}", payload["slice_id"],
        ):
            raise ValueError(f"TASK slice_id 无效: {source}")
        if payload["task_kind"] not in {"owner", "gate"}:
            raise ValueError(f"TASK task_kind 无效: {source}")
        gate_type = payload["gate_type"]
        if not isinstance(gate_type, str) or ((payload["task_kind"] == "owner") != (gate_type == "")):
            raise ValueError(f"TASK gate_type 与 task_kind 冲突: {source}")
        ownership = payload["ownership_history"]
        if not isinstance(ownership, list) or len(ownership) > 100 or any(
            not isinstance(item, dict)
            or set(item) != {"at", "action", "actor", "previous_actor", "evidence"}
            or item.get("action") not in {"claim", "rebind"}
            for item in ownership
        ):
            raise ValueError(f"TASK ownership_history 无效: {source}")
        state_action_receipt = payload.get("state_action_receipt")
        if state_action_receipt is not None:
            expected_states = {
                "claim": "claimed", "block": "blocked", "wait": "waiting_input", "resume": "claimed",
            }
            action = state_action_receipt.get("action") if isinstance(state_action_receipt, dict) else None
            reason = state_action_receipt.get("reason") if isinstance(state_action_receipt, dict) else None
            generation = (
                state_action_receipt.get("candidate_generation")
                if isinstance(state_action_receipt, dict) else None
            )
            if (
                not isinstance(state_action_receipt, dict)
                or set(state_action_receipt) != {
                    "action", "actor", "reason", "target_state", "candidate_generation",
                }
                or action not in expected_states
                or state_action_receipt.get("target_state") != expected_states[action]
                or not isinstance(state_action_receipt.get("actor"), str)
                or not state_action_receipt["actor"]
                or len(state_action_receipt["actor"]) > ACTOR_MAX_CHARS
                or not isinstance(reason, str)
                or len(reason) > 2000
                or ((action in {"block", "wait"}) != bool(reason))
                or isinstance(generation, bool)
                or not isinstance(generation, int)
                or generation < 0
            ):
                raise ValueError(f"TASK state_action_receipt 无效: {source}")
        completion_history = payload.get("completion_history", [])
        completion_fields = {
            "from_state", "completed_revision", "completed_at", "reopened_at", "reopened_by", "reason",
            "candidate_id", "candidate_manifest", "candidate_sha256", "generation", "artifacts",
            "external_artifacts", "verified", "unverified", "mistake_check", "report", "event_receipts",
            "acknowledged_by",
        }
        if not isinstance(completion_history, list) or len(completion_history) > 100:
            raise ValueError(f"TASK completion_history 无效: {source}")
        for entry in completion_history:
            if (
                not isinstance(entry, dict)
                or set(entry) != completion_fields
                or entry.get("from_state") not in {"completed", "acknowledged"}
                or isinstance(entry.get("completed_revision"), bool)
                or not isinstance(entry.get("completed_revision"), int)
                or entry["completed_revision"] < 1
                or isinstance(entry.get("generation"), bool)
                or not isinstance(entry.get("generation"), int)
                or entry["generation"] < 1
                or not isinstance(entry.get("candidate_id"), str)
                or not re.fullmatch(r"CAND-[0-9]{8}-[A-Z0-9]{6}", entry["candidate_id"])
                or not isinstance(entry.get("candidate_sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", entry["candidate_sha256"])
            ):
                raise ValueError(f"TASK completion_history 条目无效: {source}")
            for field, max_chars, allow_empty in (
                ("candidate_manifest", 500, False), ("reopened_by", ACTOR_MAX_CHARS, False),
                ("reason", 1000, False), ("mistake_check", 2000, False),
                ("report", 500, False), ("acknowledged_by", ACTOR_MAX_CHARS, True),
            ):
                validate_upgrade_task_text(entry[field], field, allow_empty=allow_empty, max_chars=max_chars)
            validate_upgrade_task_timestamp(entry["completed_at"], "completion_history.completed_at")
            validate_upgrade_task_timestamp(entry["reopened_at"], "completion_history.reopened_at")
            for field, max_chars in (
                ("artifacts", 500), ("external_artifacts", 1000), ("verified", 2000),
                ("unverified", 2000), ("event_receipts", 1000),
            ):
                validate_upgrade_task_list(entry[field], field, max_chars=max_chars)
            if (
                (not entry["artifacts"] and not entry["external_artifacts"])
                or not entry["verified"] or not entry["unverified"]
                or (entry["from_state"] == "acknowledged") != bool(entry["acknowledged_by"])
            ):
                raise ValueError(f"TASK completion_history 证据无效: {source}")
        attempts = payload.get("gate_attempts", [])
        if payload["task_kind"] == "owner" and attempts:
            raise ValueError(f"owner TASK 不得含 gate_attempts: {source}")
        if payload["task_kind"] == "gate":
            if not isinstance(attempts, list) or len(attempts) > 100:
                raise ValueError(f"gate TASK 尝试记录无效: {source}")
            generations: list[int] = []
            for attempt in attempts:
                if (
                    not isinstance(attempt, dict)
                    or set(attempt) != {
                        "at", "decision", "candidate_id", "candidate_manifest", "candidate_sha256",
                        "generation", "evidence", "actor", "report", "report_sha256",
                    }
                    or attempt.get("decision") not in {"pass", "fail"}
                    or not isinstance(attempt.get("candidate_id"), str)
                    or not re.fullmatch(r"CAND-[0-9]{8}-[A-Z0-9]{6}", attempt["candidate_id"])
                    or isinstance(attempt.get("generation"), bool)
                    or not isinstance(attempt.get("generation"), int)
                    or attempt["generation"] < 1
                    or not isinstance(attempt.get("candidate_manifest"), str)
                    or not attempt["candidate_manifest"]
                    or not re.fullmatch(r"[0-9a-f]{64}", attempt.get("candidate_sha256", ""))
                    or not re.fullmatch(r"[0-9a-f]{64}", attempt.get("report_sha256", ""))
                ):
                    raise ValueError(f"gate TASK 尝试条目无效: {source}")
                generations.append(attempt["generation"])
            if generations != sorted(set(generations)):
                raise ValueError(f"gate TASK generation 必须严格递增: {source}")
        if payload.get("temporary_executor") is not None:
            raise ValueError(f"1.5 切片 TASK 不允许绑定 legacy temporary_executor: {source}")
    task_id = validate_upgrade_task_text(payload["task_id"], "task_id")
    if task_id != source.stem or not UPGRADE_TASK_ID_RE.fullmatch(task_id):
        raise ValueError(f"任务 ID 与文件名不一致或格式无效: {source}")
    impact = payload.get("impact_declaration")
    if isinstance(impact, dict) and impact.get("owner_task") != task_id:
        raise ValueError(f"任务影响声明 owner_task 与 TASK 不一致: {source}")
    temporary = payload.get("temporary_executor")
    if isinstance(temporary, dict) and (impact is None or temporary.get("impact") != impact):
        raise ValueError(f"temporary_executor impact 与 TASK impact_declaration 不一致: {source}")
    department = validate_upgrade_task_text(payload["department"], "department")
    from_department = validate_upgrade_task_text(payload["from_department"], "from_department")
    if department not in departments or from_department not in departments:
        raise ValueError(f"任务部门不存在: {source}")
    for field, max_chars in (
        ("title", 200), ("node", 200), ("details", 2000), ("acceptance_exit", 2000),
        ("confirmation", 2000), ("domain_stage", 200),
    ):
        validate_upgrade_task_text(payload[field], field, max_chars=max_chars)
    for field, max_chars in (
        ("authorization_evidence", 1000), ("claimed_by", ACTOR_MAX_CHARS), ("block_reason", 2000),
        ("mistake_check", 2000), ("report", 500),
    ):
        validate_upgrade_task_text(payload[field], field, allow_empty=True, max_chars=max_chars)
    if "acknowledged_by" in payload:
        validate_upgrade_task_text(payload["acknowledged_by"], "acknowledged_by", allow_empty=True)
    validate_upgrade_task_list(payload["failure_paths"], "failure_paths", min_items=1, max_items=3, max_chars=1000)
    for field, max_chars in (
        ("pointers", 500), ("artifacts", 500), ("external_artifacts", 1000),
        ("verified", 2000), ("unverified", 2000), ("event_receipts", 1000),
    ):
        validate_upgrade_task_list(payload[field], field, max_chars=max_chars)
    for raw in payload["artifacts"]:
        path = Path(raw)
        if path.is_absolute() or ".." in path.parts or raw in {"", "."}:
            raise ValueError(f"任务本地产物路径无效: {source}")
    for raw in payload["external_artifacts"]:
        parsed_url = urlparse(raw)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc or parsed_url.username or parsed_url.password:
            raise ValueError(f"任务外部产物 URL 无效: {source}")
    authorization = payload["authorization_state"]
    if not isinstance(authorization, str) or authorization not in UPGRADE_TASK_AUTH_STATES:
        raise ValueError(f"任务授权状态无效: {source}")
    if authorization in {"user_confirmed", "user_rejected"} and not payload["authorization_evidence"].strip():
        raise ValueError(f"任务授权证据缺失: {source}")
    history = payload["authorization_history"]
    if not isinstance(history, list):
        raise ValueError(f"任务授权历史无效: {source}")
    history_times: list[dt.datetime] = []
    for entry in history:
        if not isinstance(entry, dict) or set(entry) != {"at", "state", "evidence"}:
            raise ValueError(f"任务授权历史条目无效: {source}")
        history_times.append(validate_upgrade_task_timestamp(entry["at"], "authorization_history.at"))
        if not isinstance(entry["state"], str) or entry["state"] not in UPGRADE_TASK_AUTH_STATES:
            raise ValueError(f"任务授权历史状态无效: {source}")
        validate_upgrade_task_text(entry["evidence"], "authorization_history.evidence", max_chars=1000)
    has_evidence = bool(payload["authorization_evidence"].strip())
    if bool(history) != has_evidence:
        raise ValueError(f"任务当前授权证据与历史有无不一致: {source}")
    if history and (history[-1]["state"] != authorization or history[-1]["evidence"] != payload["authorization_evidence"]):
        raise ValueError(f"任务当前授权与历史最新记录不一致: {source}")
    state = payload["execution_state"]
    if not isinstance(state, str) or state not in UPGRADE_TASK_STATES or (expected_state is not None and state != expected_state):
        raise ValueError(f"任务执行状态无效或与目录不一致: {source}")
    if not isinstance(payload["completion_class"], str) or payload["completion_class"] not in {"standard", "audit"}:
        raise ValueError(f"任务完成类型无效: {source}")
    expected_completion_class = "audit" if any(
        role["name"] == department and role["layer"] == "audit" for role in ROLE_DEFS.values()
    ) else "standard"
    if payload["completion_class"] != expected_completion_class:
        raise ValueError(f"任务完成类型与部门层级不一致: {source}")
    revision = payload["revision"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise ValueError(f"任务 revision 无效: {source}")
    created_at = validate_upgrade_task_timestamp(payload["created_at"], "created_at")
    updated_at = validate_upgrade_task_timestamp(payload["updated_at"], "updated_at")
    if updated_at < created_at:
        raise ValueError(f"任务 updated_at 早于 created_at: {source}")
    validate_upgrade_resolution(
        payload.get("resolution"), source, task_id=task_id, execution_state=state,
        revision=revision, created_at=created_at, updated_at=updated_at,
        has_temporary=payload.get("temporary_executor") is not None,
    )
    if history_times != sorted(history_times) or any(timestamp < created_at or timestamp > updated_at for timestamp in history_times):
        raise ValueError(f"任务授权历史时间顺序或边界无效: {source}")
    if state == "queued" and payload["claimed_by"].strip():
        raise ValueError(f"待领取任务不得预填 claimed_by: {source}")
    if state not in {"blocked", "waiting_input"} and payload["block_reason"].strip():
        raise ValueError(f"任务当前状态不得预填 block_reason: {source}")
    if state in {"blocked", "waiting_input"} and not payload["block_reason"].strip():
        raise ValueError(f"阻断或等待输入任务缺失 block_reason: {source}")
    if state in {"queued", "claimed", "blocked", "waiting_input"} and any((
        payload["artifacts"], payload["external_artifacts"], payload["verified"], payload["unverified"],
        payload["mistake_check"].strip(), payload["report"].strip(), payload["event_receipts"],
    )):
        raise ValueError(f"未完成任务不得预填交付结果: {source}")
    if state in UPGRADE_TASK_STATES - {"queued"} and not payload["claimed_by"].strip():
        raise ValueError(f"任务已进入执行生命周期但 claimed_by 缺失: {source}")
    if state in {"claimed", "completed", "acknowledged"} and authorization not in {"none", "user_confirmed"}:
        raise ValueError(f"任务当前执行状态与授权状态冲突: {source}")
    if state in {"blocked", "waiting_input"} and authorization in {"user_required", "user_rejected"} and not has_evidence:
        raise ValueError(f"阻断或等待输入任务的授权变更缺失证据: {source}")
    if state in {"completed", "acknowledged"}:
        if not payload["artifacts"] and not payload["external_artifacts"]:
            raise ValueError(f"已完成任务缺失产物: {source}")
        if not payload["verified"] or not payload["unverified"] or not payload["mistake_check"].strip():
            raise ValueError(f"已完成任务缺失验证或自检记录: {source}")
        if not payload["report"].strip():
            raise ValueError(f"已完成任务缺失 report 记录: {source}")
        if payload["completion_class"] == "audit" and payload["report"] not in payload["artifacts"]:
            raise ValueError(f"审核任务 report 未同时列入本地产物: {source}")
    if state == "acknowledged" and not payload.get("acknowledged_by", "").strip():
        raise ValueError(f"已核收任务缺失 acknowledged_by: {source}")
    if state != "acknowledged" and payload.get("acknowledged_by", "").strip():
        raise ValueError(f"未核收任务不得预填 acknowledged_by: {source}")


def preflight_upgrade_tasks(collab: Path, roles: list[str]) -> None:
    tasks = collab / "tasks"
    if tasks.is_symlink():
        raise ValueError("tasks 路径不能是符号链接")
    if not tasks.exists():
        return
    if not plain_path_within(tasks, collab, kind="dir"):
        raise ValueError("tasks 路径不安全")
    departments = {ROLE_DEFS[role]["name"] for role in roles}
    seen_destinations: set[str] = set()
    for entry in tasks.iterdir():
        if entry.is_symlink():
            raise ValueError(f"任务路径不安全: {entry}")
        if entry.is_dir():
            if entry.name not in UPGRADE_TASK_STATES or not plain_path_within(entry, collab, kind="dir"):
                raise ValueError(f"任务目录无效: {entry}")
            candidates = list(entry.iterdir())
            expected_state = entry.name
        elif entry.is_file() and entry.name.startswith("TASK-") and entry.suffix == ".json":
            candidates = [entry]
            expected_state = None
        else:
            raise ValueError(f"tasks 中含非标准项: {entry}")
        for source in candidates:
            if source.is_symlink() or not source.is_file() or not source.name.startswith("TASK-") or source.suffix != ".json":
                raise ValueError(f"任务目录含非标准文件: {source}")
            if source.name in seen_destinations:
                raise ValueError(f"任务迁移目标重复: {source.name}")
            seen_destinations.add(source.name)
            try:
                payload = strict_json_loads(read_utf8(source), source=str(source))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"任务 JSON 损坏: {source}: {exc}") from exc
            validate_upgrade_task_payload(payload, source, departments, expected_state)


def iter_upgrade_task_payloads(collab: Path) -> list[tuple[Path, dict]]:
    tasks = collab / "tasks"
    if tasks.is_symlink():
        raise ValueError("tasks 路径不能是符号链接")
    if not tasks.exists():
        return []
    if not plain_path_within(tasks, collab, kind="dir"):
        raise ValueError("tasks 路径不安全")
    result: list[tuple[Path, dict]] = []
    for entry in sorted(tasks.iterdir()):
        candidates = sorted(entry.iterdir()) if entry.is_dir() else [entry]
        for source in candidates:
            if source.is_symlink() or not source.is_file() or not source.name.startswith("TASK-") or source.suffix != ".json":
                raise ValueError(f"任务路径不安全: {source}")
            payload = strict_json_loads(read_utf8(source), source=str(source))
            if not isinstance(payload, dict):
                raise ValueError(f"任务根节点必须是 JSON 对象: {source}")
            result.append((source, payload))
    return result


def validate_session_thread_identities(state_payload: dict, task_payloads: list[tuple[Path, dict]]) -> None:
    owners: dict[str, str] = {}

    def register(value: object, owner: str) -> None:
        if not isinstance(value, str):
            raise ValueError(f"thread_id 类型无效: {owner}")
        if not value:
            return
        if len(value) > 300 or value.startswith("=") or any(char.isspace() for char in value):
            raise ValueError(f"thread_id 不可表示为归档回执: {owner}")
        previous = owners.get(value)
        if previous is not None:
            raise ValueError(f"thread_id 全局冲突: {value}: {previous} / {owner}")
        owners[value] = owner

    for department, item in state_payload["departments"].items():
        register(item.get("thread_id", ""), f"正式部门:{department}:current")
        register(item.get("previous_thread_id", ""), f"正式部门:{department}:previous")
    for source, task in task_payloads:
        temporary = task.get("temporary_executor")
        if not isinstance(temporary, dict):
            continue
        session = temporary.get("temporary_session")
        if not isinstance(session, dict):
            raise ValueError(f"临时会话结构无效: {source}")
        register(session.get("thread_id", ""), f"临时外包:{task.get('task_id', source.stem)}")


def migrated_session_state(
    collab: Path,
    roles: list[str],
    registry_text: str,
    date: str,
    session_mode: str,
    task_payloads: list[tuple[Path, dict]],
) -> dict:
    state_path = collab / "会话启动状态.json"
    if state_path.is_symlink():
        raise ValueError("会话启动状态不能是符号链接")
    expected_departments = {ROLE_DEFS[key]["name"]: key for key in roles}
    if state_path.exists():
        if not plain_path_within(state_path, collab, kind="file"):
            raise ValueError("会话启动状态路径不安全")
        source_payload = strict_json_loads(read_utf8(state_path), source=str(state_path))
        if not isinstance(source_payload, dict) or source_payload.get("schema_version") != 1:
            raise ValueError("会话启动状态版本无效")
        source_departments = source_payload.get("departments")
        if not isinstance(source_departments, dict) or set(source_departments) != set(expected_departments):
            raise ValueError("会话启动状态与部门表的部门集合不一致")
        departments: dict[str, dict[str, object]] = {}
        for department, role_id in expected_departments.items():
            item = source_departments[department]
            if not isinstance(item, dict) or item.get("role_id") != role_id:
                raise ValueError(f"会话启动状态角色无效: {department}")
            step = item.get("step")
            if step not in {"pending", "created", "onboarded", "registered", "failed"}:
                raise ValueError(f"会话启动状态 step 无效: {department}")
            notification_mode = item.get("notification_mode", session_mode)
            if notification_mode not in {"auto", "manual"}:
                raise ValueError(f"会话通知模式无效: {department}")
            migrated: dict[str, object] = {
                "role_id": role_id,
                "active": item.get("active", True),
                "notification_mode": notification_mode,
                "step": step,
            }
            defaults = {
                "thread_id": "",
                "previous_thread_id": "",
                "failed_from": "",
                "evidence": "",
                "operation_id": "MIGRATE-" + hashlib.sha256(f"{department}:{date}".encode("utf-8")).hexdigest()[:10].upper(),
                "note": "",
                "updated_at": date,
            }
            for field, default in defaults.items():
                value = item.get(field, default)
                if not isinstance(value, str):
                    raise ValueError(f"会话启动状态 {field} 类型无效: {department}")
                migrated[field] = value
            history = item.get("lifecycle_history", [])
            if not isinstance(history, list):
                raise ValueError(f"会话启动状态 lifecycle_history 类型无效: {department}")
            migrated["lifecycle_history"] = history
            if migrated["previous_thread_id"] and "operation_id" not in item:
                migrated["operation_id"] = (
                    "SWITCH-MIGRATE-"
                    + hashlib.sha256(f"{department}:{date}:switch".encode("utf-8")).hexdigest()[:10].upper()
                )
            validate_session_item_lifecycle(migrated, department)
            departments[department] = migrated
        payload = {
            "schema_version": 1,
            "protocol_version": PROTOCOL_VERSION,
            "created_at": source_payload.get("created_at")
            if isinstance(source_payload.get("created_at"), str) and source_payload.get("created_at")
            else registry_value(registry_text, "创建日期", date),
            "updated_at": source_payload.get("updated_at") if isinstance(source_payload.get("updated_at"), str) else date,
            "profile": source_payload.get("profile")
            if isinstance(source_payload.get("profile"), str) and source_payload.get("profile")
            else registry_value(registry_text, "项目类型", "已有项目"),
            "session_mode": source_payload.get("session_mode")
            if source_payload.get("session_mode") in {"auto", "manual"} else session_mode,
            "role_order": source_payload.get("role_order")
            if isinstance(source_payload.get("role_order"), list)
            and all(isinstance(role, str) for role in source_payload["role_order"])
            and set(source_payload["role_order"]) == set(roles)
            else roles,
            "departments": departments,
        }
    else:
        payload = strict_json_loads(
            session_state_payload(
                roles, date, session_mode, registry_value(registry_text, "项目类型", "已有项目"),
            ), source="generated legacy fallback session state",
        )
        if not isinstance(payload, dict):
            raise ValueError("生成的会话状态无效")
        session_data = registry_session_data(registry_text)
        notification_map = {"自动": "auto", "人工": "manual"}
        for key, saved in session_data.items():
            item = payload["departments"].get(ROLE_DEFS[key]["name"])
            if item is None:
                continue
            saved_notification = saved.get("notification", "")
            if saved_notification not in {"", "待登记", "-"}:
                notification = notification_map.get(saved_notification, saved_notification)
                if notification not in {"auto", "manual"}:
                    raise ValueError(f"旧部门表通知模式无效: {key}")
                item["notification_mode"] = notification
            session_id = saved.get("session_id", "")
            if session_id and session_id not in {"待登记", "-"}:
                item["thread_id"] = session_id
                item["step"] = "registered"
                item["note"] = "仅因旧协议确实缺失会话状态文件，从部门表降级恢复"
    validate_current_session_payload(payload)
    validate_session_thread_identities(payload, task_payloads)
    return payload


def registry_data_from_session_state(payload: dict) -> dict[str, dict[str, str]]:
    status_labels = {
        "pending": "待启用",
        "created": "上岗中",
        "onboarded": "上岗中",
        "registered": "已启用",
        "failed": "失败",
    }
    result: dict[str, dict[str, str]] = {}
    for department, item in payload["departments"].items():
        result[item["role_id"]] = {
            "session_id": item.get("thread_id") or "待登记",
            "notification": item["notification_mode"],
            "status": status_labels[item["step"]] if item.get("active", True) else "已停用",
        }
    return result


def load_legacy_archive_recovery(
    collab: Path, task_payloads: list[tuple[Path, dict]], previous_protocol_version: str | None,
) -> tuple[Path, dict]:
    path = collab / ".locks" / LEGACY_ARCHIVE_RECOVERY_FILE
    empty = {"schema_version": 1, "protocol_version": PROTOCOL_VERSION, "entries": {}}
    if not path.exists():
        return path, empty
    if path.is_symlink() or not plain_path_within(path, collab, kind="file"):
        raise ValueError("legacy archive recovery 路径不安全")
    payload = strict_json_loads(read_utf8(path), source=str(path))
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "protocol_version", "entries"}:
        raise ValueError("legacy archive recovery 结构无效")
    entries = payload.get("entries")
    if (
        payload.get("schema_version") != 1
        or payload.get("protocol_version") not in {PROTOCOL_VERSION, previous_protocol_version}
        or not isinstance(entries, dict)
    ):
        raise ValueError("legacy archive recovery 版本无效")
    sources = {task.get("task_id"): source for source, task in task_payloads}
    for task_id, entry in entries.items():
        source = sources.get(task_id)
        if (
            source is None
            or not isinstance(entry, dict)
            or set(entry) != {"task_sha256", "thread_id", "state", "receipt", "verified_at"}
            or not re.fullmatch(r"[0-9a-f]{64}", entry.get("task_sha256", ""))
            or hashlib.sha256(source.read_bytes()).hexdigest() != entry["task_sha256"]
            or not isinstance(entry.get("thread_id"), str)
            or entry.get("state") not in {"pending", "verified"}
            or not isinstance(entry.get("receipt"), str)
            or not isinstance(entry.get("verified_at"), str)
            or (entry["state"] == "pending" and (entry["receipt"] or entry["verified_at"]))
            or (
                entry["state"] == "verified"
                and (not entry["receipt"] or not entry["verified_at"] or not valid_archive_receipt(entry["receipt"], entry["thread_id"]))
            )
        ):
            raise ValueError(f"legacy archive recovery 条目无效或与原 TASK 不一致: {task_id}")
    payload = json.loads(json.dumps(payload, ensure_ascii=False))
    payload["protocol_version"] = PROTOCOL_VERSION
    return path, payload


def scan_archive_recovery_actions(
    task_payloads: list[tuple[Path, dict]], previous_protocol_version: str | None,
    recovery_entries: dict[str, dict],
) -> tuple[list[tuple[Path, str, str]], list[tuple[str, str]]]:
    repairs: list[tuple[Path, str, str]] = []
    pending: list[tuple[str, str]] = []
    for source, payload in task_payloads:
        temporary = payload.get("temporary_executor")
        if not isinstance(temporary, dict):
            continue
        session = temporary.get("temporary_session")
        if not isinstance(session, dict):
            continue
        thread_id = session.get("thread_id", "")
        if session.get("state") == "archived":
            if not isinstance(thread_id, str) or not thread_id:
                raise ValueError(f"legacy archived 会话缺少 thread_id，不能生成可恢复归档动作: {payload.get('task_id', source.stem)}")
            if thread_id and valid_archive_receipt(session.get("evidence", ""), thread_id):
                continue
            recovery = recovery_entries.get(payload["task_id"])
            if recovery is not None:
                if recovery.get("thread_id") != thread_id:
                    raise ValueError(f"legacy archive recovery thread_id 漂移: {payload['task_id']}")
                if recovery.get("state") == "verified":
                    continue
            elif previous_protocol_version == PROTOCOL_VERSION:
                raise ValueError(f"当前协议 archived 会话缺少精确归档回执且无恢复账本: {payload.get('task_id', source.stem)}")
            repairs.append((source, payload["task_id"], thread_id))
        elif (
            session.get("state") == "standby"
            and thread_id
            and temporary.get("promotion_state") == "archived"
            and isinstance(temporary.get("workspace"), dict)
            and temporary["workspace"].get("state") == "removed"
            and isinstance(temporary.get("cleanup_operation"), dict)
            and temporary["cleanup_operation"].get("state") == "verified"
        ):
            pending.append((payload["task_id"], thread_id))
    return repairs, pending


def merge_legacy_archive_recovery(
    payload: dict, repairs: list[tuple[Path, str, str]],
) -> dict:
    merged = json.loads(json.dumps(payload, ensure_ascii=False))
    entries = merged["entries"]
    for source, task_id, thread_id in repairs:
        existing = entries.get(task_id)
        if existing is not None:
            if existing["thread_id"] != thread_id or existing["task_sha256"] != hashlib.sha256(source.read_bytes()).hexdigest():
                raise ValueError(f"legacy archive recovery 既有条目与迁移源冲突: {task_id}")
            continue
        entries[task_id] = {
            "task_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "thread_id": thread_id,
            "state": "pending",
            "receipt": "",
            "verified_at": "",
        }
    return merged


def load_legacy_closeout_index(
    collab: Path, task_payloads: list[tuple[Path, dict]], recovery_entries: dict[str, dict],
    previous_protocol_version: str | None,
) -> tuple[Path, list[str], list[str]]:
    path = collab / ".locks" / LEGACY_CLOSEOUT_INDEX_FILE
    existing: list[str] = []
    existing_recovery_ids: list[str] = []
    if path.exists():
        if path.is_symlink() or not plain_path_within(path, collab, kind="file"):
            raise ValueError("legacy closeout index 路径不安全")
        payload = strict_json_loads(read_utf8(path), source=str(path))
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version", "protocol_version", "task_ids", "archive_recovery_task_ids",
        }:
            raise ValueError("legacy closeout index 结构无效")
        raw_ids = payload.get("task_ids")
        raw_recovery_ids = payload.get("archive_recovery_task_ids")
        if (
            payload.get("schema_version") != 1
            or payload.get("protocol_version") not in {PROTOCOL_VERSION, previous_protocol_version}
            or not isinstance(raw_ids, list)
            or not isinstance(raw_recovery_ids, list)
            or len(raw_ids) > 10000
            or raw_ids != sorted(set(raw_ids))
            or raw_recovery_ids != sorted(set(raw_recovery_ids))
            or not set(raw_recovery_ids) <= set(raw_ids)
            or any(not isinstance(task_id, str) or not UPGRADE_TASK_ID_RE.fullmatch(task_id) for task_id in raw_ids)
            or any(
                not isinstance(task_id, str) or not UPGRADE_TASK_ID_RE.fullmatch(task_id)
                for task_id in raw_recovery_ids
            )
        ):
            raise ValueError("legacy closeout index 内容无效")
        existing = raw_ids
        existing_recovery_ids = raw_recovery_ids
    by_id = {task.get("task_id"): task for _, task in task_payloads}
    for task_id in existing:
        task = by_id.get(task_id)
        if task is None or task.get("schema_version") == 2 or not isinstance(task.get("temporary_executor"), dict):
            raise ValueError(f"legacy closeout index 指向非 legacy temporary TASK: {task_id}")
    indexed = set(existing)
    recovery_required = set(existing_recovery_ids) | set(recovery_entries)
    for _, task in task_payloads:
        if task.get("schema_version") == 2 or not isinstance(task.get("temporary_executor"), dict):
            continue
        if task["task_id"] in recovery_entries:
            indexed.add(task["task_id"])
        temporary = task["temporary_executor"]
        workspace = temporary.get("workspace")
        cleanup = temporary.get("cleanup_operation")
        session = temporary.get("temporary_session")
        terminal = (
            task.get("execution_state") == "acknowledged"
            and temporary.get("promotion_state") == "archived"
            and isinstance(workspace, dict) and workspace.get("state") == "removed"
            and isinstance(cleanup, dict) and cleanup.get("state") == "verified"
            and isinstance(session, dict) and session.get("state") in {"archived", "cancelled"}
        )
        recovery = recovery_entries.get(task["task_id"])
        if terminal and (recovery is None or recovery.get("state") == "verified"):
            continue
        indexed.add(task["task_id"])
    if len(indexed) > 10000:
        raise ValueError("legacy closeout index 超过 10000 条安全上限")
    if not recovery_required <= indexed:
        raise ValueError("legacy closeout index 缺少 archive recovery TASK 身份")
    return path, sorted(indexed), sorted(recovery_required)


def migrated_slice_control(
    collab: Path, previous_protocol_version: str | None, task_payloads: list[tuple[Path, dict]],
) -> str:
    path = collab / ".locks" / "slice-control.json"
    if previous_protocol_version != "1.5.0":
        if previous_protocol_version == PROTOCOL_VERSION:
            if path.is_symlink() or not plain_path_within(path, collab, kind="file"):
                raise ValueError("当前切片控制缺失或不安全")
            return read_utf8(path)
        return slice_control_payload()
    if path.is_symlink() or not plain_path_within(path, collab, kind="file"):
        raise ValueError("1.5.0 切片控制缺失或不安全")
    payload = strict_json_loads(read_utf8(path), source=str(path))
    top_fields = {
        "schema_version", "protocol_version", "active_slice", "history", "candidate_ids", "host_runtime",
    }
    if (
        not isinstance(payload, dict) or set(payload) != top_fields
        or payload.get("schema_version") != 1
        or payload.get("protocol_version") != "1.5.0"
        or payload.get("host_runtime") != {"mode": "manual-degraded", "capabilities": []}
        or not isinstance(payload.get("history"), list) or len(payload["history"]) > 100
        or not isinstance(payload.get("candidate_ids"), list)
        or len(payload["candidate_ids"]) > 100000
        or len(payload["candidate_ids"]) != len(set(payload["candidate_ids"]))
        or any(not isinstance(item, str) or not re.fullmatch(r"CAND-[0-9]{8}-[A-Z0-9]{6}", item)
               for item in payload["candidate_ids"])
    ):
        raise ValueError("1.5.0 切片控制顶层结构无效")
    active = payload.get("active_slice")
    if active is not None:
        old_fields = {
            "slice_id", "owner_task_id", "required_gates", "gate_tasks", "candidate",
            "user_exit", "created_at", "metrics",
        }
        if not isinstance(active, dict) or set(active) != old_fields:
            raise ValueError("1.5.0 活动切片结构无效")
        if (
            not re.fullmatch(r"SLICE-[0-9]{8}-[A-Z0-9]{6}", active.get("slice_id", ""))
            or not UPGRADE_TASK_ID_RE.fullmatch(active.get("owner_task_id", ""))
            or not isinstance(active.get("required_gates"), list)
            or len(active["required_gates"]) > 2
            or len(active["required_gates"]) != len(set(active["required_gates"]))
            or not isinstance(active.get("gate_tasks"), dict)
            or any(gate not in active["required_gates"] for gate in active["gate_tasks"])
            or any(not UPGRADE_TASK_ID_RE.fullmatch(task_id) for task_id in active["gate_tasks"].values())
            or not isinstance(active.get("metrics"), list)
        ):
            raise ValueError("1.5.0 活动切片身份或 gate 结构无效")
        by_id = {task.get("task_id"): task for _, task in task_payloads}
        expected_ids = {active["owner_task_id"], *active["gate_tasks"].values()}
        if not expected_ids <= set(by_id) or any(
            by_id[task_id].get("slice_id") != active["slice_id"] for task_id in expected_ids
        ):
            raise ValueError("1.5.0 活动切片引用的 TASK 缺失或身份不一致")
        candidate = active.get("candidate")
        if candidate is not None and (
            not isinstance(candidate, dict)
            or set(candidate) != {"candidate_id", "manifest", "sha256", "generation", "bound_by", "bound_at"}
            or not re.fullmatch(r"CAND-[0-9]{8}-[A-Z0-9]{6}", candidate.get("candidate_id", ""))
            or candidate["candidate_id"] not in payload["candidate_ids"]
            or not re.fullmatch(r"[0-9a-f]{64}", candidate.get("sha256", ""))
            or isinstance(candidate.get("generation"), bool)
            or not isinstance(candidate.get("generation"), int)
            or candidate["generation"] < 1
        ):
            raise ValueError("1.5.0 活动候选身份无效")
        user_exit = active.get("user_exit")
        if (
            not isinstance(user_exit, dict)
            or set(user_exit) != {"status", "evidence", "actor", "at"}
            or user_exit.get("status") not in {"pending", "verified", "not_applicable"}
            or not all(isinstance(user_exit.get(field), str) for field in ("evidence", "actor", "at"))
            or (user_exit["status"] == "pending" and any(user_exit[field] for field in ("evidence", "actor", "at")))
            or (user_exit["status"] != "pending" and not all(user_exit[field] for field in ("evidence", "actor", "at")))
        ):
            raise ValueError("1.5.0 用户出口结构无效")
        active["revision_requests"] = []
        active["user_exit"] = {
            **user_exit,
            "candidate_id": candidate["candidate_id"] if candidate else "",
            "generation": candidate["generation"] if candidate else 0,
        }
    payload["protocol_version"] = PROTOCOL_VERSION
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def emit_pending_archive_actions(actions: list[tuple[str, str]]) -> None:
    for task_id, thread_id in actions:
        print(
            f"ARCHIVE_THREAD_REQUIRED:{thread_id} | task:{task_id}"
            f" | reason:existing-standby-archive"
        )


def upgrade_backup_record(path: Path, backup: Path | None) -> dict[str, object]:
    if backup is None:
        return {"backup": None, "sha256": None, "mode": None}
    data = backup.read_bytes()
    return {
        "backup": backup,
        "sha256": hashlib.sha256(data).hexdigest(),
        "mode": stat.S_IMODE(backup.stat().st_mode),
    }


def write_upgrade_rollback_manifest(
    backup_root: Path,
    project: Path,
    originals: dict[Path, dict[str, object]],
    directory_modes: dict[Path, int | None],
    *,
    operation_id: str,
    source_protocol: str,
) -> None:
    entries = []
    for path, record in sorted(originals.items(), key=lambda item: str(item[0])):
        backup = record["backup"]
        entries.append({
            "target": path.relative_to(project).as_posix(),
            "existed": backup is not None,
            "backup": backup.relative_to(backup_root).as_posix() if isinstance(backup, Path) else None,
            "sha256": record["sha256"],
            "mode": f"{record['mode']:04o}" if isinstance(record["mode"], int) else None,
        })
    directories = [
        {
            "target": path.relative_to(project).as_posix(),
            "existed": mode is not None,
            "mode": f"{mode:04o}" if mode is not None else None,
        }
        for path, mode in sorted(directory_modes.items(), key=lambda item: str(item[0]))
    ]
    payload = {
        "schema_version": 2,
        "operation": "upgrade",
        "operation_id": operation_id,
        "source_protocol": source_protocol,
        "target_protocol": PROTOCOL_VERSION,
        "files": entries,
        "directories": directories,
    }
    write_utf8_atomic(
        backup_root / "rollback-manifest.json",
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )


def restore_upgrade_originals(
    originals: dict[Path, dict[str, object]], directory_modes: dict[Path, int | None],
) -> None:
    preflight_failures: list[str] = []
    removable_files = {path for path, record in originals.items() if record.get("backup") is None}
    removable_directories = {path for path, mode in directory_modes.items() if mode is None}
    for path, record in originals.items():
        backup = record.get("backup")
        try:
            if path.is_symlink():
                raise ValueError("恢复文件目标变成了符号链接")
            if backup is None:
                if path.exists() and not path.is_file():
                    raise ValueError("本应删除的恢复目标不是普通文件")
                continue
            if not isinstance(backup, Path) or backup.is_symlink() or not backup.is_file():
                raise ValueError("备份文件缺失或不安全")
            data = backup.read_bytes()
            if hashlib.sha256(data).hexdigest() != record.get("sha256") or not isinstance(record.get("mode"), int):
                raise ValueError("备份哈希或权限记录不一致")
            if path.exists() and not path.is_file():
                raise ValueError("恢复文件目标类型无效")
        except (OSError, ValueError) as exc:
            preflight_failures.append(f"{path}: {exc}")
    for path, expected_mode in directory_modes.items():
        try:
            if path.is_symlink():
                raise ValueError("恢复目录变成了符号链接")
            if path.exists() and not path.is_dir():
                raise ValueError("恢复目录目标不是普通目录")
            if expected_mode is None and path.exists():
                allowed = {
                    candidate for candidate in removable_files | removable_directories
                    if candidate != path and path in candidate.parents
                }
                extras = [
                    child for child in path.rglob("*")
                    if child not in allowed
                ]
                if extras:
                    raise ValueError(
                        "升级后新建目录出现未登记内容: "
                        + ", ".join(str(child) for child in sorted(extras, key=str)[:10])
                    )
        except (OSError, ValueError) as exc:
            preflight_failures.append(f"{path}: {exc}")
    if preflight_failures:
        raise RuntimeError("回滚预检失败，未改动当前现场: " + " | ".join(preflight_failures))

    failures: list[str] = []
    for path, record in sorted(originals.items(), key=lambda item: len(item[0].parts), reverse=True):
        backup = record["backup"]
        try:
            if backup is None:
                if path.exists() or path.is_symlink():
                    if path.is_dir() and not path.is_symlink():
                        raise ValueError("本应不存在的恢复目标变成了目录")
                    path.unlink()
            else:
                if not isinstance(backup, Path) or backup.is_symlink() or not backup.is_file():
                    raise ValueError("备份文件缺失或不安全")
                data = backup.read_bytes()
                expected_sha = record["sha256"]
                mode = record["mode"]
                if hashlib.sha256(data).hexdigest() != expected_sha or not isinstance(mode, int):
                    raise ValueError("备份哈希或权限记录不一致")
                write_bytes_atomic(path, data, mode=mode)
            if backup is None:
                if path.exists() or path.is_symlink():
                    raise ValueError("未能恢复为不存在")
            else:
                if not path.is_file() or path.is_symlink():
                    raise ValueError("恢复目标不是普通文件")
                if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
                    raise ValueError("恢复后哈希不一致")
                if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) != record["mode"]:
                    raise ValueError("恢复后权限不一致")
        except (OSError, ValueError) as exc:
            failures.append(f"{path}: {exc}")
    for path, expected_mode in sorted(directory_modes.items(), key=lambda item: len(item[0].parts), reverse=True):
        try:
            if path.is_symlink():
                raise ValueError("恢复目录变成了符号链接")
            if expected_mode is None:
                if path.exists():
                    if not path.is_dir():
                        raise ValueError("本应不存在的目录恢复目标不是目录")
                    path.rmdir()
                if path.exists() or path.is_symlink():
                    raise ValueError("未能移除升级新建目录")
                continue
            if not path.exists():
                if path.parent.is_symlink():
                    raise ValueError("恢复目录的父路径不能是符号链接")
                path.mkdir(parents=True, exist_ok=True)
            if not path.is_dir() or path.is_symlink():
                raise ValueError("恢复目标不是普通目录")
            if os.name != "nt":
                os.chmod(path, expected_mode)
                if stat.S_IMODE(path.stat().st_mode) != expected_mode:
                    raise ValueError("恢复后目录权限不一致")
        except (OSError, ValueError) as exc:
            failures.append(f"{path}: {exc}")
    if failures:
        raise RuntimeError("回滚逐项校验失败: " + " | ".join(failures))


def recover_upgrade_transaction(collab: Path, *, announce: bool = True) -> bool:
    """Recover an interrupted upgrade from its durable, pre-mutation rollback manifest."""
    marker = collab / UPGRADE_TRANSACTION_FILE
    if not marker.exists():
        return False
    if marker.is_symlink() or not plain_path_within(marker, collab, kind="file"):
        raise ValueError("升级事务标记不安全")
    payload = strict_json_loads(read_utf8(marker), source=str(marker))
    expected_fields = {
        "schema_version", "kind", "operation_id", "phase", "backup_dir",
        "rollback_manifest_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise ValueError("升级事务标记结构无效")
    operation_id = payload.get("operation_id")
    if (
        payload.get("schema_version") != 1
        or payload.get("kind") != "upgrade"
        or payload.get("phase") not in {"prepared", "applying", "protocol_written"}
        or not isinstance(operation_id, str)
        or not re.fullmatch(r"UPGRADE-[0-9]{8}T[0-9]{6}-[A-F0-9]{8}", operation_id)
    ):
        raise ValueError("升级事务标记内容无效")
    expected_backup_relative = f"升级备份/{operation_id}"
    if payload.get("backup_dir") != expected_backup_relative:
        raise ValueError("升级事务备份路径无效")
    backup_root = collab / expected_backup_relative
    if not plain_path_within(backup_root, collab, kind="dir"):
        raise ValueError("升级事务备份目录不安全")
    manifest_path = backup_root / "rollback-manifest.json"
    if not plain_path_within(manifest_path, backup_root, kind="file"):
        raise ValueError("升级回滚清单不安全")
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha = payload.get("rollback_manifest_sha256")
    if (
        not isinstance(manifest_sha, str)
        or not re.fullmatch(r"[0-9a-f]{64}", manifest_sha)
        or hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha
    ):
        raise ValueError("升级回滚清单哈希不匹配")
    manifest = strict_json_loads(manifest_bytes.decode("utf-8"), source=str(manifest_path))
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version", "operation", "operation_id", "source_protocol", "target_protocol",
        "files", "directories",
    }:
        raise ValueError("升级回滚清单结构无效")
    if (
        manifest.get("schema_version") != 2
        or manifest.get("operation") != "upgrade"
        or manifest.get("operation_id") != operation_id
        or not isinstance(manifest.get("source_protocol"), str)
        or manifest.get("target_protocol") != PROTOCOL_VERSION
        or not isinstance(manifest.get("files"), list)
        or not isinstance(manifest.get("directories"), list)
    ):
        raise ValueError("升级回滚清单版本无效")
    project = collab.parents[1]
    originals: dict[Path, dict[str, object]] = {}
    directory_modes: dict[Path, int | None] = {}
    for entry in manifest["files"]:
        if not isinstance(entry, dict) or set(entry) != {"target", "existed", "backup", "sha256", "mode"}:
            raise ValueError("升级回滚文件记录无效")
        target_raw = entry["target"]
        if not isinstance(target_raw, str) or Path(target_raw).is_absolute() or ".." in Path(target_raw).parts:
            raise ValueError("升级回滚目标越界")
        target = project / target_raw
        try:
            target.relative_to(project)
        except ValueError as exc:
            raise ValueError("升级回滚目标越界") from exc
        if target in originals or target == marker:
            raise ValueError("升级回滚目标重复或包含事务标记")
        if entry["existed"] is True:
            backup_raw, digest, mode_raw = entry["backup"], entry["sha256"], entry["mode"]
            if (
                not isinstance(backup_raw, str) or Path(backup_raw).is_absolute() or ".." in Path(backup_raw).parts
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or not isinstance(mode_raw, str) or not re.fullmatch(r"[0-7]{4}", mode_raw)
            ):
                raise ValueError("升级回滚备份记录无效")
            backup = backup_root / backup_raw
            if not plain_path_within(backup, backup_root, kind="file") or hashlib.sha256(backup.read_bytes()).hexdigest() != digest:
                raise ValueError("升级回滚备份缺失或哈希不匹配")
            originals[target] = {"backup": backup, "sha256": digest, "mode": int(mode_raw, 8)}
        elif entry["existed"] is False and entry["backup"] is None and entry["sha256"] is None and entry["mode"] is None:
            originals[target] = {"backup": None, "sha256": None, "mode": None}
        else:
            raise ValueError("升级回滚缺失文件记录无效")
    for entry in manifest["directories"]:
        if not isinstance(entry, dict) or set(entry) != {"target", "existed", "mode"}:
            raise ValueError("升级回滚目录记录无效")
        target_raw, mode_raw = entry["target"], entry["mode"]
        if not isinstance(target_raw, str) or Path(target_raw).is_absolute() or ".." in Path(target_raw).parts:
            raise ValueError("升级回滚目录越界")
        target = project / target_raw
        if target in directory_modes:
            raise ValueError("升级回滚目录重复")
        if entry["existed"] is True and isinstance(mode_raw, str) and re.fullmatch(r"[0-7]{4}", mode_raw):
            directory_modes[target] = int(mode_raw, 8)
        elif entry["existed"] is False and mode_raw is None:
            directory_modes[target] = None
        else:
            raise ValueError("升级回滚目录权限记录无效")
    restore_upgrade_originals(originals, directory_modes)
    marker.unlink()
    fsync_dir(collab)
    if announce:
        print(f"UPGRADE_RECOVERY_OK | {operation_id} | restored_from:{backup_root}")
    return True


def run_explicit_rollback(collab: Path, manifest_argument: str) -> int:
    if read_protocol_version_for_upgrade(collab) != PROTOCOL_VERSION:
        print("显式回滚只允许从当前协议执行。", file=sys.stderr)
        return 9
    try:
        if not current_runtime_complete(collab):
            raise ValueError("当前协议运行时或受管状态不完整，拒绝回滚")
        protocol_path = collab / PROTOCOL_FILE
        protocol = strict_json_loads(read_utf8(protocol_path), source=str(protocol_path))
        upgrade_identity = protocol.get("upgrade_identity") if isinstance(protocol, dict) else None
        if not isinstance(upgrade_identity, dict):
            raise ValueError("当前现场没有可回滚的升级代次身份")
        active_150_rollback = upgrade_identity.get("source_protocol") == "1.5.0"
        if collaboration_work_mode(collab) != "frozen":
            raise ValueError("先由已登记统筹会话冻结新工作，再执行协议回滚")
        manifest = Path(manifest_argument).expanduser()
        if not manifest.is_absolute():
            manifest = collab.parents[1] / manifest
        manifest = manifest.resolve(strict=True)
        backup_parent = (collab / "升级备份").resolve(strict=True)
        manifest.relative_to(backup_parent)
        if manifest.name != "rollback-manifest.json" or manifest.is_symlink() or not manifest.is_file():
            raise ValueError("rollback manifest 必须是升级备份中的普通 rollback-manifest.json")
        backup_root = manifest.parent
        operation_id = backup_root.name
        if not re.fullmatch(r"UPGRADE-[0-9]{8}T[0-9]{6}-[A-F0-9]{8}", operation_id):
            raise ValueError("升级备份 operation_id 无效")
        if not active_150_rollback:
            slice_control = collab / ".locks" / "slice-control.json"
            slice_payload = strict_json_loads(read_utf8(slice_control), source=str(slice_control))
            if not isinstance(slice_payload, dict) or slice_payload.get("active_slice") is not None:
                raise ValueError("仍有活动切片，拒绝回滚")
            for task_path in sorted((collab / "tasks").glob("TASK-*.json")):
                payload = strict_json_loads(read_utf8(task_path), source=str(task_path))
                if isinstance(payload, dict) and payload.get("schema_version") == 2:
                    raise ValueError(f"已创建 1.5 TASK，拒绝回滚: {task_path.name}")
        if (collab / UPGRADE_TRANSACTION_FILE).exists():
            raise ValueError("已有升级事务标记，拒绝叠加显式回滚")
        manifest_bytes = manifest.read_bytes()
        payload = strict_json_loads(manifest_bytes.decode("utf-8"), source=str(manifest))
        if (
            not isinstance(payload, dict) or payload.get("schema_version") != 2
            or payload.get("operation") != "upgrade" or payload.get("operation_id") != operation_id
            or payload.get("target_protocol") != PROTOCOL_VERSION
        ):
            raise ValueError("rollback manifest 身份或目标协议无效")
        if (
            upgrade_identity.get("operation_id") != operation_id
            or upgrade_identity.get("source_protocol") != payload.get("source_protocol")
            or upgrade_identity.get("target_protocol") != payload.get("target_protocol")
            or upgrade_identity.get("rollback_manifest_sha256") != hashlib.sha256(manifest_bytes).hexdigest()
            or upgrade_identity.get("target_state_sha256")
            != rollback_surface_state_sha256(collab.parents[1], payload)
        ):
            raise ValueError("rollback manifest 不属于当前升级代次或当前受管状态")
        marker_payload = {
            "schema_version": 1,
            "kind": "upgrade",
            "operation_id": operation_id,
            "phase": "prepared",
            "backup_dir": f"升级备份/{operation_id}",
            "rollback_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        }
        write_utf8_atomic(
            collab / UPGRADE_TRANSACTION_FILE,
            json.dumps(marker_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            mode=0o600,
        )
        recover_upgrade_transaction(collab, announce=False)
    except RuntimeError as exc:
        print(f"RECOVERY_BLOCKED | {exc}", file=sys.stderr)
        return 10
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ROLLBACK_DENIED | {exc}", file=sys.stderr)
        return 9
    print(f"ROLLBACK_OK | {operation_id} | restored_protocol:{payload['source_protocol']}")
    return 0


def run_upgrade(collab: Path, *, role_policy_overlay_file: str = "") -> int:
    existing_state = validate_existing_collaboration(collab, require_current=False)
    if existing_state is None:
        return 3
    registry_text, roles = existing_state
    try:
        preflight_upgrade_tasks(collab, roles)
    except ValueError as exc:
        print(f"升级已停止:任务真值未通过完整性预检: {exc}", file=sys.stderr)
        return 9
    try:
        previous_protocol_version = read_protocol_version_for_upgrade(collab)
        previous_protocol = read_protocol_payload_for_upgrade(collab)
        role_policy_overlays, changed_role_policies = role_policy_overlays_for_upgrade(
            collab, roles, previous_protocol,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"升级已停止:协议版本真值无效: {exc}", file=sys.stderr)
        return 9
    incoming_overlays: dict[str, dict[str, object]] = {}
    if role_policy_overlay_file:
        try:
            overlay_source = Path(role_policy_overlay_file).expanduser()
            if overlay_source.is_symlink():
                raise ValueError("岗位职责追加层输入不能是符号链接")
            overlay_source = overlay_source.resolve(strict=True)
            if not overlay_source.is_file():
                raise ValueError("岗位职责追加层输入必须是普通文件")
            incoming_payload = strict_json_loads(read_utf8(overlay_source), source=str(overlay_source))
            incoming_overlays = validate_role_policy_overlays(incoming_payload, roles)
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"升级已停止:岗位职责追加层输入无效: {exc}", file=sys.stderr)
            return 9
    unresolved_changes = [role_id for role_id in changed_role_policies if role_id not in incoming_overlays]
    if unresolved_changes:
        print(
            "升级已停止:检测到岗位说明相对受管基线发生变化，不能猜测项目想保留什么: "
            + ", ".join(unresolved_changes)
            + "。请将已确认的项目新增职责迁移为只追加 JSON，再传 --role-policy-overlay-file。",
            file=sys.stderr,
        )
        return 9
    role_policy_overlays.update(incoming_overlays)
    date = dt.date.today().isoformat()
    profile = registry_value(registry_text, "项目类型", "已有项目")
    session_mode = registry_value(registry_text, "会话创建模式", "manual")
    if session_mode not in {"auto", "manual"}:
        session_mode = "manual"
    try:
        task_payloads = iter_upgrade_task_payloads(collab)
        claimed_legacy = [
            source.name for source, task in task_payloads
            if previous_protocol_version != PROTOCOL_VERSION
            and task.get("schema_version") == 1
            and task.get("execution_state") == "claimed"
        ]
        if claimed_legacy:
            raise ValueError(
                "1.4 TASK 仍在 claimed，不能在升级后把在办 owner 隐入冷历史："
                + ", ".join(claimed_legacy)
            )
        state_payload = migrated_session_state(
            collab, roles, registry_text, date, session_mode, task_payloads,
        )
        legacy_archive_recovery_path, legacy_archive_recovery = load_legacy_archive_recovery(
            collab, task_payloads, previous_protocol_version,
        )
        legacy_archive_repairs, pending_archive_actions = scan_archive_recovery_actions(
            task_payloads, previous_protocol_version, legacy_archive_recovery["entries"],
        )
        target_legacy_archive_recovery = merge_legacy_archive_recovery(
            legacy_archive_recovery, legacy_archive_repairs,
        )
        (
            legacy_closeout_index_path,
            legacy_closeout_task_ids,
            legacy_archive_recovery_task_ids,
        ) = load_legacy_closeout_index(
            collab, task_payloads, target_legacy_archive_recovery["entries"],
            previous_protocol_version,
        )
        target_slice_control = migrated_slice_control(
            collab, previous_protocol_version, task_payloads,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"升级已停止:会话或归档真值未通过预检: {exc}", file=sys.stderr)
        return 9
    dispatch_control_path = collab / ".locks" / "dispatch-control.json"
    dispatch_control_ready = False
    dispatch_mode = ""
    if dispatch_control_path.is_symlink():
        print("派单控制不能是符号链接，已拒绝升级。", file=sys.stderr)
        return 3
    if dispatch_control_path.exists():
        try:
            dispatch_mode = collaboration_work_mode(collab)
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"升级已停止:派单控制无效: {exc}", file=sys.stderr)
            return 9
        dispatch_control_ready = True
    if (
        previous_protocol_version == PROTOCOL_VERSION
        and current_runtime_complete(collab)
        and dispatch_control_ready
    ):
        print(f"UPGRADE_NOT_NEEDED | protocol:{PROTOCOL_VERSION}")
        for _, task_id, thread_id in legacy_archive_repairs:
            print(
                f"ARCHIVE_THREAD_REQUIRED:{thread_id} | task:{task_id}"
                " | reason:legacy-unverified-archive"
            )
        emit_pending_archive_actions(pending_archive_actions)
        return 0
    if not dispatch_control_ready or dispatch_mode != "frozen":
        print(
            "升级已停止:P0_FREEZE_REQUIRED | 派单控制必须存在且由已登记统筹会话先冻结新工作，"
            "不得在升级内部静默创建 normal 控制。",
            file=sys.stderr,
        )
        return 9

    pending_legacy = []
    for key in roles:
        department = collab / "部门" / ROLE_DEFS[key]["name"]
        if not plain_path_within(department, collab, kind="dir"):
            print(f"部门目录缺失或不安全: {department}", file=sys.stderr)
            return 3
        inbox = department / "收件箱.md"
        if inbox.is_symlink() or (inbox.exists() and not plain_path_within(inbox, collab, kind="file")):
            print(f"收件箱路径不安全: {inbox}", file=sys.stderr)
            return 3
        if inbox.exists() and legacy_inbox_has_tasks(read_utf8(inbox)):
            pending_legacy.append(ROLE_DEFS[key]["name"])
    if pending_legacy:
        print(
            "升级已停止:以下旧收件箱仍有待办,"
            "请先完成或人工转存后再升级,"
            "避免在转换任务真相源时丢失内容: " + ", ".join(pending_legacy),
            file=sys.stderr,
        )
        return 9

    session_data = registry_data_from_session_state(state_payload)
    active_roles = [
        role for role in roles
        if state_payload["departments"][ROLE_DEFS[role]["name"]].get("active", True)
    ]
    scripts_root = collab / "scripts"
    backup_parent = collab / "升级备份"
    guarded_directories = (
        (scripts_root, "scripts"),
        (backup_parent, "升级备份"),
        (collab / ".locks", ".locks"),
        (collab / "tasks", "tasks"),
        (collab / "模板", "模板"),
        (collab / "专项结论", "专项结论"),
    )
    for directory, label in guarded_directories:
        if directory.is_symlink() or (directory.exists() and not plain_path_within(directory, collab, kind="dir")):
            print(f"{label} 目录越界、经过符号链接或不是普通目录,已拒绝升级。", file=sys.stderr)
            return 3
    managed: dict[Path, tuple[str, int | None]] = {
        collab / PROTOCOL_FILE: (protocol_payload(date), 0o600),
        collab / "README.md": (readme_markdown(date), None),
        collab / "部门表.md": (
            registry_markdown(
                roles, state_payload["profile"], state_payload["created_at"],
                state_payload["session_mode"], session_data,
            ), None,
        ),
        collab / "路由表.md": (route_table_markdown(active_roles, date), None),
        collab / "会话启动清单.md": (session_startup_markdown(active_roles, session_mode, date), None),
        collab / "会话启动状态.json": (json.dumps(state_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", 0o600),
        collab / "任务交接模板.md": (handoff_template_markdown(), None),
        collab / "模板" / "工作报告.md": (work_reports_readme_markdown({"name": "填写部门"}, date), None),
        collab / "模板" / "审核报告.md": (reports_readme_markdown({"name": "填写审核部门"}, date), None),
        collab / "模板" / "专项结论.md": (special_conclusion_readme(date), None),
        collab / "scripts" / "agent_team_log.py": (log_writer_script(), 0o755),
        collab / "scripts" / "agent_team_task.py": (task_writer_script(), 0o755),
        collab / "scripts" / "agent_team_session.py": (session_state_script(), 0o755),
        collab / "scripts" / "agent_team_temporary.py": (temporary_executor_script(), 0o755),
    }
    slice_control_path = collab / ".locks" / "slice-control.json"
    if slice_control_path.is_symlink():
        print("切片控制不能是符号链接，已拒绝升级。", file=sys.stderr)
        return 3
    managed[slice_control_path] = (target_slice_control, 0o600)
    if target_legacy_archive_recovery["entries"] or legacy_archive_recovery_path.exists():
        managed[legacy_archive_recovery_path] = (
            json.dumps(target_legacy_archive_recovery, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            0o600,
        )
    managed[legacy_closeout_index_path] = (
        legacy_closeout_index_payload(
            legacy_closeout_task_ids, legacy_archive_recovery_task_ids,
        ),
        0o600,
    )
    mistakes = collab / "错题集.md"
    if mistakes.is_symlink() or (mistakes.exists() and not plain_path_within(mistakes, collab, kind="file")):
        print("错题集路径不安全，已拒绝升级。", file=sys.stderr)
        return 3
    if not mistakes.exists():
        managed[mistakes] = (cuoti_markdown(date), None)
    obsolete = [collab / "读取路由规则.md", collab / "scripts" / "agent_team_read.py"]
    for key in roles:
        role = ROLE_DEFS[key]
        department = collab / "部门" / role["name"]
        if not plain_path_within(department, collab, kind="dir"):
            print(f"部门目录不安全: {department}", file=sys.stderr)
            return 3
        managed[department / "岗位说明.md"] = (
            role_markdown(key, role, date, role_policy_overlays.get(key)), None,
        )
        managed[department / "上岗引导.md"] = (bootstrap_markdown(key, role), None)
        handoff = department / "交接班文档.md"
        if handoff.is_symlink() or (handoff.exists() and not plain_path_within(handoff, collab, kind="file")):
            print(f"交接班文档路径不安全: {handoff}", file=sys.stderr)
            return 3
        if not handoff.exists():
            managed[handoff] = (state_markdown(key, role, date), None)
        inbox = department / "收件箱.md"
        if not inbox.exists() or "<!-- agent-team task index; use scripts/agent_team_task.py -->" not in read_utf8(inbox):
            managed[inbox] = (inbox_markdown(key, role, date), None)

    operation_id = "UPGRADE-" + dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8].upper()
    backup_root = backup_parent / operation_id
    project = collab.parents[1]
    project_guide = collab.parent / "agent-guide.md"
    if project_guide.is_symlink() or (project_guide.exists() and not plain_path_within(project_guide, project, kind="file")):
        print("项目 agent-guide.md 越界、经过符号链接或不是普通文件,已拒绝升级。", file=sys.stderr)
        return 3
    try:
        backup_parent.mkdir(parents=False, exist_ok=True)
        if not plain_path_within(backup_parent, collab, kind="dir"):
            raise ValueError("升级备份目录不安全")
        backup_root.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        print(f"无法创建升级备份: {exc}", file=sys.stderr)
        return 6
    originals: dict[Path, dict[str, object]] = {}
    rollback_directories = (
        collab / "tasks",
        collab / ".locks",
        collab / "scripts",
        collab / "模板",
        collab / "专项结论",
    )
    directory_modes: dict[Path, int | None] = {
        path: stat.S_IMODE(path.stat().st_mode) if path.exists() else None
        for path in rollback_directories
    }
    migrated_task_destinations: list[Path] = []
    try:
        tasks = collab / "tasks"
        legacy_tasks: list[tuple[Path, Path]] = []
        if tasks.is_symlink():
            raise ValueError("tasks 路径不能是符号链接")
        if tasks.exists():
            if tasks.is_symlink() or not tasks.is_dir() or not plain_path_within(tasks, collab, kind="dir"):
                raise ValueError("tasks 路径不安全")
            for state in ("queued", "claimed", "blocked", "waiting_input", "completed", "acknowledged"):
                state_dir = tasks / state
                if not state_dir.exists():
                    directory_modes[state_dir] = None
                    continue
                if state_dir.is_symlink() or not state_dir.is_dir() or not plain_path_within(state_dir, collab, kind="dir"):
                    raise ValueError(f"任务状态目录不安全: {state}")
                directory_modes[state_dir] = stat.S_IMODE(state_dir.stat().st_mode)
                for source in state_dir.iterdir():
                    if source.is_symlink() or not source.is_file() or not source.name.startswith("TASK-") or source.suffix != ".json":
                        raise ValueError(f"旧任务目录含非标准文件: {source}")
                    try:
                        payload = strict_json_loads(read_utf8(source), source=str(source))
                    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                        raise ValueError(f"旧任务 JSON 损坏: {source}: {exc}") from exc
                    if not isinstance(payload, dict) or payload.get("task_id") != source.stem:
                        raise ValueError(f"旧任务 ID 与文件名不一致: {source}")
                    if payload.get("execution_state") != state:
                        raise ValueError(f"旧任务状态与目录不一致: {source}")
                    destination = tasks / source.name
                    if destination.exists() or any(existing == destination for _, existing in legacy_tasks):
                        raise ValueError(f"旧任务迁移目标冲突: {destination}")
                    legacy_tasks.append((source, destination))
        if project_guide.exists():
            guide_backup = backup_root / "project-docs-agent-guide.md"
            shutil.copy2(project_guide, guide_backup)
            originals[project_guide] = upgrade_backup_record(project_guide, guide_backup)
        else:
            originals[project_guide] = upgrade_backup_record(project_guide, None)
        if dispatch_control_path.is_symlink():
            raise ValueError("派单控制不能是符号链接")
        if dispatch_control_path.exists():
            if not dispatch_control_path.is_file() or not plain_path_within(
                dispatch_control_path, collab, kind="file"
            ):
                raise ValueError("派单控制路径不安全")
            dispatch_backup = backup_root / dispatch_control_path.relative_to(collab)
            dispatch_backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dispatch_control_path, dispatch_backup)
            originals[dispatch_control_path] = upgrade_backup_record(
                dispatch_control_path, dispatch_backup
            )
        else:
            originals[dispatch_control_path] = upgrade_backup_record(dispatch_control_path, None)
        for path in [*managed, *obsolete]:
            if path.exists():
                if path.is_symlink() or not path.is_file():
                    raise ValueError(f"受管文件不安全: {path}")
                relative = path.relative_to(collab)
                backup = backup_root / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup)
                originals[path] = upgrade_backup_record(path, backup)
            else:
                originals[path] = upgrade_backup_record(path, None)
        hot_state_path = collab / ".locks" / "hot-state.json"
        if hot_state_path not in originals:
            if hot_state_path.exists():
                if hot_state_path.is_symlink() or not plain_path_within(hot_state_path, collab, kind="file"):
                    raise ValueError("hot-state 路径不安全")
                hot_backup = backup_root / hot_state_path.relative_to(collab)
                hot_backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(hot_state_path, hot_backup)
                originals[hot_state_path] = upgrade_backup_record(hot_state_path, hot_backup)
            else:
                originals[hot_state_path] = upgrade_backup_record(hot_state_path, None)
        for source, destination in legacy_tasks:
            relative = source.relative_to(collab)
            backup = backup_root / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, backup)
            originals[source] = upgrade_backup_record(source, backup)
            originals[destination] = upgrade_backup_record(destination, None)
        write_upgrade_rollback_manifest(
            backup_root, project, originals, directory_modes,
            operation_id=operation_id,
            source_protocol=previous_protocol_version or "legacy-unversioned",
        )
        rollback_manifest = backup_root / "rollback-manifest.json"
        upgrade_marker = collab / UPGRADE_TRANSACTION_FILE
        marker_payload = {
            "schema_version": 1,
            "kind": "upgrade",
            "operation_id": operation_id,
            "phase": "prepared",
            "backup_dir": f"升级备份/{operation_id}",
            "rollback_manifest_sha256": hashlib.sha256(rollback_manifest.read_bytes()).hexdigest(),
        }
        write_utf8_atomic(
            upgrade_marker,
            json.dumps(marker_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            mode=0o600,
        )
        marker_payload["phase"] = "applying"
        write_utf8_atomic(
            upgrade_marker,
            json.dumps(marker_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            mode=0o600,
        )
        locks = collab / ".locks"
        if locks.exists() and (locks.is_symlink() or not locks.is_dir()):
            raise ValueError(".locks 路径不安全")
        tasks.mkdir(exist_ok=True)
        locks.mkdir(mode=0o700, exist_ok=True)
        if not dispatch_control_path.exists() or collaboration_work_mode(collab) != "frozen":
            raise ValueError("P0_FREEZE_REQUIRED | 升级应用阶段派单控制缺失或不再 frozen")
        scripts_root.mkdir(exist_ok=True)
        (collab / "模板").mkdir(exist_ok=True)
        (collab / "专项结论").mkdir(exist_ok=True)
        if not plain_path_within(scripts_root, collab, kind="dir"):
            raise ValueError("scripts 目录不安全")
        for source, destination in legacy_tasks:
            os.replace(source, destination)
            migrated_task_destinations.append(destination)
        for state in ("queued", "claimed", "blocked", "waiting_input", "completed", "acknowledged"):
            state_dir = tasks / state
            if state_dir.exists():
                state_dir.rmdir()
        # 协议版本是整次升级的提交标记。其他文件全部落盘后才最后写它;
        # 进程在此之前崩溃时,旧/缺失版本会迫使下次操作重新升级。
        for path, (content, mode) in managed.items():
            if path.name == PROTOCOL_FILE:
                continue
            write_utf8_atomic(path, content, mode=mode)
        for path in obsolete:
            if path.exists():
                if path.is_symlink() or not path.is_file():
                    raise ValueError(f"废弃读取工具路径不安全: {path}")
                path.unlink()
        append_agent_guide(project)
        _, protocol_mode = managed[collab / PROTOCOL_FILE]
        write_utf8_atomic(
            collab / PROTOCOL_FILE,
            protocol_payload(
                date,
                managed_manifest_from_disk(collab, roles),
                role_policy_overlays,
                {
                    "operation_id": operation_id,
                    "source_protocol": previous_protocol_version or "legacy-unversioned",
                    "target_protocol": PROTOCOL_VERSION,
                    "target_state_sha256": rollback_surface_state_sha256(
                        project,
                        strict_json_loads(
                            rollback_manifest.read_text(encoding="utf-8"),
                            source=str(rollback_manifest),
                        ),
                    ),
                    "rollback_manifest_sha256": marker_payload["rollback_manifest_sha256"],
                },
            ),
            mode=protocol_mode,
        )
        marker_payload["phase"] = "protocol_written"
        write_utf8_atomic(
            upgrade_marker,
            json.dumps(marker_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            mode=0o600,
        )
        if not current_runtime_complete(collab):
            raise ValueError("升级后运行时完整性校验失败")
        task_runtime_path = collab / "scripts" / "agent_team_task.py"
        task_runtime_spec = importlib.util.spec_from_file_location(
            f"agent_team_upgrade_rebuild_{operation_id.replace('-', '_')}",
            task_runtime_path,
        )
        if task_runtime_spec is None or task_runtime_spec.loader is None:
            raise ValueError("升级后无法载入任务运行时重建热索引")
        task_runtime = importlib.util.module_from_spec(task_runtime_spec)
        task_runtime_spec.loader.exec_module(task_runtime)
        task_runtime.init_layout()
        task_runtime.render_inboxes(force=True)
        final_manifest_payload = strict_json_loads(
            rollback_manifest.read_text(encoding="utf-8"),
            source=str(rollback_manifest),
        )
        write_utf8_atomic(
            collab / PROTOCOL_FILE,
            protocol_payload(
                date,
                managed_manifest_from_disk(collab, roles),
                role_policy_overlays,
                {
                    "operation_id": operation_id,
                    "source_protocol": previous_protocol_version or "legacy-unversioned",
                    "target_protocol": PROTOCOL_VERSION,
                    "target_state_sha256": rollback_surface_state_sha256(
                        project, final_manifest_payload,
                    ),
                    "rollback_manifest_sha256": marker_payload["rollback_manifest_sha256"],
                },
            ),
            mode=protocol_mode,
        )
        if not current_runtime_complete(collab):
            raise ValueError("升级后热索引重建改变了受管或回滚绑定状态")
        upgrade_marker.unlink()
        fsync_dir(collab)
    except Exception as exc:
        try:
            restore_upgrade_originals(originals, directory_modes)
            upgrade_marker = collab / UPGRADE_TRANSACTION_FILE
            if upgrade_marker.exists() and not upgrade_marker.is_symlink() and upgrade_marker.is_file():
                upgrade_marker.unlink()
                fsync_dir(collab)
        except Exception as rollback_exc:
            print(
                f"升级失败，且回滚未完成: upgrade_error={exc}; rollback_error={rollback_exc}; "
                f"backup={backup_root}",
                file=sys.stderr,
            )
            return 10
        print(f"升级失败，已按回滚清单原子恢复并逐项校验: {exc}; backup={backup_root}", file=sys.stderr)
        return 6
    print(f"UPGRADE_OK | {operation_id} | protocol:{PROTOCOL_VERSION} | backup:{backup_root}")
    if role_policy_overlays:
        print("ROLE_POLICY_OVERLAYS_APPLIED | " + ",".join(sorted(role_policy_overlays)))
    for _, task_id, thread_id in legacy_archive_repairs:
        print(
            f"ARCHIVE_THREAD_REQUIRED:{thread_id} | task:{task_id}"
            f" | reason:legacy-unverified-archive"
        )
    emit_pending_archive_actions(pending_archive_actions)
    return 0


def run_deactivate_roles(collab: Path, role_ids: list[str], evidence: str) -> int:
    existing_state = validate_existing_collaboration(collab)
    if existing_state is None:
        return 3
    registry_text, all_roles = existing_state
    if not role_ids or validate_roles(role_ids):
        return 4
    if len(role_ids) != len(set(role_ids)):
        print("停用角色不能重复。", file=sys.stderr)
        return 4
    if "lead" in role_ids:
        print("统筹部不能停用。", file=sys.stderr)
        return 4
    evidence = evidence.strip()
    if not evidence or len(evidence) > 1000 or any(ord(char) < 32 and char not in "\t\n\r" for char in evidence):
        print("--deactivation-evidence 必须提供不超过 1000 字的用户确认依据。", file=sys.stderr)
        return 4
    paths = {
        "部门表.md": collab / "部门表.md",
        "会话启动清单.md": collab / "会话启动清单.md",
        "路由表.md": collab / "路由表.md",
        "会话启动状态.json": collab / "会话启动状态.json",
        PROTOCOL_FILE: collab / PROTOCOL_FILE,
    }
    try:
        originals = {name: read_utf8(path) for name, path in paths.items()}
        state = strict_json_loads(originals["会话启动状态.json"], source=str(paths["会话启动状态.json"]))
        protocol_before = strict_json_loads(originals[PROTOCOL_FILE], source=str(paths[PROTOCOL_FILE]))
        state_roles = validate_current_session_payload(state)
        if set(state_roles) != set(all_roles):
            raise ValueError("会话状态与部门表的历史角色集合不一致")
        for role_id in role_ids:
            department = ROLE_DEFS[role_id]["name"]
            item = state["departments"].get(department)
            if not isinstance(item, dict) or not item.get("active", False):
                raise ValueError(f"部门未启用或不存在: {department}")
            if item["step"] != "pending" or item["thread_id"] or item["previous_thread_id"]:
                raise ValueError(f"部门仍有会话事实，须先完成归档并恢复为 pending: {department}")
        for task_path in sorted((collab / "tasks").glob("TASK-*.json")):
            task = strict_json_loads(read_utf8(task_path), source=str(task_path))
            if not isinstance(task, dict):
                raise ValueError(f"TASK 根节点无效: {task_path.name}")
            if (
                task.get("department") in {ROLE_DEFS[role]["name"] for role in role_ids}
                and task.get("resolution") is None
                and task.get("execution_state") != "acknowledged"
            ):
                raise ValueError(f"部门仍有未核收任务: {task_path.name}")
        active_roles = [
            role for role in all_roles
            if role not in role_ids and state["departments"][ROLE_DEFS[role]["name"]].get("active", True)
        ]
        layers = {ROLE_DEFS[role]["layer"] for role in active_roles}
        if layers != {"management", "execution", "audit"}:
            raise ValueError("停用后会破坏管理、执行、审核三层最小结构")
        date = dt.date.today().isoformat()
        for role_id in role_ids:
            item = state["departments"][ROLE_DEFS[role_id]["name"]]
            history = list(item["lifecycle_history"])
            history.append({
                "event": "deactivated", "at": date, "actor": "scaffold/deactivate-roles",
                "evidence": evidence, "thread_id": "",
            })
            item["lifecycle_history"] = history
            item["active"] = False
            item["note"] = "用户确认停用: " + evidence
            item["evidence"] = evidence
            item["updated_at"] = date
        state["updated_at"] = date
        profile = state["profile"]
        session_mode = state["session_mode"]
        if session_mode not in {"auto", "manual"}:
            session_mode = "manual"
        updated = {
            "部门表.md": registry_markdown(
                all_roles, profile, state["created_at"], session_mode, registry_data_from_session_state(state),
            ),
            "会话启动清单.md": session_startup_markdown(active_roles, session_mode, date),
            "路由表.md": route_table_markdown(active_roles, date),
            "会话启动状态.json": json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        }
        operation_id = "DEACT-" + dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8].upper()
        marker = collab / DEACTIVATE_TRANSACTION_FILE
        transaction = {
            "schema_version": 1, "kind": "deactivate_roles", "operation_id": operation_id,
            "phase": "applying", "roles": role_ids, "originals": originals,
        }
        write_utf8_atomic(marker, json.dumps(transaction, ensure_ascii=False, indent=2, sort_keys=True) + "\n", mode=0o600)
        for name, content in updated.items():
            write_utf8_atomic(paths[name], content, mode=0o600 if name == "会话启动状态.json" else None)
        write_utf8_atomic(
            paths[PROTOCOL_FILE],
            protocol_payload(
                date, managed_manifest_from_disk(collab, all_roles),
                protocol_before.get(ROLE_POLICY_OVERRIDE_FIELD, {}) if isinstance(protocol_before, dict) else {},
            ),
            mode=0o600,
        )
        transaction["phase"] = "committed"
        write_utf8_atomic(marker, json.dumps(transaction, ensure_ascii=False, indent=2, sort_keys=True) + "\n", mode=0o600)
        marker.unlink()
        fsync_dir(collab)
    except Exception as exc:
        try:
            recover_deactivate_roles_transaction(collab, announce=False)
        except Exception as recovery_exc:
            print(f"停用部门失败且自动回滚失败: {exc}; {recovery_exc}", file=sys.stderr)
            return 10
        print(f"停用部门失败，已回滚: {exc}", file=sys.stderr)
        return 6
    print(f"DEACTIVATE_ROLES_OK | roles={','.join(role_ids)} | evidence_recorded=true")
    return 0


def run_add_roles(collab: Path, add_roles: list[str]) -> int:
    """增量模式:在已有协作层追加部门。"""
    if not collab.exists():
        print("docs/collaboration/ 不存在,无法增量。请先用 --roles 正常创建。", file=sys.stderr)
        return 3
    try:
        if collaboration_work_mode(collab) == "frozen":
            print("P0_FREEZE_ACTIVE | add-roles 已冻结；止血期间不得扩编团队", file=sys.stderr)
            return 5
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"派单控制无效，已拒绝扩编: {exc}", file=sys.stderr)
        return 5
    err = validate_roles(add_roles)
    if err:
        return err
    existing_state = validate_existing_collaboration(collab)
    if existing_state is None:
        return 3
    registry_text, existing_roles = existing_state
    registered = set(existing_roles)
    session_state_path = collab / "会话启动状态.json"
    try:
        existing_session_payload = strict_json_loads(
            read_utf8(session_state_path), source=str(session_state_path),
        )
        session_roles = validate_current_session_payload(existing_session_payload)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"会话启动状态格式无效,已拒绝增量修改: {exc}", file=sys.stderr)
        return 3
    if set(session_roles) != set(existing_roles):
        print("会话启动状态与部门表的部门集合不一致,已拒绝增量修改。", file=sys.stderr)
        return 3

    today = dt.date.today()
    date = today.isoformat()
    depts_root = collab / "部门"
    depts_root.mkdir(parents=True, exist_ok=True)

    created, new_roles, repaired, reactivated, skipped = [], [], [], [], []
    for key in add_roles:
        role = ROLE_DEFS[key]
        department_path = depts_root / role["name"]
        in_registry = key in registered
        on_disk = department_path.exists()
        if in_registry and on_disk:
            item = existing_session_payload["departments"].get(role["name"], {})
            if item.get("active", True):
                skipped.append(key)
            else:
                reactivated.append(key)
            continue
        if not in_registry and on_disk:
            print(f"协作层状态不一致:部门目录已存在但部门表未登记 {key},已拒绝增量修改。", file=sys.stderr)
            return 3
        created.append(key)
        if in_registry:
            repaired.append(key)
        else:
            new_roles.append(key)

    # 先在临时目录完整生成所有新部门,再更新路由表;任一步失败时回滚本次部门。
    if created or reactivated:
        registry = collab / "部门表.md"
        startup = collab / "会话启动清单.md"
        routes = collab / "路由表.md"
        session_state = collab / "会话启动状态.json"
        protocol = collab / PROTOCOL_FILE
        if (
            not plain_path_within(routes, collab, kind="file")
            or not plain_path_within(session_state, collab, kind="file")
            or not plain_path_within(protocol, collab, kind="file")
        ):
            print("协作层缺少当前版本的路由表或会话启动状态,请先执行升级。", file=sys.stderr)
            return 3
        try:
            startup_text = read_utf8(startup)
            routes_text = read_utf8(routes)
            session_text = read_utf8(session_state)
            protocol_text = read_utf8(protocol)
            session_payload = strict_json_loads(session_text, source=str(session_state))
            protocol_payload_before_add = strict_json_loads(protocol_text, source=str(protocol))
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"无法读取当前路由/会话状态: {exc}", file=sys.stderr)
            return 3
        all_roles = existing_roles + [key for key in new_roles if key not in existing_roles]
        try:
            session_roles = validate_current_session_payload(session_payload)
        except ValueError as exc:
            print(f"会话启动状态格式无效,已拒绝增量修改: {exc}", file=sys.stderr)
            return 3
        if set(session_roles) != set(existing_roles):
            print("会话启动状态与部门表的部门集合不一致,已拒绝增量修改。", file=sys.stderr)
            return 3
        if not isinstance(protocol_payload_before_add, dict):
            print("协议版本 JSON 根节点无效,已拒绝增量修改。", file=sys.stderr)
            return 3
        try:
            existing_role_overlays = validate_role_policy_overlays(
                protocol_payload_before_add.get(ROLE_POLICY_OVERRIDE_FIELD, {}), existing_roles,
            )
        except ValueError as exc:
            print(f"岗位职责项目追加层登记无效,已拒绝增量修改: {exc}", file=sys.stderr)
            return 3
        departments_state = session_payload["departments"]
        notification_mode = registry_value(registry_text, "会话创建模式", "manual")
        if notification_mode not in {"auto", "manual"}:
            notification_mode = "manual"
        for key in new_roles:
            role = ROLE_DEFS[key]
            departments_state[role["name"]] = {
                "role_id": key,
                "active": True,
                "notification_mode": notification_mode,
                "step": "pending",
                "thread_id": "",
                "previous_thread_id": "",
                "failed_from": "",
                "evidence": "",
                "operation_id": "INIT-" + hashlib.sha256(f"{date}:{key}".encode("utf-8")).hexdigest()[:10].upper(),
                "note": "",
                "updated_at": date,
                "lifecycle_history": [],
            }
        for key in reactivated:
            item = departments_state[ROLE_DEFS[key]["name"]]
            history = list(item["lifecycle_history"])
            history.append({
                "event": "reactivated", "at": date, "actor": "scaffold/add-roles",
                "evidence": "explicit --add-roles reactivation request", "thread_id": "",
            })
            item["lifecycle_history"] = history
            item["active"] = True
            item["note"] = "通过 --add-roles 重新启用"
            item["evidence"] = "explicit --add-roles reactivation request"
            item["updated_at"] = date
        session_payload["protocol_version"] = PROTOCOL_VERSION
        session_payload["updated_at"] = date
        session_payload["role_order"] = all_roles
        profile = session_payload["profile"]
        session_data = registry_data_from_session_state(session_payload)
        # 增量路径必须与同一天直接新建同样的团队收敛到同一份派生文档。
        # 会话状态 JSON 才是真值，不再向部门表或启动清单追加一段“待登记”副本。
        updated_registry = registry_markdown(
            all_roles, profile, session_payload["created_at"], notification_mode, session_data,
        )
        active_roles = [
            role for role in all_roles
            if session_payload["departments"][ROLE_DEFS[role]["name"]].get("active", True)
        ]
        updated_startup = session_startup_markdown(active_roles, notification_mode, date)
        updated_routes = route_table_markdown(active_roles, date)
        updated_session = json.dumps(session_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        build_root = Path(tempfile.mkdtemp(prefix=".add-roles-build-", dir=collab))
        marker = collab / ADD_TRANSACTION_FILE
        operation_id = "ADD-" + dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8].upper()
        try:
            generated_files: dict[str, dict[str, str]] = {}
            for key in created:
                create_department(build_root, key, ROLE_DEFS[key], date)
                sentinel_payload = {
                    "schema_version": 1,
                    "kind": "agent-team-add-role-owned",
                    "operation_id": operation_id,
                    "role_id": key,
                }
                department = build_root / ROLE_DEFS[key]["name"]
                write_utf8_atomic(
                    department / ADD_ROLE_SENTINEL,
                    json.dumps(sentinel_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    mode=0o600,
                )
                generated_files[key] = {
                    entry.name: hashlib.sha256(entry.read_bytes()).hexdigest()
                    for entry in department.iterdir() if entry.is_file()
                }
            transaction = {
                "schema_version": 2,
                "kind": "add_roles",
                "operation_id": operation_id,
                "phase": "applying",
                "created_roles": created,
                "originals": {
                    "部门表.md": registry_text,
                    "会话启动清单.md": startup_text,
                    "路由表.md": routes_text,
                    "会话启动状态.json": session_text,
                    PROTOCOL_FILE: protocol_text,
                },
                "generated_files": generated_files,
            }
            write_utf8_atomic(marker, json.dumps(transaction, ensure_ascii=False, indent=2, sort_keys=True) + "\n", mode=0o600)
            for key in created:
                source = build_root / ROLE_DEFS[key]["name"]
                destination = depts_root / ROLE_DEFS[key]["name"]
                os.replace(source, destination)
            write_utf8_atomic(registry, updated_registry)
            write_utf8_atomic(startup, updated_startup)
            write_utf8_atomic(routes, updated_routes)
            write_utf8_atomic(session_state, updated_session, mode=0o600)
            write_utf8_atomic(
                protocol,
                protocol_payload(
                    date,
                    managed_manifest_from_disk(collab, all_roles),
                    existing_role_overlays,
                ),
                mode=0o600,
            )
            transaction["phase"] = "committed"
            write_utf8_atomic(
                marker, json.dumps(transaction, ensure_ascii=False, indent=2, sort_keys=True) + "\n", mode=0o600,
            )
            for key in created:
                (depts_root / ROLE_DEFS[key]["name"] / ADD_ROLE_SENTINEL).unlink()
            marker.unlink()
            fsync_dir(collab)
        except Exception as exc:
            try:
                if marker.exists():
                    recover_add_roles_transaction(collab, announce=False)
            except Exception as recovery_exc:
                print(f"增量新增部门失败,自动回滚也失败: {exc}; {recovery_exc}", file=sys.stderr)
                return 10
            print(f"增量新增部门失败,已从持久化事务标记回滚: {exc}", file=sys.stderr)
            return 6
        finally:
            shutil.rmtree(build_root, ignore_errors=True)

    print(f"增量更新协作层: {collab}")
    if created:
        print("已新增部门:")
        for key in created:
            role = ROLE_DEFS[key]
            action = "修复缺失目录" if key in repaired else "新增并登记"
            print(f"- {LAYER_CN.get(role.get('layer', ''), '')} · {role['name']} ({key}) · {action}")
    if reactivated:
        print("已重新启用部门:")
        for key in reactivated:
            role = ROLE_DEFS[key]
            print(f"- {LAYER_CN.get(role.get('layer', ''), '')} · {role['name']} ({key})")
    if skipped:
        print(f"已存在跳过: {', '.join(skipped)}")
    print("部门表、路由表、会话启动清单与会话状态已同步;"
          "新部门保持 pending,实际上岗并登记会话 ID 后才启用。")
    return 0


def run_locked(args: argparse.Namespace, target: Path) -> int:
    collab = target / "docs" / "collaboration"

    if collab.exists():
        try:
            upgrade_marker = collab / UPGRADE_TRANSACTION_FILE
            add_marker = collab / ADD_TRANSACTION_FILE
            deactivate_marker = collab / DEACTIVATE_TRANSACTION_FILE
            if sum(path.exists() for path in (upgrade_marker, add_marker, deactivate_marker)) > 1:
                raise ValueError("同时存在多个协作事务标记，拒绝猜测恢复顺序")
            recover_upgrade_transaction(collab)
            recover_add_roles_transaction(collab)
            recover_deactivate_roles_transaction(collab)
        except RuntimeError as exc:
            print(f"RECOVERY_BLOCKED | 未完成的协作事务无法安全恢复: {exc}", file=sys.stderr)
            return 10
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"发现未完成的协作事务,但无法安全恢复: {exc}", file=sys.stderr)
            return 10

    if args.rollback_collaboration:
        if args.upgrade_collaboration or args.add_roles or args.deactivate_roles:
            print("--rollback-collaboration 不能与升级或增删部门参数同时使用。", file=sys.stderr)
            return 4
        if not collab.exists():
            print("docs/collaboration/ 不存在,无可回滚内容。", file=sys.stderr)
            return 3
        return run_explicit_rollback(collab, args.rollback_collaboration)

    if args.upgrade_collaboration:
        if args.add_roles or args.deactivate_roles:
            print("--upgrade-collaboration 不能与增删部门参数同时使用。", file=sys.stderr)
            return 4
        if not collab.exists():
            print("docs/collaboration/ 不存在,无可升级内容。", file=sys.stderr)
            return 3
        return run_upgrade(
            collab,
            role_policy_overlay_file=args.role_policy_overlay_file,
        )

    if args.role_policy_overlay_file:
        print("--role-policy-overlay-file 只能与 --upgrade-collaboration 一起使用。", file=sys.stderr)
        return 4

    # 增量模式
    add_roles = [item.strip() for item in args.add_roles.split(",") if item.strip()]
    deactivate_roles = [item.strip() for item in args.deactivate_roles.split(",") if item.strip()]
    if add_roles and deactivate_roles:
        print("--add-roles 与 --deactivate-roles 不能同时使用。", file=sys.stderr)
        return 4
    if add_roles:
        return run_add_roles(collab, add_roles)
    if deactivate_roles:
        return run_deactivate_roles(collab, deactivate_roles, args.deactivation_evidence)
    if args.deactivation_evidence:
        print("--deactivation-evidence 只能与 --deactivate-roles 一起使用。", file=sys.stderr)
        return 4

    # 全新创建模式
    if args.session_mode is None:
        print("未确认会话创建模式。请先确认 auto(工具自动创建会话) 或 manual(用户手动创建窗口),再传 --session-mode。", file=sys.stderr)
        return 5
    foundation_path: Path | None = None
    if args.create_minimal_foundation:
        if not args.allow_without_foundation:
            print("创建通用最小地基需要用户确认后同时传 --allow-without-foundation。", file=sys.stderr)
            return 2
        if args.foundation_file:
            print("--create-minimal-foundation 不能与 --foundation-file 同时使用。", file=sys.stderr)
            return 2
        valid_foundation_args, foundation_error = validate_minimal_foundation_args(args)
        if not valid_foundation_args:
            print(foundation_error, file=sys.stderr)
            return 2
        overview = target / "docs" / "overview.md"
        if overview.exists():
            print("docs/overview.md 已存在;为避免覆盖用户文件,请改用 --foundation-file 或先由用户决定如何处理。", file=sys.stderr)
            return 2
    else:
        try:
            foundation_path = validate_foundation_file(target, args.foundation_file)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        relative_foundation = foundation_path.relative_to(target.resolve(strict=True)).as_posix()
        if relative_foundation != "docs/spec.md" and not args.allow_without_foundation:
            print("非软件或自定义地基需要用户确认后同时传 --allow-without-foundation。", file=sys.stderr)
            return 2
    if collab.exists():
        print("docs/collaboration/ 已存在,为避免覆盖已中止。要追加部门请用 --add-roles,要小步更新请读取现有文件后手动改。", file=sys.stderr)
        return 3

    roles = [item.strip() for item in args.roles.split(",") if item.strip()]
    err = validate_roles(roles, require_layers=True)
    if err:
        return err

    today = dt.date.today()
    date = today.isoformat()
    docs_dir = target / "docs"
    docs_existed_before = docs_dir.exists()
    core_paths = [target / "docs" / name for name in ("overview.md", "progress.md", "agent-guide.md")]
    existed_before = {path: path.exists() for path in core_paths}
    build_collab: Path | None = None
    collab_published = False
    try:
        if args.create_minimal_foundation:
            create_minimal_foundation(
                target, args.profile, date,
                goal=args.foundation_goal,
                deliverable=args.foundation_deliverable,
                audience=args.foundation_audience,
                acceptance=args.foundation_acceptance,
                resources=args.foundation_resources,
                risks=args.foundation_risks,
            )
            validate_foundation_file(target, "docs/overview.md")
        else:
            ensure_core_docs(target, date)

        # 先在同一文件系统的临时目录完整生成,再原子替换为 collaboration/,避免失败后留下半套协作层。
        build_collab = Path(tempfile.mkdtemp(prefix=".collaboration-build-", dir=docs_dir))
        write_utf8_atomic(build_collab / "README.md", readme_markdown(date))
        write_utf8_atomic(build_collab / "部门表.md", registry_markdown(roles, args.profile, date, args.session_mode))
        write_utf8_atomic(build_collab / "路由表.md", route_table_markdown(roles, date))
        write_utf8_atomic(build_collab / "会话启动清单.md", session_startup_markdown(roles, args.session_mode, date))
        write_utf8_atomic(
            build_collab / "会话启动状态.json",
            session_state_payload(roles, date, args.session_mode, args.profile), mode=0o600,
        )
        write_utf8_atomic(build_collab / "错题集.md", cuoti_markdown(date))
        write_utf8_atomic(build_collab / "任务交接模板.md", handoff_template_markdown())
        templates_dir = build_collab / "模板"
        templates_dir.mkdir(parents=True)
        write_utf8_atomic(templates_dir / "工作报告.md", work_reports_readme_markdown({"name": "填写部门"}, date))
        write_utf8_atomic(templates_dir / "审核报告.md", reports_readme_markdown({"name": "填写审核部门"}, date))
        write_utf8_atomic(templates_dir / "专项结论.md", special_conclusion_readme(date))
        special_dir = build_collab / "专项结论"
        special_dir.mkdir(parents=True)
        scripts_dir = build_collab / "scripts"
        scripts_dir.mkdir(parents=True)
        log_writer = scripts_dir / "agent_team_log.py"
        write_utf8_atomic(log_writer, log_writer_script(), mode=0o755)
        task_writer = scripts_dir / "agent_team_task.py"
        write_utf8_atomic(task_writer, task_writer_script(), mode=0o755)
        session_writer = scripts_dir / "agent_team_session.py"
        write_utf8_atomic(session_writer, session_state_script(), mode=0o755)
        temporary_writer = scripts_dir / "agent_team_temporary.py"
        write_utf8_atomic(temporary_writer, temporary_executor_script(), mode=0o755)
        locks_dir = build_collab / ".locks"
        locks_dir.mkdir(mode=0o700)
        write_utf8_atomic(
            locks_dir / "dispatch-control.json",
            json.dumps(
                {"schema_version": 1, "mode": "normal", "updated_at": "", "history": []},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ) + "\n",
            mode=0o600,
        )
        write_utf8_atomic(locks_dir / "slice-control.json", slice_control_payload(), mode=0o600)
        write_utf8_atomic(
            locks_dir / LEGACY_CLOSEOUT_INDEX_FILE,
            legacy_closeout_index_payload(),
            mode=0o600,
        )
        tasks_dir = build_collab / "tasks"
        tasks_dir.mkdir()
        depts_root = build_collab / "部门"
        depts_root.mkdir(parents=True)
        for key in roles:
            create_department(depts_root, key, ROLE_DEFS[key], date)
        write_utf8_atomic(
            build_collab / PROTOCOL_FILE,
            protocol_payload(date, managed_manifest_from_disk(build_collab, roles)),
            mode=0o600,
        )
        os.replace(build_collab, collab)
        build_collab = None
        collab_published = True
        append_agent_guide(target)
    except Exception as exc:
        if build_collab is not None:
            shutil.rmtree(build_collab, ignore_errors=True)
        if collab_published:
            shutil.rmtree(collab, ignore_errors=True)
        for path, existed in existed_before.items():
            if not existed and path.is_file() and not path.is_symlink():
                path.unlink(missing_ok=True)
        if not docs_existed_before:
            try:
                docs_dir.rmdir()
            except OSError:
                pass
        print(f"生成协作层失败,已回滚本次协作层和新建地基文件: {exc}", file=sys.stderr)
        return 6

    print(f"已创建多会话协作层: {collab}")
    print(f"会话创建模式: {args.session_mode}")
    if args.session_mode == "auto":
        print("提醒:auto 只表示本协作层按自动会话模式生成;部门会话仍需实际调用会话管理工具创建、发送上岗引导并登记会话 ID。")
    print(f"会话启动清单: {collab / '会话启动清单.md'}")
    print("角色:")
    for key in roles:
        role = ROLE_DEFS[key]
        print(f"- {LAYER_CN.get(role.get('layer', ''), '')} · {role['name']} ({key})")
    return 0


def main() -> int:
    args = parse_args()
    target = Path(args.target).expanduser().resolve()
    if not target.exists():
        print(f"目标目录不存在: {target}", file=sys.stderr)
        return 1
    if not target.is_dir():
        print(f"目标路径不是目录: {target}", file=sys.stderr)
        return 1
    layout_ok, layout_error = validate_target_layout(target)
    if not layout_ok:
        print(layout_error, file=sys.stderr)
        return 1
    try:
        with project_lock(target):
            layout_ok, layout_error = validate_target_layout(target)
            if not layout_ok:
                print(layout_error, file=sys.stderr)
                return 1
            return run_locked(args, target)
    except CollaborationBusyError as exc:
        print(str(exc), file=sys.stderr)
        return 7


if __name__ == "__main__":
    raise SystemExit(main())
