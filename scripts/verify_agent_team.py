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
        "scripts/agent_team_session.py", "scripts/agent_team_temporary.py",
    ]
    for relative in required:
        check((collab / relative).is_file(), f"missing generated file: {relative}")
    check(not (collab / "读取路由规则.md").exists(), "obsolete reading rules generated")
    check(not (collab / "scripts" / "agent_team_read.py").exists(), "obsolete reader generated")
    protocol = json.loads((collab / "协议版本.json").read_text(encoding="utf-8"))
    check(protocol["protocol_version"] == "1.4.5", "unexpected protocol version")
    for script in (collab / "scripts").glob("*.py"):
        py_compile.compile(str(script), doraise=True)
    guide = (project / "docs" / "agent-guide.md").read_text(encoding="utf-8")
    check("受管协议版本:1.4.5" in guide and "任务真值" in guide, "project guide not refreshed")
    collaboration_readme = (collab / "README.md").read_text(encoding="utf-8")
    check("有归档工具时立即调用" in collaboration_readme
          and "host=<真实工具>" in collaboration_readme
          and "调用失败或没有工具时提醒用户" in collaboration_readme
          and "我目前无法自动归档这个会话" in collaboration_readme
          and "归档完成后告诉我一声" in collaboration_readme
          and "user_confirmation=" in collaboration_readme
          and "temporary_session=standby" in collaboration_readme,
          "generated collaboration guide omitted the automatic path or lightweight manual fallback")
    check("archive-request" not in collaboration_readme and "--archive-mode" not in collaboration_readme,
          "generated collaboration guide retained the rejected hard archive gate")
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
    log_path.write_text(
        f"---\n部门: 执行部\n覆盖: legacy\n---\n\n# 执行部 · 旧平铺日志\n\n"
        "- 2026-01-01T00:00+08:00 | DEC-LEGACY | DECISION | task:PROJECT | legacy-event\n",
        encoding="utf-8",
    )
    receipt = run([
        sys.executable, str(log_tool), "append", "--department", "执行部", "--task-id", "PROJECT",
        "--type", "DECISION", "--initiator", "user", "--fact", "选择稳定任务路径",
        "--trigger", "对抗审查", "--impact", "协作层", "--result", "使用平铺任务文件",
        "--pointer", "docs/collaboration/任务交接模板.md",
    ])
    check(receipt.stdout.startswith("LOG_OK |"), "log receipt malformed")
    first_task = next((project / "docs" / "collaboration" / "tasks").glob("TASK-*.json")).stem
    spoofed_temporary = run([
        sys.executable, str(log_tool), "append", "--department", "执行部", "--task-id", first_task,
        "--type", "CHANGE", "--initiator", "user", "--fact", "临时外包任务内目标调整",
        "--trigger", "用户直接沟通", "--impact", "当前 TASK", "--result", "已同步当前 brief",
        "--pointer", f"docs/collaboration/tasks/{first_task}.json",
        "--executor-type", "temporary", "--executor-id", "temp-executor-1",
        "--parent-department", "执行部",
    ], ok=False)
    check("未绑定临时执行者" in spoofed_temporary.stderr, "ordinary TASK spoofed a temporary log identity")
    log_text = log_path.read_text(encoding="utf-8")
    check(log_text.count("<!-- agent-team:formal-log:start -->") == 1, "formal log section marker missing")
    check(log_text.count("<!-- agent-team:temporary-log:start -->") == 1, "temporary log section marker missing")
    check(log_text.count("DEC-LEGACY") == 1 and log_text.index("DEC-LEGACY") < log_text.index("executor_type:formal"),
          "legacy flat log event was not preserved in the formal section")
    formal_position = log_text.index("executor_type:formal")
    check(formal_position < log_text.index("<!-- agent-team:formal-log:end -->"), "formal event escaped formal section")
    wrong_parent = run([
        sys.executable, str(log_tool), "append", "--department", "执行部", "--task-id", first_task,
        "--type", "MILESTONE", "--initiator", "agent", "--fact", "错误父部门",
        "--result", "应拒绝", "--pointer", f"docs/collaboration/tasks/{first_task}.json",
        "--executor-type", "temporary", "--executor-id", "temp-executor-1",
        "--parent-department", "统筹部",
    ], ok=False)
    check("必须写入父部门周日志" in wrong_parent.stderr, "temporary log crossed parent department")

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


def verify_temporary_executor(root: Path) -> None:
    project = make_project(root, "temporary-executor")
    (project / ".gitignore").write_text("/.agent-team/\n", encoding="utf-8")
    (project / "app").mkdir()
    (project / "app" / "base.py").write_text("VALUE = 'base'\n", encoding="utf-8")
    run(["git", "init", "-b", "main"], cwd=project)
    run(["git", "config", "user.name", "Agent Team Verify"], cwd=project)
    run(["git", "config", "user.email", "verify@example.invalid"], cwd=project)
    run(["git", "add", "."], cwd=project)
    run(["git", "commit", "-m", "foundation"], cwd=project)
    scaffold(project, "lead,design,dev,test")
    run(["git", "add", "."], cwd=project)
    run(["git", "commit", "-m", "agent team collaboration"], cwd=project)

    collab = project / "docs" / "collaboration"
    task_tool = collab / "scripts" / "agent_team_task.py"
    temporary_tool = collab / "scripts" / "agent_team_temporary.py"
    session_tool = collab / "scripts" / "agent_team_session.py"
    for step, evidence in (("created", "lead-create"), ("onboarded", "lead-onboard"), ("registered", "lead-register")):
        run([
            sys.executable, str(session_tool), "mark", "--department", "统筹部", "--step", step,
            "--thread-id", "lead-thread", "--evidence", evidence,
        ])

    def enqueue_dev(title: str, auth: str, evidence: str = "") -> str:
        args = [
            sys.executable, str(task_tool), "enqueue", "--department", "开发部",
            "--from-department", "统筹部", "--title", title, "--node", "开发节点",
            "--details", title, "--acceptance-exit", "可复验交付", "--failure-path", "越界时拒绝",
            "--authorization-state", auth,
        ]
        if evidence:
            args += ["--authorization-evidence", evidence]
        return task_id_from(run(args))

    formal = enqueue_dev("正式任务 A", "none")
    run([sys.executable, str(task_tool), "claim", "--task-id", formal, "--claimed-by", "dev-session"])
    temporary = enqueue_dev("临时任务 B", "user_confirmed", "user-requested-temporary-outsourcing")

    manual = run([
        sys.executable, str(temporary_tool), "preflight", "--task-id", temporary,
        "--parent-department", "开发部", "--write-path", "app/b.py",
    ], ok=False)
    check("TEMP_ADMISSION_MANUAL" in manual.stdout, "missing formal impact declaration claimed safe admission")
    run([sys.executable, str(task_tool), "block", "--task-id", formal, "--reason", "等待正式依赖"])
    blocked_manual = run([
        sys.executable, str(temporary_tool), "preflight", "--task-id", temporary,
        "--parent-department", "开发部", "--write-path", "app/b.py",
    ], ok=False)
    check("TEMP_ADMISSION_MANUAL" in blocked_manual.stdout,
          "blocked formal task without impact declaration disappeared from admission")

    formal_path = collab / "tasks" / f"{formal}.json"
    formal_revision = json.loads(formal_path.read_text(encoding="utf-8"))["revision"]
    run([
        sys.executable, str(temporary_tool), "declare-impact", "--task-id", formal,
        "--expected-revision", str(formal_revision), "--base-revision", "HEAD",
        "--write-path", "app/a.py", "--shared-contract", "auth-v1",
    ])
    run([sys.executable, str(task_tool), "resume", "--task-id", formal])
    unsafe = run([
        sys.executable, str(temporary_tool), "preflight", "--task-id", temporary,
        "--parent-department", "开发部", "--write-path", "app/b.py",
        "--shared-contract", "auth-v1",
    ], ok=False)
    check("TEMP_ADMISSION_UNSAFE" in unsafe.stdout, "shared contract overlap was not rejected")
    exclude_file = project / ".git" / "info" / "exclude"
    original_exclude = exclude_file.read_text(encoding="utf-8")
    exclude_file.write_text(original_exclude + "\napp/b.py\n", encoding="utf-8")
    (project / "app" / "b.py").write_text("IGNORED = True\n", encoding="utf-8")
    ignored_manual = run([
        sys.executable, str(temporary_tool), "preflight", "--task-id", temporary,
        "--parent-department", "开发部", "--write-path", "app/b.py",
    ], ok=False)
    check("TEMP_ADMISSION_MANUAL" in ignored_manual.stdout and "ignored" in ignored_manual.stdout,
          "ignored content inside write scope claimed safe admission")
    (project / "app" / "b.py").unlink()
    exclude_file.write_text(original_exclude, encoding="utf-8")

    design_task = task_id_from(run([
        sys.executable, str(task_tool), "enqueue", "--department", "设计部", "--from-department", "统筹部",
        "--title", "临时设计任务", "--node", "设计节点", "--details", "验证通用父部门模型",
        "--acceptance-exit", "设计产物可复验", "--failure-path", "父部门写死时拒绝",
        "--authorization-state", "user_confirmed", "--authorization-evidence", "user-requested-design-outsourcing",
    ]))
    design_preflight = run([
        sys.executable, str(temporary_tool), "preflight", "--task-id", design_task,
        "--parent-department", "设计部", "--write-path", "design/card.svg",
    ])
    check("TEMP_ADMISSION_SAFE" in design_preflight.stdout, "temporary executor model was hard-coded to development")
    design_execution_denied = run([
        sys.executable, str(temporary_tool), "provision", "--task-id", design_task,
        "--parent-department", "设计部", "--executor-id", "temp-design-1",
        "--display-name", "临时设计外包", "--current-brief", "设计卡片",
        "--client-key", "client-temp-design", "--scan-boundary-evidence", "已检查扫描边界",
        "--base-revision", "HEAD", "--write-path", "design/card.svg",
    ], ok=False)
    check("只支持临时开发外包" in design_execution_denied.stderr,
          "non-development parent entered an unimplemented professional delivery chain")

    provisioned = run([
        sys.executable, str(temporary_tool), "provision", "--task-id", temporary,
        "--parent-department", "开发部", "--executor-id", "temp-dev-1",
        "--display-name", "临时开发外包", "--current-brief", "新增独立模块 B",
        "--client-key", "client-temp-b", "--scan-boundary-evidence", "已检查 watcher 与构建扫描不包含 /.agent-team/",
        "--base-revision", "HEAD", "--write-path", "app/b.py",
    ])
    check(provisioned.stdout.startswith("TEMP_PROVISION_OK |"), "temporary workspace was not provisioned")
    idempotent = run([
        sys.executable, str(temporary_tool), "provision", "--task-id", temporary,
        "--parent-department", "开发部", "--executor-id", "temp-dev-1",
        "--display-name", "临时开发外包", "--current-brief", "新增独立模块 B",
        "--client-key", "client-temp-b", "--scan-boundary-evidence", "已检查 watcher 与构建扫描不包含 /.agent-team/",
        "--base-revision", "HEAD", "--write-path", "app/b.py",
    ])
    check(idempotent.stdout.startswith("TEMP_PROVISION_IDEMPOTENT |"), "provision retry was not idempotent")
    idempotency_conflict = run([
        sys.executable, str(temporary_tool), "provision", "--task-id", temporary,
        "--parent-department", "开发部", "--executor-id", "different-executor",
        "--display-name", "伪造重试", "--current-brief", "不同请求",
        "--client-key", "client-temp-b", "--scan-boundary-evidence", "不同扫描声明",
        "--base-revision", "HEAD", "--write-path", "app/other.py",
    ], ok=False)
    check("IDEMPOTENCY_CONFLICT" in idempotency_conflict.stderr,
          "same client key accepted a different provision request")

    task_path = collab / "tasks" / f"{temporary}.json"
    legacy_payload = json.loads(task_path.read_text(encoding="utf-8"))
    legacy_temp = legacy_payload["temporary_executor"]
    legacy_temp.pop("promotion_operation")
    legacy_temp.pop("cleanup_operation")
    legacy_temp["operation"].pop("request_digest")
    legacy_temp["operation"].pop("history")
    task_path.write_text(json.dumps(legacy_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload = json.loads(task_path.read_text(encoding="utf-8"))
    temp = payload["temporary_executor"]
    workspace = project / temp["workspace"]["path"]
    rule = workspace / ".agent-team" / "临时执行规则.md"
    check(rule.is_file() and "专业质量标准" in rule.read_text(encoding="utf-8"), "temporary rule missing")
    no_rule_candidate = run([
        sys.executable, str(temporary_tool), "candidate", "--task-id", temporary, "--commit", "HEAD",
    ], ok=False)
    check("尚未 active" in no_rule_candidate.stderr,
          f"candidate bypassed temporary rule confirmation: {no_rule_candidate.stderr.strip()}")
    normalized_temp = json.loads(task_path.read_text(encoding="utf-8"))["temporary_executor"]
    check("promotion_operation" in normalized_temp and "cleanup_operation" in normalized_temp
          and normalized_temp["operation"]["request_digest"] == "legacy-unknown",
          "legacy temporary TASK did not normalize safely for 1.4.5")
    copied_scripts = workspace / "docs" / "collaboration" / "scripts"
    copied_task_list = run([sys.executable, str(copied_scripts / "agent_team_task.py"), "list"])
    check(temporary in copied_task_list.stdout, "worktree task tool read the non-authoritative TASK copy")
    copied_session_show = run([sys.executable, str(copied_scripts / "agent_team_session.py"), "show"])
    check("开发部" in copied_session_show.stdout, "worktree session tool missed the main control root")
    copied_log = run([
        sys.executable, str(copied_scripts / "agent_team_log.py"), "append",
        "--department", "开发部", "--task-id", temporary, "--type", "MILESTONE",
        "--initiator", "agent", "--fact", "临时 workspace 已验证", "--result", "主控制根保持唯一",
        "--pointer", f"docs/collaboration/tasks/{temporary}.json", "--executor-type", "temporary",
        "--executor-id", "temp-dev-1", "--parent-department", "开发部",
    ])
    check(copied_log.stdout.startswith("LOG_OK |"), "worktree log tool failed to route to main control root")
    spoofed_parent = run([
        sys.executable, str(copied_scripts / "agent_team_log.py"), "append",
        "--department", "设计部", "--task-id", temporary, "--type", "MILESTONE",
        "--initiator", "agent", "--fact", "伪造父部门", "--result", "必须拒绝",
        "--pointer", f"docs/collaboration/tasks/{temporary}.json", "--executor-type", "temporary",
        "--executor-id", "temp-dev-1", "--parent-department", "设计部",
    ], ok=False)
    check("TASK 真值不一致" in spoofed_parent.stderr, "temporary log spoofed a different parent department")
    main_logs = collab / "部门" / "开发部" / "日志"
    check(any("临时 workspace 已验证" in path.read_text(encoding="utf-8") for path in main_logs.glob("*.md")),
          "worktree log tool wrote into the non-authoritative collaboration copy")
    copied_logs = workspace / "docs" / "collaboration" / "部门" / "开发部" / "日志"
    check(not list(copied_logs.glob("*.md")), "non-authoritative worktree log copy was mutated")
    hidden_control = project / "docs" / "collaboration-hidden"
    collab.rename(hidden_control)
    try:
        control_root_failure = run([
            sys.executable, str(copied_scripts / "agent_team_log.py"), "append",
            "--department", "开发部", "--task-id", temporary, "--type", "MILESTONE",
            "--initiator", "agent", "--fact", "主控制根缺失", "--result", "必须停止",
            "--pointer", "docs/spec.md", "--executor-type", "temporary",
            "--executor-id", "temp-dev-1", "--parent-department", "开发部",
        ], ok=False)
        check("CONTROL_ROOT_ERROR" in control_root_failure.stderr,
              "worktree tool silently fell back to the non-authoritative control copy")
        check(not list(copied_logs.glob("*.md")), "control-root failure mutated the worktree copy")
    finally:
        hidden_control.rename(collab)
    run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "active", "--thread-id", "temporary-thread-1",
        "--rule-digest", temp["rule"]["digest"], "--evidence", "rule-read-confirmed",
    ])
    ordinary_block = run([
        sys.executable, str(task_tool), "block", "--task-id", temporary, "--reason", "普通工具越权",
    ], ok=False)
    check("只能通过 agent_team_temporary.py" in ordinary_block.stderr,
          "ordinary task tool mutated temporary lifecycle axes")
    run([
        sys.executable, str(temporary_tool), "pause", "--task-id", temporary,
        "--state", "blocked", "--reason", "等待独立依赖",
    ])
    resumed = run([
        sys.executable, str(temporary_tool), "resume", "--task-id", temporary,
        "--evidence", "dependency-ready",
    ])
    check("active" in resumed.stdout, "temporary pause/resume lost confirmed rule state")
    amended = run([
        sys.executable, str(temporary_tool), "amend", "--task-id", temporary,
        "--expected-brief-revision", "1", "--current-brief", "新增独立模块 B 并保留现有接口",
        "--write-path", "app/b.py",
    ])
    check("brief_revision:2" in amended.stdout and "admission:safe" in amended.stdout,
          "brief amend did not re-run admission atomically")
    amended_payload = json.loads(task_path.read_text(encoding="utf-8"))["temporary_executor"]
    check(amended_payload["attempt"] == 2 and amended_payload["integration"] is None
          and amended_payload["rule"]["confirmed_at"] == ""
          and amended_payload["temporary_session"]["state"] == "awaiting_rule_confirmation",
          "brief amend retained stale attempt, integration, or rule confirmation")
    amended_rule_text = rule.read_text(encoding="utf-8")
    check("新增独立模块 B 并保留现有接口" in amended_rule_text,
          "brief amend did not regenerate the temporary rule")
    run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "active", "--thread-id", "temporary-thread-1",
        "--rule-digest", amended_payload["rule"]["digest"], "--evidence", "amended-rule-read-confirmed",
    ])
    before_stale_amend = task_path.read_bytes()
    stale_amend = run([
        sys.executable, str(temporary_tool), "amend", "--task-id", temporary,
        "--expected-brief-revision", "1", "--current-brief", "过期修改",
        "--write-path", "app/b.py",
    ], ok=False)
    check("已过期" in stale_amend.stderr and task_path.read_bytes() == before_stale_amend,
          "stale brief amend mutated TASK truth")

    (workspace / "app" / "b.py").write_text("VALUE = 'temporary-b'\n", encoding="utf-8")
    run(["git", "add", "app/b.py"], cwd=workspace)
    run(["git", "commit", "-m", "add temporary module b"], cwd=workspace)
    candidate = run([sys.executable, str(temporary_tool), "candidate", "--task-id", temporary, "--commit", "HEAD"])
    check(candidate.stdout.startswith("TEMP_CANDIDATE_OK |"), "candidate was not frozen")
    run([
        sys.executable, str(temporary_tool), "accept", "--task-id", temporary,
        "--state", "confirmed", "--evidence", "user-approved-first-candidate",
    ])
    (workspace / "app" / "b.py").write_text("VALUE = 'temporary-b-v2'\n", encoding="utf-8")
    run(["git", "add", "app/b.py"], cwd=workspace)
    run(["git", "commit", "-m", "revise temporary module b"], cwd=workspace)
    run([sys.executable, str(temporary_tool), "candidate", "--task-id", temporary, "--commit", "HEAD"])
    stale_acceptance_submit = run([
        sys.executable, str(temporary_tool), "submit", "--task-id", temporary,
        "--candidate-revision", "3", "--evidence", "must-not-reuse-old-user-approval",
    ], ok=False)
    check("当前候选尚未获得用户确认" in stale_acceptance_submit.stderr,
          "old user acceptance was automatically attached to a different candidate")
    run([
        sys.executable, str(temporary_tool), "accept", "--task-id", temporary,
        "--state", "confirmed", "--evidence", "user-approved-second-candidate",
    ])
    run([
        sys.executable, str(temporary_tool), "review", "--task-id", temporary,
        "--candidate-revision", "3", "--decision", "pass", "--evidence", "blind-review-pass",
    ])
    rule.write_text(rule.read_text(encoding="utf-8") + "\n未登记篡改\n", encoding="utf-8")
    tampered_rule_submit = run([
        sys.executable, str(temporary_tool), "submit", "--task-id", temporary,
        "--candidate-revision", "3", "--evidence", "must-not-submit-tampered-rule",
    ], ok=False)
    check("旧确认失效" in tampered_rule_submit.stderr, "submit accepted a rule changed after confirmation")
    reconciled_rule = run([
        sys.executable, str(temporary_tool), "reconcile-rule", "--task-id", temporary,
        "--evidence", "restored-rule-from-task-truth",
    ])
    check(reconciled_rule.stdout.startswith("TEMP_RULE_RECONCILE_OK |"), "rule mismatch could not reconcile")
    reconciled_rule_digest = json.loads(task_path.read_text(encoding="utf-8"))["temporary_executor"]["rule"]["digest"]
    run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "active", "--thread-id", "temporary-thread-1",
        "--rule-digest", reconciled_rule_digest, "--evidence", "reconciled-rule-confirmed",
    ])
    submitted = run([
        sys.executable, str(temporary_tool), "submit", "--task-id", temporary,
        "--candidate-revision", "3", "--evidence", "delivery-submitted",
    ])
    check(submitted.stdout.startswith("TEMP_SUBMIT_OK |"), "delivery was not submitted")
    run([
        sys.executable, str(temporary_tool), "acknowledge", "--task-id", temporary,
        "--acknowledged-by", "统筹部/lead-thread",
    ])
    run([
        sys.executable, str(temporary_tool), "absorb", "--task-id", temporary,
        "--scope", "preflight", "--state", "completed", "--evidence", "first-delivery-inventory-complete",
    ])
    reworked = run([
        sys.executable, str(temporary_tool), "rework", "--task-id", temporary,
        "--evidence", "formal-review-requested-rework",
    ])
    check("attempt:3" in reworked.stdout, "formal rework did not advance attempt")
    reworked_temp = json.loads(task_path.read_text(encoding="utf-8"))["temporary_executor"]
    check(reworked_temp["delivery"] is None and reworked_temp["integration"] is None
          and reworked_temp["temporary_session"]["state"] == "awaiting_rule_confirmation",
          "rework retained stale delivery, integration, or rule confirmation")
    check(reworked_temp["absorption"]["preflight"] == "pending"
          and reworked_temp["absorption"]["receipts"] == []
          and reworked_temp["absorption"]["history"][-1]["attempt"] == 2
          and reworked_temp["absorption"]["history"][-1]["snapshot"]["preflight"] == "completed",
          "rework retained active absorption evidence or lost its invalidation history")
    run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "active", "--thread-id", "temporary-thread-1",
        "--rule-digest", reworked_temp["rule"]["digest"], "--evidence", "rework-rule-confirmed",
    ])
    run([sys.executable, str(temporary_tool), "candidate", "--task-id", temporary, "--commit", "HEAD"])
    run([
        sys.executable, str(temporary_tool), "accept", "--task-id", temporary,
        "--state", "confirmed", "--evidence", "user-approved-rework",
    ])
    run([
        sys.executable, str(temporary_tool), "review", "--task-id", temporary,
        "--candidate-revision", "5", "--decision", "pass", "--evidence", "rework-review-pass",
    ])
    run([
        sys.executable, str(temporary_tool), "submit", "--task-id", temporary,
        "--candidate-revision", "5", "--evidence", "rework-delivery-submitted",
    ])
    premature_absorption = run([
        sys.executable, str(temporary_tool), "absorb", "--task-id", temporary,
        "--scope", "preflight", "--state", "completed", "--evidence", "too-early",
    ], ok=False)
    check("统筹接管" in premature_absorption.stderr, "knowledge absorption started before lead takeover")
    run([
        sys.executable, str(temporary_tool), "acknowledge", "--task-id", temporary,
        "--acknowledged-by", "统筹部/lead-thread",
    ])
    early_cleanup = run([
        sys.executable, str(temporary_tool), "cleanup", "--task-id", temporary,
        "--evidence", "must-not-clean-before-integration",
    ], ok=False)
    check("integrated" in early_cleanup.stderr and workspace.exists(), "unintegrated delivery was cleaned")
    run([
        sys.executable, str(temporary_tool), "absorb", "--task-id", temporary,
        "--scope", "preflight", "--state", "completed", "--evidence", "acceptance-contract-checked",
    ])
    premature_final = run([
        sys.executable, str(temporary_tool), "absorb", "--task-id", temporary,
        "--scope", "parent-department", "--state", "completed", "--evidence", "too-early",
    ], ok=False)
    check("integrated" in premature_final.stderr, "final knowledge absorption closed before formal integration")

    delivery = json.loads(task_path.read_text(encoding="utf-8"))["temporary_executor"]["delivery"]["locator"]
    tested_base = run(["git", "rev-parse", "main"], cwd=project).stdout.strip()
    delivery_tree = run(["git", "rev-parse", f"{delivery}^{{tree}}"], cwd=project).stdout.strip()
    run([sys.executable, "-m", "py_compile", str(workspace / "app" / "b.py")])
    test_task = task_id_from(run([
        sys.executable, str(task_tool), "enqueue", "--department", "测试部", "--from-department", "统筹部",
        "--title", "验证临时交付合并候选", "--node", "正式测试", "--details", "运行真实候选测试并绑定 commit/tree",
        "--acceptance-exit", "正式报告绑定已测试 tree", "--failure-path", "测试证据与候选不一致",
        "--authorization-state", "none", "--pointer", f"docs/collaboration/tasks/{temporary}.json",
    ]))
    run([sys.executable, str(task_tool), "claim", "--task-id", test_task, "--claimed-by", "test-session"])
    report = collab / "部门" / "测试部" / "报告" / "temporary-integration-test.md"
    report.write_text(f"""---
type: audit_report
department: 测试部
target: {temporary}
status: final
date: {dt.date.today().isoformat()}
related_task: {test_task}
decision: pass
tags: [temporary-executor, integration]
summary: 已运行候选编译与定向回归
tested_commit: {delivery}
tested_tree: {delivery_tree}
result: pass
---

# 正式测试

实际运行 Python 编译检查，候选通过。
""", encoding="utf-8")
    report_relative = report.relative_to(project).as_posix()
    run([
        sys.executable, str(task_tool), "complete", "--task-id", test_task,
        "--artifact", report_relative, "--report", report_relative,
        "--verified", "实际运行候选 Python 编译检查并核对 commit/tree",
        "--unverified", "无", "--mistake-check", "未使用自填字符串代替正式报告",
    ])
    run([sys.executable, str(task_tool), "ack", "--task-id", test_task, "--acknowledged-by", "统筹部/lead-thread"])
    fake_test_evidence = run([
        sys.executable, str(temporary_tool), "record-integration-test", "--task-id", temporary,
        "--tested-base", tested_base, "--commit", delivery, "--test-definition", "fake",
        "--environment", "fake", "--evidence", "plain-text-pass", "--result", "pass",
        "--test-task-id", temporary, "--report", report_relative,
    ], ok=False)
    check("审核层 TASK" in fake_test_evidence.stderr, "plain caller text impersonated formal test evidence")
    run([
        sys.executable, str(temporary_tool), "record-integration-test", "--task-id", temporary,
        "--tested-base", tested_base, "--commit", delivery, "--test-definition", "compile and targeted regression",
        "--environment", "temporary verifier", "--evidence", report_relative, "--result", "pass",
        "--test-task-id", test_task, "--report", report_relative,
    ])
    before_post_test_amend = task_path.read_bytes()
    post_test_amend = run([
        sys.executable, str(temporary_tool), "amend", "--task-id", temporary,
        "--expected-brief-revision", "2", "--current-brief", "测试后实质新需求",
        "--write-path", "app/b.py",
    ], ok=False)
    check("必须先 rework" in post_test_amend.stderr and task_path.read_bytes() == before_post_test_amend,
          "post-test amend retained or reused stale integration evidence")
    drift_tree = run(["git", "rev-parse", f"{tested_base}^{{tree}}"], cwd=project).stdout.strip()
    drift_commit = run(
        ["git", "commit-tree", drift_tree, "-p", tested_base, "-m", "simulated main drift"], cwd=project,
    ).stdout.strip()
    run(["git", "branch", "drift-main", drift_commit], cwd=project)
    drift_denied = run([
        sys.executable, str(temporary_tool), "promote", "--task-id", temporary, "--main-branch", "drift-main",
    ], ok=False)
    check("main 已漂移" in drift_denied.stderr, "main drift reused stale test evidence")
    promoted = run([
        sys.executable, str(temporary_tool), "promote", "--task-id", temporary, "--main-branch", "main",
    ])
    check(promoted.stdout.startswith("TEMP_PROMOTE_OK |"), "tested tree was not promoted")
    check((project / "app" / "b.py").is_file(), "promoted product tree missing temporary delivery")
    promotion_crash = json.loads(task_path.read_text(encoding="utf-8"))
    promotion_crash_temp = promotion_crash["temporary_executor"]
    promotion_crash_temp["promotion_state"] = "ready"
    promotion_crash_temp["promotion_operation"]["state"] = "started"
    promotion_crash_temp["promotion_operation"]["history"].append({
        "state": "started", "at": dt.datetime.now(dt.timezone.utc).isoformat(), "via": "simulated-crash",
    })
    promotion_crash_temp["integration"].pop("promoted_at", None)
    promotion_crash_temp["integration"].pop("main_branch", None)
    task_path.write_text(json.dumps(promotion_crash, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    reconciled_promotion = run([
        sys.executable, str(temporary_tool), "reconcile-promotion", "--task-id", temporary,
    ])
    check("integrated" in reconciled_promotion.stdout,
          "promotion crash after Git update could not reconcile TASK truth")

    for scope, state, evidence in (
        ("parent-department", "completed", "development-knowledge-absorbed"),
        ("project-global", "not_applicable", "no-global-contract-change"),
        ("final", "completed", "absorption-gate-closed"),
    ):
        run([
            sys.executable, str(temporary_tool), "absorb", "--task-id", temporary,
            "--scope", scope, "--state", state, "--evidence", evidence,
        ])
    delivery_record = json.loads(task_path.read_text(encoding="utf-8"))["temporary_executor"]["delivery"]
    run(["git", "update-ref", "-d", delivery_record["protected_ref"]], cwd=project)
    missing_protection_cleanup = run([
        sys.executable, str(temporary_tool), "cleanup", "--task-id", temporary,
        "--evidence", "must-not-clean-without-protected-delivery",
    ], ok=False)
    check("保护 ref 缺失" in missing_protection_cleanup.stderr and workspace.exists(),
          "cleanup removed the only delivery evidence")
    run(["git", "update-ref", delivery_record["protected_ref"], delivery], cwd=project)
    premature_archive = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "archived", "--evidence", "must-not-precede-resource-cleanup",
    ], ok=False)
    check("资源清理验证完成后" in premature_archive.stderr,
          "temporary session was marked archived before resource cleanup")
    cleaned = run([
        sys.executable, str(temporary_tool), "cleanup", "--task-id", temporary,
        "--evidence", "user-approved-lifecycle-complete",
    ])
    check(cleaned.stdout.startswith("TEMP_CLEANUP_OK |")
          and "ARCHIVE_THREAD_REQUIRED:temporary-thread-1" in cleaned.stdout,
          "temporary cleanup did not return the real thread archive action")
    check(not workspace.exists(), "temporary workspace survived verified cleanup")
    cleaned_payload = json.loads(task_path.read_text(encoding="utf-8"))
    check(cleaned_payload["temporary_executor"]["promotion_state"] == "archived"
          and cleaned_payload["temporary_executor"]["temporary_session"]["state"] == "standby",
          "resource cleanup falsely marked the real temporary session archived")
    unbound_archive_receipt = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "archived", "--evidence", "host=set_thread_archived archived=true",
    ], ok=False)
    check("绑定当前 thread_id" in unbound_archive_receipt.stderr,
          "temporary session accepted an archive receipt not bound to the real thread")
    sourceless_archive_receipt = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "archived", "--evidence", "thread_id=temporary-thread-1 archived=true",
    ], ok=False)
    check("host 或 user_confirmation" in sourceless_archive_receipt.stderr,
          "archive path accepted a receipt without an automatic or user-confirmation source")
    wrong_thread_archive_receipt = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "archived",
        "--evidence", "host=set_thread_archived thread_id=temporary-thread-1-extra archived=true",
    ], ok=False)
    check("绑定当前 thread_id" in wrong_thread_archive_receipt.stderr,
          "archive path accepted a prefixed but incorrect thread ID")
    empty_host_archive_receipt = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "archived",
        "--evidence", "host= thread_id=temporary-thread-1 archived=true",
    ], ok=False)
    check("host 或 user_confirmation" in empty_host_archive_receipt.stderr,
          "archive path accepted an empty host source")
    empty_user_archive_receipt = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "archived",
        "--evidence", "user_confirmation= thread_id=temporary-thread-1 archived=true",
    ], ok=False)
    check("host 或 user_confirmation" in empty_user_archive_receipt.stderr,
          "archive path accepted an empty user-confirmation source")
    inexact_archived_flag = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "archived",
        "--evidence", "host=set_thread_archived thread_id=temporary-thread-1 archived=trueish",
    ], ok=False)
    check("包含 archived=true" in inexact_archived_flag.stderr,
          "archive path accepted an inexact archived flag")
    conflicting_thread_receipt = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "archived",
        "--evidence", (
            "host=set_thread_archived thread_id=temporary-thread-1 "
            "thread_id=another-thread archived=true"
        ),
    ], ok=False)
    check("绑定当前 thread_id" in conflicting_thread_receipt.stderr,
          "archive path accepted conflicting thread IDs")
    conflicting_archived_receipt = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "archived",
        "--evidence", (
            "host=set_thread_archived thread_id=temporary-thread-1 "
            "archived=false archived=true"
        ),
    ], ok=False)
    check("包含 archived=true" in conflicting_archived_receipt.stderr,
          "archive path accepted conflicting archived flags")
    automatic_archive = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "archived",
        "--evidence", "host=set_thread_archived thread_id=temporary-thread-1 archived=true",
    ])
    automatically_archived = json.loads(task_path.read_text(encoding="utf-8"))
    check(automatic_archive.stdout.startswith("TEMP_SESSION_OK |")
          and automatically_archived["temporary_executor"]["temporary_session"]["state"] == "archived"
          and "host=set_thread_archived" in automatically_archived["temporary_executor"]["temporary_session"]["evidence"],
          "real host archive receipt did not close the automatic path")
    automatic_archive_retry = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "archived",
        "--evidence", "host=set_thread_archived thread_id=temporary-thread-1 archived=true retry=true",
    ])
    check(automatic_archive_retry.stdout.startswith("TEMP_SESSION_OK |"),
          "idempotent automatic archive receipt retry was rejected")
    cleanup_crash = json.loads(task_path.read_text(encoding="utf-8"))
    cleanup_crash_temp = cleanup_crash["temporary_executor"]
    cleanup_crash_temp["promotion_state"] = "integrated"
    cleanup_crash_temp["workspace"]["state"] = "ready"
    cleanup_crash_temp["temporary_session"]["state"] = "standby"
    cleanup_crash_temp["cleanup_operation"]["state"] = "started"
    cleanup_crash_temp["cleanup_operation"]["history"].append({
        "state": "started", "at": dt.datetime.now(dt.timezone.utc).isoformat(), "via": "simulated-crash",
    })
    task_path.write_text(json.dumps(cleanup_crash, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    reconciled_cleanup = run([
        sys.executable, str(temporary_tool), "reconcile-cleanup", "--task-id", temporary,
    ])
    reconciled_payload = json.loads(task_path.read_text(encoding="utf-8"))
    check("ARCHIVE_THREAD_REQUIRED:temporary-thread-1" in reconciled_cleanup.stdout
          and reconciled_payload["temporary_executor"]["temporary_session"]["state"] == "standby",
          "cleanup reconcile falsely closed or lost the real thread archive action")
    manual_archive = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "archived",
        "--evidence", "user_confirmation=用户明确说已经归档 thread_id=temporary-thread-1 archived=true",
    ])
    manually_archived = json.loads(task_path.read_text(encoding="utf-8"))
    check(manual_archive.stdout.startswith("TEMP_SESSION_OK |")
          and "user_confirmation=" in manually_archived["temporary_executor"]["temporary_session"]["evidence"],
          "clear user confirmation did not close the lightweight manual archive path")
    manual_archive_retry = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "archived",
        "--evidence", "user_confirmation=用户再次确认 thread_id=temporary-thread-1 archived=true retry=true",
    ])
    check(manual_archive_retry.stdout.startswith("TEMP_SESSION_OK |"),
          "idempotent manual archive receipt retry was rejected")
    protected = run(["git", "rev-parse", delivery_record["protected_ref"]], cwd=project)
    check(protected.stdout.strip() == delivery, "protected delivery evidence was lost during cleanup")
    missing_thread_original = json.loads(task_path.read_text(encoding="utf-8"))
    missing_thread = json.loads(json.dumps(missing_thread_original))
    missing_thread_temp = missing_thread["temporary_executor"]
    missing_thread_temp["promotion_state"] = "integrated"
    missing_thread_temp["temporary_session"].update(state="standby", thread_id="", evidence="lost-thread-regression")
    missing_thread_temp["cleanup_operation"]["state"] = "started"
    missing_thread_temp["cleanup_operation"]["history"].append({
        "state": "started", "at": dt.datetime.now(dt.timezone.utc).isoformat(), "via": "missing-thread-regression",
    })
    task_path.write_text(json.dumps(missing_thread, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    missing_thread_reconcile = run([
        sys.executable, str(temporary_tool), "reconcile-cleanup", "--task-id", temporary,
    ], ok=False)
    check("真实 thread_id 缺失" in missing_thread_reconcile.stderr,
          "integrated cleanup without a thread id was misclassified as no-session abandonment")
    task_path.write_text(json.dumps(missing_thread_original, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    abandoned = enqueue_dev("用户放弃的临时任务", "user_confirmed", "user-requested-temporary-outsourcing")
    abandoned_provision = run([
        sys.executable, str(temporary_tool), "provision", "--task-id", abandoned,
        "--parent-department", "开发部", "--executor-id", "temp-dev-abandoned",
        "--display-name", "待放弃临时开发外包", "--current-brief", "验证放弃任务清理收口",
        "--client-key", "client-temp-abandoned", "--scan-boundary-evidence", "已检查扫描边界",
        "--base-revision", "HEAD", "--write-path", "app/abandoned.py",
    ])
    check(abandoned_provision.stdout.startswith("TEMP_PROVISION_OK |"),
          "abandoned cleanup regression workspace was not provisioned")
    abandoned_path = collab / "tasks" / f"{abandoned}.json"
    abandoned_payload = json.loads(abandoned_path.read_text(encoding="utf-8"))
    abandoned_workspace = project / abandoned_payload["temporary_executor"]["workspace"]["path"]
    run([
        sys.executable, str(temporary_tool), "abandon", "--task-id", abandoned,
        "--evidence", "user-explicitly-replaced-the-scope",
    ])
    abandoned_cleanup = run([
        sys.executable, str(temporary_tool), "cleanup", "--task-id", abandoned,
        "--evidence", "user-approved-abandoned-workspace-cleanup",
    ])
    check(abandoned_cleanup.stdout.startswith("TEMP_CLEANUP_OK |")
          and "NO_THREAD_ARCHIVE_REQUIRED" in abandoned_cleanup.stdout
          and not abandoned_workspace.exists(),
          "abandoned temporary resources were not cleaned")
    abandoned_closed = json.loads(abandoned_path.read_text(encoding="utf-8"))
    check(abandoned_closed["execution_state"] == "completed"
          and abandoned_closed["temporary_executor"]["promotion_state"] == "archived"
          and abandoned_closed["temporary_executor"]["temporary_session"]["state"] == "cancelled"
          and abandoned_closed["artifacts"] == [f"docs/collaboration/tasks/{abandoned}.json"],
          "abandoned cleanup left the ordinary TASK axis active")
    run([sys.executable, str(task_tool), "list"])

    abandoned_crash = abandoned_closed
    abandoned_crash["execution_state"] = "claimed"
    abandoned_crash["artifacts"] = []
    abandoned_crash["external_artifacts"] = []
    abandoned_crash["verified"] = []
    abandoned_crash["unverified"] = []
    abandoned_crash["mistake_check"] = ""
    abandoned_crash["report"] = ""
    abandoned_crash["event_receipts"] = []
    abandoned_crash_temp = abandoned_crash["temporary_executor"]
    abandoned_crash_temp["promotion_state"] = "abandoned"
    abandoned_crash_temp["workspace"]["state"] = "ready"
    abandoned_crash_temp["temporary_session"]["state"] = "standby"
    abandoned_crash_temp["cleanup_operation"]["state"] = "started"
    abandoned_crash_temp["cleanup_operation"]["history"].append({
        "state": "started", "at": dt.datetime.now(dt.timezone.utc).isoformat(), "via": "simulated-crash",
    })
    abandoned_path.write_text(json.dumps(abandoned_crash, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    abandoned_reconciled = run([
        sys.executable, str(temporary_tool), "reconcile-cleanup", "--task-id", abandoned,
    ])
    abandoned_recovered = json.loads(abandoned_path.read_text(encoding="utf-8"))
    check("NO_THREAD_ARCHIVE_REQUIRED" in abandoned_reconciled.stdout
          and abandoned_recovered["execution_state"] == "completed"
          and abandoned_recovered["temporary_executor"]["temporary_session"]["state"] == "cancelled",
          "abandoned cleanup reconcile left the ordinary TASK axis active")
    abandoned_ack = run([
        sys.executable, str(temporary_tool), "acknowledge", "--task-id", abandoned,
        "--acknowledged-by", "统筹部/lead-thread",
    ])
    abandoned_archived = json.loads(abandoned_path.read_text(encoding="utf-8"))
    check(abandoned_ack.stdout.startswith("TEMP_ACK_ABANDONED_OK |")
          and abandoned_archived["execution_state"] == "acknowledged"
          and abandoned_archived["temporary_executor"]["promotion_state"] == "archived",
          "lead could not acknowledge a verified abandoned cleanup")
    run([sys.executable, str(task_tool), "list"])

    run(["git", "reflog", "expire", "--expire=now", "--all"], cwd=project)
    run(["git", "gc", "--prune=now"], cwd=project)
    run(["git", "cat-file", "-e", f"{delivery}^{{commit}}"], cwd=project)
    legacy_protocol = collab / "协议版本.json"
    legacy_protocol_payload = json.loads(legacy_protocol.read_text(encoding="utf-8"))
    legacy_protocol_payload["protocol_version"] = "1.4.1"
    legacy_protocol.write_text(json.dumps(legacy_protocol_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    project_guide = project / "docs" / "agent-guide.md"
    project_guide.write_text(
        project_guide.read_text(encoding="utf-8").replace("受管协议版本:1.4.5", "受管协议版本:1.4.1"),
        encoding="utf-8",
    )
    corrupt_legacy = json.loads(task_path.read_text(encoding="utf-8"))
    corrupt_legacy["temporary_executor"]["executor_type"] = "corrupted"
    task_path.write_text(json.dumps(corrupt_legacy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    corrupt_upgrade = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"], ok=False)
    check("temporary_executor 版本或类型无效" in corrupt_upgrade.stderr,
          "1.4.1 upgrade accepted a corrupt temporary executor truth")
    check(json.loads(legacy_protocol.read_text(encoding="utf-8"))["protocol_version"] == "1.4.1",
          "failed temporary TASK preflight advanced protocol")
    corrupt_legacy["temporary_executor"]["executor_type"] = "temporary"
    valid_legacy = json.loads(json.dumps(corrupt_legacy))
    corruptions = (
        ("candidate", {}, "candidate 结构无效"),
        ("review", {"decision": "pass"}, "review 结构无效"),
        ("delivery", {}, "delivery 结构无效"),
        ("integration", {"result": "pass"}, "integration 结构无效"),
    )
    for field, malformed, expected_error in corruptions:
        damaged = json.loads(json.dumps(valid_legacy))
        damaged["temporary_executor"][field] = malformed
        task_path.write_text(json.dumps(damaged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rejected = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"], ok=False)
        check(expected_error in rejected.stderr, f"1.4.1 upgrade accepted malformed nested {field}")
        check(json.loads(legacy_protocol.read_text(encoding="utf-8"))["protocol_version"] == "1.4.1",
              f"failed nested {field} preflight advanced protocol")
    for operation_mutation, expected_error in (
        ({"state": "not-a-real-operation-state"}, "operation state 无效"),
        ({"resources": [42]}, "operation resources 无效"),
        ({"history": [{"state": "verified", "at": ""}]}, "history 事件内容无效"),
        ({"history": [{"state": "started", "at": "2026-01-01T00:00:00+00:00"}]}, "history 末项不一致"),
    ):
        damaged = json.loads(json.dumps(valid_legacy))
        damaged["temporary_executor"]["operation"].update(operation_mutation)
        task_path.write_text(json.dumps(damaged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rejected = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"], ok=False)
        check(expected_error in rejected.stderr, "1.4.1 upgrade accepted malformed operation")
        check(json.loads(legacy_protocol.read_text(encoding="utf-8"))["protocol_version"] == "1.4.1",
              "failed operation preflight advanced protocol")
    valid_session = valid_legacy["temporary_executor"]["temporary_session"]
    malformed_sessions = (
        ("not-dict", ["not", "a", "session"]),
        ("missing-key", {key: value for key, value in valid_session.items() if key != "evidence"}),
        ("extra-key", {**valid_session, "unexpected": "field"}),
        ("state-type", {**valid_session, "state": 42}),
        ("thread-id-type", {**valid_session, "thread_id": 42}),
        ("evidence-type", {**valid_session, "evidence": ["not", "text"]}),
    )
    for label, malformed_session in malformed_sessions:
        damaged = json.loads(json.dumps(valid_legacy))
        damaged["temporary_executor"]["temporary_session"] = malformed_session
        task_path.write_text(json.dumps(damaged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rejected = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"], ok=False)
        check("temporary_executor 会话结构无效" in rejected.stderr,
              f"1.4.1 upgrade did not cleanly reject malformed session {label}")
        check(json.loads(legacy_protocol.read_text(encoding="utf-8"))["protocol_version"] == "1.4.1",
              f"failed session {label} preflight advanced protocol")
    corrupt_legacy = valid_legacy
    task_path.write_text(json.dumps(corrupt_legacy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    repaired_upgrade = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    repaired_payload = json.loads(task_path.read_text(encoding="utf-8"))
    check(repaired_upgrade.stdout.startswith("UPGRADE_OK |")
          and "ARCHIVE_THREAD_REQUIRED:temporary-thread-1" in repaired_upgrade.stdout,
          "valid legacy temporary TASK did not surface the real thread archive repair")
    check(repaired_payload["temporary_executor"]["temporary_session"]["state"] == "standby"
          and "旧 archived 记录缺少宿主收据" in repaired_payload["temporary_executor"]["temporary_session"]["evidence"],
          "legacy upgrade retained an unverified archived session state")
    for invalid_evidence, label in (
        ("host= thread_id=temporary-thread-1 archived=true", "empty-source"),
        ("host=set_thread_archived thread_id=temporary-thread-1-extra archived=true", "wrong-thread"),
        ("host=set_thread_archived thread_id=temporary-thread-1 archived=trueish", "inexact-flag"),
        (
            "host=set_thread_archived thread_id=temporary-thread-1 "
            "thread_id=another-thread archived=true",
            "conflicting-thread",
        ),
        (
            "host=set_thread_archived thread_id=temporary-thread-1 archived=false archived=true",
            "conflicting-flag",
        ),
    ):
        invalid_144_receipt = json.loads(task_path.read_text(encoding="utf-8"))
        invalid_144_receipt["temporary_executor"]["temporary_session"].update(
            state="archived",
            evidence=invalid_evidence,
        )
        task_path.write_text(json.dumps(invalid_144_receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        current_protocol_payload = json.loads(legacy_protocol.read_text(encoding="utf-8"))
        current_protocol_payload["protocol_version"] = "1.4.4"
        legacy_protocol.write_text(json.dumps(current_protocol_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        project_guide.write_text(
            project_guide.read_text(encoding="utf-8").replace("受管协议版本:1.4.5", "受管协议版本:1.4.4"),
            encoding="utf-8",
        )
        rejected_144_upgrade = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
        rejected_144_payload = json.loads(task_path.read_text(encoding="utf-8"))
        check("ARCHIVE_THREAD_REQUIRED:temporary-thread-1" in rejected_144_upgrade.stdout
              and rejected_144_payload["temporary_executor"]["temporary_session"]["state"] == "standby",
              f"1.4.4 upgrade retained an invalid {label} archive receipt")

    receipt_143 = "host=set_thread_archived thread_id=temporary-thread-1 archived=true"
    run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "archived",
        "--evidence", receipt_143,
    ])
    protocol_143_payload = json.loads(legacy_protocol.read_text(encoding="utf-8"))
    protocol_143_payload["protocol_version"] = "1.4.3"
    legacy_protocol.write_text(json.dumps(protocol_143_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    project_guide.write_text(
        project_guide.read_text(encoding="utf-8").replace("受管协议版本:1.4.5", "受管协议版本:1.4.3"),
        encoding="utf-8",
    )
    preserved_143_upgrade = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    preserved_143_payload = json.loads(task_path.read_text(encoding="utf-8"))
    check(preserved_143_upgrade.stdout.startswith("UPGRADE_OK |")
          and "ARCHIVE_THREAD_REQUIRED" not in preserved_143_upgrade.stdout
          and json.loads(legacy_protocol.read_text(encoding="utf-8"))["protocol_version"] == "1.4.5"
          and preserved_143_payload["temporary_executor"]["temporary_session"]["state"] == "archived"
          and preserved_143_payload["temporary_executor"]["temporary_session"]["evidence"] == receipt_143,
          "real 1.4.3 host receipt was needlessly invalidated during 1.4.5 upgrade")

    receipt_144_automatic = "archive_mode=automatic host=set_thread_archived thread_id=temporary-thread-1 archived=true"
    run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "archived",
        "--evidence", receipt_144_automatic,
    ])
    automatic_144_protocol = json.loads(legacy_protocol.read_text(encoding="utf-8"))
    automatic_144_protocol["protocol_version"] = "1.4.4"
    legacy_protocol.write_text(json.dumps(automatic_144_protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    project_guide.write_text(
        project_guide.read_text(encoding="utf-8").replace("受管协议版本:1.4.5", "受管协议版本:1.4.4"),
        encoding="utf-8",
    )
    preserved_144_automatic = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    preserved_144_automatic_payload = json.loads(task_path.read_text(encoding="utf-8"))
    check(preserved_144_automatic.stdout.startswith("UPGRADE_OK |")
          and "ARCHIVE_THREAD_REQUIRED" not in preserved_144_automatic.stdout
          and json.loads(legacy_protocol.read_text(encoding="utf-8"))["protocol_version"] == "1.4.5"
          and preserved_144_automatic_payload["temporary_executor"]["temporary_session"]["state"] == "archived"
          and preserved_144_automatic_payload["temporary_executor"]["temporary_session"]["evidence"] == receipt_144_automatic,
          "real 1.4.4 automatic receipt was needlessly invalidated during 1.4.5 upgrade")

    receipt_144_manual = (
        "archive_mode=manual thread_id=temporary-thread-1 archived=true "
        "user_confirmation=我已将该会话归档 evidence=current-user-message"
    )
    run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "archived",
        "--evidence", receipt_144_manual,
    ])
    manual_144_protocol = json.loads(legacy_protocol.read_text(encoding="utf-8"))
    manual_144_protocol["protocol_version"] = "1.4.4"
    legacy_protocol.write_text(json.dumps(manual_144_protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    project_guide.write_text(
        project_guide.read_text(encoding="utf-8").replace("受管协议版本:1.4.5", "受管协议版本:1.4.4"),
        encoding="utf-8",
    )
    preserved_144_manual = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    preserved_144_manual_payload = json.loads(task_path.read_text(encoding="utf-8"))
    check(preserved_144_manual.stdout.startswith("UPGRADE_OK |")
          and "ARCHIVE_THREAD_REQUIRED" not in preserved_144_manual.stdout
          and json.loads(legacy_protocol.read_text(encoding="utf-8"))["protocol_version"] == "1.4.5"
          and preserved_144_manual_payload["temporary_executor"]["temporary_session"]["state"] == "archived"
          and preserved_144_manual_payload["temporary_executor"]["temporary_session"]["evidence"] == receipt_144_manual,
          "real 1.4.4 manual receipt was needlessly invalidated during 1.4.5 upgrade")

    pending_144_archive = json.loads(task_path.read_text(encoding="utf-8"))
    pending_144_archive["temporary_executor"]["temporary_session"].update(
        state="standby",
        evidence="waiting-for-user-manual-archive",
    )
    task_path.write_text(json.dumps(pending_144_archive, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending_144_protocol = json.loads(legacy_protocol.read_text(encoding="utf-8"))
    pending_144_protocol["protocol_version"] = "1.4.4"
    legacy_protocol.write_text(json.dumps(pending_144_protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    project_guide.write_text(
        project_guide.read_text(encoding="utf-8").replace("受管协议版本:1.4.5", "受管协议版本:1.4.4"),
        encoding="utf-8",
    )
    pending_archive_upgrade = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    pending_archive_payload = json.loads(task_path.read_text(encoding="utf-8"))
    check("ARCHIVE_THREAD_REQUIRED:temporary-thread-1" in pending_archive_upgrade.stdout
          and "reason:existing-standby-archive" in pending_archive_upgrade.stdout
          and pending_archive_payload["temporary_executor"]["temporary_session"]["state"] == "standby"
          and pending_archive_payload["temporary_executor"]["temporary_session"]["evidence"] == "waiting-for-user-manual-archive",
          "1.4.4 cleaned standby session did not resurface its pending archive action")


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
    flat_guide.write_text(flat_guide.read_text(encoding="utf-8").replace("受管协议版本:1.4.5", "受管协议版本:1.3.0"), encoding="utf-8")
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
    check("受管协议版本:1.4.5" in flat_guide.read_text(encoding="utf-8"),
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
        verify_temporary_executor(root)
        verify_upgrade_and_guards(root)
    print("VERIFY_OK | scaffold, task, temporary executor, tested-tree promotion, absorption, log, session, upgrade, migration, and path guards passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VerifyError as exc:
        print(f"VERIFY_ERROR | {exc}", file=sys.stderr)
        raise SystemExit(1)
