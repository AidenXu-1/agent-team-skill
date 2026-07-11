#!/usr/bin/env python3
"""Black-box regression verifier for the Agent Team scaffold."""

from __future__ import annotations

import datetime as dt
import json
import os
import py_compile
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCAFFOLD = Path(os.environ.get("AGENT_TEAM_SCAFFOLD", ROOT / "scripts" / "scaffold_team.py")).expanduser().resolve()
SPEC = """# Project

## 目标与用户需求

为真实项目成员建立可持续协作的多会话工作流。

## 交付范围

交付可运行的协作文件和状态管理工具。

## 验收标准

所有生成工具可编译，任务可完整流转，失败不会损坏真值。
"""


class VerifyError(RuntimeError):
    pass


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    ok: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if ok and result.returncode != 0:
        raise VerifyError(f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}\n{result.stderr}")
    if not ok and result.returncode == 0:
        raise VerifyError(f"command unexpectedly succeeded: {' '.join(args)}\n{result.stdout}")
    return result


def check(condition: bool, message: str) -> None:
    if not condition:
        raise VerifyError(message)


def make_project(root: Path, name: str) -> Path:
    project = root / name
    (project / "docs").mkdir(parents=True)
    (project / "docs" / "spec.md").write_text(SPEC, encoding="utf-8")
    return project


def scaffold(project: Path, roles: str = "lead,do,review") -> None:
    run([
        sys.executable, str(SCAFFOLD), str(project),
        "--profile", "通用项目协作", "--roles", roles, "--session-mode", "manual",
        "--foundation-file", "docs/spec.md",
    ])


def task_id_from(receipt: subprocess.CompletedProcess[str]) -> str:
    parts = [part.strip() for part in receipt.stdout.strip().split("|")]
    check(len(parts) >= 3 and parts[0] == "TASK_ENQUEUED", "enqueue receipt malformed")
    return parts[1]


def enqueue(task_tool: Path, title: str, auth: str = "none", evidence: str = "") -> str:
    args = [
        sys.executable, str(task_tool), "enqueue",
        "--department", "执行部", "--from-department", "统筹部",
        "--title", title, "--node", "单节点", "--details", "完成确定性验证",
        "--acceptance-exit", "用户看到验证结果",
        "--failure-path", "错误输入被明确拒绝",
        "--confirmation", "无需额外确认", "--domain-stage", "实现验证",
        "--authorization-state", auth,
    ]
    if evidence:
        args += ["--authorization-evidence", evidence]
    return task_id_from(run(args))


def verify_generated(project: Path) -> None:
    collab = project / "docs" / "collaboration"
    required = [
        "协议版本.json", "README.md", "路由表.md", "部门表.md", "会话启动清单.md",
        "会话启动状态.json", "任务交接模板.md", "模板/工作报告.md", "模板/审核报告.md",
        "模板/专项结论.md", "scripts/agent_team_log.py", "scripts/agent_team_task.py",
        "scripts/agent_team_session.py",
    ]
    for relative in required:
        check((collab / relative).is_file(), f"missing generated file: {relative}")
    check(not (collab / "读取路由规则.md").exists(), "obsolete reading rules generated")
    check(not (collab / "scripts" / "agent_team_read.py").exists(), "obsolete reader generated")
    protocol = json.loads((collab / "协议版本.json").read_text(encoding="utf-8"))
    check(protocol["protocol_version"] == "1.3.1", "unexpected protocol version")
    for script in (collab / "scripts").glob("*.py"):
        py_compile.compile(str(script), doraise=True)
    guide = (project / "docs" / "agent-guide.md").read_text(encoding="utf-8")
    check("受管协议版本:1.3.1" in guide and "任务真值" in guide, "project guide not refreshed")
    for department in ("统筹部", "执行部", "检验部"):
        root = collab / "部门" / department
        check((root / "报告").is_dir() and (root / "日志").is_dir(), "department output directories missing")
        check(not (root / "报告" / "README.md").exists(), "duplicated report README generated")
        check(not list((root / "日志").glob("*.md")), "empty weekly log should be lazy")
    inbox = (collab / "部门" / "执行部" / "收件箱.md").read_text(encoding="utf-8")
    check("../../tasks/" in inbox, "inbox does not use stable clickable task path")


def verify_product_development_boundary(root: Path) -> None:
    deprecated = make_project(root, "deprecated-ai-role")
    denied = run([
        sys.executable, str(SCAFFOLD), str(deprecated), "--profile", "AI 产品",
        "--roles", "lead,product,design,dev,ai,test", "--session-mode", "manual",
        "--foundation-file", "docs/spec.md",
    ], ok=False)
    check("已取消独立角色" in denied.stderr and "归开发部" in denied.stderr, "deprecated AI department was accepted")
    check(not (deprecated / "docs" / "collaboration").exists(), "failed deprecated-role scaffold left collaboration files")

    project = make_project(root, "ai-product-without-ai-department")
    scaffold(project, "lead,product,design,dev,test")
    collab = project / "docs" / "collaboration"
    check(not (collab / "部门" / "AI工程部").exists(), "AI department was generated")
    product = (collab / "部门" / "产品部" / "岗位说明.md").read_text(encoding="utf-8")
    development = (collab / "部门" / "开发部" / "岗位说明.md").read_text(encoding="utf-8")
    check("整个产品规划" in product and "AI 行为验收目标" in product, "product ownership is incomplete")
    check(all(term in development for term in ("模型/API 接入", "Prompt", "RAG", "Agent 链路", "评测集")),
          "development role does not own the full AI implementation")

    registry = collab / "部门表.md"
    text = registry.read_text(encoding="utf-8")
    marker = "\n\n## 使用规则"
    registry.write_text(text.replace(marker, "\n| 执行层 | AI工程部 | `ai` | old-ai-thread | manual | 已启用 |" + marker), encoding="utf-8")
    protocol_path = collab / "协议版本.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    protocol["protocol_version"] = "1.2.0"
    protocol_path.write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    blocked_upgrade = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"], ok=False)
    check("不会自动删除或合并" in blocked_upgrade.stderr, "legacy AI department was silently migrated")
    check(json.loads(protocol_path.read_text(encoding="utf-8"))["protocol_version"] == "1.2.0",
          "blocked legacy-role upgrade mutated protocol state")


def verify_foundation_contract(root: Path) -> None:
    project = make_project(root, "foundation-contract")
    base = [
        sys.executable, str(SCAFFOLD), str(project), "--profile", "通用项目协作",
        "--roles", "lead,do,review", "--session-mode", "manual",
    ]
    missing = run(base, ok=False)
    check("--foundation-file" in missing.stderr, "fresh scaffold did not require explicit Agent-reviewed foundation")
    check(not (project / "docs" / "collaboration").exists(), "missing foundation declaration mutated project")

    custom = project / "docs" / "overview.md"
    custom.write_text("# 项目概览\n\n由 Agent 负责语义复核。\n", encoding="utf-8")
    denied_custom = run(base + ["--foundation-file", "docs/overview.md"], ok=False)
    check("--allow-without-foundation" in denied_custom.stderr, "custom foundation bypassed user confirmation gate")
    check(not (project / "docs" / "collaboration").exists(), "custom foundation denial mutated project")

    if os.name != "nt":
        outside = root / "outside-foundation.md"
        outside.write_text("# outside\n", encoding="utf-8")
        linked = project / "docs" / "linked.md"
        linked.symlink_to(outside)
        denied_link = run(base + ["--foundation-file", "docs/linked.md", "--allow-without-foundation"], ok=False)
        check("符号链接" in denied_link.stderr, "symlink foundation was accepted")
        check(not (project / "docs" / "collaboration").exists(), "symlink foundation denial mutated project")

    generated = root / "minimal-foundation"
    generated.mkdir()
    run(base[:2] + [str(generated)] + base[3:] + [
        "--allow-without-foundation", "--create-minimal-foundation",
        "--foundation-goal", "用户确认的目标", "--foundation-deliverable", "用户确认的交付物",
        "--foundation-audience", "使用者", "--foundation-acceptance", "可复验的验收标准",
        "--foundation-resources", "已知资源", "--foundation-risks", "已知风险与复核方式",
    ])
    check((generated / "docs" / "overview.md").is_file(), "confirmed minimal foundation was not created")
    check((generated / "docs" / "collaboration").is_dir(), "minimal foundation did not publish collaboration layer")


def verify_tasks(project: Path) -> None:
    collab = project / "docs" / "collaboration"
    tool = collab / "scripts" / "agent_team_task.py"
    session = collab / "scripts" / "agent_team_session.py"
    fake_department = collab / "部门" / "未登记部"
    fake_department.mkdir()
    (fake_department / "岗位说明.md").write_text("# 未登记部\n\n> 所在层:执行层\n", encoding="utf-8")
    fake_inbox = fake_department / "收件箱.md"
    fake_inbox.write_text("# 伪造收件箱\n\n<!-- agent-team task index; use scripts/agent_team_task.py -->\n", encoding="utf-8")
    fake_inbox_before = fake_inbox.read_bytes()
    task_count_before_fake = len(list((collab / "tasks").glob("TASK-*.json")))
    denied_fake = run([
        sys.executable, str(tool), "enqueue", "--department", "未登记部", "--from-department", "统筹部",
        "--title", "伪造部门任务", "--node", "单节点", "--details", "不应进入任务系统",
        "--acceptance-exit", "明确拒绝", "--failure-path", "未登记部门", "--authorization-state", "none",
    ], ok=False)
    check("未知部门" in denied_fake.stderr, "unregistered directory bypassed department registry")
    check(len(list((collab / "tasks").glob("TASK-*.json"))) == task_count_before_fake,
          "unregistered department enqueue mutated TASK store")
    check(fake_inbox.read_bytes() == fake_inbox_before, "unregistered department enqueue mutated inbox")
    for child in fake_department.iterdir():
        child.unlink()
    fake_department.rmdir()
    for step, evidence in (("created", "lead-create"), ("onboarded", "lead-send"), ("registered", "lead-register")):
        run([sys.executable, str(session), "mark", "--department", "统筹部", "--step", step,
             "--thread-id", "lead-thread", "--evidence", evidence])
    result_file = project / "docs" / "result.txt"
    result_file.write_text("verified\n", encoding="utf-8")

    gated = enqueue(tool, "待授权任务", "user_required")
    denied = run([sys.executable, str(tool), "claim", "--task-id", gated, "--claimed-by", "s1"], ok=False)
    check("授权状态禁止领取" in denied.stderr, "user_required task was claimable")
    run([sys.executable, str(tool), "authorize", "--task-id", gated, "--state", "user_confirmed", "--evidence", "user-message-1"])
    run([sys.executable, str(tool), "claim", "--task-id", gated, "--claimed-by", "s1"])

    second = enqueue(tool, "第二任务")
    busy = run([sys.executable, str(tool), "claim", "--task-id", second, "--claimed-by", "s2"], ok=False)
    check("已有在办任务" in busy.stderr, "claimed task did not block a second claim")
    run([sys.executable, str(tool), "block", "--task-id", gated, "--reason", "等待依赖"])
    run([sys.executable, str(tool), "claim", "--task-id", second, "--claimed-by", "s2"])
    resume_busy = run([sys.executable, str(tool), "resume", "--task-id", gated], ok=False)
    check("已有其他在办任务" in resume_busy.stderr, "resume ignored active task")

    missing = run([
        sys.executable, str(tool), "complete", "--task-id", second,
        "--artifact", "docs/missing.txt", "--verified", "检查", "--unverified", "无",
        "--mistake-check", "无命中",
    ], ok=False)
    check("不存在" in missing.stderr, "nonexistent artifact accepted")
    root_artifact = run([
        sys.executable, str(tool), "complete", "--task-id", second,
        "--artifact", ".", "--verified", "检查", "--unverified", "无", "--mistake-check", "无命中",
    ], ok=False)
    check("根目录不能作为任务产物" in root_artifact.stderr, "project root accepted as artifact")

    done = run([
        sys.executable, str(tool), "complete", "--task-id", second,
        "--artifact", "docs/result.txt", "--verified", "结果文件存在", "--unverified", "无",
        "--mistake-check", "无命中",
    ])
    check(done.stdout.startswith("TASK_STATE_OK |"), "completion receipt missing")
    wrong_ack = run([sys.executable, str(tool), "ack", "--task-id", second,
                     "--acknowledged-by", "执行部/会话"], ok=False)
    check("必须匹配当前已登记统筹会话" in wrong_ack.stderr, "non-lead ack was accepted")
    run([sys.executable, str(tool), "ack", "--task-id", second, "--acknowledged-by", "统筹部/lead-thread"])
    task_path = collab / "tasks" / f"{second}.json"
    check(task_path.is_file(), "stable task file missing")
    payload = json.loads(task_path.read_text(encoding="utf-8"))
    check(payload["execution_state"] == "acknowledged", "ack state not persisted")
    missing_report_payload = dict(payload)
    missing_report_payload["report"] = ""
    task_path.write_text(json.dumps(missing_report_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    denied_missing_report = run([sys.executable, str(tool), "list"], ok=False)
    check("缺失 report" in denied_missing_report.stderr, "completed task with empty report failed open")
    task_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    check(not any((collab / "tasks" / state).exists() for state in ("queued", "claimed", "completed")), "state directories recreated")
    if os.name != "nt":
        check(stat.S_IMODE(task_path.stat().st_mode) == 0o600, "task JSON mode is not 0600")

    rejected = enqueue(tool, "已拒绝任务", "user_rejected", "user-message-2")
    denied = run([sys.executable, str(tool), "claim", "--task-id", rejected, "--claimed-by", "s3"], ok=False)
    check("user_rejected" in denied.stderr, "rejected task was claimable")

    inbox = collab / "部门" / "执行部" / "收件箱.md"
    inbox.write_text("# custom\n", encoding="utf-8")
    stale = run([
        sys.executable, str(tool), "enqueue", "--department", "执行部", "--from-department", "统筹部",
        "--title", "索引恢复", "--node", "单节点", "--details", "测试",
        "--acceptance-exit", "可见", "--failure-path", "索引损坏", "--authorization-state", "none",
    ])
    check("TASK_INDEX_STALE" in stale.stderr, "markerless inbox did not warn")
    run([sys.executable, str(tool), "rebuild-index"])
    check("agent-team task index" in inbox.read_text(encoding="utf-8"), "force index rebuild failed")

    schema_task = enqueue(tool, "结构校验任务", "user_required")
    schema_path = collab / "tasks" / f"{schema_task}.json"
    original = json.loads(schema_path.read_text(encoding="utf-8"))
    missing_auth = dict(original)
    del missing_auth["authorization_state"]
    schema_path.write_text(json.dumps(missing_auth, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    denied_missing_auth = run(
        [sys.executable, str(tool), "claim", "--task-id", schema_task, "--claimed-by", "schema-test"], ok=False,
    )
    check("任务字段缺失" in denied_missing_auth.stderr, "missing authorization_state failed open")
    check(json.loads(schema_path.read_text(encoding="utf-8"))["execution_state"] == "queued",
          "missing authorization_state caused task mutation")
    schema_path.write_text(json.dumps(original, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    invalid_cases = [
        ("title", "\x00", "控制字符"),
        ("title", "x" * 201, "超长"),
        ("claimed_by", "ghost", "待领取任务不得预填"),
        ("block_reason", "ghost", "不得预填 block_reason"),
        ("created_at", "yesterday", "时间戳无效"),
        ("external_artifacts", ["file:///etc/passwd"], "外部产物 URL 无效"),
    ]
    for field, value, expected_error in invalid_cases:
        corrupted = dict(original)
        corrupted[field] = value
        schema_path.write_text(json.dumps(corrupted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        denied_schema = run([sys.executable, str(tool), "list"], ok=False)
        check(expected_error in denied_schema.stderr, f"invalid canonical field failed open: {field}")
        schema_path.write_text(json.dumps(original, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for history_field, value, expected_error in (
        ("at", "yesterday", "时间戳无效"),
        ("evidence", "bad\x00evidence", "控制字符"),
    ):
        corrupted = json.loads(json.dumps(original, ensure_ascii=False))
        corrupted["authorization_evidence"] = "user-evidence"
        corrupted["authorization_history"] = [{
            "at": original["created_at"], "state": "user_required", "evidence": "user-evidence",
        }]
        corrupted["authorization_history"][0][history_field] = value
        schema_path.write_text(json.dumps(corrupted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        denied_history = run([sys.executable, str(tool), "list"], ok=False)
        check(expected_error in denied_history.stderr, f"invalid authorization history failed open: {history_field}")
        schema_path.write_text(json.dumps(original, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    orphan_history = json.loads(json.dumps(original, ensure_ascii=False))
    orphan_history["authorization_state"] = "none"
    orphan_history["authorization_evidence"] = ""
    orphan_history["authorization_history"] = [{
        "at": original["created_at"], "state": "user_confirmed", "evidence": "orphan-evidence",
    }]
    schema_path.write_text(json.dumps(orphan_history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    denied_orphan_history = run([sys.executable, str(tool), "list"], ok=False)
    check("证据与历史有无不一致" in denied_orphan_history.stderr,
          "authorization history without current evidence failed open")
    schema_path.write_text(json.dumps(original, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rejected_claimed = json.loads(json.dumps(original, ensure_ascii=False))
    rejected_claimed["execution_state"] = "claimed"
    rejected_claimed["claimed_by"] = "schema-test"
    rejected_claimed["authorization_state"] = "user_rejected"
    rejected_claimed["authorization_evidence"] = "user-rejected-evidence"
    rejected_claimed["authorization_history"] = [{
        "at": original["created_at"], "state": "user_rejected", "evidence": "user-rejected-evidence",
    }]
    schema_path.write_text(json.dumps(rejected_claimed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    denied_rejected_claimed = run([sys.executable, str(tool), "list"], ok=False)
    check("执行状态与授权状态冲突" in denied_rejected_claimed.stderr,
          "claimed task with rejected authorization failed open")
    schema_path.write_text(json.dumps(original, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    missing_department = dict(original)
    del missing_department["department"]
    schema_path.write_text(json.dumps(missing_department, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    before_invalid_enqueue = len(list((collab / "tasks").glob("TASK-*.json")))
    denied_missing_department = run([
        sys.executable, str(tool), "enqueue", "--department", "执行部", "--from-department", "统筹部",
        "--title", "不应创建", "--node", "单节点", "--details", "测试",
        "--acceptance-exit", "可见", "--failure-path", "结构缺失", "--authorization-state", "none",
    ], ok=False)
    check("任务字段缺失" in denied_missing_department.stderr, "missing department was not rejected canonically")
    check(len(list((collab / "tasks").glob("TASK-*.json"))) == before_invalid_enqueue,
          "enqueue mutated task store after missing department preflight")
    schema_path.write_text(json.dumps(original, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    before = len(list((collab / "tasks").glob("TASK-*.json")))
    corrupt = collab / "tasks" / "TASK-20200101-BROKEN.json"
    corrupt.write_text("{broken", encoding="utf-8")
    refused = run([
        sys.executable, str(tool), "enqueue", "--department", "执行部", "--from-department", "统筹部",
        "--title", "不应创建", "--node", "单节点", "--details", "测试",
        "--acceptance-exit", "可见", "--failure-path", "损坏", "--authorization-state", "none",
    ], ok=False)
    check("TASK_ERROR" in refused.stderr, "corrupt canonical task did not stop mutation")
    check(len(list((collab / "tasks").glob("TASK-*.json"))) == before + 1, "mutation occurred after corrupt task")
    corrupt.unlink()

    audit_id = task_id_from(run([
        sys.executable, str(tool), "enqueue", "--department", "检验部", "--from-department", "统筹部",
        "--title", "独立审核", "--node", "审核节点", "--details", "验证审核报告硬闸",
        "--acceptance-exit", "统筹部收到独立结论", "--failure-path", "缺报告时拒绝完成",
        "--authorization-state", "none",
    ]))
    run([sys.executable, str(tool), "claim", "--task-id", audit_id, "--claimed-by", "review-session"])
    no_report = run([
        sys.executable, str(tool), "complete", "--task-id", audit_id,
        "--artifact", "docs/result.txt", "--verified", "检查", "--unverified", "无",
        "--mistake-check", "无命中",
    ], ok=False)
    check("审核报告" in no_report.stderr, "audit task completed without audit report")
    report = collab / "部门" / "检验部" / "报告" / "audit.md"
    report.write_text(f"""---
type: audit_report
department: 检验部
target: Agent Team
status: final
date: {dt.date.today().isoformat()}
related_task: {audit_id}
decision: pass
tags: []
summary: 审核硬闸通过
---

# 审核结论

独立证据已复验。
""", encoding="utf-8")
    completed_audit = run([
        sys.executable, str(tool), "complete", "--task-id", audit_id,
        "--artifact", str(report.relative_to(project)), "--report", str(report.relative_to(project)),
        "--verified", "审核报告格式与证据通过", "--unverified", "无", "--mistake-check", "无命中",
    ])
    check(completed_audit.stdout.startswith("TASK_STATE_OK |"), "valid audit report was rejected")


def verify_log_and_session(project: Path, root: Path) -> None:
    collab = project / "docs" / "collaboration"
    log_tool = collab / "scripts" / "agent_team_log.py"
    log_dir = collab / "部门" / "执行部" / "日志"
    outside = root / "outside-log.md"
    outside.write_text("outside\n", encoding="utf-8")
    week = dt.date.today().isocalendar()
    log_path = log_dir / f"{week.year}-W{week.week:02d}.md"
    if hasattr(os, "link"):
        os.link(outside, log_path)
        denied = run([
            sys.executable, str(log_tool), "append", "--department", "执行部", "--task-id", "PROJECT",
            "--type", "DECISION", "--initiator", "user", "--fact", "选择稳定任务路径",
            "--trigger", "对抗审查", "--impact", "协作层", "--result", "使用平铺任务文件",
            "--pointer", "docs/collaboration/任务交接模板.md",
        ], ok=False)
        check("硬链接" in denied.stderr, "hard-linked log was writable")
        log_path.unlink()
    receipt = run([
        sys.executable, str(log_tool), "append", "--department", "执行部", "--task-id", "PROJECT",
        "--type", "DECISION", "--initiator", "user", "--fact", "选择稳定任务路径",
        "--trigger", "对抗审查", "--impact", "协作层", "--result", "使用平铺任务文件",
        "--pointer", "docs/collaboration/任务交接模板.md",
    ])
    check(receipt.stdout.startswith("LOG_OK |"), "log receipt malformed")

    session = collab / "scripts" / "agent_team_session.py"
    run([sys.executable, str(session), "mark", "--department", "执行部", "--step", "created",
         "--thread-id", "thread-1", "--evidence", "create-receipt"])
    run([sys.executable, str(session), "mark", "--department", "执行部", "--step", "failed",
         "--evidence", "send-failed", "--note", "temporary error"])
    wrong = run([sys.executable, str(session), "mark", "--department", "执行部", "--step", "registered",
                 "--thread-id", "thread-1", "--evidence", "bad"], ok=False)
    check("失败重试必须" in wrong.stderr, "failed session skipped retry point")
    run([sys.executable, str(session), "mark", "--department", "执行部", "--step", "onboarded",
         "--thread-id", "thread-1", "--evidence", "send-receipt"])
    run([sys.executable, str(session), "mark", "--department", "执行部", "--step", "registered",
         "--thread-id", "thread-1", "--evidence", "register-receipt"])
    ascii_env = os.environ.copy()
    ascii_env.update(LC_ALL="C", LANG="C", PYTHONUTF8="0")
    shown = run([sys.executable, str(session), "show"], env=ascii_env)
    check("执行部" in shown.stdout, "session tool did not preserve UTF-8 output under ASCII locale")
    state = json.loads((collab / "会话启动状态.json").read_text(encoding="utf-8"))
    check(state["departments"]["执行部"]["notification_mode"] == "manual", "initial notification mode missing")
    run([sys.executable, str(session), "set-notification", "--department", "执行部",
         "--mode", "auto", "--evidence", "user-approved-notification-change"])
    run([sys.executable, str(session), "begin-switch", "--department", "执行部",
         "--old-thread-id", "thread-1", "--reason", "user approved"])
    for step, evidence in (("created", "create-2"), ("onboarded", "send-2"), ("registered", "register-2")):
        args = [sys.executable, str(session), "mark", "--department", "执行部", "--step", step,
                "--thread-id", "thread-2", "--evidence", evidence]
        run(args)
    run([sys.executable, str(session), "finish-switch", "--department", "执行部",
         "--new-thread-id", "thread-2", "--evidence", "archive-receipt"])
    state = json.loads((collab / "会话启动状态.json").read_text(encoding="utf-8"))
    check(state["departments"]["执行部"]["thread_id"] == "thread-2", "switch did not persist new thread")
    check(state["departments"]["执行部"]["notification_mode"] == "auto", "notification mode did not persist")
    registry = (collab / "部门表.md").read_text(encoding="utf-8")
    check("thread-2" in registry and "auto" in registry, "session index was not refreshed")


def verify_upgrade_and_guards(root: Path) -> None:
    duplicate_project = make_project(root, "duplicate-upgrade-preflight")
    scaffold(duplicate_project)
    duplicate_collab = duplicate_project / "docs" / "collaboration"
    duplicate_tool = duplicate_collab / "scripts" / "agent_team_task.py"
    duplicate_id = enqueue(duplicate_tool, "重复迁移目标")
    duplicate_flat = duplicate_collab / "tasks" / f"{duplicate_id}.json"
    duplicate_payload = json.loads(duplicate_flat.read_text(encoding="utf-8"))
    queued_dir = duplicate_collab / "tasks" / "queued"
    claimed_dir = duplicate_collab / "tasks" / "claimed"
    queued_dir.mkdir()
    claimed_dir.mkdir()
    duplicate_flat.rename(queued_dir / duplicate_flat.name)
    claimed_payload = dict(duplicate_payload)
    claimed_payload["execution_state"] = "claimed"
    claimed_payload["claimed_by"] = "legacy-session"
    (claimed_dir / duplicate_flat.name).write_text(
        json.dumps(claimed_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    duplicate_protocol = duplicate_collab / "协议版本.json"
    duplicate_protocol_payload = json.loads(duplicate_protocol.read_text(encoding="utf-8"))
    duplicate_protocol_payload["protocol_version"] = "1.0.0"
    duplicate_protocol.write_text(json.dumps(duplicate_protocol_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    duplicate_denied = run([sys.executable, str(SCAFFOLD), str(duplicate_project), "--upgrade-collaboration"], ok=False)
    check("迁移目标重复" in duplicate_denied.stderr, "duplicate legacy task destination passed preflight")
    check(not (duplicate_collab / "升级备份").exists(), "duplicate task preflight created backup side effects")

    flat_project = make_project(root, "flat-upgrade-preflight")
    scaffold(flat_project)
    flat_collab = flat_project / "docs" / "collaboration"
    flat_tool = flat_collab / "scripts" / "agent_team_task.py"
    flat_id = enqueue(flat_tool, "1.3.0 平铺任务")
    flat_task = flat_collab / "tasks" / f"{flat_id}.json"
    flat_original = flat_task.read_bytes()
    flat_payload = json.loads(flat_original)
    del flat_payload["department"]
    flat_task.write_text(json.dumps(flat_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    flat_protocol = flat_collab / "协议版本.json"
    protocol_payload = json.loads(flat_protocol.read_text(encoding="utf-8"))
    protocol_payload["protocol_version"] = "1.3.0"
    flat_protocol.write_text(json.dumps(protocol_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    flat_guide = flat_project / "docs" / "agent-guide.md"
    flat_guide.write_text(flat_guide.read_text(encoding="utf-8").replace("受管协议版本:1.3.1", "受管协议版本:1.3.0"), encoding="utf-8")
    task_tool_before = flat_tool.read_bytes()
    denied_flat = run([sys.executable, str(SCAFFOLD), str(flat_project), "--upgrade-collaboration"], ok=False)
    check("任务真值未通过完整性预检" in denied_flat.stderr, "corrupt flat TASK did not block upgrade")
    check(json.loads(flat_protocol.read_text(encoding="utf-8"))["protocol_version"] == "1.3.0",
          "failed flat TASK preflight advanced protocol")
    check(flat_tool.read_bytes() == task_tool_before, "failed flat TASK preflight replaced runtime")
    check(not (flat_collab / "升级备份").exists(), "failed flat TASK preflight created upgrade side effects")
    flat_task.write_bytes(flat_original)
    clean_flat_upgrade = run([sys.executable, str(SCAFFOLD), str(flat_project), "--upgrade-collaboration"])
    check(clean_flat_upgrade.stdout.startswith("UPGRADE_OK |"), "clean flat 1.3.0 upgrade failed")
    check(flat_task.read_bytes() == flat_original, "clean flat upgrade rewrote TASK truth")
    check("受管协议版本:1.3.1" in flat_guide.read_text(encoding="utf-8"),
          "upgrade did not refresh project agent-guide")
    current_corrupt = json.loads(flat_original)
    current_corrupt["title"] = "bad\x00title"
    flat_task.write_text(json.dumps(current_corrupt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    denied_current = run([sys.executable, str(SCAFFOLD), str(flat_project), "--upgrade-collaboration"], ok=False)
    check("任务真值未通过完整性预检" in denied_current.stderr,
          "same-version upgrade no-op ignored corrupt TASK")
    flat_task.write_bytes(flat_original)

    project = make_project(root, "upgrade")
    scaffold(project)
    collab = project / "docs" / "collaboration"
    tool = collab / "scripts" / "agent_team_task.py"
    old_id = enqueue(tool, "旧任务")
    flat = collab / "tasks" / f"{old_id}.json"
    payload = json.loads(flat.read_text(encoding="utf-8"))
    legacy_dir = collab / "tasks" / "queued"
    legacy_dir.mkdir()
    flat.rename(legacy_dir / flat.name)
    protocol = json.loads((collab / "协议版本.json").read_text(encoding="utf-8"))
    protocol["protocol_version"] = "1.0.0"
    (collab / "协议版本.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (collab / "读取路由规则.md").write_text("legacy\n", encoding="utf-8")
    (collab / "scripts" / "agent_team_read.py").write_text("legacy\n", encoding="utf-8")
    upgraded = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    check(upgraded.stdout.startswith("UPGRADE_OK |"), "legacy upgrade failed")
    check((collab / "tasks" / f"{old_id}.json").is_file() and not legacy_dir.exists(), "legacy task path not migrated")
    check(not (collab / "scripts" / "agent_team_read.py").exists(), "obsolete reader survived upgrade")

    missing = collab / "模板" / "工作报告.md"
    missing.unlink()
    repaired = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    check(repaired.stdout.startswith("UPGRADE_OK |"), "same-version missing runtime was not repaired")
    check(missing.is_file(), "missing runtime file not restored")

    no_op_target = collab / "路由表.md"
    no_op_target.unlink()
    denied = run([sys.executable, str(SCAFFOLD), str(project), "--add-roles", "do"], ok=False)
    check("缺失" in denied.stderr or "不安全" in denied.stderr or "协议" in denied.stderr, "add-role no-op ignored broken runtime")
    run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])

    outside = root / "outside-scripts"
    outside.mkdir()
    scripts = collab / "scripts"
    safe = collab / "scripts-safe"
    scripts.rename(safe)
    scripts.symlink_to(outside, target_is_directory=True)
    rejected = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"], ok=False)
    check("scripts" in rejected.stderr and ("不安全" in rejected.stderr or "越界" in rejected.stderr), "scripts symlink upgrade was not rejected")
    check(not any(outside.iterdir()), "upgrade wrote through scripts symlink")


def main() -> int:
    py_compile.compile(str(SCAFFOLD), doraise=True)
    with tempfile.TemporaryDirectory(prefix="agent-team-verify-") as temp:
        root = Path(temp)
        project = make_project(root, "main")
        scaffold(project)
        verify_generated(project)
        verify_foundation_contract(root)
        verify_product_development_boundary(root)
        verify_tasks(project)
        verify_log_and_session(project, root)
        verify_upgrade_and_guards(root)
    print("VERIFY_OK | scaffold, task, index, log, session, upgrade, migration, and path guards passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerifyError as exc:
        print(f"VERIFY_ERROR | {exc}", file=sys.stderr)
        raise SystemExit(1)
