#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Task-bound temporary executor lifecycle for generated Agent Team projects."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
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


PROTOCOL_VERSION = "1.4.15"
DISPATCH_CONTROL_NAME = "dispatch-control.json"
TASK_RE = re.compile(r"^TASK-[0-9]{8}-[A-Z0-9]{6}$")
SAFE_STATES = {"safe", "manual", "unsafe", "waiting_base"}
USER_ACCEPTANCE = {"pending", "confirmed", "rejected", "delegated", "not_applicable"}
PROMOTION_STATES = {
    "not_submitted", "submitted", "reviewing", "waiting_base", "ready", "integrated",
    "archived", "cancelled", "abandoned",
}
SESSION_STATES = {"provisioning", "awaiting_rule_confirmation", "active", "standby", "archived", "failed", "cancelled"}
ABSORPTION_STATES = {"pending", "completed", "not_applicable"}


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="minutes")


def run_git(project: Path, *args: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(project), *args], text=True, encoding="utf-8",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if ok and result.returncode != 0:
        raise ValueError(f"Git 操作失败: {' '.join(args)}: {result.stderr.strip() or result.stdout.strip()}")
    return result


def reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict:
    payload: dict = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"JSON 含重复 key: {key}")
        payload[key] = value
    return payload


def load_json_text(text: str, *, label: str) -> object:
    try:
        return json.loads(text, object_pairs_hook=reject_duplicate_json_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} JSON 无效: {exc.msg}") from exc


def ensure_plain_directory(path: Path, root: Path, *, label: str) -> Path:
    root_lexical = Path(os.path.abspath(str(root)))
    path_lexical = Path(os.path.abspath(str(path)))
    if root_lexical.is_symlink():
        raise ValueError(f"{label} 父根禁止符号链接")
    try:
        relative = path_lexical.relative_to(root_lexical)
        root_resolved = root_lexical.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} 路径越界或根目录无效") from exc
    current = root_lexical
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} 禁止符号链接: {current}")
    try:
        resolved = path_lexical.resolve(strict=True)
        resolved.relative_to(root_resolved)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} 不存在或 resolved 路径越界") from exc
    if not resolved.is_dir():
        raise ValueError(f"{label} 必须是普通目录")
    return path_lexical


def ensure_plain_file(path: Path, root: Path, *, label: str) -> Path:
    root_directory = ensure_plain_directory(root, root, label=f"{label} 父根")
    root_lexical = Path(os.path.abspath(str(root_directory)))
    path_lexical = Path(os.path.abspath(str(path)))
    try:
        relative = path_lexical.relative_to(root_lexical)
        root_resolved = root_lexical.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} 路径越界") from exc
    current = root_lexical
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{label} 禁止符号链接: {current}")
    try:
        resolved = path_lexical.resolve(strict=True)
        resolved.relative_to(root_resolved)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} 不存在或 resolved 路径越界") from exc
    if not resolved.is_file():
        raise ValueError(f"{label} 必须是普通文件")
    return path_lexical


def read_plain_json(path: Path, root: Path, *, label: str) -> object:
    safe_path = ensure_plain_file(path, root, label=label)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(safe_path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"{label} 必须是普通文件")
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            return load_json_text(handle.read(), label=label)
    finally:
        if fd >= 0:
            os.close(fd)


def local_project() -> Path:
    return Path(__file__).resolve().parents[3]


def main_project() -> Path:
    local = local_project()
    result = run_git(local, "worktree", "list", "--porcelain", ok=False)
    if result.returncode != 0:
        raise ValueError("临时外包首轮只支持 Git 项目")
    first = next((line[9:] for line in result.stdout.splitlines() if line.startswith("worktree ")), "")
    if not first:
        raise ValueError("无法发现 Git 主 worktree")
    root = Path(first).resolve(strict=True)
    collab = root / "docs" / "collaboration"
    try:
        ensure_plain_directory(collab, root, label="docs/collaboration 控制根")
    except ValueError as exc:
        raise ValueError("主 worktree 缺少安全的 docs/collaboration 控制根")
    return root


PROJECT = local_project()
COLLAB = PROJECT / "docs" / "collaboration"
TASKS = COLLAB / "tasks"
LOCKS = COLLAB / ".locks"
SESSION_STATE = COLLAB / "会话启动状态.json"


def configure_project(*, require_git: bool) -> None:
    global PROJECT, COLLAB, TASKS, LOCKS, SESSION_STATE
    PROJECT = main_project() if require_git else local_project()
    COLLAB = PROJECT / "docs" / "collaboration"
    TASKS = COLLAB / "tasks"
    LOCKS = COLLAB / ".locks"
    SESSION_STATE = COLLAB / "会话启动状态.json"


def secure_collaboration_root() -> Path:
    return ensure_plain_directory(COLLAB, PROJECT, label="docs/collaboration 控制根")


def secure_tasks_root() -> Path:
    collab = secure_collaboration_root()
    return ensure_plain_directory(TASKS, collab, label="tasks 真值目录")


def secure_locks_root() -> Path:
    collab = secure_collaboration_root()
    if LOCKS.is_symlink():
        raise ValueError(".locks 禁止符号链接")
    try:
        LOCKS.mkdir(mode=0o700)
    except FileExistsError:
        pass
    return ensure_plain_directory(LOCKS, collab, label=".locks 目录")


def work_mode() -> str:
    locks = secure_locks_root()
    control = locks / DISPATCH_CONTROL_NAME
    if not control.exists():
        raise ValueError("派单控制缺失；为避免失控派单，已拒绝临时外包动作")
    payload = read_plain_json(control, locks, label="派单控制")
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
            or len(event["actor"]) > 200 or any(char.isspace() for char in event["actor"])
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
    last = history[-1]
    expected_mode = "frozen" if last["action"] == "freeze" else "normal"
    if payload["mode"] != expected_mode or payload.get("updated_at") != last.get("at"):
        raise ValueError("派单控制当前模式与历史不一致")
    return payload["mode"]


def enforce_stop_loss(args: argparse.Namespace) -> None:
    if work_mode() != "frozen":
        return
    cleanup_commands = {"pause", "abandon"}
    if args.cmd in cleanup_commands:
        return
    if args.cmd == "session-mark" and args.state in {"standby", "archived", "failed"}:
        return
    raise ValueError(
        "P0_FREEZE_ACTIVE | 临时外包推进已冻结；只允许暂停、放弃、归档或失败记账，禁止删除证据"
    )


def read_session_state() -> dict:
    collab = secure_collaboration_root()
    payload = read_plain_json(SESSION_STATE, collab, label="会话启动状态")
    if not isinstance(payload, dict):
        raise ValueError("会话启动状态根节点必须是 JSON 对象")
    if payload.get("schema_version") != 1 or payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("会话启动状态版本与临时外包工具不一致")
    departments = payload.get("departments")
    if not isinstance(departments, dict):
        raise ValueError("会话启动状态缺少部门登记")
    identities: dict[str, str] = {}
    for department, item in departments.items():
        if not isinstance(department, str) or not isinstance(item, dict):
            raise ValueError("会话启动状态部门条目无效")
        for field in ("thread_id", "previous_thread_id"):
            thread_id = item.get(field, "")
            if not isinstance(thread_id, str):
                raise ValueError(f"会话启动状态 {department}.{field} 无效")
            if not thread_id:
                continue
            if len(thread_id) > 300 or thread_id.startswith("=") or any(char.isspace() for char in thread_id):
                raise ValueError(f"会话启动状态 {department}.{field} 不可表示为归档回执")
            owner = f"{department}.{field}"
            if thread_id in identities:
                raise ValueError(
                    f"会话启动状态 thread_id 重复: {thread_id} ({identities[thread_id]}, {owner})"
                )
            identities[thread_id] = owner
    return payload


def task_files() -> list[Path]:
    tasks = secure_tasks_root()
    result: list[Path] = []
    for path in sorted(tasks.glob("TASK-*.json")):
        if TASK_RE.fullmatch(path.stem):
            result.append(path)
    return result


def clean(name: str, value: str, *, max_chars: int = 2000) -> str:
    result = value.strip()
    if not result:
        raise ValueError(f"{name} 不能为空")
    if len(result) > max_chars or any(ord(ch) < 32 and ch != "\t" for ch in result):
        raise ValueError(f"{name} 超长或含控制字符")
    return result


def clean_thread_id(value: str) -> str:
    result = clean("thread-id", value, max_chars=300)
    if any(char.isspace() for char in result):
        raise ValueError("thread-id 不能包含空格、换行或制表符")
    if result.startswith("="):
        raise ValueError("thread-id 不能以等号开头")
    return result


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


def clean_list(name: str, values: list[str], *, allow_none: bool = False) -> list[str]:
    result = [clean(name, value, max_chars=500) for value in values]
    if not result and not allow_none:
        raise ValueError(f"{name} 至少提供一项")
    if len(set(result)) != len(result):
        raise ValueError(f"{name} 不能重复")
    return result


def clean_revision(name: str, value: str) -> str:
    result = clean(name, value, max_chars=300)
    if result.startswith("-"):
        raise ValueError(f"{name} 不能以连字符开头")
    return result


def clean_branch(name: str) -> str:
    result = clean("main-branch", name, max_chars=200)
    if result.startswith("-") or run_git(PROJECT, "check-ref-format", f"refs/heads/{result}", ok=False).returncode != 0:
        raise ValueError("main-branch 不是安全的 Git 分支名")
    return result


def safe_relative_path(raw: str, *, field: str) -> str:
    value = clean(field, raw, max_chars=500).replace("\\", "/")
    path = Path(value)
    if path.is_absolute() or value in {".", ".."} or ".." in path.parts:
        raise ValueError(f"{field} 必须是项目内相对路径")
    forbidden = (".git", ".agent-team", "docs/collaboration")
    if any(value == prefix or value.startswith(prefix + "/") for prefix in forbidden):
        raise ValueError(f"{field} 禁止授权控制根、Git 元数据或临时系统目录: {value}")
    return path.as_posix().rstrip("/")


def safe_write_paths(values: list[str]) -> list[str]:
    return clean_list("write-path", [safe_relative_path(value, field="write-path") for value in values])


def safe_project_artifact(raw: str, *, field: str) -> str:
    value = clean(field, raw, max_chars=500).replace("\\", "/")
    path = Path(value)
    if path.is_absolute() or value in {".", ".."} or ".." in path.parts:
        raise ValueError(f"{field} 必须是项目内相对路径")
    candidate = PROJECT / path
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(PROJECT.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{field} 不存在、越界或经过无效路径") from exc
    if candidate.is_symlink() or not resolved.is_file():
        raise ValueError(f"{field} 必须是项目内普通文件")
    return resolved.relative_to(PROJECT.resolve(strict=True)).as_posix()


def configured_departments() -> set[str]:
    payload = read_session_state()
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("协作层协议版本与临时外包工具不一致")
    departments = payload.get("departments")
    if not isinstance(departments, dict):
        raise ValueError("会话状态缺少部门登记")
    return set(departments)


def registered_lead_actor() -> str:
    payload = read_session_state()
    departments = payload.get("departments", {})
    lead = departments.get("统筹部")
    if not isinstance(lead, dict) or lead.get("step") != "registered" or not lead.get("thread_id"):
        raise ValueError("统筹部尚未登记可核收的当前会话")
    return f"统筹部/{lead['thread_id']}"


def task_path(task_id: str) -> Path:
    if not TASK_RE.fullmatch(task_id):
        raise ValueError("TASK ID 格式无效")
    return secure_tasks_root() / f"{task_id}.json"


def read_task(task_id: str) -> dict:
    path = task_path(task_id)
    try:
        payload = read_plain_json(path, path.parent, label=f"TASK {task_id}")
    except ValueError as exc:
        raise ValueError(f"TASK 不存在、路径不安全或 JSON 无效: {task_id}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("TASK 真值根节点必须是 JSON 对象")
    if payload.get("task_id") != task_id or not isinstance(payload.get("revision"), int):
        raise ValueError("TASK 真值损坏")
    return payload


def fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def write_task(task: dict, *, expected_revision: int) -> None:
    path = task_path(task["task_id"])
    current = read_plain_json(path, path.parent, label=f"TASK {task['task_id']}")
    if not isinstance(current, dict):
        raise ValueError("TASK 真值根节点必须是 JSON 对象")
    if current.get("revision") != expected_revision:
        raise ValueError(f"TASK revision 已变化，预期 {expected_revision}，实际 {current.get('revision')}")
    task["revision"] = expected_revision + 1
    task["updated_at"] = now_iso()
    validate_extension(task)
    temp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    data = json.dumps(task, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("TASK 临时文件写入失败")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(temp, path)
        fsync_dir(path.parent)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def named_lock(filename: str):
    if filename not in {"tasks.lock", "identity.lock"}:
        raise ValueError("锁文件名无效")
    locks = secure_locks_root()
    lock_path = locks / filename
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
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
        if os.name == "nt":
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


@contextmanager
def task_lock():
    with named_lock("tasks.lock"):
        yield


@contextmanager
def identity_lock():
    with named_lock("identity.lock"):
        yield


def validate_impact(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "write_paths", "shared_contracts", "external_effects", "base_revision", "owner_task", "admission",
    }:
        raise ValueError("影响声明结构无效")
    for key in ("write_paths", "shared_contracts", "external_effects"):
        if not isinstance(value[key], list) or any(not isinstance(item, str) or not item for item in value[key]):
            raise ValueError(f"影响声明 {key} 无效")
    if not isinstance(value["base_revision"], str) or not isinstance(value["owner_task"], str):
        raise ValueError("影响声明基准或 owner 无效")
    if value["admission"] not in SAFE_STATES:
        raise ValueError("影响声明 admission 无效")
    return value


def validate_operation_history(history: object, current_state: str, label: str) -> None:
    allowed_states = {"planned", "started", "succeeded", "verified", "failed"}
    if not isinstance(history, list) or not history:
        raise ValueError(f"{label} history 无效")
    for event in history:
        if not isinstance(event, dict) or set(event) - {"state", "at", "reason", "via"} or not {"state", "at"}.issubset(event):
            raise ValueError(f"{label} history 事件结构无效")
        if event["state"] not in allowed_states or not isinstance(event["at"], str) or not event["at"]:
            raise ValueError(f"{label} history 事件内容无效")
        for key in ("reason", "via"):
            if key in event and (not isinstance(event[key], str) or not event[key]):
                raise ValueError(f"{label} history {key} 无效")
    if history[-1]["state"] != current_state:
        raise ValueError(f"{label} 当前状态与 history 末项不一致")


def validate_extension(task: dict) -> None:
    if "impact_declaration" in task:
        validate_impact(task["impact_declaration"])
    temp = task.get("temporary_executor")
    if temp is None:
        return
    required = {
        "schema_version", "executor_type", "executor_id", "display_name", "parent_department",
        "current_brief", "brief_revision", "impact", "workspace", "rule", "user_acceptance",
        "promotion_state", "temporary_session", "attempt", "candidate_revision", "candidate",
        "review", "delivery", "integration", "absorption", "operation", "promotion_operation", "cleanup_operation",
    }
    if not isinstance(temp, dict) or set(temp) != required:
        raise ValueError("temporary_executor 结构无效")
    if temp["schema_version"] != 1 or temp["executor_type"] != "temporary":
        raise ValueError("temporary_executor 版本或类型无效")
    if temp["parent_department"] != task.get("department"):
        raise ValueError("临时执行者父部门必须等于 TASK 所属部门")
    workspace = temp["workspace"]
    if not isinstance(workspace, dict) or set(workspace) != {"path", "branch", "base_revision", "state"} or any(
        not isinstance(workspace[key], str) for key in workspace
    ):
        raise ValueError("workspace 结构无效")
    rule = temp["rule"]
    if not isinstance(rule, dict) or set(rule) != {"version", "digest", "source_protocol", "confirmed_at"} or any(
        not isinstance(rule[key], str) for key in rule
    ):
        raise ValueError("rule 结构无效")
    acceptance = temp["user_acceptance"]
    required_acceptance = {"state", "evidence", "candidate_revision", "candidate_digest"}
    if not isinstance(acceptance, dict) or set(acceptance) != required_acceptance:
        raise ValueError("user_acceptance 结构无效")
    session = temp["temporary_session"]
    if not isinstance(session, dict) or set(session) != {"state", "thread_id", "evidence"} or any(
        not isinstance(session[key], str) for key in session
    ):
        raise ValueError("temporary_session 结构无效")
    if session["thread_id"] and (
        len(session["thread_id"]) > 300
        or session["thread_id"].startswith("=")
        or any(char.isspace() for char in session["thread_id"])
    ):
        raise ValueError("temporary_session thread_id 不可表示为归档回执")
    absorption = temp["absorption"]
    required_absorption = {"preflight", "parent_department", "project_global", "final", "receipts"}
    if not isinstance(absorption, dict) or required_absorption - set(absorption) or set(absorption) - required_absorption - {"history"}:
        raise ValueError("absorption 结构无效")
    if not isinstance(absorption["receipts"], list) or ("history" in absorption and not isinstance(absorption["history"], list)):
        raise ValueError("absorption receipts/history 无效")
    for field, minimum in (("brief_revision", 1), ("attempt", 1), ("candidate_revision", 0)):
        if isinstance(temp[field], bool) or not isinstance(temp[field], int) or temp[field] < minimum:
            raise ValueError(f"temporary_executor {field} 无效")
    if temp["user_acceptance"]["state"] not in USER_ACCEPTANCE:
        raise ValueError("用户验收状态无效")
    if temp["promotion_state"] not in PROMOTION_STATES:
        raise ValueError("晋升状态无效")
    if temp["temporary_session"]["state"] not in SESSION_STATES:
        raise ValueError("临时会话状态无效")
    validate_impact(temp["impact"])
    if task.get("impact_declaration") != temp["impact"]:
        raise ValueError("temporary_executor impact 与 TASK impact_declaration 不一致")
    if temp["absorption"]["preflight"] not in ABSORPTION_STATES:
        raise ValueError("前置知识清点状态无效")
    for key in ("parent_department", "project_global", "final"):
        if temp["absorption"][key] not in ABSORPTION_STATES:
            raise ValueError("知识吸收状态无效")
    candidate = temp["candidate"]
    if candidate is not None:
        required_candidate = {"revision", "kind", "locator", "digest", "brief_revision", "created_at"}
        if not isinstance(candidate, dict) or set(candidate) != required_candidate:
            raise ValueError("candidate 结构无效")
        if not isinstance(candidate["revision"], int) or candidate["revision"] != temp["candidate_revision"]:
            raise ValueError("candidate revision 与 TASK 不一致")
        if not isinstance(candidate["brief_revision"], int) or candidate["brief_revision"] < 1:
            raise ValueError("candidate brief_revision 无效")
        if any(not isinstance(candidate[key], str) or not candidate[key] for key in ("kind", "locator", "digest", "created_at")):
            raise ValueError("candidate 文本字段无效")
    if not isinstance(acceptance["candidate_revision"], int) or not isinstance(acceptance["candidate_digest"], str):
        raise ValueError("user_acceptance 候选绑定无效")
    review = temp["review"]
    if review is not None:
        if not isinstance(review, dict) or set(review) != {"candidate_revision", "decision", "evidence", "reviewed_at"}:
            raise ValueError("review 结构无效")
        if review["decision"] not in {"pass", "fail"} or not isinstance(review["candidate_revision"], int):
            raise ValueError("review 内容无效")
    delivery = temp["delivery"]
    if delivery is not None:
        required_delivery = {"candidate_revision", "locator", "digest", "protected_ref", "evidence", "submitted_at"}
        if not isinstance(delivery, dict) or set(delivery) != required_delivery:
            raise ValueError("delivery 结构无效")
        if not isinstance(delivery["candidate_revision"], int) or any(
            not isinstance(delivery[key], str) or not delivery[key]
            for key in ("locator", "digest", "protected_ref", "evidence", "submitted_at")
        ):
            raise ValueError("delivery 内容无效")
        if candidate is not None and (
            delivery["candidate_revision"] != candidate["revision"]
            or delivery["locator"] != candidate["locator"] or delivery["digest"] != candidate["digest"]
        ):
            raise ValueError("delivery 与 candidate 不一致")
    if review is not None and candidate is not None and review["candidate_revision"] != candidate["revision"]:
        raise ValueError("review 与 candidate revision 不一致")
    if candidate is not None and acceptance["state"] in {"confirmed", "delegated", "not_applicable"}:
        if acceptance["candidate_revision"] != candidate["revision"] or acceptance["candidate_digest"] != candidate["digest"]:
            raise ValueError("用户验收与 candidate 不一致")
    integration = temp["integration"]
    if integration is not None:
        required_integration = {
            "candidate_commit", "tested_base", "tested_commit", "tree_oid", "test_definition",
            "environment", "evidence", "test_task_id", "report", "unverified", "result", "tested_at",
        }
        optional_integration = {"promoted_at", "main_branch"}
        if not isinstance(integration, dict) or required_integration - set(integration) or set(integration) - required_integration - optional_integration:
            raise ValueError("integration 结构无效")
        if integration["result"] not in {"pass", "fail"} or any(
            not isinstance(integration[key], str) or not integration[key] for key in required_integration - {"result"}
        ):
            raise ValueError("integration 内容无效")
    operation = temp["operation"]
    if not isinstance(operation, dict) or any(not isinstance(operation.get(key), str) or not operation.get(key) for key in ("id", "client_key", "state", "request_digest")):
        raise ValueError("operation 结构无效")
    if operation["state"] not in {"planned", "started", "succeeded", "verified", "failed"}:
        raise ValueError("operation state 无效")
    if not isinstance(operation.get("resources"), list) or any(not isinstance(item, str) or not item for item in operation["resources"]):
        raise ValueError("operation resources/history 无效")
    validate_operation_history(operation.get("history"), operation["state"], "operation")
    for field, required_fields in (
        ("promotion_operation", {"id", "state", "main_branch", "expected_old", "candidate_commit", "tree_oid", "history"}),
        ("cleanup_operation", {"id", "state", "workspace", "branch", "workspace_head", "evidence", "history"}),
    ):
        record = temp[field]
        if record is not None:
            if not isinstance(record, dict) or set(record) != required_fields:
                raise ValueError(f"{field} 结构无效")
            if not isinstance(record["history"], list) or any(
                not isinstance(record[key], str) or not record[key] for key in required_fields - {"history"}
            ):
                raise ValueError(f"{field} 内容无效")
            if record["state"] not in {"planned", "started", "succeeded", "verified", "failed"}:
                raise ValueError(f"{field} state 无效")
            validate_operation_history(record["history"], record["state"], field)
    if session["state"] == "archived":
        cleanup = temp.get("cleanup_operation")
        if (
            not session["thread_id"]
            or not session["evidence"]
            or not valid_archive_receipt(session["evidence"], session["thread_id"])
            or temp["promotion_state"] != "archived"
            or workspace["state"] != "removed"
            or not isinstance(cleanup, dict)
            or cleanup.get("state") != "verified"
        ):
            raise ValueError("临时会话 archived 缺少真实归档收据或资源清理证据")
    cleanup = temp.get("cleanup_operation")
    cleanup_state = cleanup.get("state") if isinstance(cleanup, dict) else None
    cleanup_terminal = (
        temp["promotion_state"] == "archived"
        and workspace["state"] == "removed"
        and cleanup_state == "verified"
    )
    if any((temp["promotion_state"] == "archived", workspace["state"] == "removed", cleanup_state == "verified")) and not cleanup_terminal:
        raise ValueError("临时资源终态必须同时满足 promotion=archived、workspace=removed 和 cleanup=verified")
    promotion_operation = temp.get("promotion_operation")
    if (
        isinstance(promotion_operation, dict)
        and promotion_operation.get("state") == "verified"
        and temp["promotion_state"] not in {"integrated", "archived"}
    ):
        raise ValueError("已验证的晋升事务不能退回 integrated 之前")
    if cleanup_terminal:
        if session["thread_id"] and session["state"] not in {"standby", "archived"}:
            raise ValueError("资源已清理且存在真实 thread_id 时，会话必须待归档或已归档")
        if not session["thread_id"] and session["state"] != "cancelled":
            raise ValueError("资源已清理但 thread_id 缺失；仅“从未创建真实会话”可收口为 cancelled")
    if session["state"] == "cancelled" and (session["thread_id"] or not cleanup_terminal):
        raise ValueError("cancelled 只能表示从未创建真实会话且资源已验证清理")


def overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def dirty_paths() -> list[str]:
    result = run_git(PROJECT, "status", "--porcelain=v1", "-z")
    entries = result.stdout.split("\0")
    paths: list[str] = []
    for entry in entries:
        if not entry:
            continue
        raw = entry[3:]
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1]
        paths.append(raw)
    return paths


def product_dirty_paths() -> list[str]:
    return [
        path for path in dirty_paths()
        if path != "docs/collaboration" and not path.startswith("docs/collaboration/")
        and path != ".agent-team" and not path.startswith(".agent-team/")
    ]


def impact_from_args(args: argparse.Namespace, task_id: str, base_revision: str) -> dict:
    effects = clean_list("external-effect", args.external_effect or ["none"])
    if effects != ["none"]:
        raise ValueError("首轮临时外包禁止真实外部副作用")
    return {
        "write_paths": safe_write_paths(args.write_path),
        "shared_contracts": clean_list("shared-contract", args.shared_contract, allow_none=True),
        "external_effects": effects,
        "base_revision": base_revision,
        "owner_task": task_id,
        "admission": "manual",
    }


def admission_for(task_id: str, impact: dict) -> tuple[str, list[str]]:
    reasons: list[str] = []
    unknown = False
    for path in task_files():
        other = read_task(path.stem)
        if other.get("task_id") == task_id:
            continue
        other_temp = other.get("temporary_executor")
        active = other.get("execution_state") in {"claimed", "blocked", "waiting_input"}
        if isinstance(other_temp, dict) and other_temp.get("promotion_state") not in {"archived", "abandoned", "cancelled"}:
            active = True
        if not active:
            continue
        declared = other.get("impact_declaration")
        if not isinstance(declared, dict):
            unknown = True
            reasons.append(f"{other.get('task_id', path.stem)} 缺少机器可读影响声明")
            continue
        validate_impact(declared)
        if any(overlap(a, b) for a in impact["write_paths"] for b in declared["write_paths"]):
            reasons.append(f"与 {other['task_id']} 写路径重叠")
            return "unsafe", reasons
        shared = sorted(set(impact["shared_contracts"]) & set(declared["shared_contracts"]))
        if shared:
            reasons.append(f"与 {other['task_id']} 共享契约重叠: {', '.join(shared)}")
            return "unsafe", reasons
        if declared["external_effects"] != ["none"]:
            reasons.append(f"{other['task_id']} 存在外部副作用")
            return "unsafe", reasons
    dirty = dirty_paths()
    conflicts = sorted(path for path in dirty if any(overlap(path, allowed) for allowed in impact["write_paths"]))
    if conflicts:
        reasons.append("主工作区脏路径与临时任务重叠: " + ", ".join(conflicts))
        return "waiting_base", reasons
    ignored_result = run_git(
        PROJECT, "ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--",
        *impact["write_paths"], ok=False,
    )
    if ignored_result.returncode != 0:
        reasons.append("无法检查允许范围内的 ignored 内容")
        return "manual", reasons
    ignored = sorted(path for path in ignored_result.stdout.split("\0") if path)
    if ignored:
        reasons.append("允许范围内存在 ignored 内容，无法自动证明安全: " + ", ".join(ignored[:20]))
        return "manual", reasons
    if unknown:
        return "manual", reasons
    return "safe", reasons or ["写路径、共享契约、外部影响与已声明在办任务均独立"]


def cmd_declare_impact(args: argparse.Namespace) -> int:
    task = read_task(args.task_id)
    if task.get("temporary_executor") is not None:
        raise ValueError("已绑定临时执行者的 TASK 必须通过 amend/rework 维护唯一影响真值")
    if task["revision"] != args.expected_revision:
        raise ValueError("expected-revision 与 TASK 当前 revision 不一致")
    revision_name = clean_revision("base-revision", args.base_revision)
    base = run_git(PROJECT, "rev-parse", "--verify", f"{revision_name}^{{commit}}").stdout.strip()
    impact = impact_from_args(args, args.task_id, base)
    admission, reasons = admission_for(args.task_id, impact)
    if admission in {"unsafe", "waiting_base"}:
        raise ValueError(f"影响声明与已有在办任务冲突: {'；'.join(reasons)}")
    impact["admission"] = admission
    task["impact_declaration"] = impact
    write_task(task, expected_revision=args.expected_revision)
    print(f"TEMP_IMPACT_OK | {args.task_id} | revision:{task['revision']} | admission:{admission}")
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    task = read_task(args.task_id)
    if task.get("department") != args.parent_department:
        raise ValueError("parent-department 必须等于 TASK 所属部门")
    revision_name = clean_revision("base-revision", args.base_revision)
    base = run_git(PROJECT, "rev-parse", "--verify", f"{revision_name}^{{commit}}").stdout.strip()
    impact = impact_from_args(args, args.task_id, base)
    state, reasons = admission_for(args.task_id, impact)
    print(f"TEMP_ADMISSION_{state.upper()} | {args.task_id} | " + "；".join(reasons))
    return 0 if state == "safe" else 4


def rule_text(task: dict, temp: dict) -> str:
    allowed = "\n".join(f"- `{path}`" for path in temp["impact"]["write_paths"])
    return f"""# 临时执行规则

> rule_version: 1.0.0
> protocol_version: {PROTOCOL_VERSION}
> task_id: {task['task_id']}
> executor_id: {temp['executor_id']}
> parent_department: {temp['parent_department']}

## 身份与目标

你是只绑定 `{task['task_id']}` 的{temp['display_name']}。当前目标：{temp['current_brief']}

## 允许写入

{allowed}

所有命令 cwd 必须位于登记 workspace。禁止写主工作区、`docs/collaboration/`、`.git/`、未授权模块、密钥、生产配置和真实外部系统。

## 必读事实

按 TASK 指针读取 Spec、相关 ADR、`docs/conventions.md`、相关代码和测试。默认不读父部门完整岗位说明、收件箱、交接班、progress 和长期报告。

## 工作边界

遵守父部门的专业质量标准，但不继承父部门组织身份和正式写权限。需求范围、共享契约、写入范围、依赖、数据库、认证、权限、密钥、隐私、付费、生产、发布或真实发送发生变化时，立即暂停并升级统筹部。

## 收口

任务内调整必须同步 TASK brief。用户确认后固定候选；独立子 Agent 审查只在用户要求时调用且只给结论。最终 delivery submit 后进入 standby。正式吸收或用户明确放弃前不得清理 workspace。
"""


def write_current_rule(task: dict, temp: dict) -> str:
    workspace = workspace_for(temp)
    rule_path = workspace / ".agent-team" / "临时执行规则.md"
    if rule_path.is_symlink():
        raise ValueError("临时规则不能是符号链接")
    content = rule_text(task, temp)
    temporary = rule_path.parent / f".{rule_path.name}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, rule_path)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def require_current_rule_confirmation(temp: dict) -> None:
    if temp["temporary_session"]["state"] != "active":
        raise ValueError("临时会话尚未 active，不能固定候选或 submit")
    if not temp["rule"]["confirmed_at"] or not temp["rule"]["digest"]:
        raise ValueError("当前临时规则尚未确认")
    workspace = workspace_for(temp)
    rule_path = workspace / ".agent-team" / "临时执行规则.md"
    if rule_path.is_symlink() or not rule_path.is_file():
        raise ValueError("当前临时规则缺失或不安全")
    actual = hashlib.sha256(rule_path.read_bytes()).hexdigest()
    if actual != temp["rule"]["digest"]:
        raise ValueError("workspace 临时规则已变化，旧确认失效")


def ignored_workspace(relative: str) -> bool:
    return run_git(PROJECT, "check-ignore", "-q", "--", relative, ok=False).returncode == 0


def provision_request_digest(args: argparse.Namespace, *, base: str, impact: dict) -> str:
    payload = {
        "task_id": args.task_id,
        "parent_department": args.parent_department,
        "executor_id": clean("executor-id", args.executor_id, max_chars=200),
        "display_name": clean("display-name", args.display_name, max_chars=100),
        "current_brief": clean("current-brief", args.current_brief),
        "base_revision": base,
        "write_paths": impact["write_paths"],
        "shared_contracts": impact["shared_contracts"],
        "external_effects": impact["external_effects"],
        "scan_boundary_evidence": clean("scan-boundary-evidence", args.scan_boundary_evidence, max_chars=1000),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def cmd_provision(args: argparse.Namespace) -> int:
    task = read_task(args.task_id)
    if task.get("department") != args.parent_department or args.parent_department not in configured_departments():
        raise ValueError("父部门不存在或与 TASK 所属部门不一致")
    if args.parent_department != "开发部":
        raise ValueError("首轮运行适配只支持临时开发外包；其他父部门目前只支持通用建模与 preflight")
    if task.get("authorization_state") != "user_confirmed" or not task.get("authorization_evidence"):
        raise ValueError("临时外包必须有用户主动发起的确认与证据")
    revision_name = clean_revision("base-revision", args.base_revision)
    base = run_git(PROJECT, "rev-parse", "--verify", f"{revision_name}^{{commit}}").stdout.strip()
    impact = impact_from_args(args, args.task_id, base)
    request_digest = provision_request_digest(args, base=base, impact=impact)
    if task.get("temporary_executor"):
        existing = task["temporary_executor"]
        if existing["operation"]["client_key"] == args.client_key:
            if existing["operation"].get("request_digest") != request_digest:
                raise ValueError("IDEMPOTENCY_CONFLICT: 相同 client key 对应不同创建请求")
            if existing["workspace"]["state"] == "ready" and existing["operation"]["state"] == "verified":
                print(f"TEMP_PROVISION_IDEMPOTENT | {args.task_id} | {existing['workspace']['path']}")
                return 0
            raise ValueError("同一创建操作尚未 verified，请先运行 reconcile-provision")
        raise ValueError("TASK 已绑定其他临时执行者")
    if task.get("execution_state") != "queued":
        raise ValueError("只有 queued TASK 可以创建临时执行者")
    admission, reasons = admission_for(args.task_id, impact)
    impact["admission"] = admission
    if admission != "safe":
        raise ValueError(f"并行判断为 {admission}: {'；'.join(reasons)}")
    relative_workspace = f".agent-team/workspaces/{args.task_id}"
    if not ignored_workspace(relative_workspace):
        raise ValueError("项目根必须先忽略 /.agent-team/，避免 workspace 被构建或提交扫描")
    workspace = PROJECT / relative_workspace
    branch = f"codex/temp-{args.task_id.lower()}"
    operation_id = "TEMP-" + dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8].upper()
    timestamp = now_iso()
    temp = {
        "schema_version": 1,
        "executor_type": "temporary",
        "executor_id": clean("executor-id", args.executor_id, max_chars=200),
        "display_name": clean("display-name", args.display_name, max_chars=100),
        "parent_department": args.parent_department,
        "current_brief": clean("current-brief", args.current_brief),
        "brief_revision": 1,
        "impact": impact,
        "workspace": {"path": relative_workspace, "branch": branch, "base_revision": base, "state": "planned"},
        "rule": {"version": "1.0.0", "digest": "", "source_protocol": PROTOCOL_VERSION, "confirmed_at": ""},
        "user_acceptance": {"state": "pending", "evidence": "", "candidate_revision": 0, "candidate_digest": ""},
        "promotion_state": "not_submitted",
        "temporary_session": {"state": "provisioning", "thread_id": "", "evidence": ""},
        "attempt": 1,
        "candidate_revision": 0,
        "candidate": None,
        "review": None,
        "delivery": None,
        "integration": None,
        "absorption": {"preflight": "pending", "parent_department": "pending", "project_global": "pending", "final": "pending", "receipts": []},
        "operation": {
            "id": operation_id, "client_key": clean("client-key", args.client_key, max_chars=200),
            "request_digest": request_digest, "state": "planned", "resources": [],
            "scan_boundary_evidence": clean("scan-boundary-evidence", args.scan_boundary_evidence, max_chars=1000),
            "history": [{"state": "planned", "at": timestamp}],
        },
        "promotion_operation": None,
        "cleanup_operation": None,
    }
    revision = task["revision"]
    task["temporary_executor"] = temp
    task["impact_declaration"] = impact
    task["claimed_by"] = temp["executor_id"]
    task["execution_state"] = "blocked"
    task["block_reason"] = "临时 workspace 正在创建"
    write_task(task, expected_revision=revision)
    started = read_task(args.task_id)
    started_temp = started["temporary_executor"]
    started_temp["operation"]["state"] = "started"
    started_temp["operation"]["history"].append({"state": "started", "at": now_iso()})
    write_task(started, expected_revision=started["revision"])
    try:
        workspace.parent.mkdir(parents=True, exist_ok=True)
        if workspace.exists() or run_git(PROJECT, "show-ref", "--verify", f"refs/heads/{branch}", ok=False).returncode == 0:
            raise ValueError("计划创建的 workspace 或 branch 已存在，需先 reconcile")
        run_git(PROJECT, "worktree", "add", "-b", branch, str(workspace), base)
        inner = workspace / ".agent-team"
        inner.mkdir(mode=0o700)
        ownership = {
            "operation_id": operation_id, "task_id": args.task_id, "executor_id": temp["executor_id"],
            "workspace": relative_workspace, "branch": branch, "base_revision": base,
        }
        (inner / "ownership.json").write_text(json.dumps(ownership, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rule = rule_text(task, temp)
        rule_path = inner / "临时执行规则.md"
        rule_path.write_text(rule, encoding="utf-8")
        digest = hashlib.sha256(rule.encode("utf-8")).hexdigest()
    except Exception as exc:
        current = read_task(args.task_id)
        current_temp = current["temporary_executor"]
        current_temp["workspace"]["state"] = "failed"
        current_temp["operation"]["state"] = "failed"
        current_temp["operation"]["history"].append({"state": "failed", "at": now_iso(), "reason": str(exc)})
        current["block_reason"] = f"临时 workspace 创建失败: {exc}"
        write_task(current, expected_revision=current["revision"])
        raise
    current = read_task(args.task_id)
    current_temp = current["temporary_executor"]
    current_temp["workspace"]["state"] = "ready"
    current_temp["rule"]["digest"] = digest
    current_temp["operation"]["state"] = "verified"
    current_temp["operation"]["history"].extend([
        {"state": "succeeded", "at": now_iso()}, {"state": "verified", "at": now_iso()},
    ])
    current_temp["operation"]["resources"] = [relative_workspace, branch, str(rule_path.relative_to(PROJECT))]
    current["execution_state"] = "claimed"
    current["block_reason"] = ""
    write_task(current, expected_revision=current["revision"])
    print(f"TEMP_PROVISION_OK | {args.task_id} | {relative_workspace} | rule_sha256:{digest}")
    return 0


def cmd_reconcile_provision(args: argparse.Namespace) -> int:
    task, temp = temp_task(args.task_id)
    initial_lifecycle = (
        temp["promotion_state"] == "not_submitted"
        and temp.get("candidate") is None
        and temp.get("review") is None
        and temp.get("delivery") is None
        and temp.get("integration") is None
        and temp.get("promotion_operation") is None
        and temp.get("cleanup_operation") is None
    )
    if not initial_lifecycle:
        raise ValueError("只有候选形成前的初始创建事务可 reconcile provision")
    operation_state = temp["operation"]["state"]
    if operation_state == "verified" and (
        task.get("execution_state") != "claimed" or temp["workspace"]["state"] != "ready"
    ):
        raise ValueError("已验证创建事务与当前 TASK 生命周期不一致")
    workspace = PROJECT / temp["workspace"]["path"]
    branch = temp["workspace"]["branch"]
    branch_exists = run_git(PROJECT, "show-ref", "--verify", f"refs/heads/{branch}", ok=False).returncode == 0
    if workspace.is_dir() and branch_exists:
        marker = workspace / ".agent-team" / "ownership.json"
        rule = workspace / ".agent-team" / "临时执行规则.md"
        if marker.is_symlink() or rule.is_symlink() or not marker.is_file() or not rule.is_file():
            raise ValueError("已存在 workspace 缺少 ownership marker 或临时规则，拒绝猜测修复")
        ownership = read_plain_json(marker, workspace, label="workspace ownership marker")
        if not isinstance(ownership, dict):
            raise ValueError("workspace ownership marker 根节点无效")
        expected_ownership = {
            "operation_id": temp["operation"]["id"], "task_id": args.task_id,
            "executor_id": temp["executor_id"], "workspace": temp["workspace"]["path"],
            "branch": temp["workspace"]["branch"], "base_revision": temp["workspace"]["base_revision"],
        }
        if any(ownership.get(key) != value for key, value in expected_ownership.items()):
            raise ValueError("已存在 workspace ownership 与创建操作不一致")
        workspace_for(temp)
        digest = hashlib.sha256(rule.read_bytes()).hexdigest()
        if temp["rule"]["digest"] and temp["rule"]["digest"] != digest:
            raise ValueError("已存在临时规则 digest 与 TASK 不一致")
        if operation_state == "verified":
            print(f"TEMP_RECONCILE_OK | {args.task_id} | verified | idempotent")
            return 0
        temp["rule"]["digest"] = digest
        temp["workspace"]["state"] = "ready"
        temp["operation"]["state"] = "verified"
        temp["operation"].setdefault("history", []).append({"state": "verified", "at": now_iso(), "via": "reconcile"})
        temp["operation"]["resources"] = [temp["workspace"]["path"], branch, str(rule.relative_to(PROJECT))]
        task["execution_state"] = "claimed"
        task["block_reason"] = ""
        write_task(task, expected_revision=task["revision"])
        print(f"TEMP_RECONCILE_OK | {args.task_id} | verified")
        return 0
    if workspace.exists() != branch_exists:
        raise ValueError("workspace 与 branch 只存在一半，保留现场并转人工处理")
    if operation_state == "verified":
        raise ValueError("已验证创建事务的 workspace/branch 缺失，拒绝覆写真值")
    temp["workspace"]["state"] = "failed"
    temp["operation"]["state"] = "failed"
    temp["operation"].setdefault("history", []).append({"state": "failed", "at": now_iso(), "reason": "no-owned-resource"})
    task["execution_state"] = "blocked"
    task["block_reason"] = "创建操作没有可验证资源，可在核对后 reset-failed-provision"
    write_task(task, expected_revision=task["revision"])
    print(f"TEMP_RECONCILE_BLOCKED | {args.task_id} | no-owned-resource")
    return 4


def cmd_reset_failed_provision(args: argparse.Namespace) -> int:
    task, temp = temp_task(args.task_id)
    if temp["operation"]["state"] != "failed":
        raise ValueError("只有 reconcile 确认失败且无资源的创建操作可以重置")
    if (
        temp["promotion_state"] != "not_submitted"
        or any(temp.get(field) is not None for field in ("candidate", "review", "delivery", "integration"))
        or temp.get("promotion_operation") is not None
        or temp.get("cleanup_operation") is not None
    ):
        raise ValueError("已进入候选或后续事务的 TASK 不能重置创建真值")
    session = temp["temporary_session"]
    if session.get("thread_id"):
        raise ValueError("创建失败状态仍登记真实 thread_id，必须先完成会话归档收口，不能重置真值")
    if session.get("state") not in {"provisioning", "failed"}:
        raise ValueError("临时会话已越过创建失败阶段，不能重置创建真值")
    workspace = PROJECT / temp["workspace"]["path"]
    branch = temp["workspace"]["branch"]
    if workspace.exists() or run_git(PROJECT, "show-ref", "--verify", f"refs/heads/{branch}", ok=False).returncode == 0:
        raise ValueError("仍存在 workspace 或 branch，拒绝重置")
    evidence = clean("evidence", args.evidence, max_chars=1000)
    history = list(task.get("temporary_operation_history", []))
    history.append({
        "operation_id": temp["operation"]["id"], "client_key": temp["operation"]["client_key"],
        "result": "reset_after_verified_absence", "evidence": evidence, "at": now_iso(),
    })
    task["temporary_operation_history"] = history
    task.pop("temporary_executor")
    task["execution_state"] = "queued"
    task["claimed_by"] = ""
    task["block_reason"] = ""
    write_task(task, expected_revision=task["revision"])
    print(f"TEMP_PROVISION_RESET_OK | {args.task_id}")
    return 0


def temp_task(task_id: str) -> tuple[dict, dict]:
    task = read_task(task_id)
    temp = task.get("temporary_executor")
    if not isinstance(temp, dict):
        raise ValueError("TASK 未绑定临时执行者")
    changed = False
    for key in ("promotion_operation", "cleanup_operation"):
        if key not in temp:
            temp[key] = None
            changed = True
    operation = temp.get("operation")
    if isinstance(operation, dict):
        if "request_digest" not in operation:
            operation["request_digest"] = "legacy-unknown"
            changed = True
        if "history" not in operation:
            operation["history"] = [{"state": operation.get("state", "unknown"), "at": now_iso(), "via": "legacy-normalize"}]
            changed = True
    acceptance = temp.get("user_acceptance")
    if isinstance(acceptance, dict) and "candidate_digest" not in acceptance:
        acceptance["candidate_digest"] = ""
        if acceptance.get("state") in {"confirmed", "delegated", "not_applicable"}:
            acceptance["state"] = "pending"
            acceptance["evidence"] = ""
        changed = True
    integration = temp.get("integration")
    if isinstance(integration, dict) and (not integration.get("test_task_id") or not integration.get("report")):
        temp["integration"] = None
        if temp.get("promotion_state") in {"ready", "integrated"}:
            temp["promotion_state"] = "reviewing"
        changed = True
    if changed:
        write_task(task, expected_revision=task["revision"])
        task = read_task(task_id)
        temp = task["temporary_executor"]
    validate_extension(task)
    return task, temp


def require_verified_provision(temp: dict) -> None:
    operation = temp.get("operation")
    workspace = temp.get("workspace")
    if (
        not isinstance(operation, dict)
        or operation.get("state") != "verified"
        or not isinstance(workspace, dict)
        or workspace.get("state") != "ready"
    ):
        raise ValueError("临时 workspace 创建事务尚未 verified，必须先 reconcile/reset provision")


def operation_has_reconciled_failure(operation: object) -> bool:
    if not isinstance(operation, dict) or operation.get("state") != "failed":
        return False
    history = operation.get("history")
    return bool(
        isinstance(history, list)
        and history
        and isinstance(history[-1], dict)
        and history[-1].get("state") == "failed"
        and history[-1].get("via") == "reconcile"
    )


def require_settled_promotion(temp: dict) -> None:
    operation = temp.get("promotion_operation")
    if not isinstance(operation, dict):
        return
    if operation.get("state") == "verified" and temp.get("promotion_state") in {"integrated", "archived"}:
        return
    if operation_has_reconciled_failure(operation):
        return
    raise ValueError("存在未收口的晋升事务，必须先 reconcile promotion")


def require_cleanup_not_started(temp: dict) -> None:
    if isinstance(temp.get("cleanup_operation"), dict):
        raise ValueError("清理事务已经开始，必须先 reconcile cleanup；收口前冻结其他写操作")


def archive_ready_session(temp: dict) -> tuple[dict, str]:
    cleanup = temp.get("cleanup_operation")
    if (
        temp["promotion_state"] != "archived"
        or temp["workspace"]["state"] != "removed"
        or not isinstance(cleanup, dict)
        or cleanup.get("state") != "verified"
    ):
        raise ValueError("真实会话只能在临时资源清理验证完成后登记 archived")
    session = temp["temporary_session"]
    thread_id = session.get("thread_id", "")
    if not thread_id:
        raise ValueError("临时会话缺少真实 thread_id，不能登记 archived")
    return session, thread_id


def registered_thread_owners() -> tuple[dict[str, str], dict[str, str]]:
    session_state = read_session_state()
    departments = session_state.get("departments")
    if not isinstance(departments, dict):
        raise ValueError("会话启动状态缺少部门登记")
    formal_owners: dict[str, str] = {}
    for department, item in departments.items():
        if not isinstance(department, str) or not isinstance(item, dict):
            raise ValueError("会话启动状态部门条目无效")
        for field in ("thread_id", "previous_thread_id"):
            registered = item.get(field, "")
            if registered and not isinstance(registered, str):
                raise ValueError(f"会话启动状态 {field} 无效")
            if registered:
                formal_owners[registered] = f"{department}.{field}"
    temporary_owners: dict[str, str] = {}
    for path in task_files():
        other = read_task(path.stem)
        other_temp = other.get("temporary_executor")
        if not isinstance(other_temp, dict):
            continue
        validate_extension(other)
        other_session = other_temp.get("temporary_session")
        registered = other_session.get("thread_id", "") if isinstance(other_session, dict) else ""
        if not registered:
            continue
        if registered in formal_owners:
            raise ValueError(
                f"持久状态 thread_id 重复: {registered} ({formal_owners[registered]}, {path.stem})"
            )
        if registered in temporary_owners:
            raise ValueError(
                f"持久状态 thread_id 重复: {registered} ({temporary_owners[registered]}, {path.stem})"
            )
        temporary_owners[registered] = path.stem
    return formal_owners, temporary_owners


def require_unique_thread_id(task_id: str, thread_id: str) -> None:
    formal_owners, temporary_owners = registered_thread_owners()
    if thread_id in formal_owners:
        department, field = formal_owners[thread_id].rsplit(".", 1)
        identity = "当前会话" if field == "thread_id" else "待归档旧会话"
        raise ValueError(f"thread-id 已被正式部门{identity}占用: {department}")
    owner = temporary_owners.get(thread_id)
    if owner and owner != task_id:
        raise ValueError(f"thread-id 已被其他临时 TASK 占用: {owner}")


def cmd_session(args: argparse.Namespace) -> int:
    task, temp = temp_task(args.task_id)
    state = args.state
    allowed = {
        "active": {"provisioning", "awaiting_rule_confirmation", "failed"}, "standby": {"active"},
        "archived": {"standby", "archived"},
        "failed": {"provisioning", "awaiting_rule_confirmation", "active"},
    }
    if state == "cancelled":
        raise ValueError("临时会话状态转换非法：cancelled 只由无真实会话的 abandoned cleanup 内部写入")
    if state not in allowed or temp["temporary_session"]["state"] not in allowed[state]:
        raise ValueError("临时会话状态转换非法")
    if state in {"active", "standby", "failed"}:
        require_verified_provision(temp)
    evidence = clean("evidence", args.evidence, max_chars=1000)
    if state == "active":
        thread_id = clean_thread_id(args.thread_id)
        registered_thread_id = temp["temporary_session"].get("thread_id", "")
        if registered_thread_id and thread_id != registered_thread_id:
            raise ValueError("临时会话已绑定真实 thread_id，重试 active 必须使用原始 ID")
        if clean("rule-digest", args.rule_digest, max_chars=128) != temp["rule"]["digest"]:
            raise ValueError("临时会话确认的规则 digest 不匹配")
        with identity_lock():
            require_unique_thread_id(args.task_id, thread_id)
            temp["temporary_session"].update(state=state, thread_id=thread_id, evidence=evidence)
            temp["rule"]["confirmed_at"] = now_iso()
            write_task(task, expected_revision=task["revision"])
        print(f"TEMP_SESSION_OK | {args.task_id} | {state}")
        return 0
    elif state == "failed" and args.thread_id:
        thread_id = clean_thread_id(args.thread_id)
        registered_thread_id = temp["temporary_session"].get("thread_id", "")
        if registered_thread_id and thread_id != registered_thread_id:
            raise ValueError("临时会话已绑定真实 thread_id，failed 只能保留原始 ID")
        with identity_lock():
            require_unique_thread_id(args.task_id, thread_id)
            temp["temporary_session"].update(state=state, thread_id=thread_id, evidence=evidence)
            write_task(task, expected_revision=task["revision"])
        print(f"TEMP_SESSION_OK | {args.task_id} | {state}")
        return 0
    elif state == "archived":
        session, thread_id = archive_ready_session(temp)
        if not valid_archive_receipt(evidence, thread_id):
            raise ValueError(
                "归档回执必须绑定当前 thread_id、包含 archived=true，并注明 host 或 user_confirmation"
            )
        if session["state"] == "archived":
            print(f"TEMP_SESSION_OK | {args.task_id} | archived | idempotent")
            return 0
        session.update(state=state, evidence=evidence)
    else:
        temp["temporary_session"].update(state=state, evidence=evidence)
    write_task(task, expected_revision=task["revision"])
    print(f"TEMP_SESSION_OK | {args.task_id} | {state}")
    return 0


def cmd_reconcile_rule(args: argparse.Namespace) -> int:
    task, temp = temp_task(args.task_id)
    if task["execution_state"] != "claimed" or temp["promotion_state"] != "not_submitted":
        raise ValueError("当前生命周期不能重建临时规则")
    require_verified_provision(temp)
    temp["rule"]["digest"] = write_current_rule(task, temp)
    temp["rule"]["confirmed_at"] = ""
    temp["temporary_session"]["state"] = "awaiting_rule_confirmation"
    temp["temporary_session"]["evidence"] = clean("evidence", args.evidence, max_chars=1000)
    write_task(task, expected_revision=task["revision"])
    print(f"TEMP_RULE_RECONCILE_OK | {args.task_id} | rule_sha256:{temp['rule']['digest']}")
    return 0


def cmd_amend(args: argparse.Namespace) -> int:
    task, temp = temp_task(args.task_id)
    if task["execution_state"] != "claimed" or temp["promotion_state"] != "not_submitted":
        raise ValueError("当前生命周期不允许直接 amend；已 submit 或接管的 TASK 必须先 rework")
    require_verified_provision(temp)
    require_current_rule_confirmation(temp)
    if temp["brief_revision"] != args.expected_brief_revision:
        raise ValueError("expected-brief-revision 已过期")
    base = temp["impact"]["base_revision"]
    impact = impact_from_args(args, args.task_id, base)
    admission, reasons = admission_for(args.task_id, impact)
    impact["admission"] = admission
    temp["current_brief"] = clean("current-brief", args.current_brief)
    temp["brief_revision"] += 1
    temp["impact"] = impact
    task["impact_declaration"] = impact
    temp["candidate"] = None
    temp["review"] = None
    temp["delivery"] = None
    temp["integration"] = None
    temp["promotion_state"] = "not_submitted"
    temp["promotion_operation"] = None
    temp["cleanup_operation"] = None
    temp["attempt"] += 1
    temp["candidate_revision"] += 1
    temp["user_acceptance"] = {"state": "pending", "evidence": "", "candidate_revision": temp["candidate_revision"], "candidate_digest": ""}
    temp["rule"]["confirmed_at"] = ""
    temp["temporary_session"]["state"] = "awaiting_rule_confirmation"
    temp["rule"]["digest"] = write_current_rule(task, temp)
    if admission != "safe":
        task["execution_state"] = "blocked"
        task["block_reason"] = f"brief amend 后并行判断为 {admission}: {'；'.join(reasons)}"
    write_task(task, expected_revision=task["revision"])
    print(f"TEMP_AMEND_OK | {args.task_id} | brief_revision:{temp['brief_revision']} | admission:{admission}")
    return 0 if admission == "safe" else 4


def cmd_accept(args: argparse.Namespace) -> int:
    task, temp = temp_task(args.task_id)
    if args.state not in USER_ACCEPTANCE - {"pending"}:
        raise ValueError("用户验收状态无效")
    if task["execution_state"] != "claimed" or temp["promotion_state"] != "not_submitted":
        raise ValueError("只有正在执行的临时 TASK 可以记录用户验收")
    require_verified_provision(temp)
    candidate = temp.get("candidate")
    if not isinstance(candidate, dict):
        raise ValueError("必须先固定候选，再把用户验收绑定到候选 commit/tree")
    temp["user_acceptance"] = {
        "state": args.state, "evidence": clean("evidence", args.evidence, max_chars=1000),
        "candidate_revision": candidate["revision"], "candidate_digest": candidate["digest"],
    }
    write_task(task, expected_revision=task["revision"])
    print(f"TEMP_ACCEPTANCE_OK | {args.task_id} | {args.state}")
    return 0


def cmd_pause(args: argparse.Namespace) -> int:
    task, temp = temp_task(args.task_id)
    if task["execution_state"] != "claimed" or temp["promotion_state"] != "not_submitted":
        raise ValueError("当前临时 TASK 不能暂停")
    require_verified_provision(temp)
    task["execution_state"] = args.state
    task["block_reason"] = clean("reason", args.reason)
    if temp["temporary_session"]["state"] == "active":
        temp["temporary_session"]["state"] = "standby"
    write_task(task, expected_revision=task["revision"])
    print(f"TEMP_PAUSE_OK | {args.task_id} | {args.state}")
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    task, temp = temp_task(args.task_id)
    if task["execution_state"] not in {"blocked", "waiting_input"} or temp["promotion_state"] != "not_submitted":
        raise ValueError("当前临时 TASK 不能恢复")
    require_verified_provision(temp)
    admission, reasons = admission_for(args.task_id, temp["impact"])
    temp["impact"]["admission"] = admission
    task["impact_declaration"] = temp["impact"]
    if admission != "safe":
        task["block_reason"] = f"resume 重新准入为 {admission}: {'；'.join(reasons)}"
        if temp["temporary_session"]["state"] == "active":
            temp["temporary_session"]["state"] = "standby"
        write_task(task, expected_revision=task["revision"])
        print(f"TEMP_RESUME_BLOCKED | {args.task_id} | admission:{admission} | {'；'.join(reasons)}")
        return 4
    task["execution_state"] = "claimed"
    task["block_reason"] = ""
    temp["temporary_session"]["evidence"] = clean("evidence", args.evidence, max_chars=1000)
    temp["temporary_session"]["state"] = "active" if temp["rule"]["confirmed_at"] else "awaiting_rule_confirmation"
    write_task(task, expected_revision=task["revision"])
    print(f"TEMP_RESUME_OK | {args.task_id} | {temp['temporary_session']['state']}")
    return 0


def workspace_for(temp: dict) -> Path:
    workspace = PROJECT / temp["workspace"]["path"]
    marker = workspace / ".agent-team" / "ownership.json"
    if workspace.is_symlink() or not workspace.is_dir() or marker.is_symlink() or not marker.is_file():
        raise ValueError("临时 workspace 或 ownership marker 缺失")
    payload = read_plain_json(marker, workspace, label="workspace ownership marker")
    if not isinstance(payload, dict):
        raise ValueError("workspace ownership marker 根节点无效")
    expected = {
        "operation_id": temp["operation"]["id"], "task_id": temp["impact"]["owner_task"],
        "executor_id": temp["executor_id"], "workspace": temp["workspace"]["path"],
        "branch": temp["workspace"]["branch"], "base_revision": temp["workspace"]["base_revision"],
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise ValueError("workspace ownership marker 不匹配")
    listing = run_git(PROJECT, "worktree", "list", "--porcelain").stdout.split("\n\n")
    registered = False
    expected_branch = f"branch refs/heads/{temp['workspace']['branch']}"
    for block in listing:
        lines = block.splitlines()
        if f"worktree {workspace.resolve()}" in lines and expected_branch in lines:
            registered = True
            break
    if not registered:
        raise ValueError("workspace 未以预期 branch 登记在 Git worktree 列表")
    return workspace


def changed_paths(base: str, commit: str) -> list[str]:
    output = run_git(PROJECT, "diff", "--name-only", "-z", f"{base}..{commit}").stdout
    return [item for item in output.split("\0") if item]


def cmd_candidate(args: argparse.Namespace) -> int:
    task, temp = temp_task(args.task_id)
    if task["execution_state"] != "claimed" or temp["promotion_state"] != "not_submitted":
        raise ValueError("只有正在执行且未 submit 的临时 TASK 可固定候选")
    require_verified_provision(temp)
    require_current_rule_confirmation(temp)
    workspace = workspace_for(temp)
    if run_git(workspace, "status", "--porcelain").stdout.strip():
        raise ValueError("候选固定前 workspace 必须干净")
    revision_name = clean_revision("commit", args.commit)
    commit = run_git(workspace, "rev-parse", "--verify", f"{revision_name}^{{commit}}").stdout.strip()
    branch_head = run_git(workspace, "rev-parse", "HEAD").stdout.strip()
    if commit != branch_head:
        raise ValueError("候选 commit 必须等于 workspace 当前 HEAD")
    paths = changed_paths(temp["workspace"]["base_revision"], commit)
    outside = [path for path in paths if not any(overlap(path, allowed) for allowed in temp["impact"]["write_paths"])]
    if outside:
        raise ValueError("候选包含越界路径: " + ", ".join(outside))
    if any(path == "docs/collaboration" or path.startswith("docs/collaboration/") for path in paths):
        raise ValueError("候选修改了无权威的协作层副本")
    tree = run_git(PROJECT, "rev-parse", f"{commit}^{{tree}}").stdout.strip()
    temp["candidate_revision"] += 1
    temp["candidate"] = {
        "revision": temp["candidate_revision"], "kind": "git_commit", "locator": commit,
        "digest": tree, "brief_revision": temp["brief_revision"], "created_at": now_iso(),
    }
    temp["review"] = None
    temp["delivery"] = None
    temp["user_acceptance"] = {
        "state": "pending", "evidence": "", "candidate_revision": temp["candidate_revision"],
        "candidate_digest": tree,
    }
    write_task(task, expected_revision=task["revision"])
    print(f"TEMP_CANDIDATE_OK | {args.task_id} | revision:{temp['candidate_revision']} | commit:{commit} | tree:{tree}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    task, temp = temp_task(args.task_id)
    if (
        task["execution_state"] != "claimed"
        or temp["promotion_state"] != "not_submitted"
        or temp["temporary_session"]["state"] != "active"
    ):
        raise ValueError("独立审查只能绑定尚未 submit 的活跃候选")
    require_verified_provision(temp)
    candidate = temp["candidate"]
    if not candidate or candidate["revision"] != args.candidate_revision:
        raise ValueError("审查目标候选不存在或 revision 已失效")
    temp["review"] = {
        "candidate_revision": args.candidate_revision, "decision": args.decision,
        "evidence": clean("evidence", args.evidence, max_chars=1000), "reviewed_at": now_iso(),
    }
    write_task(task, expected_revision=task["revision"])
    print(f"TEMP_REVIEW_OK | {args.task_id} | {args.decision} | revision:{args.candidate_revision}")
    return 0


def cmd_submit(args: argparse.Namespace) -> int:
    task, temp = temp_task(args.task_id)
    if task["execution_state"] != "claimed" or temp["promotion_state"] != "not_submitted":
        raise ValueError("只有正在执行且未 submit 的临时 TASK 可提交 delivery")
    require_verified_provision(temp)
    require_current_rule_confirmation(temp)
    candidate = temp["candidate"]
    if not candidate or candidate["revision"] != args.candidate_revision:
        raise ValueError("提交候选不存在或已失效")
    acceptance = temp["user_acceptance"]
    if acceptance.get("state") not in {"confirmed", "delegated", "not_applicable"}:
        raise ValueError("当前候选尚未获得用户确认、明确委托或不适用记录")
    if acceptance.get("candidate_revision") != candidate["revision"] or acceptance.get("candidate_digest") != candidate["digest"]:
        raise ValueError("用户验收证据没有绑定当前候选 commit/tree")
    if temp["review"] and temp["review"]["candidate_revision"] == args.candidate_revision and temp["review"]["decision"] != "pass":
        raise ValueError("当前候选独立审查未通过")
    workspace = workspace_for(temp)
    if run_git(workspace, "status", "--porcelain").stdout.strip():
        raise ValueError("submit 前 workspace 必须干净")
    workspace_head = run_git(workspace, "rev-parse", "HEAD").stdout.strip()
    if workspace_head != candidate["locator"]:
        raise ValueError("submit 时 workspace HEAD 与固定候选不一致，需重新固定候选并验收")
    workspace_tree = run_git(workspace, "rev-parse", "HEAD^{tree}").stdout.strip()
    candidate_tree = run_git(PROJECT, "rev-parse", f"{candidate['locator']}^{{tree}}").stdout.strip()
    if workspace_tree != candidate["digest"] or candidate_tree != candidate["digest"]:
        raise ValueError("submit 时 workspace tree 与固定候选 digest 不一致")
    protected_ref = f"refs/agent-team/deliveries/{args.task_id}-C{args.candidate_revision}"
    existing = run_git(PROJECT, "rev-parse", "--verify", protected_ref, ok=False)
    if existing.returncode == 0 and existing.stdout.strip() != candidate["locator"]:
        raise ValueError("delivery 保护 ref 已指向其他候选")
    run_git(PROJECT, "update-ref", protected_ref, candidate["locator"])
    temp["delivery"] = {
        "candidate_revision": args.candidate_revision, "locator": candidate["locator"],
        "digest": candidate["digest"], "protected_ref": protected_ref,
        "evidence": clean("evidence", args.evidence, max_chars=1000), "submitted_at": now_iso(),
    }
    temp["promotion_state"] = "submitted"
    temp["temporary_session"]["state"] = "standby"
    task["execution_state"] = "completed"
    task["artifacts"] = [temp["workspace"]["path"]]
    task["verified"] = ["候选 commit、允许路径、workspace 清洁状态和 delivery 保护 ref 已验证"]
    task["unverified"] = ["尚未完成正式集成验证与知识吸收"]
    task["mistake_check"] = "临时交付未冒充正式集成"
    task["report"] = "不适用"
    write_task(task, expected_revision=task["revision"])
    print(f"TEMP_SUBMIT_OK | {args.task_id} | delivery:{candidate['locator']} | protected:{protected_ref}")
    return 0


def cmd_rework(args: argparse.Namespace) -> int:
    task, temp = temp_task(args.task_id)
    if task["execution_state"] not in {"completed", "acknowledged", "blocked", "waiting_input"}:
        raise ValueError("当前 TASK 不在可返工状态")
    if temp["promotion_state"] in {"integrated", "archived", "abandoned", "cancelled"}:
        raise ValueError("已集成、归档、放弃或取消的临时 TASK 不能返工")
    require_verified_provision(temp)
    require_settled_promotion(temp)
    admission, reasons = admission_for(args.task_id, temp["impact"])
    temp["impact"]["admission"] = admission
    task["impact_declaration"] = temp["impact"]
    if admission != "safe":
        if task["execution_state"] in {"blocked", "waiting_input"}:
            task["block_reason"] = f"rework 重新准入为 {admission}: {'；'.join(reasons)}"
        write_task(task, expected_revision=task["revision"])
        print(f"TEMP_REWORK_BLOCKED | {args.task_id} | admission:{admission} | {'；'.join(reasons)}")
        return 4
    invalidated_attempt = temp["attempt"]
    temp["attempt"] += 1
    temp["candidate"] = None
    temp["review"] = None
    temp["delivery"] = None
    temp["integration"] = None
    temp["promotion_state"] = "not_submitted"
    temp["promotion_operation"] = None
    temp["cleanup_operation"] = None
    prior_absorption = dict(temp["absorption"])
    absorption_history = list(prior_absorption.get("history", []))
    absorption_history.append({
        "attempt": invalidated_attempt, "snapshot": {
            key: prior_absorption.get(key) for key in ("preflight", "parent_department", "project_global", "final")
        }, "receipts": list(prior_absorption.get("receipts", [])), "invalidated_at": now_iso(),
    })
    temp["absorption"] = {
        "preflight": "pending", "parent_department": "pending", "project_global": "pending",
        "final": "pending", "receipts": [], "history": absorption_history,
    }
    temp["candidate_revision"] += 1
    temp["user_acceptance"] = {"state": "pending", "evidence": "", "candidate_revision": temp["candidate_revision"], "candidate_digest": ""}
    temp["temporary_session"]["state"] = "awaiting_rule_confirmation"
    temp["temporary_session"]["evidence"] = clean("evidence", args.evidence, max_chars=1000)
    temp["rule"]["confirmed_at"] = ""
    temp["rule"]["digest"] = write_current_rule(task, temp)
    task["execution_state"] = "claimed"
    task.pop("acknowledged_by", None)
    task["block_reason"] = ""
    task["artifacts"] = []
    task["external_artifacts"] = []
    task["verified"] = []
    task["unverified"] = []
    task["mistake_check"] = ""
    task["report"] = ""
    task["event_receipts"] = []
    write_task(task, expected_revision=task["revision"])
    print(f"TEMP_REWORK_OK | {args.task_id} | attempt:{temp['attempt']} | rule_sha256:{temp['rule']['digest']}")
    return 0


def cmd_acknowledge(args: argparse.Namespace) -> int:
    task, temp = temp_task(args.task_id)
    actor = clean("acknowledged-by", args.acknowledged_by, max_chars=300)
    expected = registered_lead_actor()
    if actor != expected:
        raise ValueError(f"acknowledged-by 必须匹配当前登记统筹会话: {expected}")
    abandoned_archived = (
        task["execution_state"] == "completed"
        and temp["promotion_state"] == "archived"
        and bool(temp.get("operation", {}).get("abandon_evidence"))
    )
    if abandoned_archived:
        task["execution_state"] = "acknowledged"
        task["acknowledged_by"] = actor
        write_task(task, expected_revision=task["revision"])
        print(f"TEMP_ACK_ABANDONED_OK | {args.task_id} | {task['acknowledged_by']}")
        return 0
    if task["execution_state"] != "completed" or temp["promotion_state"] != "submitted":
        raise ValueError("只有已 submit 的临时交付或已清理的 abandoned 任务可以核收")
    require_verified_provision(temp)
    task["execution_state"] = "acknowledged"
    task["acknowledged_by"] = actor
    temp["promotion_state"] = "reviewing"
    write_task(task, expected_revision=task["revision"])
    print(f"TEMP_ACK_OK | {args.task_id} | {task['acknowledged_by']}")
    return 0


def cmd_absorb(args: argparse.Namespace) -> int:
    task, temp = temp_task(args.task_id)
    field = args.scope.replace("-", "_")
    if args.scope == "preflight":
        if task["execution_state"] != "acknowledged" or temp["promotion_state"] not in {"reviewing", "waiting_base", "ready", "integrated"}:
            raise ValueError("前置知识清点必须在统筹接管 delivery 后进行")
    else:
        integration = temp.get("integration")
        if temp["promotion_state"] != "integrated" or not isinstance(integration, dict) or integration.get("result") != "pass":
            raise ValueError("所属部门、项目全局和最终吸收必须在正式验证通过并 integrated 后进行")
    require_verified_provision(temp)
    require_settled_promotion(temp)
    require_cleanup_not_started(temp)
    temp["absorption"][field] = args.state
    temp["absorption"]["receipts"].append({
        "scope": args.scope, "state": args.state, "evidence": clean("evidence", args.evidence, max_chars=1000),
        "at": now_iso(),
    })
    if args.scope == "final" and args.state == "completed":
        for required in ("preflight", "parent_department", "project_global"):
            if temp["absorption"][required] not in {"completed", "not_applicable"}:
                raise ValueError(f"最终吸收前 {required} 尚未收口")
    write_task(task, expected_revision=task["revision"])
    print(f"TEMP_ABSORPTION_OK | {args.task_id} | {args.scope}:{args.state}")
    return 0


YAML_FIELD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)$")


def yaml_scalar(value: str, *, field: str) -> str:
    scalar = value.strip()
    if not scalar:
        raise ValueError(f"正式测试报告 YAML 字段为空: {field}")
    if scalar.startswith('"') or scalar.endswith('"'):
        if not (scalar.startswith('"') and scalar.endswith('"')):
            raise ValueError(f"正式测试报告 YAML 引号无效: {field}")
        try:
            decoded = json.loads(scalar)
        except json.JSONDecodeError as exc:
            raise ValueError(f"正式测试报告 YAML 字符串无效: {field}") from exc
        if not isinstance(decoded, str):
            raise ValueError(f"正式测试报告 YAML 字段必须是标量: {field}")
        return decoded
    if scalar.startswith("'") or scalar.endswith("'"):
        if not (scalar.startswith("'") and scalar.endswith("'")):
            raise ValueError(f"正式测试报告 YAML 引号无效: {field}")
        return scalar[1:-1].replace("''", "'")
    return scalar


def audit_report_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        raise ValueError("正式测试报告缺少文件开头 YAML frontmatter")
    closing = text.find("\n---\n", 4)
    if closing < 0:
        raise ValueError("正式测试报告 YAML frontmatter 未闭合")
    fields: dict[str, str] = {}
    for line_number, line in enumerate(text[4:closing].splitlines(), start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line != line.lstrip():
            raise ValueError(f"正式测试报告 YAML 只允许顶层单行字段: line {line_number}")
        match = YAML_FIELD_RE.fullmatch(line)
        if not match:
            raise ValueError(f"正式测试报告 YAML 字段格式无效: line {line_number}")
        key, raw_value = match.groups()
        if key in fields:
            raise ValueError(f"正式测试报告 YAML 含重复字段: {key}")
        fields[key] = yaml_scalar(raw_value, field=key)
    return fields


def formal_test_evidence(args: argparse.Namespace, *, commit: str, tree: str, result: str) -> tuple[str, str]:
    test_task = read_task(args.test_task_id)
    if test_task.get("execution_state") != "acknowledged" or test_task.get("completion_class") != "audit":
        raise ValueError("正式测试证据必须来自已完成并由统筹核收的审核层 TASK")
    state = read_plain_json(COLLAB / "会话启动状态.json", COLLAB, label="会话启动状态")
    departments = state.get("departments") if isinstance(state, dict) else None
    department_state = departments.get(test_task.get("department")) if isinstance(departments, dict) else None
    if (
        not isinstance(department_state, dict)
        or department_state.get("role_id") not in {"review", "test", "security", "finance"}
    ):
        raise ValueError("正式测试 TASK 声称 audit，但会话真值中该部门不属于审核层")
    expected_pointer = f"docs/collaboration/tasks/{args.task_id}.json"
    if expected_pointer not in test_task.get("pointers", []):
        raise ValueError("正式测试 TASK 未指向当前临时 TASK")
    report_relative = safe_project_artifact(args.report, field="report")
    if test_task.get("report") != report_relative or report_relative not in test_task.get("artifacts", []):
        raise ValueError("正式测试报告未作为测试 TASK 的权威产物提交")
    report = PROJECT / report_relative
    if report.is_symlink() or not report.is_file():
        raise ValueError("正式测试报告不存在或路径不安全")
    expected_root = f"docs/collaboration/部门/{test_task['department']}/报告/"
    if not report_relative.startswith(expected_root):
        raise ValueError("正式测试报告不在测试部门报告目录")
    text = report.read_text(encoding="utf-8-sig")
    fields = audit_report_frontmatter(text)
    expected_fields = {
        "type": "audit_report",
        "department": test_task["department"],
        "target": args.task_id,
        "status": "final",
        "related_task": args.test_task_id,
        "decision": result,
        "tested_commit": commit,
        "tested_tree": tree,
        "result": result,
    }
    missing = sorted(key for key in expected_fields if key not in fields)
    if missing:
        raise ValueError("正式测试报告 YAML 缺少字段: " + ", ".join(missing))
    mismatched = sorted(key for key, expected in expected_fields.items() if fields.get(key) != expected)
    if mismatched:
        raise ValueError("正式测试报告 YAML 未精确绑定当前审核任务、commit、tree 和结论: " + ", ".join(mismatched))
    return args.test_task_id, report_relative


def cmd_integration(args: argparse.Namespace) -> int:
    task, temp = temp_task(args.task_id)
    if (
        task["execution_state"] != "acknowledged"
        or not temp["delivery"]
        or temp["promotion_state"] not in {"reviewing", "waiting_base", "ready"}
    ):
        raise ValueError("统筹部接管 delivery 后才能记录正式集成候选")
    require_verified_provision(temp)
    require_settled_promotion(temp)
    commit_name = clean_revision("commit", args.commit)
    base_name = clean_revision("tested-base", args.tested_base)
    commit = run_git(PROJECT, "rev-parse", "--verify", f"{commit_name}^{{commit}}").stdout.strip()
    base = run_git(PROJECT, "rev-parse", "--verify", f"{base_name}^{{commit}}").stdout.strip()
    tree = run_git(PROJECT, "rev-parse", f"{commit}^{{tree}}").stdout.strip()
    if args.result == "pass" and run_git(PROJECT, "merge-base", "--is-ancestor", base, commit, ok=False).returncode != 0:
        raise ValueError("正式候选不包含 tested_base")
    delivery_commit = temp["delivery"]["locator"]
    if run_git(PROJECT, "merge-base", "--is-ancestor", delivery_commit, commit, ok=False).returncode != 0:
        raise ValueError("正式候选没有吸收 delivery commit")
    test_task_id, report_relative = formal_test_evidence(args, commit=commit, tree=tree, result=args.result)
    temp["integration"] = {
        "candidate_commit": commit, "tested_base": base, "tested_commit": commit,
        "tree_oid": tree, "test_definition": clean("test-definition", args.test_definition),
        "environment": clean("environment", args.environment),
        "evidence": clean("evidence", args.evidence, max_chars=1000),
        "test_task_id": test_task_id, "report": report_relative,
        "unverified": clean("unverified", args.unverified), "result": args.result, "tested_at": now_iso(),
    }
    temp["promotion_state"] = "ready" if args.result == "pass" else "reviewing"
    write_task(task, expected_revision=task["revision"])
    print(f"TEMP_INTEGRATION_OK | {args.task_id} | {args.result} | tree:{tree}")
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    task, temp = temp_task(args.task_id)
    integration = temp["integration"]
    if not integration or integration["result"] != "pass" or temp["promotion_state"] != "ready":
        raise ValueError("没有可晋升的已测试候选")
    require_verified_provision(temp)
    require_settled_promotion(temp)
    main_branch = clean_branch(args.main_branch)
    current = run_git(PROJECT, "rev-parse", main_branch).stdout.strip()
    if current != integration["tested_base"]:
        raise ValueError("main 已漂移，原测试证据失效")
    candidate_tree = run_git(PROJECT, "rev-parse", f"{integration['candidate_commit']}^{{tree}}").stdout.strip()
    if candidate_tree != integration["tree_oid"]:
        raise ValueError("候选 tree 与已测试 tree 不一致")
    dirty = product_dirty_paths()
    if dirty:
        raise ValueError("主工作区存在未解释的产品改动，拒绝晋升: " + ", ".join(dirty))
    current_branch = run_git(PROJECT, "branch", "--show-current").stdout.strip()
    if current_branch != main_branch:
        raise ValueError("主 worktree 当前未检出目标 main 分支")
    operation_id = "PROMOTE-" + dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8].upper()
    temp["promotion_operation"] = {
        "id": operation_id, "state": "planned", "main_branch": main_branch,
        "expected_old": current, "candidate_commit": integration["candidate_commit"],
        "tree_oid": integration["tree_oid"], "history": [{"state": "planned", "at": now_iso()}],
    }
    write_task(task, expected_revision=task["revision"])
    started_task, started_temp = temp_task(args.task_id)
    started_temp["promotion_operation"]["state"] = "started"
    started_temp["promotion_operation"]["history"].append({"state": "started", "at": now_iso()})
    write_task(started_task, expected_revision=started_task["revision"])
    try:
        run_git(PROJECT, "merge", "--ff-only", integration["candidate_commit"])
    except Exception as exc:
        failed_task, failed_temp = temp_task(args.task_id)
        failed_temp["promotion_operation"]["state"] = "failed"
        failed_temp["promotion_operation"]["history"].append({"state": "failed", "at": now_iso(), "reason": str(exc)})
        write_task(failed_task, expected_revision=failed_task["revision"])
        raise
    promoted = run_git(PROJECT, "rev-parse", f"{main_branch}^{{tree}}").stdout.strip()
    if promoted != integration["tree_oid"]:
        raise ValueError("晋升后 main tree 与已测试 tree 不一致")
    final_task, final_temp = temp_task(args.task_id)
    final_integration = final_temp["integration"]
    final_temp["promotion_state"] = "integrated"
    final_integration["promoted_at"] = now_iso()
    final_integration["main_branch"] = main_branch
    final_temp["promotion_operation"]["state"] = "verified"
    final_temp["promotion_operation"]["history"].extend([
        {"state": "succeeded", "at": now_iso()}, {"state": "verified", "at": now_iso()},
    ])
    write_task(final_task, expected_revision=final_task["revision"])
    print(f"TEMP_PROMOTE_OK | {args.task_id} | {main_branch} | tree:{promoted}")
    return 0


def cmd_reconcile_promotion(args: argparse.Namespace) -> int:
    task, temp = temp_task(args.task_id)
    operation = temp.get("promotion_operation")
    if not isinstance(operation, dict):
        raise ValueError("没有可 reconcile 的 promotion operation")
    if temp["promotion_state"] == "integrated" and operation.get("state") == "verified":
        print(f"TEMP_PROMOTION_RECONCILE_OK | {args.task_id} | integrated | idempotent")
        return 0
    if temp["promotion_state"] != "ready":
        raise ValueError("只有 ready 且晋升事务未完成的临时 TASK 可 reconcile promotion")
    if temp["workspace"]["state"] == "removed" or (
        isinstance(temp.get("cleanup_operation"), dict)
        and temp["cleanup_operation"].get("state") == "verified"
    ):
        raise ValueError("临时资源已清理，不能重新打开晋升状态")
    current = run_git(PROJECT, "rev-parse", operation["main_branch"]).stdout.strip()
    if current == operation["candidate_commit"]:
        tree = run_git(PROJECT, "rev-parse", f"{current}^{{tree}}").stdout.strip()
        if tree != operation["tree_oid"]:
            raise ValueError("main 已到候选 commit，但 tree 与测试证据不一致")
        temp["promotion_state"] = "integrated"
        temp["integration"]["promoted_at"] = now_iso()
        temp["integration"]["main_branch"] = operation["main_branch"]
        operation["state"] = "verified"
        operation["history"].append({"state": "verified", "at": now_iso(), "via": "reconcile"})
        write_task(task, expected_revision=task["revision"])
        print(f"TEMP_PROMOTION_RECONCILE_OK | {args.task_id} | integrated")
        return 0
    if current == operation["expected_old"]:
        history = operation.get("history", [])
        if (
            operation.get("state") == "failed"
            and history
            and history[-1].get("state") == "failed"
            and history[-1].get("via") == "reconcile"
        ):
            print(f"TEMP_PROMOTION_RECONCILE_BLOCKED | {args.task_id} | main-not-advanced | idempotent")
            return 4
        operation["state"] = "failed"
        operation["history"].append({
            "state": "failed", "at": now_iso(), "reason": "main-not-advanced", "via": "reconcile",
        })
        write_task(task, expected_revision=task["revision"])
        print(f"TEMP_PROMOTION_RECONCILE_BLOCKED | {args.task_id} | main-not-advanced")
        return 4
    raise ValueError("main 位于 expected_old 与 candidate 之外，拒绝猜测恢复")


def cmd_abandon(args: argparse.Namespace) -> int:
    task, temp = temp_task(args.task_id)
    if temp["promotion_state"] in {"integrated", "archived", "cancelled"}:
        raise ValueError("已集成、归档或取消的临时 TASK 不能改为 abandoned")
    if temp["promotion_state"] == "abandoned":
        raise ValueError("临时 TASK 已是 abandoned，不能重复改写放弃证据")
    require_verified_provision(temp)
    require_settled_promotion(temp)
    temp["promotion_state"] = "abandoned"
    temp["temporary_session"]["state"] = "standby"
    temp["operation"]["abandon_evidence"] = clean("evidence", args.evidence, max_chars=1000)
    write_task(task, expected_revision=task["revision"])
    print(f"TEMP_ABANDONED | {args.task_id}")
    return 0


def finalize_abandoned_task(task: dict, temp: dict) -> None:
    """Close the ordinary TASK axis after an abandoned workspace is gone."""
    abandon_evidence = temp.get("operation", {}).get("abandon_evidence", "")
    if not abandon_evidence or task.get("execution_state") in {"completed", "acknowledged"}:
        return
    task["execution_state"] = "completed"
    task.pop("acknowledged_by", None)
    task["block_reason"] = ""
    task["artifacts"] = [f"docs/collaboration/tasks/{task['task_id']}.json"]
    task["external_artifacts"] = []
    task["verified"] = [
        "用户已明确放弃当前临时候选；专属 workspace 与 branch 均已清理，TASK 真值已归档"
    ]
    task["unverified"] = [
        "放弃任务未形成可集成 delivery，未执行正式测试、晋升、打包、发布或外发"
    ]
    task["mistake_check"] = "abandoned 只表示用户明确终止本次临时执行，未冒充正式交付或集成完成"
    task["report"] = "不适用"
    task["event_receipts"] = []


def cmd_cleanup(args: argparse.Namespace) -> int:
    task, temp = temp_task(args.task_id)
    if temp["promotion_state"] == "integrated":
        if temp["absorption"]["final"] != "completed":
            raise ValueError("成果已集成但知识吸收尚未最终收口")
    elif temp["promotion_state"] != "abandoned":
        raise ValueError("只有 integrated 或用户明确 abandoned 后可以清理")
    require_verified_provision(temp)
    prior_cleanup = temp.get("cleanup_operation")
    if isinstance(prior_cleanup, dict):
        history = prior_cleanup.get("history", [])
        reconciled_failure = (
            prior_cleanup.get("state") == "failed"
            and bool(history)
            and history[-1].get("state") == "failed"
            and history[-1].get("via") == "reconcile"
        )
        if not reconciled_failure:
            raise ValueError("存在未收口的清理事务，必须先 reconcile cleanup")
    session = temp["temporary_session"]
    if session["state"] != "standby":
        raise ValueError("清理前临时会话必须处于 standby")
    if temp["promotion_state"] != "abandoned" and not session.get("thread_id"):
        raise ValueError("已交付临时任务缺少真实 thread_id，不能完成清理")
    delivery = temp.get("delivery")
    if delivery is not None:
        protected_ref = delivery.get("protected_ref", "")
        protected = run_git(PROJECT, "rev-parse", "--verify", f"{protected_ref}^{{commit}}", ok=False)
        if protected.returncode != 0 or protected.stdout.strip() != delivery.get("locator"):
            raise ValueError("delivery 保护 ref 缺失或已漂移，拒绝清理")
        protected_tree = run_git(PROJECT, "rev-parse", f"{protected.stdout.strip()}^{{tree}}", ok=False)
        if protected_tree.returncode != 0 or protected_tree.stdout.strip() != delivery.get("digest"):
            raise ValueError("delivery commit 或 tree 已不可读取，拒绝清理")
    elif temp["promotion_state"] != "abandoned":
        raise ValueError("已集成 TASK 缺失 delivery 证据")
    workspace = workspace_for(temp)
    if run_git(workspace, "status", "--porcelain").stdout.strip():
        raise ValueError("workspace 存在未提交内容，拒绝清理")
    marker = read_plain_json(
        workspace / ".agent-team" / "ownership.json", workspace, label="workspace ownership marker",
    )
    if not isinstance(marker, dict):
        raise ValueError("workspace ownership marker 根节点无效")
    expected_marker = {
        "operation_id": temp["operation"]["id"], "task_id": args.task_id,
        "executor_id": temp["executor_id"], "workspace": temp["workspace"]["path"],
        "branch": temp["workspace"]["branch"], "base_revision": temp["workspace"]["base_revision"],
    }
    if any(marker.get(key) != value for key, value in expected_marker.items()):
        raise ValueError("ownership marker 不匹配，拒绝清理")
    workspace_head = run_git(workspace, "rev-parse", "HEAD").stdout.strip()
    branch = temp["workspace"]["branch"]
    branch_head = run_git(PROJECT, "rev-parse", "--verify", f"refs/heads/{branch}").stdout.strip()
    if branch_head != workspace_head:
        raise ValueError("owned branch 与 workspace HEAD 不一致，拒绝清理")
    evidence = clean("evidence", args.evidence, max_chars=1000)
    operation_id = "CLEANUP-" + dt.datetime.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8].upper()
    temp["cleanup_operation"] = {
        "id": operation_id, "state": "planned", "workspace": temp["workspace"]["path"],
        "branch": branch, "workspace_head": workspace_head, "evidence": evidence,
        "history": [{"state": "planned", "at": now_iso()}],
    }
    write_task(task, expected_revision=task["revision"])
    started_task, started_temp = temp_task(args.task_id)
    started_temp["cleanup_operation"]["state"] = "started"
    started_temp["cleanup_operation"]["history"].append({"state": "started", "at": now_iso()})
    write_task(started_task, expected_revision=started_task["revision"])
    try:
        run_git(PROJECT, "worktree", "remove", str(workspace))
        branch_ref = run_git(PROJECT, "rev-parse", "--verify", f"refs/heads/{branch}", ok=False)
        if branch_ref.returncode == 0:
            run_git(PROJECT, "branch", "-D", branch)
    except Exception as exc:
        failed_task, failed_temp = temp_task(args.task_id)
        failed_temp["cleanup_operation"]["state"] = "failed"
        failed_temp["cleanup_operation"]["history"].append({"state": "failed", "at": now_iso(), "reason": str(exc)})
        write_task(failed_task, expected_revision=failed_task["revision"])
        raise
    final_task, final_temp = temp_task(args.task_id)
    final_temp["workspace"]["state"] = "removed"
    final_temp["promotion_state"] = "archived"
    final_temp["operation"]["state"] = "verified"
    final_temp["operation"]["cleanup_evidence"] = evidence
    final_temp["cleanup_operation"]["state"] = "verified"
    final_temp["cleanup_operation"]["history"].extend([
        {"state": "succeeded", "at": now_iso()}, {"state": "verified", "at": now_iso()},
    ])
    thread_id = final_temp["temporary_session"]["thread_id"]
    if not thread_id:
        final_temp["temporary_session"].update(
            state="cancelled", evidence="未创建真实临时会话，临时资源清理后无需归档",
        )
    finalize_abandoned_task(final_task, final_temp)
    write_task(final_task, expected_revision=final_task["revision"])
    archive_action = f"ARCHIVE_THREAD_REQUIRED:{thread_id}" if thread_id else "NO_THREAD_ARCHIVE_REQUIRED"
    delivery_receipt = "protected_delivery_retained" if delivery is not None else "no_delivery_user_abandoned"
    print(f"TEMP_CLEANUP_OK | {args.task_id} | {delivery_receipt} | {archive_action}")
    return 0


def cmd_reconcile_cleanup(args: argparse.Namespace) -> int:
    task, temp = temp_task(args.task_id)
    operation = temp.get("cleanup_operation")
    if not isinstance(operation, dict):
        raise ValueError("没有可 reconcile 的 cleanup operation")
    workspace = PROJECT / operation["workspace"]
    branch_exists = run_git(PROJECT, "show-ref", "--verify", f"refs/heads/{operation['branch']}", ok=False).returncode == 0
    if (
        temp["promotion_state"] == "archived"
        and temp["workspace"]["state"] == "removed"
        and operation.get("state") == "verified"
    ):
        if workspace.exists() or branch_exists:
            raise ValueError("清理真值已 verified，但 workspace 或 branch 重新出现")
        print(f"TEMP_CLEANUP_RECONCILE_OK | {args.task_id} | resources-archived | idempotent")
        return 0
    if not workspace.exists() and not branch_exists:
        prior_promotion_state = temp["promotion_state"]
        thread_id = temp["temporary_session"]["thread_id"]
        if not thread_id and prior_promotion_state != "abandoned":
            raise ValueError("临时资源已移除但真实 thread_id 缺失，保留 TASK 并转人工核对")
        delivery = temp.get("delivery")
        if delivery:
            protected = run_git(PROJECT, "rev-parse", "--verify", f"{delivery['protected_ref']}^{{commit}}", ok=False)
            if protected.returncode != 0 or protected.stdout.strip() != delivery["locator"]:
                raise ValueError("清理后 delivery 保护证据缺失，不能完成 reconcile")
        temp["workspace"]["state"] = "removed"
        temp["promotion_state"] = "archived"
        operation["state"] = "verified"
        operation["history"].append({"state": "verified", "at": now_iso(), "via": "reconcile"})
        if not thread_id:
            temp["temporary_session"].update(
                state="cancelled", evidence="未创建真实临时会话，临时资源清理后无需归档",
            )
        finalize_abandoned_task(task, temp)
        write_task(task, expected_revision=task["revision"])
        archive_action = f"ARCHIVE_THREAD_REQUIRED:{thread_id}" if thread_id else "NO_THREAD_ARCHIVE_REQUIRED"
        print(f"TEMP_CLEANUP_RECONCILE_OK | {args.task_id} | resources-archived | {archive_action}")
        return 0
    if workspace.exists() and branch_exists:
        history = operation.get("history", [])
        if (
            operation.get("state") == "failed"
            and history
            and history[-1].get("state") == "failed"
            and history[-1].get("via") == "reconcile"
        ):
            print(f"TEMP_CLEANUP_RECONCILE_BLOCKED | {args.task_id} | resources-still-present | idempotent")
            return 4
        operation["state"] = "failed"
        operation["history"].append({
            "state": "failed", "at": now_iso(), "reason": "resources-still-present", "via": "reconcile",
        })
        write_task(task, expected_revision=task["revision"])
        print(f"TEMP_CLEANUP_RECONCILE_BLOCKED | {args.task_id} | resources-still-present")
        return 4
    raise ValueError("workspace 与 branch 只剩一项，保留现场并转人工处理")


def cmd_pending_archives(args: argparse.Namespace) -> int:
    registered_thread_owners()
    pending: list[tuple[str, str]] = []
    for path in task_files():
        task = read_task(path.stem)
        temp = task.get("temporary_executor")
        if not isinstance(temp, dict):
            continue
        validate_extension(task)
        workspace = temp.get("workspace")
        cleanup = temp.get("cleanup_operation")
        session = temp.get("temporary_session")
        if not isinstance(workspace, dict) or not isinstance(cleanup, dict) or not isinstance(session, dict):
            continue
        thread_id = session.get("thread_id")
        cleaned_with_thread = (
            temp.get("promotion_state") == "archived"
            and workspace.get("state") == "removed"
            and cleanup.get("state") == "verified"
            and isinstance(thread_id, str)
            and thread_id
        )
        if not cleaned_with_thread or session.get("state") == "archived":
            continue
        if session.get("state") != "standby":
            raise ValueError(
                f"{task['task_id']} 清理后真实会话状态异常，不能生成归档提醒: {session.get('state')}"
            )
        if session.get("state") == "standby":
            pending.append((task["task_id"], thread_id))
    if not pending:
        print("NO_PENDING_THREAD_ARCHIVES")
        return 0
    for task_id, thread_id in pending:
        print(f"ARCHIVE_THREAD_REQUIRED:{thread_id} | {task_id}")
    return 0


def impact_arguments(command: argparse.ArgumentParser, *, include_base: bool = True) -> None:
    command.add_argument("--write-path", action="append", required=True)
    command.add_argument("--shared-contract", action="append", default=[])
    command.add_argument("--external-effect", action="append", default=[])
    if include_base:
        command.add_argument("--base-revision", default="HEAD")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="agent-team 单 TASK 临时外包工具")
    sub = root.add_subparsers(dest="cmd", required=True)
    declare = sub.add_parser("declare-impact")
    declare.add_argument("--task-id", required=True)
    declare.add_argument("--expected-revision", type=int, required=True)
    impact_arguments(declare)
    declare.set_defaults(func=cmd_declare_impact)
    preflight = sub.add_parser("preflight")
    preflight.add_argument("--task-id", required=True)
    preflight.add_argument("--parent-department", required=True)
    impact_arguments(preflight)
    preflight.set_defaults(func=cmd_preflight)
    provision = sub.add_parser("provision")
    provision.add_argument("--task-id", required=True)
    provision.add_argument("--parent-department", required=True)
    provision.add_argument("--executor-id", required=True)
    provision.add_argument("--display-name", required=True)
    provision.add_argument("--current-brief", required=True)
    provision.add_argument("--client-key", required=True)
    provision.add_argument("--scan-boundary-evidence", required=True)
    impact_arguments(provision)
    provision.set_defaults(func=cmd_provision)
    reconcile = sub.add_parser("reconcile-provision")
    reconcile.add_argument("--task-id", required=True)
    reconcile.set_defaults(func=cmd_reconcile_provision)
    reset_failed = sub.add_parser("reset-failed-provision")
    reset_failed.add_argument("--task-id", required=True)
    reset_failed.add_argument("--evidence", required=True)
    reset_failed.set_defaults(func=cmd_reset_failed_provision)
    session = sub.add_parser("session-mark")
    session.add_argument("--task-id", required=True)
    session.add_argument("--state", choices=("active", "standby", "archived", "failed", "cancelled"), required=True)
    session.add_argument("--thread-id", default="")
    session.add_argument("--rule-digest", default="")
    session.add_argument("--evidence", required=True)
    session.set_defaults(func=cmd_session)
    reconcile_rule = sub.add_parser("reconcile-rule")
    reconcile_rule.add_argument("--task-id", required=True)
    reconcile_rule.add_argument("--evidence", required=True)
    reconcile_rule.set_defaults(func=cmd_reconcile_rule)
    amend = sub.add_parser("amend")
    amend.add_argument("--task-id", required=True)
    amend.add_argument("--expected-brief-revision", type=int, required=True)
    amend.add_argument("--current-brief", required=True)
    impact_arguments(amend, include_base=False)
    amend.set_defaults(func=cmd_amend)
    accept = sub.add_parser("accept")
    accept.add_argument("--task-id", required=True)
    accept.add_argument("--state", choices=sorted(USER_ACCEPTANCE - {"pending"}), required=True)
    accept.add_argument("--evidence", required=True)
    accept.set_defaults(func=cmd_accept)
    pause = sub.add_parser("pause")
    pause.add_argument("--task-id", required=True)
    pause.add_argument("--state", choices=("blocked", "waiting_input"), required=True)
    pause.add_argument("--reason", required=True)
    pause.set_defaults(func=cmd_pause)
    resume = sub.add_parser("resume")
    resume.add_argument("--task-id", required=True)
    resume.add_argument("--evidence", required=True)
    resume.set_defaults(func=cmd_resume)
    candidate = sub.add_parser("candidate")
    candidate.add_argument("--task-id", required=True)
    candidate.add_argument("--commit", default="HEAD")
    candidate.set_defaults(func=cmd_candidate)
    review = sub.add_parser("review")
    review.add_argument("--task-id", required=True)
    review.add_argument("--candidate-revision", type=int, required=True)
    review.add_argument("--decision", choices=("pass", "fail"), required=True)
    review.add_argument("--evidence", required=True)
    review.set_defaults(func=cmd_review)
    submit = sub.add_parser("submit")
    submit.add_argument("--task-id", required=True)
    submit.add_argument("--candidate-revision", type=int, required=True)
    submit.add_argument("--evidence", required=True)
    submit.set_defaults(func=cmd_submit)
    rework = sub.add_parser("rework")
    rework.add_argument("--task-id", required=True)
    rework.add_argument("--evidence", required=True)
    rework.set_defaults(func=cmd_rework)
    ack = sub.add_parser("acknowledge")
    ack.add_argument("--task-id", required=True)
    ack.add_argument("--acknowledged-by", required=True)
    ack.set_defaults(func=cmd_acknowledge)
    absorb = sub.add_parser("absorb")
    absorb.add_argument("--task-id", required=True)
    absorb.add_argument("--scope", choices=("preflight", "parent-department", "project-global", "final"), required=True)
    absorb.add_argument("--state", choices=sorted(ABSORPTION_STATES - {"pending"}), required=True)
    absorb.add_argument("--evidence", required=True)
    absorb.set_defaults(func=cmd_absorb)
    integration = sub.add_parser("record-integration-test")
    integration.add_argument("--task-id", required=True)
    integration.add_argument("--tested-base", required=True)
    integration.add_argument("--commit", required=True)
    integration.add_argument("--test-definition", required=True)
    integration.add_argument("--environment", required=True)
    integration.add_argument("--evidence", required=True)
    integration.add_argument("--test-task-id", required=True)
    integration.add_argument("--report", required=True)
    integration.add_argument("--unverified", default="无")
    integration.add_argument("--result", choices=("pass", "fail"), required=True)
    integration.set_defaults(func=cmd_integration)
    promote = sub.add_parser("promote")
    promote.add_argument("--task-id", required=True)
    promote.add_argument("--main-branch", required=True)
    promote.set_defaults(func=cmd_promote)
    reconcile_promotion = sub.add_parser("reconcile-promotion")
    reconcile_promotion.add_argument("--task-id", required=True)
    reconcile_promotion.set_defaults(func=cmd_reconcile_promotion)
    abandon = sub.add_parser("abandon")
    abandon.add_argument("--task-id", required=True)
    abandon.add_argument("--evidence", required=True)
    abandon.set_defaults(func=cmd_abandon)
    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--task-id", required=True)
    cleanup.add_argument("--evidence", required=True)
    cleanup.set_defaults(func=cmd_cleanup)
    reconcile_cleanup = sub.add_parser("reconcile-cleanup")
    reconcile_cleanup.add_argument("--task-id", required=True)
    reconcile_cleanup.set_defaults(func=cmd_reconcile_cleanup)
    pending_archives = sub.add_parser("pending-archives")
    pending_archives.set_defaults(func=cmd_pending_archives, read_only=True)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        configure_project(require_git=not getattr(args, "read_only", False))
        if getattr(args, "read_only", False):
            return args.func(args)
        with task_lock():
            enforce_stop_loss(args)
            return args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"TEMP_ERROR | {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
