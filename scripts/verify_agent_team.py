#!/usr/bin/env python3
"""Black-box regression verifier for the Agent Team scaffold."""

from __future__ import annotations

import datetime as dt
import contextlib
import importlib.util
import io
import json
import os
import py_compile
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCAFFOLD = Path(os.environ.get("AGENT_TEAM_SCAFFOLD", ROOT / "scripts" / "scaffold_team.py")).expanduser().resolve()
PUBLIC_VERSION = "2.0.2"
SOURCE_VERSION = "2.0.2"
PROTOCOL_VERSION = "1.4.6"
PREVIOUS_PROTOCOL_VERSION = "1.4.5"
RUNTIME_FILES = (
    "SKILL.md",
    "agents/openai.yaml",
    "references/temporary-executor.md",
    "scripts/scaffold_team.py",
    "scripts/temporary_executor_runtime.py",
)
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


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise VerifyError(f"frontmatter contains a duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def yaml_frontmatter(text: str, *, label: str) -> dict[object, object]:
    match = re.match(r"^---\n(.*?)\n---(?:\n|$)", text, re.DOTALL)
    check(match is not None, f"{label} frontmatter is missing or malformed")
    try:
        fields = yaml.load(match.group(1), Loader=UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise VerifyError(f"{label} frontmatter is invalid YAML: {exc}") from exc
    check(isinstance(fields, dict), f"{label} frontmatter must be a YAML mapping")
    return fields


def compile_script(path: Path) -> None:
    """Compile without writing to the host user's global Python cache."""
    with tempfile.TemporaryDirectory(prefix="agent-team-pycompile-") as temp:
        py_compile.compile(str(path), cfile=str(Path(temp) / f"{path.name}.pyc"), doraise=True)


def verify_installed_copy(installed_root: Path) -> None:
    check(installed_root.is_dir() and not installed_root.is_symlink(), "installed copy root is missing or unsafe")
    actual: set[str] = set()
    for path in installed_root.rglob("*"):
        if path.is_symlink():
            raise VerifyError(f"installed copy contains symlink: {path.relative_to(installed_root)}")
        if path.is_file():
            actual.add(path.relative_to(installed_root).as_posix())
    expected = set(RUNTIME_FILES)
    check(actual == expected,
          f"installed copy file list mismatch: expected={sorted(expected)} actual={sorted(actual)}")
    for relative in RUNTIME_FILES:
        source = ROOT / relative
        installed = installed_root / relative
        check(source.is_file() and installed.is_file(), f"runtime file missing: {relative}")
        check(source.read_bytes() == installed.read_bytes(), f"installed copy content mismatch: {relative}")


def verify_install_bundle_contract(root: Path) -> None:
    installed = root / "installed-copy"
    for relative in RUNTIME_FILES:
        destination = installed / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    good = run([sys.executable, str(Path(__file__).resolve()), "--check-installed-copy", str(installed)])
    check(good.stdout.startswith("INSTALL_COPY_OK |"), "exact five-file installed copy was rejected")

    extra = installed / "README.md"
    extra.write_text("development-only\n", encoding="utf-8")
    rejected_extra = run(
        [sys.executable, str(Path(__file__).resolve()), "--check-installed-copy", str(installed)], ok=False,
    )
    check("file list mismatch" in rejected_extra.stderr, "installed copy accepted a sixth runtime file")
    extra.unlink()

    target = installed / "SKILL.md"
    original = target.read_bytes()
    target.write_bytes(original + b"\ncontent-drift\n")
    rejected_drift = run(
        [sys.executable, str(Path(__file__).resolve()), "--check-installed-copy", str(installed)], ok=False,
    )
    check("content mismatch" in rejected_drift.stderr, "installed copy accepted drifted runtime content")
    target.write_bytes(original)


def verify_repository_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    temporary_reference = (ROOT / "references" / "temporary-executor.md").read_text(encoding="utf-8")
    semantic_review = (ROOT / "tests" / "semantic_review.md").read_text(encoding="utf-8")
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    openai_yaml = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
    frontmatter_fields = yaml_frontmatter(skill, label="SKILL")
    reference_frontmatter = yaml_frontmatter(temporary_reference, label="temporary executor reference")
    semantic_frontmatter = yaml_frontmatter(semantic_review, label="semantic review matrix")
    allowed_frontmatter = {"name", "description", "license", "allowed-tools", "metadata"}
    check(set(frontmatter_fields) <= allowed_frontmatter,
          "SKILL frontmatter contains unsupported fields")
    skill_name = frontmatter_fields.get("name", "")
    check(isinstance(skill_name, str)
          and bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", skill_name))
          and len(skill_name) <= 64,
          "SKILL name violates the official hyphen-case or 64-character contract")
    description = frontmatter_fields.get("description", "")
    check(isinstance(description, str) and bool(description)
          and len(description) <= 1024 and "<" not in description and ">" not in description,
          "SKILL description violates the official metadata contract")
    metadata = frontmatter_fields.get("metadata")
    check(isinstance(metadata, dict) and metadata.get("version") == SOURCE_VERSION,
          "SKILL metadata did not identify the current source build")
    check(
        f"公开发布版本为 `{PUBLIC_VERSION}`" in readme
        and f"当前源码构建为 `{SOURCE_VERSION}`" in readme
        and f"运行协议为 `{PROTOCOL_VERSION}`" in readme,
        "README conflated or omitted public, source-build, or runtime protocol versions",
    )
    check(
        "releases/latest/download/agent-team-2.0-pure.zip" in readme
        and "releases/latest/download/agent-team-2.0-pure.zip.sha256" in readme,
        "README omitted the stable latest pure-package or checksum URL",
    )
    check(
        set(reference_frontmatter) == {"title", "status"}
        and reference_frontmatter.get("status") == "implemented"
        and f"运行协议 `{PROTOCOL_VERSION}`" in temporary_reference
        and "pending-archives" in temporary_reference
        and "YAML frontmatter" in temporary_reference,
        "temporary executor cold reference is missing or carries redundant version metadata",
    )
    check(
        "只询问“能不能并行" in temporary_reference
        and "如果可以就帮我开" in temporary_reference
        and "取消 / 先停一下" in temporary_reference
        and "不自动等于 `abandoned`" in temporary_reference
        and "长期无回复只进入 `standby`" in temporary_reference
        and "必须暂停并升级统筹部" in temporary_reference
        and "不能自行把判断写成正式 Spec" in temporary_reference,
        "temporary executor reference lost a user-intent or authority boundary",
    )
    check(
        semantic_frontmatter == {
            "title": "Agent Team 人工语义审查矩阵",
            "status": "required-before-release",
            "scope": "semantic-boundaries",
        }
        and all(f"S{index:02d}" in semantic_review for index in range(1, 17))
        and "自动测试通过不能替代" in readme
        and "本文件只保存稳定问题，不写某次执行结果" in semantic_review,
        "manual semantic release gate is missing or incomplete",
    )
    check(
        f"当前运行协议为 `{PROTOCOL_VERSION}`" in skill
        and "references/temporary-executor.md" in skill
        and "未触发时不要读取" in skill
        and "文档修订号" not in readme
        and "文档修订号" not in temporary_reference,
        "SKILL did not keep temporary-executor rules on the cold path",
    )
    workflow_lines = workflow.splitlines()
    try:
        on_index = workflow_lines.index("on:")
        jobs_index = workflow_lines.index("jobs:")
    except ValueError as exc:
        raise VerifyError("CI workflow is missing exact on/jobs sections") from exc
    trigger_lines = [
        line.strip() for line in workflow_lines[on_index + 1:jobs_index]
        if line.strip() and not line.lstrip().startswith("#")
    ]
    check(trigger_lines == ["push:", "pull_request:"],
          "CI must run on every push and pull request without branch filters")
    check('python-version: ["3.9", "3.11"]' in workflow,
          "CI no longer verifies both Python 3.9 and 3.11")
    try:
        openai_metadata = yaml.load(openai_yaml, Loader=UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise VerifyError(f"agents/openai.yaml is invalid YAML: {exc}") from exc
    interface = openai_metadata.get("interface") if isinstance(openai_metadata, dict) else None
    display_name = interface.get("display_name") if isinstance(interface, dict) else None
    short_description = interface.get("short_description") if isinstance(interface, dict) else None
    default_prompt = interface.get("default_prompt") if isinstance(interface, dict) else None
    check(isinstance(display_name, str) and bool(display_name.strip()) and len(display_name) <= 64,
          "agents/openai.yaml display_name must be a non-empty string of at most 64 characters")
    check(isinstance(short_description, str) and 25 <= len(short_description) <= 64,
          "agents/openai.yaml short_description is outside the 25-64 character UI contract")
    check(isinstance(default_prompt, str) and f"${skill_name}" in default_prompt,
          "agents/openai.yaml default_prompt does not explicitly invoke the skill")


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
        "会话启动状态.json", "任务交接模板.md", "错题集.md", "模板/工作报告.md", "模板/审核报告.md",
        "模板/专项结论.md", "scripts/agent_team_log.py", "scripts/agent_team_task.py",
        "scripts/agent_team_session.py", "scripts/agent_team_temporary.py",
    ]
    for relative in required:
        check((collab / relative).is_file(), f"missing generated file: {relative}")
    check(not (collab / "读取路由规则.md").exists(), "obsolete reading rules generated")
    check(not (collab / "scripts" / "agent_team_read.py").exists(), "obsolete reader generated")
    protocol = json.loads((collab / "协议版本.json").read_text(encoding="utf-8"))
    check(protocol["protocol_version"] == PROTOCOL_VERSION, "unexpected protocol version")
    for script in (collab / "scripts").glob("*.py"):
        compile_script(script)
    guide = (project / "docs" / "agent-guide.md").read_text(encoding="utf-8")
    check(f"受管协议版本:{PROTOCOL_VERSION}" in guide and "任务真值" in guide,
          "project guide not refreshed")
    collaboration_readme = (collab / "README.md").read_text(encoding="utf-8")
    check("有归档工具时立即调用" in collaboration_readme
          and "host=<真实工具>" in collaboration_readme
          and "调用失败或没有工具时提醒用户" in collaboration_readme
          and "我目前无法自动归档这个会话" in collaboration_readme
          and "归档完成后告诉我一声" in collaboration_readme
          and "user_confirmation=" in collaboration_readme
          and "temporary_session=standby" in collaboration_readme
          and "一旦已登记新 thread ID" in collaboration_readme
          and "`restore-old` 必须带精确绑定新 ID 的归档回执" in collaboration_readme,
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
    role_text = (collab / "部门" / "执行部" / "岗位说明.md").read_text(encoding="utf-8")
    bootstrap_text = (collab / "部门" / "执行部" / "上岗引导.md").read_text(encoding="utf-8")
    check(
        "缺失时回统筹部补齐,不自行脑补" in role_text
        and "交班只更新交接和必要日志,不等于 Git commit" in role_text
        and "换会话 / 切换会话 / 换班" in role_text,
        "slimmed role guide lost an escalation, commit, or switch-language guard",
    )
    check("换会话 / 换班" in bootstrap_text and "不 fork 旧历史" in bootstrap_text,
          "slimmed bootstrap lost the explicit session-switch boundary")


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

    tasks = collab / "tasks"
    safe_tasks = collab / "tasks-safe"
    outside_tasks = project.parent / "outside-task-store"
    outside_tasks.mkdir()
    tasks.rename(safe_tasks)
    tasks.symlink_to(outside_tasks, target_is_directory=True)
    try:
        denied_tasks_symlink = run([
            sys.executable, str(tool), "enqueue", "--department", "执行部",
            "--from-department", "统筹部", "--title", "越界任务",
            "--node", "单节点", "--details", "不得写入项目外 tasks",
            "--acceptance-exit", "明确拒绝", "--failure-path", "tasks 父目录是符号链接",
            "--authorization-state", "none",
        ], ok=False)
        check("不安全" in denied_tasks_symlink.stderr,
              "task mutation did not reject a symlinked tasks parent")
        check(not any(outside_tasks.iterdir()), "task mutation wrote through a symlinked tasks parent")
    finally:
        tasks.unlink(missing_ok=True)
        safe_tasks.rename(tasks)
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

    canonical_text = json.dumps(original, ensure_ascii=False, indent=2)
    duplicate_key_text = canonical_text.replace(
        "{\n", "{\n  \"title\": \"重复键不得静默覆盖\",\n", 1,
    ) + "\n"
    schema_path.write_text(duplicate_key_text, encoding="utf-8")
    denied_duplicate_key = run([sys.executable, str(tool), "list"], ok=False)
    check("重复" in denied_duplicate_key.stderr and "title" in denied_duplicate_key.stderr,
          "TASK JSON duplicate key was silently accepted")
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
    state_path = collab / "会话启动状态.json"
    pristine_session_truth = state_path.read_bytes()
    for invalid_thread_id in ("thread with space", "thread\twith-tab", "=receipt-ambiguous", "x" * 301):
        rejected_identity = run([
            sys.executable, str(session), "mark", "--department", "执行部", "--step", "created",
            "--thread-id", invalid_thread_id, "--evidence", "must-remain-receipt-representable",
        ], ok=False)
        check(("thread-id" in rejected_identity.stderr or "归档回执" in rejected_identity.stderr)
              and state_path.read_bytes() == pristine_session_truth,
              "formal session accepted an identity that can never be represented in an archive receipt")
    duplicate_formal_thread = run([
        sys.executable, str(session), "mark", "--department", "执行部", "--step", "created",
        "--thread-id", "lead-thread", "--evidence", "must-not-reuse-lead-thread",
    ], ok=False)
    check("thread" in duplicate_formal_thread.stderr.casefold()
          and ("占用" in duplicate_formal_thread.stderr or "冲突" in duplicate_formal_thread.stderr),
          "two formal departments accepted the same thread ID")
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
    state_text = state_path.read_text(encoding="utf-8")
    duplicate_state_text = state_text.replace(
        "{\n", "{\n  \"schema_version\": 1,\n", 1,
    )
    state_path.write_text(duplicate_state_text, encoding="utf-8")
    duplicate_session_key = run([sys.executable, str(session), "show"], ok=False)
    check("重复" in duplicate_session_key.stderr and "schema_version" in duplicate_session_key.stderr,
          "session JSON duplicate key was silently accepted")
    state_path.write_text(state_text, encoding="utf-8")
    state = json.loads(state_text)
    check(state["departments"]["执行部"]["notification_mode"] == "manual", "initial notification mode missing")
    run([sys.executable, str(session), "set-notification", "--department", "执行部",
         "--mode", "auto", "--evidence", "user-approved-notification-change"])
    run([sys.executable, str(session), "begin-switch", "--department", "统筹部",
         "--old-thread-id", "lead-thread", "--reason", "verify pre-create rollback"])
    restored_before_create = run([
        sys.executable, str(session), "restore-old", "--department", "统筹部",
        "--note", "new session was never created",
    ])
    restored_lead_state = json.loads(state_path.read_text(encoding="utf-8"))["departments"]["统筹部"]
    check(restored_before_create.stdout.startswith("SESSION_RESTORED |")
          and restored_lead_state["thread_id"] == "lead-thread"
          and restored_lead_state["previous_thread_id"] == ""
          and restored_lead_state["operation_id"].startswith("ACTIVE-"),
          "pre-create switch rollback did not safely restore the old session")
    run([sys.executable, str(session), "begin-switch", "--department", "执行部",
         "--old-thread-id", "thread-1", "--reason", "user approved"])
    for step, evidence in (("created", "create-2"), ("onboarded", "send-2"), ("registered", "register-2")):
        args = [sys.executable, str(session), "mark", "--department", "执行部", "--step", step,
                "--thread-id", "thread-2", "--evidence", evidence]
        run(args)
    before_finish_switch = state_path.read_bytes()
    unarchived_restore = run([
        sys.executable, str(session), "restore-old", "--department", "执行部",
        "--note", "must-not-discard-new-thread",
    ], ok=False)
    wrong_new_archive_restore = run([
        sys.executable, str(session), "restore-old", "--department", "执行部",
        "--note", "must-not-discard-new-thread",
        "--evidence", "host=set_thread_archived thread_id=thread-2-extra archived=true",
    ], ok=False)
    nested_switch = run([
        sys.executable, str(session), "begin-switch", "--department", "执行部",
        "--old-thread-id", "thread-2", "--reason", "must-not-overwrite-pending-old-thread",
    ], ok=False)
    check(all(result.returncode != 0 for result in (unarchived_restore, wrong_new_archive_restore, nested_switch))
          and "归档回执" in unarchived_restore.stderr
          and state_path.read_bytes() == before_finish_switch,
          "registered switch truth lost a new or previous thread without an archive receipt")
    for false_evidence in (
        "archive-receipt",
        "host=set_thread_archived thread_id=thread-1-extra archived=true",
        "host=set_thread_archived thread_id=THREAD-1 archived=true",
        "host=set_thread_archived thread_id=thread-1 archived=false",
    ):
        rejected_finish = run([
            sys.executable, str(session), "finish-switch", "--department", "执行部",
            "--new-thread-id", "thread-2", "--evidence", false_evidence,
        ], ok=False)
        check("归档" in rejected_finish.stderr and state_path.read_bytes() == before_finish_switch,
              "finish-switch accepted false archive evidence or mutated switch truth")
    finished_switch_receipt = "host=set_thread_archived thread_id=thread-1 archived=true"
    run([
        sys.executable, str(session), "finish-switch", "--department", "执行部",
        "--new-thread-id", "thread-2",
        "--evidence", finished_switch_receipt,
    ])
    state = json.loads((collab / "会话启动状态.json").read_text(encoding="utf-8"))
    check(state["departments"]["执行部"]["thread_id"] == "thread-2"
          and state["departments"]["执行部"]["evidence"] == finished_switch_receipt,
          "switch did not persist the new thread and durable old-thread archive receipt")
    check(state["departments"]["执行部"]["notification_mode"] == "auto", "notification mode did not persist")
    run([sys.executable, str(session), "set-notification", "--department", "执行部",
         "--mode", "manual", "--evidence", "notification-change-must-not-delete-archive-receipt"])
    notification_state = json.loads(state_path.read_text(encoding="utf-8"))["departments"]["执行部"]
    check(notification_state["evidence"] == finished_switch_receipt,
          "set-notification overwrote the durable finish-switch archive receipt")
    run([sys.executable, str(session), "set-notification", "--department", "执行部",
         "--mode", "auto", "--evidence", "restore-auto-notification-mode"])
    for step, evidence in (("created", "review-old-create"), ("onboarded", "review-old-onboard"),
                           ("registered", "review-old-register")):
        run([
            sys.executable, str(session), "mark", "--department", "检验部", "--step", step,
            "--thread-id", "review-old-thread", "--evidence", evidence,
        ])
    run([
        sys.executable, str(session), "begin-switch", "--department", "检验部",
        "--old-thread-id", "review-old-thread", "--reason", "verify archived new-session rollback",
    ])
    run([
        sys.executable, str(session), "mark", "--department", "检验部", "--step", "created",
        "--thread-id", "review-new-thread", "--evidence", "review-new-created",
    ])
    review_switch_truth = state_path.read_bytes()
    review_unarchived_restore = run([
        sys.executable, str(session), "restore-old", "--department", "检验部",
        "--note", "new review session failed onboarding",
    ], ok=False)
    check("归档回执" in review_unarchived_restore.stderr
          and state_path.read_bytes() == review_switch_truth,
          "created new session disappeared during restore-old without archive evidence")
    review_archive_receipt = "host=set_thread_archived thread_id=review-new-thread archived=true"
    restored_review = run([
        sys.executable, str(session), "restore-old", "--department", "检验部",
        "--note", "new review session archived after onboarding failure",
        "--evidence", review_archive_receipt,
    ])
    restored_review_state = json.loads(state_path.read_text(encoding="utf-8"))["departments"]["检验部"]
    check(restored_review.stdout.startswith("SESSION_RESTORED |")
          and restored_review_state["thread_id"] == "review-old-thread"
          and restored_review_state["previous_thread_id"] == ""
          and restored_review_state["operation_id"].startswith("ACTIVE-")
          and restored_review_state["evidence"] == review_archive_receipt,
          "restore-old did not retain the archived new-session receipt before restoring the old session")
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

    run([sys.executable, str(task_tool), "resume", "--task-id", formal])
    run([
        sys.executable, str(task_tool), "complete", "--task-id", formal,
        "--artifact", "app/base.py", "--verified", "缺声明场景已完成",
        "--unverified", "无", "--mistake-check", "未把缺声明误判为安全",
    ])
    run([
        sys.executable, str(task_tool), "ack", "--task-id", formal,
        "--acknowledged-by", "统筹部/lead-thread",
    ])

    formal = enqueue_dev("正式任务 A（已声明影响）", "none")
    formal_path = collab / "tasks" / f"{formal}.json"
    formal_revision = json.loads(formal_path.read_text(encoding="utf-8"))["revision"]
    run([
        sys.executable, str(task_tool), "declare-impact", "--task-id", formal,
        "--expected-revision", str(formal_revision), "--base-revision", "HEAD",
        "--write-path", "app/a.py", "--shared-contract", "auth-v1",
    ])
    run([sys.executable, str(task_tool), "claim", "--task-id", formal, "--claimed-by", "dev-session"])
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
    reverse_formal = enqueue_dev("正式任务反向冲突探针", "none")
    reverse_formal_path = collab / "tasks" / f"{reverse_formal}.json"
    formal_before_reverse_impact = reverse_formal_path.read_bytes()
    formal_reverse_revision = json.loads(formal_before_reverse_impact)["revision"]
    reverse_impact = run([
        sys.executable, str(task_tool), "declare-impact", "--task-id", reverse_formal,
        "--expected-revision", str(formal_reverse_revision), "--base-revision", "HEAD",
        "--write-path", "app/b.py",
    ], ok=False)
    check(("冲突" in reverse_impact.stderr or "重叠" in reverse_impact.stderr)
          and reverse_formal_path.read_bytes() == formal_before_reverse_impact,
          "formal impact declaration overwrote a conflicting active temporary scope")

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
          f"legacy temporary TASK did not normalize safely for {PROTOCOL_VERSION}")
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
    duplicate_formal_thread = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "active", "--thread-id", "lead-thread",
        "--rule-digest", temp["rule"]["digest"], "--evidence", "must-not-reuse-formal-thread",
    ], ok=False)
    check("thread" in duplicate_formal_thread.stderr.casefold()
          and ("占用" in duplicate_formal_thread.stderr or "冲突" in duplicate_formal_thread.stderr),
          "temporary session reused a formal department thread ID")
    pristine_temporary_identity = task_path.read_bytes()
    for invalid_thread_id in ("temporary thread", "temporary\tthread", "=receipt-ambiguous", "t" * 301):
        rejected_identity = run([
            sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
            "--state", "active", "--thread-id", invalid_thread_id,
            "--rule-digest", temp["rule"]["digest"], "--evidence", "must-remain-receipt-representable",
        ], ok=False)
        check(("thread-id" in rejected_identity.stderr or "归档回执" in rejected_identity.stderr)
              and task_path.read_bytes() == pristine_temporary_identity,
              "temporary session accepted an identity that can never be represented in an archive receipt")
    run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "failed", "--thread-id", "temporary-thread-1",
        "--evidence", "real-session-created-but-onboarding-failed",
    ])
    provisioning_failure = json.loads(task_path.read_text(encoding="utf-8"))["temporary_executor"]["temporary_session"]
    check(provisioning_failure["state"] == "failed"
          and provisioning_failure["thread_id"] == "temporary-thread-1",
          "failed session registration lost the real thread ID created during provisioning")
    failed_provisioning_truth = task_path.read_bytes()
    replaced_provisioning_thread = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "active", "--thread-id", "temporary-thread-replacement",
        "--rule-digest", temp["rule"]["digest"], "--evidence", "must-not-replace-created-thread",
    ], ok=False)
    check("原始 ID" in replaced_provisioning_thread.stderr
          and task_path.read_bytes() == failed_provisioning_truth,
          "provisioning failure retry replaced a recorded real thread ID")
    run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "active", "--thread-id", "temporary-thread-1",
        "--rule-digest", temp["rule"]["digest"], "--evidence", "rule-read-confirmed",
    ])
    run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "failed", "--evidence", "temporary-session-connection-failed",
    ])
    failed_session_truth = task_path.read_bytes()
    replaced_failed_thread = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "active", "--thread-id", "temporary-thread-replacement",
        "--rule-digest", temp["rule"]["digest"], "--evidence", "must-not-replace-failed-thread",
    ], ok=False)
    check("原始 ID" in replaced_failed_thread.stderr
          and task_path.read_bytes() == failed_session_truth,
          "failed temporary session retry replaced a real thread ID without archive evidence")
    run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "active", "--thread-id", "temporary-thread-1",
        "--rule-digest", temp["rule"]["digest"], "--evidence", "same-thread-reconnected",
    ])
    active_session_truth = task_path.read_bytes()
    cancelled_real_thread = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "cancelled", "--evidence", "must-not-cancel-real-thread",
    ], ok=False)
    check("cleanup 内部" in cancelled_real_thread.stderr
          and task_path.read_bytes() == active_session_truth,
          "active temporary session with a real thread ID was falsely cancelled")
    formal_reuses_temporary = run([
        sys.executable, str(session_tool), "mark", "--department", "设计部", "--step", "created",
        "--thread-id", "temporary-thread-1", "--evidence", "must-not-reuse-temporary-thread",
    ], ok=False)
    check("thread" in formal_reuses_temporary.stderr.casefold()
          and ("占用" in formal_reuses_temporary.stderr or "冲突" in formal_reuses_temporary.stderr),
          "formal department reused an active temporary thread ID")

    duplicate_temp = enqueue_dev(
        "第二个临时任务用于会话 ID 唯一性", "user_confirmed", "user-requested-temporary-outsourcing",
    )
    run([
        sys.executable, str(temporary_tool), "provision", "--task-id", duplicate_temp,
        "--parent-department", "开发部", "--executor-id", "temp-dev-duplicate-thread",
        "--display-name", "临时开发外包二", "--current-brief", "验证临时会话 ID 唯一性",
        "--client-key", "client-temp-duplicate-thread", "--scan-boundary-evidence", "已检查扫描边界",
        "--base-revision", "HEAD", "--write-path", "app/duplicate-thread.py",
    ])
    duplicate_temp_path = collab / "tasks" / f"{duplicate_temp}.json"
    duplicate_temp_payload = json.loads(duplicate_temp_path.read_text(encoding="utf-8"))["temporary_executor"]
    temp_reuses_temp = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", duplicate_temp,
        "--state", "active", "--thread-id", "temporary-thread-1",
        "--rule-digest", duplicate_temp_payload["rule"]["digest"],
        "--evidence", "must-not-reuse-other-temporary-thread",
    ], ok=False)
    check("thread" in temp_reuses_temp.stderr.casefold()
          and ("占用" in temp_reuses_temp.stderr or "冲突" in temp_reuses_temp.stderr),
          "two temporary TASKs accepted the same thread ID")
    run([
        sys.executable, str(temporary_tool), "abandon", "--task-id", duplicate_temp,
        "--evidence", "duplicate-thread-probe-complete",
    ])
    duplicate_cleanup = run([
        sys.executable, str(temporary_tool), "cleanup", "--task-id", duplicate_temp,
        "--evidence", "remove-duplicate-thread-probe",
    ])
    check("NO_THREAD_ARCHIVE_REQUIRED" in duplicate_cleanup.stdout,
          "temporary thread uniqueness probe did not close its unused workspace")
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
    (workspace / "app" / "b.py").write_text("VALUE = 'temporary-b-v3'\n", encoding="utf-8")
    run(["git", "add", "app/b.py"], cwd=workspace)
    run(["git", "commit", "-m", "change workspace after frozen candidate"], cwd=workspace)
    candidate_after_new_commit = run([
        sys.executable, str(temporary_tool), "submit", "--task-id", temporary,
        "--candidate-revision", "3", "--evidence", "must-not-submit-stale-candidate",
    ], ok=False)
    check("workspace HEAD" in candidate_after_new_commit.stderr
          and "固定候选" in candidate_after_new_commit.stderr,
          "submit accepted a newer workspace commit than the frozen candidate")
    run([sys.executable, str(temporary_tool), "candidate", "--task-id", temporary, "--commit", "HEAD"])
    current_candidate_revision = json.loads(
        task_path.read_text(encoding="utf-8"),
    )["temporary_executor"]["candidate"]["revision"]
    run([
        sys.executable, str(temporary_tool), "accept", "--task-id", temporary,
        "--state", "confirmed", "--evidence", "user-approved-third-candidate",
    ])
    run([
        sys.executable, str(temporary_tool), "review", "--task-id", temporary,
        "--candidate-revision", str(current_candidate_revision),
        "--decision", "pass", "--evidence", "third-candidate-review-pass",
    ])
    rule.write_text(rule.read_text(encoding="utf-8") + "\n未登记篡改\n", encoding="utf-8")
    tampered_rule_submit = run([
        sys.executable, str(temporary_tool), "submit", "--task-id", temporary,
        "--candidate-revision", str(current_candidate_revision), "--evidence", "must-not-submit-tampered-rule",
    ], ok=False)
    check("旧确认失效" in tampered_rule_submit.stderr, "submit accepted a rule changed after confirmation")
    reconciled_rule = run([
        sys.executable, str(temporary_tool), "reconcile-rule", "--task-id", temporary,
        "--evidence", "restored-rule-from-task-truth",
    ])
    check(reconciled_rule.stdout.startswith("TEMP_RULE_RECONCILE_OK |"), "rule mismatch could not reconcile")
    reconciled_rule_digest = json.loads(task_path.read_text(encoding="utf-8"))["temporary_executor"]["rule"]["digest"]
    awaiting_rule_truth = task_path.read_bytes()
    replaced_awaiting_thread = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "active", "--thread-id", "temporary-thread-after-rule",
        "--rule-digest", reconciled_rule_digest, "--evidence", "must-not-replace-thread-after-rule-reconcile",
    ], ok=False)
    check("原始 ID" in replaced_awaiting_thread.stderr
          and task_path.read_bytes() == awaiting_rule_truth,
          "awaiting-rule reactivation replaced the original temporary thread ID")
    run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "active", "--thread-id", "temporary-thread-1",
        "--rule-digest", reconciled_rule_digest, "--evidence", "reconciled-rule-confirmed",
    ])
    submitted = run([
        sys.executable, str(temporary_tool), "submit", "--task-id", temporary,
        "--candidate-revision", str(current_candidate_revision), "--evidence", "delivery-submitted",
    ])
    check(submitted.stdout.startswith("TEMP_SUBMIT_OK |"), "delivery was not submitted")
    submitted_truth = task_path.read_bytes()
    post_submit_review = run([
        sys.executable, str(temporary_tool), "review", "--task-id", temporary,
        "--candidate-revision", str(current_candidate_revision),
        "--decision", "fail", "--evidence", "must-not-rewrite-submitted-review",
    ], ok=False)
    check("未 submit" in post_submit_review.stderr
          and task_path.read_bytes() == submitted_truth,
          "review rewrote candidate evidence after delivery submit")
    post_submit_candidate = run([
        sys.executable, str(temporary_tool), "candidate", "--task-id", temporary, "--commit", "HEAD",
    ], ok=False)
    check("未 submit" in post_submit_candidate.stderr
          and task_path.read_bytes() == submitted_truth,
          "candidate command replaced frozen truth after delivery submit")
    post_submit_provision_reconcile = run([
        sys.executable, str(temporary_tool), "reconcile-provision", "--task-id", temporary,
    ], ok=False)
    check("初始创建事务" in post_submit_provision_reconcile.stderr
          and task_path.read_bytes() == submitted_truth,
          "reconcile-provision reopened ordinary TASK state after delivery submit")
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
    rework_candidate_revision = json.loads(
        task_path.read_text(encoding="utf-8"),
    )["temporary_executor"]["candidate"]["revision"]
    run([
        sys.executable, str(temporary_tool), "accept", "--task-id", temporary,
        "--state", "confirmed", "--evidence", "user-approved-rework",
    ])
    run([
        sys.executable, str(temporary_tool), "review", "--task-id", temporary,
        "--candidate-revision", str(rework_candidate_revision),
        "--decision", "pass", "--evidence", "rework-review-pass",
    ])
    run([
        sys.executable, str(temporary_tool), "submit", "--task-id", temporary,
        "--candidate-revision", str(rework_candidate_revision), "--evidence", "rework-delivery-submitted",
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
    tested_base_tree = run(["git", "rev-parse", f"{tested_base}^{{tree}}"], cwd=project).stdout.strip()
    delivery_tree = run(["git", "rev-parse", f"{delivery}^{{tree}}"], cwd=project).stdout.strip()
    compile_script(workspace / "app" / "b.py")
    test_task = task_id_from(run([
        sys.executable, str(task_tool), "enqueue", "--department", "测试部", "--from-department", "统筹部",
        "--title", "验证临时交付合并候选", "--node", "正式测试", "--details", "运行真实候选测试并绑定 commit/tree",
        "--acceptance-exit", "正式报告绑定已测试 tree", "--failure-path", "测试证据与候选不一致",
        "--authorization-state", "none", "--pointer", f"docs/collaboration/tasks/{temporary}.json",
    ]))
    test_task_path = collab / "tasks" / f"{test_task}.json"
    test_task_revision = json.loads(test_task_path.read_text(encoding="utf-8"))["revision"]
    run([
        sys.executable, str(task_tool), "declare-impact", "--task-id", test_task,
        "--expected-revision", str(test_task_revision),
        "--write-path", "tests/temporary-integration-test.py",
        "--base-revision", "HEAD",
    ])
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
tested_commit: {tested_base}
tested_tree: {tested_base_tree}
result: fail
---

# 伪造正文

下面三行只是正文子串，不能覆盖 frontmatter 中的失败真相：

tested_commit: {delivery}
tested_tree: {delivery_tree}
result: pass
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
    substring_false_positive = run([
        sys.executable, str(temporary_tool), "record-integration-test", "--task-id", temporary,
        "--tested-base", tested_base, "--commit", delivery,
        "--test-definition", "compile and targeted regression", "--environment", "temporary verifier",
        "--evidence", report_relative, "--result", "pass",
        "--test-task-id", test_task, "--report", report_relative,
    ], ok=False)
    check("frontmatter" in substring_false_positive.stderr.casefold()
          or "tested_commit" in substring_false_positive.stderr
          or "tested_tree" in substring_false_positive.stderr
          or "result" in substring_false_positive.stderr,
          "formal test evidence accepted matching substrings outside authoritative frontmatter")
    report.write_text(f"""---
type: audit_report
department: 测试部
target: {temporary}
status: final
date: {dt.date.today().isoformat()}
related_task: {test_task}
decision: pass
tested_commit: {delivery}
tested_commit: {delivery}
tested_tree: {delivery_tree}
result: pass
---

# 重复字段探针
""", encoding="utf-8")
    duplicate_report_field = run([
        sys.executable, str(temporary_tool), "record-integration-test", "--task-id", temporary,
        "--tested-base", tested_base, "--commit", delivery,
        "--test-definition", "compile and targeted regression", "--environment", "temporary verifier",
        "--evidence", report_relative, "--result", "pass",
        "--test-task-id", test_task, "--report", report_relative,
    ], ok=False)
    check("重复字段" in duplicate_report_field.stderr and "tested_commit" in duplicate_report_field.stderr,
          "formal test report accepted duplicate authoritative YAML fields")
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
    unresolved_promotion_truth = task_path.read_bytes()
    unresolved_retest = run([
        sys.executable, str(temporary_tool), "record-integration-test", "--task-id", temporary,
        "--tested-base", tested_base, "--commit", delivery,
        "--test-definition", "must-reconcile-first", "--environment", "temporary verifier",
        "--evidence", report_relative, "--result", "pass",
        "--test-task-id", test_task, "--report", report_relative,
    ], ok=False)
    unresolved_promote = run([
        sys.executable, str(temporary_tool), "promote", "--task-id", temporary, "--main-branch", "main",
    ], ok=False)
    unresolved_rework = run([
        sys.executable, str(temporary_tool), "rework", "--task-id", temporary,
        "--evidence", "must-not-overwrite-unresolved-promotion",
    ], ok=False)
    unresolved_abandon = run([
        sys.executable, str(temporary_tool), "abandon", "--task-id", temporary,
        "--evidence", "must-not-overwrite-unresolved-promotion",
    ], ok=False)
    unresolved_absorb = run([
        sys.executable, str(temporary_tool), "absorb", "--task-id", temporary,
        "--scope", "preflight", "--state", "completed",
        "--evidence", "must-not-advance-unresolved-promotion",
    ], ok=False)
    check(all("reconcile" in result.stderr for result in (
              unresolved_retest, unresolved_promote, unresolved_rework, unresolved_abandon, unresolved_absorb,
          ))
          and task_path.read_bytes() == unresolved_promotion_truth,
          "an unresolved promotion transaction was overwritten by a lifecycle command")
    unresolved_failure = json.loads(unresolved_promotion_truth.decode("utf-8"))
    unresolved_failure_operation = unresolved_failure["temporary_executor"]["promotion_operation"]
    unresolved_failure_operation["state"] = "failed"
    unresolved_failure_operation["history"].append({
        "state": "failed", "at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "reason": "simulated-direct-promote-failure-without-reconcile",
    })
    task_path.write_text(json.dumps(unresolved_failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    unresolved_failure_truth = task_path.read_bytes()
    failed_rework = run([
        sys.executable, str(temporary_tool), "rework", "--task-id", temporary,
        "--evidence", "must-not-erase-unreconciled-failure",
    ], ok=False)
    failed_abandon = run([
        sys.executable, str(temporary_tool), "abandon", "--task-id", temporary,
        "--evidence", "must-not-erase-unreconciled-failure",
    ], ok=False)
    failed_absorb = run([
        sys.executable, str(temporary_tool), "absorb", "--task-id", temporary,
        "--scope", "preflight", "--state", "completed",
        "--evidence", "must-not-advance-unreconciled-failure",
    ], ok=False)
    check(all("reconcile" in result.stderr for result in (failed_rework, failed_abandon, failed_absorb))
          and task_path.read_bytes() == unresolved_failure_truth,
          "a failed but unreconciled promotion transaction was erased or advanced")
    task_path.write_bytes(unresolved_promotion_truth)
    reconciled_promotion = run([
        sys.executable, str(temporary_tool), "reconcile-promotion", "--task-id", temporary,
    ])
    check("integrated" in reconciled_promotion.stdout,
          "promotion crash after Git update could not reconcile TASK truth")
    integrated_before_provision_reconcile = task_path.read_bytes()
    integrated_provision_reconcile = run([
        sys.executable, str(temporary_tool), "reconcile-provision", "--task-id", temporary,
    ], ok=False)
    check("初始创建事务" in integrated_provision_reconcile.stderr
          and task_path.read_bytes() == integrated_before_provision_reconcile,
          "reconcile-provision reopened an integrated TASK")
    integrated_before_retest = task_path.read_bytes()
    integrated_retest = run([
        sys.executable, str(temporary_tool), "record-integration-test", "--task-id", temporary,
        "--tested-base", tested_base, "--commit", delivery,
        "--test-definition", "must-not-demote-integrated", "--environment", "temporary verifier",
        "--evidence", report_relative, "--result", "pass",
        "--test-task-id", test_task, "--report", report_relative,
    ], ok=False)
    check("接管 delivery" in integrated_retest.stderr
          and task_path.read_bytes() == integrated_before_retest,
          "record-integration-test demoted an integrated delivery back to ready")
    idempotent_promotion_reconcile = run([
        sys.executable, str(temporary_tool), "reconcile-promotion", "--task-id", temporary,
    ])
    check("idempotent" in idempotent_promotion_reconcile.stdout
          and task_path.read_bytes() == integrated_before_retest,
          "repeated promotion reconcile was not read-only after verified integration")
    integrated_before_abandon = task_path.read_bytes()
    integrated_abandon = run([
        sys.executable, str(temporary_tool), "abandon", "--task-id", temporary,
        "--evidence", "must-not-rewrite-integrated-truth",
    ], ok=False)
    check("不能" in integrated_abandon.stderr
          and ("integrated" in integrated_abandon.stderr.casefold() or "已集成" in integrated_abandon.stderr)
          and task_path.read_bytes() == integrated_before_abandon,
          "integrated temporary delivery was rewritten as abandoned")

    for scope, state, evidence in (
        ("parent-department", "completed", "development-knowledge-absorbed"),
        ("project-global", "not_applicable", "no-global-contract-change"),
        ("final", "completed", "absorption-gate-closed"),
    ):
        run([
            sys.executable, str(temporary_tool), "absorb", "--task-id", temporary,
            "--scope", scope, "--state", state, "--evidence", evidence,
        ])
    pre_cleanup_truth = task_path.read_bytes()
    pre_cleanup_payload = json.loads(pre_cleanup_truth.decode("utf-8"))
    pre_cleanup_temp = pre_cleanup_payload["temporary_executor"]
    workspace_head = run(["git", "rev-parse", "HEAD"], cwd=workspace).stdout.strip()
    for unresolved_cleanup_state in ("started", "failed"):
        simulated_cleanup = json.loads(json.dumps(pre_cleanup_payload, ensure_ascii=False))
        cleanup_history = [
            {"state": "planned", "at": dt.datetime.now(dt.timezone.utc).isoformat()},
            {"state": "started", "at": dt.datetime.now(dt.timezone.utc).isoformat()},
        ]
        if unresolved_cleanup_state == "failed":
            cleanup_history.append({
                "state": "failed", "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "reason": "simulated-direct-cleanup-failure-without-reconcile",
            })
        simulated_cleanup["temporary_executor"]["cleanup_operation"] = {
            "id": f"CLEANUP-SIMULATED-{unresolved_cleanup_state.upper()}",
            "state": unresolved_cleanup_state,
            "workspace": pre_cleanup_temp["workspace"]["path"],
            "branch": pre_cleanup_temp["workspace"]["branch"],
            "workspace_head": workspace_head,
            "evidence": "simulated-cleanup-crash",
            "history": cleanup_history,
        }
        task_path.write_text(
            json.dumps(simulated_cleanup, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        unresolved_cleanup_truth = task_path.read_bytes()
        blocked_absorb = run([
            sys.executable, str(temporary_tool), "absorb", "--task-id", temporary,
            "--scope", "final", "--state", "not_applicable",
            "--evidence", "must-not-rewrite-absorption-after-cleanup-started",
        ], ok=False)
        check("reconcile cleanup" in blocked_absorb.stderr
              and task_path.read_bytes() == unresolved_cleanup_truth,
              f"{unresolved_cleanup_state} cleanup transaction allowed absorption evidence to change")
    task_path.write_bytes(pre_cleanup_truth)
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
    pending_before = task_path.read_bytes()
    locks_root = collab / ".locks"
    locks_before = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in locks_root.iterdir()
    }
    pending_first = run([sys.executable, str(temporary_tool), "pending-archives"])
    pending_second = run([sys.executable, str(temporary_tool), "pending-archives"])
    locks_after = {
        path.name: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in locks_root.iterdir()
    }
    check(pending_first.stdout == pending_second.stdout
          and f"ARCHIVE_THREAD_REQUIRED:temporary-thread-1 | {temporary}" in pending_first.stdout
          and task_path.read_bytes() == pending_before and locks_after == locks_before,
          "pending archive query was not repeatable and read-only")
    cleanup_reconcile_truth = task_path.read_bytes()
    cleanup_reconcile_idempotent = run([
        sys.executable, str(temporary_tool), "reconcile-cleanup", "--task-id", temporary,
    ])
    check("idempotent" in cleanup_reconcile_idempotent.stdout
          and task_path.read_bytes() == cleanup_reconcile_truth,
          "verified cleanup reconcile rewrote archived terminal truth")
    archived_candidate_revision = cleaned_payload["temporary_executor"]["candidate"]["revision"]
    archived_truth = task_path.read_bytes()
    archived_review = run([
        sys.executable, str(temporary_tool), "review", "--task-id", temporary,
        "--candidate-revision", str(archived_candidate_revision),
        "--decision", "fail", "--evidence", "must-not-rewrite-archived-review",
    ], ok=False)
    check("未 submit" in archived_review.stderr and task_path.read_bytes() == archived_truth,
          "review rewrote evidence after cleanup reached archived")
    archived_reconcile = run([
        sys.executable, str(temporary_tool), "reconcile-promotion", "--task-id", temporary,
    ], ok=False)
    pending_after_rejected_reconcile = run([sys.executable, str(temporary_tool), "pending-archives"])
    check("只有 ready" in archived_reconcile.stderr
          and task_path.read_bytes() == archived_truth
          and f"ARCHIVE_THREAD_REQUIRED:temporary-thread-1 | {temporary}" in pending_after_rejected_reconcile.stdout,
          "promotion reconcile reopened archived resources or hid the pending archive action")
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
    wrong_case_archive_receipt = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "archived",
        "--evidence", "host=set_thread_archived thread_id=TEMPORARY-THREAD-1 archived=true",
    ], ok=False)
    check("绑定当前 thread_id" in wrong_case_archive_receipt.stderr,
          "archive path treated a differently-cased thread ID as the same exact identity")
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
    automatic_archive_truth = task_path.read_bytes()
    automatic_archive_evidence = automatically_archived["temporary_executor"]["temporary_session"]["evidence"]
    no_pending_after_archive = run([sys.executable, str(temporary_tool), "pending-archives"])
    check(no_pending_after_archive.stdout.strip() == "NO_PENDING_THREAD_ARCHIVES",
          "pending archive query retained an already archived session")
    automatic_archive_retry = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "archived",
        "--evidence", "host=set_thread_archived thread_id=temporary-thread-1 archived=true retry=true",
    ])
    check("idempotent" in automatic_archive_retry.stdout
          and task_path.read_bytes() == automatic_archive_truth
          and json.loads(task_path.read_text(encoding="utf-8"))["temporary_executor"]["temporary_session"]["evidence"]
          == automatic_archive_evidence,
          "idempotent automatic archive receipt retry replaced terminal evidence")
    archived_impact_truth = task_path.read_bytes()
    archived_revision = json.loads(task_path.read_text(encoding="utf-8"))["revision"]
    archived_impact = run([
        sys.executable, str(temporary_tool), "declare-impact", "--task-id", temporary,
        "--expected-revision", str(archived_revision), "--base-revision", "HEAD",
        "--write-path", "app/archived-impact.py",
    ], ok=False)
    check("已绑定临时执行者" in archived_impact.stderr
          and task_path.read_bytes() == archived_impact_truth,
          "declare-impact split top-level and temporary impact truth after archive")
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
    unresolved_cleanup_truth = task_path.read_bytes()
    unresolved_cleanup_retry = run([
        sys.executable, str(temporary_tool), "cleanup", "--task-id", temporary,
        "--evidence", "must-reconcile-cleanup-first",
    ], ok=False)
    check("reconcile cleanup" in unresolved_cleanup_retry.stderr
          and task_path.read_bytes() == unresolved_cleanup_truth,
          "cleanup retry overwrote an unresolved cleanup transaction")
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
    missing_thread_temp["workspace"]["state"] = "ready"
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
    abandoned_standby = abandoned_path.read_bytes()
    cancelled_from_standby = run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", abandoned,
        "--state", "cancelled", "--evidence", "must-not-dead-end-abandoned-cleanup",
    ], ok=False)
    check("状态转换非法" in cancelled_from_standby.stderr
          and abandoned_path.read_bytes() == abandoned_standby,
          "standby session entered cancelled before abandoned resources could be reconciled")
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
        project_guide.read_text(encoding="utf-8").replace(
            f"受管协议版本:{PROTOCOL_VERSION}", "受管协议版本:1.4.1",
        ),
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
    corrupt_legacy["temporary_executor"]["temporary_session"].update(
        state="archived", evidence="legacy-archive-without-a-verifiable-receipt",
    )
    task_path.write_text(json.dumps(corrupt_legacy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    repaired_upgrade = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    repaired_payload = json.loads(task_path.read_text(encoding="utf-8"))
    check(repaired_upgrade.stdout.startswith("UPGRADE_OK |")
          and "ARCHIVE_THREAD_REQUIRED:temporary-thread-1" in repaired_upgrade.stdout,
          "valid legacy temporary TASK did not surface the real thread archive repair")
    check(repaired_payload["temporary_executor"]["temporary_session"]["state"] == "standby"
          and "旧 archived 记录缺少宿主收据" in repaired_payload["temporary_executor"]["temporary_session"]["evidence"],
          "legacy upgrade retained an unverified archived session state")

    receipt_141 = "host=set_thread_archived thread_id=temporary-thread-1 archived=true"
    run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "archived", "--evidence", receipt_141,
    ])
    valid_141_protocol = json.loads(legacy_protocol.read_text(encoding="utf-8"))
    valid_141_protocol["protocol_version"] = "1.4.1"
    legacy_protocol.write_text(
        json.dumps(valid_141_protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    project_guide.write_text(
        project_guide.read_text(encoding="utf-8").replace(
            f"受管协议版本:{PROTOCOL_VERSION}", "受管协议版本:1.4.1",
        ),
        encoding="utf-8",
    )
    preserved_141 = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    preserved_141_payload = json.loads(task_path.read_text(encoding="utf-8"))
    check(preserved_141.stdout.startswith("UPGRADE_OK |")
          and "ARCHIVE_THREAD_REQUIRED" not in preserved_141.stdout
          and preserved_141_payload["temporary_executor"]["temporary_session"]["state"] == "archived"
          and preserved_141_payload["temporary_executor"]["temporary_session"]["evidence"] == receipt_141,
          "an exact legacy archive receipt was discarded only because its protocol version was old")

    for invalid_evidence, label in (
        ("host= thread_id=temporary-thread-1 archived=true", "empty-source"),
        ("host=set_thread_archived thread_id=temporary-thread-1-extra archived=true", "wrong-thread"),
        ("host=set_thread_archived thread_id=TEMPORARY-THREAD-1 archived=true", "wrong-case-thread"),
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
            project_guide.read_text(encoding="utf-8").replace(
                f"受管协议版本:{PROTOCOL_VERSION}", "受管协议版本:1.4.4",
            ),
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
        project_guide.read_text(encoding="utf-8").replace(
            f"受管协议版本:{PROTOCOL_VERSION}", "受管协议版本:1.4.3",
        ),
        encoding="utf-8",
    )
    preserved_143_upgrade = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    preserved_143_payload = json.loads(task_path.read_text(encoding="utf-8"))
    check(preserved_143_upgrade.stdout.startswith("UPGRADE_OK |")
          and "ARCHIVE_THREAD_REQUIRED" not in preserved_143_upgrade.stdout
          and json.loads(legacy_protocol.read_text(encoding="utf-8"))["protocol_version"] == PROTOCOL_VERSION
          and preserved_143_payload["temporary_executor"]["temporary_session"]["state"] == "archived"
          and preserved_143_payload["temporary_executor"]["temporary_session"]["evidence"] == receipt_143,
          f"real 1.4.3 host receipt was needlessly invalidated during {PROTOCOL_VERSION} upgrade")

    receipt_144_automatic = "archive_mode=automatic host=set_thread_archived thread_id=temporary-thread-1 archived=true"
    fixture_144_automatic = json.loads(task_path.read_text(encoding="utf-8"))
    fixture_144_automatic["temporary_executor"]["temporary_session"].update(
        state="archived", evidence=receipt_144_automatic,
    )
    task_path.write_text(
        json.dumps(fixture_144_automatic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    automatic_144_protocol = json.loads(legacy_protocol.read_text(encoding="utf-8"))
    automatic_144_protocol["protocol_version"] = "1.4.4"
    legacy_protocol.write_text(json.dumps(automatic_144_protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    project_guide.write_text(
        project_guide.read_text(encoding="utf-8").replace(
            f"受管协议版本:{PROTOCOL_VERSION}", "受管协议版本:1.4.4",
        ),
        encoding="utf-8",
    )
    preserved_144_automatic = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    preserved_144_automatic_payload = json.loads(task_path.read_text(encoding="utf-8"))
    check(preserved_144_automatic.stdout.startswith("UPGRADE_OK |")
          and "ARCHIVE_THREAD_REQUIRED" not in preserved_144_automatic.stdout
          and json.loads(legacy_protocol.read_text(encoding="utf-8"))["protocol_version"] == PROTOCOL_VERSION
          and preserved_144_automatic_payload["temporary_executor"]["temporary_session"]["state"] == "archived"
          and preserved_144_automatic_payload["temporary_executor"]["temporary_session"]["evidence"] == receipt_144_automatic,
          f"real 1.4.4 automatic receipt was needlessly invalidated during {PROTOCOL_VERSION} upgrade")

    receipt_144_manual = (
        "archive_mode=manual thread_id=temporary-thread-1 archived=true "
        "user_confirmation=我已将该会话归档 evidence=current-user-message"
    )
    fixture_144_manual = json.loads(task_path.read_text(encoding="utf-8"))
    fixture_144_manual["temporary_executor"]["temporary_session"].update(
        state="archived", evidence=receipt_144_manual,
    )
    task_path.write_text(
        json.dumps(fixture_144_manual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    manual_144_protocol = json.loads(legacy_protocol.read_text(encoding="utf-8"))
    manual_144_protocol["protocol_version"] = "1.4.4"
    legacy_protocol.write_text(json.dumps(manual_144_protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    project_guide.write_text(
        project_guide.read_text(encoding="utf-8").replace(
            f"受管协议版本:{PROTOCOL_VERSION}", "受管协议版本:1.4.4",
        ),
        encoding="utf-8",
    )
    preserved_144_manual = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    preserved_144_manual_payload = json.loads(task_path.read_text(encoding="utf-8"))
    check(preserved_144_manual.stdout.startswith("UPGRADE_OK |")
          and "ARCHIVE_THREAD_REQUIRED" not in preserved_144_manual.stdout
          and json.loads(legacy_protocol.read_text(encoding="utf-8"))["protocol_version"] == PROTOCOL_VERSION
          and preserved_144_manual_payload["temporary_executor"]["temporary_session"]["state"] == "archived"
          and preserved_144_manual_payload["temporary_executor"]["temporary_session"]["evidence"] == receipt_144_manual,
          f"real 1.4.4 manual receipt was needlessly invalidated during {PROTOCOL_VERSION} upgrade")

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
        project_guide.read_text(encoding="utf-8").replace(
            f"受管协议版本:{PROTOCOL_VERSION}", "受管协议版本:1.4.4",
        ),
        encoding="utf-8",
    )
    pending_archive_upgrade = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    pending_archive_payload = json.loads(task_path.read_text(encoding="utf-8"))
    check("ARCHIVE_THREAD_REQUIRED:temporary-thread-1" in pending_archive_upgrade.stdout
          and "reason:existing-standby-archive" in pending_archive_upgrade.stdout
          and pending_archive_payload["temporary_executor"]["temporary_session"]["state"] == "standby"
          and pending_archive_payload["temporary_executor"]["temporary_session"]["evidence"] == "waiting-for-user-manual-archive",
          "1.4.4 cleaned standby session did not resurface its pending archive action")
    same_version_pending_before = task_path.read_bytes()
    same_version_pending = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    check("ARCHIVE_THREAD_REQUIRED:temporary-thread-1" in same_version_pending.stdout
          and "reason:existing-standby-archive" in same_version_pending.stdout
          and task_path.read_bytes() == same_version_pending_before,
          "same-version upgrade stopped replaying a pending manual archive reminder")

    valid_pending_truth = task_path.read_bytes()
    missing_thread_truth = json.loads(valid_pending_truth)
    missing_thread_truth["temporary_executor"]["temporary_session"]["thread_id"] = ""
    task_path.write_text(
        json.dumps(missing_thread_truth, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    missing_thread_pending = run([
        sys.executable, str(temporary_tool), "pending-archives",
    ], ok=False)
    check("thread_id" in missing_thread_pending.stderr and "NO_PENDING_THREAD_ARCHIVES" not in missing_thread_pending.stdout,
          "cleaned standby session with a missing thread ID silently disappeared from pending archives")
    missing_thread_protocol = json.loads(legacy_protocol.read_text(encoding="utf-8"))
    missing_thread_protocol["protocol_version"] = PREVIOUS_PROTOCOL_VERSION
    legacy_protocol.write_text(
        json.dumps(missing_thread_protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    project_guide.write_text(
        project_guide.read_text(encoding="utf-8").replace(
            f"受管协议版本:{PROTOCOL_VERSION}", f"受管协议版本:{PREVIOUS_PROTOCOL_VERSION}",
        ),
        encoding="utf-8",
    )
    invalid_missing_thread_before = task_path.read_bytes()
    missing_thread_upgrade = run([
        sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration",
    ], ok=False)
    check("thread_id" in missing_thread_upgrade.stderr
          and task_path.read_bytes() == invalid_missing_thread_before,
          "upgrade silently accepted or rewrote a cleaned standby session with no thread ID")
    current_protocol_again = json.loads(legacy_protocol.read_text(encoding="utf-8"))
    current_protocol_again["protocol_version"] = PROTOCOL_VERSION
    legacy_protocol.write_text(
        json.dumps(current_protocol_again, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    project_guide.write_text(
        project_guide.read_text(encoding="utf-8").replace(
            f"受管协议版本:{PREVIOUS_PROTOCOL_VERSION}", f"受管协议版本:{PROTOCOL_VERSION}",
        ),
        encoding="utf-8",
    )
    missing_thread_add_before = {
        relative: (collab / relative).read_bytes()
        for relative in ("部门表.md", "会话启动状态.json", "协议版本.json")
    }
    missing_thread_add = run([
        sys.executable, str(SCAFFOLD), str(project), "--add-roles", "research",
    ], ok=False)
    check("不完整" in missing_thread_add.stderr
          and all((collab / relative).read_bytes() == content
                  for relative, content in missing_thread_add_before.items()),
          "add-role continued from a current-version TASK with a silently missing archive thread ID")


def verify_resume_admission_guards(root: Path) -> None:
    project = make_project(root, "resume-admission-guards")
    (project / ".gitignore").write_text("/.agent-team/\n", encoding="utf-8")
    (project / "app").mkdir()
    (project / "app" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (project / "app" / "b.py").write_text("B = 1\n", encoding="utf-8")
    run(["git", "init", "-b", "main"], cwd=project)
    run(["git", "config", "user.name", "Agent Team Verify"], cwd=project)
    run(["git", "config", "user.email", "verify@example.invalid"], cwd=project)
    run(["git", "add", "."], cwd=project)
    run(["git", "commit", "-m", "foundation"], cwd=project)
    scaffold(project, "lead,dev,test")
    run(["git", "add", "."], cwd=project)
    run(["git", "commit", "-m", "agent team collaboration"], cwd=project)

    collab = project / "docs" / "collaboration"
    task_tool = collab / "scripts" / "agent_team_task.py"
    temporary_tool = collab / "scripts" / "agent_team_temporary.py"
    formal = task_id_from(run([
        sys.executable, str(task_tool), "enqueue", "--department", "测试部",
        "--from-department", "统筹部", "--title", "正式路径任务",
        "--node", "测试节点", "--details", "持有 app/a.py 的正式影响声明",
        "--acceptance-exit", "状态转换可复验", "--failure-path", "路径冲突时停止",
        "--authorization-state", "none",
    ]))
    formal_path = collab / "tasks" / f"{formal}.json"
    formal_revision = json.loads(formal_path.read_text(encoding="utf-8"))["revision"]
    run([
        sys.executable, str(task_tool), "declare-impact", "--task-id", formal,
        "--expected-revision", str(formal_revision), "--base-revision", "HEAD",
        "--write-path", "app/a.py",
    ])
    run([
        sys.executable, str(task_tool), "claim", "--task-id", formal,
        "--claimed-by", "test-session",
    ])

    temporary = task_id_from(run([
        sys.executable, str(task_tool), "enqueue", "--department", "开发部",
        "--from-department", "统筹部", "--title", "临时路径任务",
        "--node", "开发节点", "--details", "初始只修改 app/b.py",
        "--acceptance-exit", "状态转换可复验", "--failure-path", "路径冲突时停止",
        "--authorization-state", "user_confirmed",
        "--authorization-evidence", "user-requested-temporary-executor",
    ]))
    run([
        sys.executable, str(temporary_tool), "provision", "--task-id", temporary,
        "--parent-department", "开发部", "--executor-id", "resume-guard-temp",
        "--display-name", "临时开发外包", "--current-brief", "只修改 app/b.py",
        "--client-key", "resume-admission-client", "--scan-boundary-evidence", "已检查扫描边界",
        "--base-revision", "HEAD", "--write-path", "app/b.py",
    ])
    temporary_path = collab / "tasks" / f"{temporary}.json"
    provisioned_payload = json.loads(temporary_path.read_text(encoding="utf-8"))
    for unresolved_state in ("started", "failed"):
        half_provision = json.loads(json.dumps(provisioned_payload, ensure_ascii=False))
        half_provision["execution_state"] = "blocked"
        half_provision["block_reason"] = "simulated unresolved provision transaction"
        provision_operation = half_provision["temporary_executor"]["operation"]
        provision_operation["state"] = unresolved_state
        provision_operation["history"].append({
            "state": unresolved_state,
            "at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "reason": "simulated-provision-interruption",
        })
        temporary_path.write_text(
            json.dumps(half_provision, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        half_provision_truth = temporary_path.read_bytes()
        unresolved_commands = (
            [sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
             "--state", "failed", "--thread-id", "must-not-register-before-provision-reconcile",
             "--evidence", "must-reconcile-provision-first"],
            [sys.executable, str(temporary_tool), "resume", "--task-id", temporary,
             "--evidence", "must-reconcile-provision-first"],
            [sys.executable, str(temporary_tool), "rework", "--task-id", temporary,
             "--evidence", "must-reconcile-provision-first"],
            [sys.executable, str(temporary_tool), "abandon", "--task-id", temporary,
             "--evidence", "must-reconcile-provision-first"],
        )
        rejected_commands = [run(command, ok=False) for command in unresolved_commands]
        check(all("workspace 创建事务尚未 verified" in result.stderr for result in rejected_commands)
              and temporary_path.read_bytes() == half_provision_truth,
              f"{unresolved_state} provision transaction accepted a session ID or was overwritten")
        if unresolved_state == "failed":
            orphaned_identity = json.loads(json.dumps(half_provision, ensure_ascii=False))
            orphaned_identity["temporary_executor"]["temporary_session"].update({
                "state": "failed",
                "thread_id": "legacy-real-thread-must-not-disappear",
                "evidence": "legacy-or-corrupt-pre-reconcile-identity",
            })
            temporary_path.write_text(
                json.dumps(orphaned_identity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
            orphaned_identity_truth = temporary_path.read_bytes()
            rejected_reset = run([
                sys.executable, str(temporary_tool), "reset-failed-provision", "--task-id", temporary,
                "--evidence", "must-not-delete-recorded-real-session",
            ], ok=False)
            check("真实 thread_id" in rejected_reset.stderr
                  and temporary_path.read_bytes() == orphaned_identity_truth,
                  "reset-failed-provision deleted a recorded real session identity")
    temporary_path.write_text(
        json.dumps(provisioned_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    run([
        sys.executable, str(temporary_tool), "session-mark", "--task-id", temporary,
        "--state", "active", "--thread-id", "resume-guard-thread",
        "--rule-digest", provisioned_payload["temporary_executor"]["rule"]["digest"],
        "--evidence", "temporary-rule-confirmed",
    ])
    amended = run([
        sys.executable, str(temporary_tool), "amend", "--task-id", temporary,
        "--expected-brief-revision", "1", "--current-brief", "改为修改 app/a.py",
        "--write-path", "app/a.py",
    ], ok=False)
    blocked_payload = json.loads(temporary_path.read_text(encoding="utf-8"))
    check(amended.returncode == 4
          and blocked_payload["execution_state"] == "blocked"
          and blocked_payload["temporary_executor"]["impact"]["admission"] == "unsafe",
          "conflicting amend did not persist a blocked unsafe temporary task")
    blocked_attempt = blocked_payload["temporary_executor"]["attempt"]

    denied_resume = run([
        sys.executable, str(temporary_tool), "resume", "--task-id", temporary,
        "--evidence", "must-recheck-current-impact",
    ], ok=False)
    after_resume = json.loads(temporary_path.read_text(encoding="utf-8"))
    check(denied_resume.returncode == 4 and "TEMP_RESUME_BLOCKED" in denied_resume.stdout
          and after_resume["execution_state"] == "blocked"
          and after_resume["temporary_executor"]["impact"]["admission"] == "unsafe",
          "temporary resume bypassed its current conflicting impact declaration")

    denied_rework = run([
        sys.executable, str(temporary_tool), "rework", "--task-id", temporary,
        "--evidence", "must-recheck-current-impact",
    ], ok=False)
    after_rework = json.loads(temporary_path.read_text(encoding="utf-8"))
    check(denied_rework.returncode == 4 and "TEMP_REWORK_BLOCKED" in denied_rework.stdout
          and after_rework["execution_state"] == "blocked"
          and after_rework["temporary_executor"]["attempt"] == blocked_attempt,
          "temporary rework bypassed impact admission or advanced the attempt while blocked")

    run([
        sys.executable, str(task_tool), "block", "--task-id", formal,
        "--reason", "验证正式 resume 反向冲突",
    ])
    formal_blocked = formal_path.read_bytes()
    formal_resume = run([
        sys.executable, str(task_tool), "resume", "--task-id", formal,
    ], ok=False)
    check(("冲突" in formal_resume.stderr or "重叠" in formal_resume.stderr)
          and formal_path.read_bytes() == formal_blocked,
          "formal resume bypassed a conflicting active temporary scope")


def verify_upgrade_and_guards(root: Path) -> None:
    session_project = make_project(root, "upgrade-session-truth")
    scaffold(session_project, "lead,do,review,dev")
    session_collab = session_project / "docs" / "collaboration"
    session_tool = session_collab / "scripts" / "agent_team_session.py"
    run([
        sys.executable, str(session_tool), "mark", "--department", "统筹部", "--step", "created",
        "--thread-id", "lead-created-thread", "--evidence", "lead-created-receipt",
    ])
    for step, evidence in (("created", "do-created-receipt"), ("onboarded", "do-onboarded-receipt")):
        run([
            sys.executable, str(session_tool), "mark", "--department", "执行部", "--step", step,
            "--thread-id", "do-onboarded-thread", "--evidence", evidence,
        ])
    for step, evidence in (
        ("created", "review-created-receipt"),
        ("onboarded", "review-onboarded-receipt"),
        ("registered", "review-registered-receipt"),
    ):
        run([
            sys.executable, str(session_tool), "mark", "--department", "检验部", "--step", step,
            "--thread-id", "review-registered-thread", "--evidence", evidence,
        ])
    for step, evidence in (
        ("created", "dev-old-created"),
        ("onboarded", "dev-old-onboarded"),
        ("registered", "dev-old-registered"),
    ):
        run([
            sys.executable, str(session_tool), "mark", "--department", "开发部", "--step", step,
            "--thread-id", "dev-old-thread", "--evidence", evidence,
        ])
    run([
        sys.executable, str(session_tool), "begin-switch", "--department", "开发部",
        "--old-thread-id", "dev-old-thread", "--reason", "preserve-switch-operation-during-upgrade",
    ])
    run([
        sys.executable, str(session_tool), "mark", "--department", "开发部", "--step", "created",
        "--thread-id", "dev-new-thread", "--evidence", "dev-new-created-receipt",
    ])
    session_state_path = session_collab / "会话启动状态.json"
    before_upgrade_state = json.loads(session_state_path.read_text(encoding="utf-8"))
    preserved_fields = (
        "step", "thread_id", "previous_thread_id", "evidence", "operation_id",
        "failed_from", "note", "notification_mode",
    )
    expected_session_truth = {
        department: {field: item.get(field) for field in preserved_fields}
        for department, item in before_upgrade_state["departments"].items()
    }
    session_protocol_path = session_collab / "协议版本.json"
    session_protocol = json.loads(session_protocol_path.read_text(encoding="utf-8"))
    session_protocol["protocol_version"] = PREVIOUS_PROTOCOL_VERSION
    session_protocol_path.write_text(
        json.dumps(session_protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    before_upgrade_state["protocol_version"] = PREVIOUS_PROTOCOL_VERSION
    session_state_path.write_text(
        json.dumps(before_upgrade_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    session_guide = session_project / "docs" / "agent-guide.md"
    session_guide.write_text(
        session_guide.read_text(encoding="utf-8").replace(
            f"受管协议版本:{PROTOCOL_VERSION}", f"受管协议版本:{PREVIOUS_PROTOCOL_VERSION}",
        ),
        encoding="utf-8",
    )
    session_upgraded = run([
        sys.executable, str(SCAFFOLD), str(session_project), "--upgrade-collaboration",
    ])
    upgraded_state = json.loads(session_state_path.read_text(encoding="utf-8"))
    actual_session_truth = {
        department: {field: item.get(field) for field in preserved_fields}
        for department, item in upgraded_state["departments"].items()
    }
    check(session_upgraded.stdout.startswith("UPGRADE_OK |")
          and upgraded_state["protocol_version"] == PROTOCOL_VERSION
          and actual_session_truth == expected_session_truth,
          "1.4.5 -> 1.4.6 upgrade rewrote session step, evidence, previous thread, or operation truth")

    fresh_roles_project = make_project(root, "fresh-add-role-parity")
    scaffold(fresh_roles_project, "lead,do,review,dev")
    incremental_roles_project = make_project(root, "incremental-add-role-parity")
    scaffold(incremental_roles_project)
    incremental_collab = incremental_roles_project / "docs" / "collaboration"
    added_role = run([
        sys.executable, str(SCAFFOLD), str(incremental_roles_project), "--add-roles", "dev",
    ])
    check(added_role.returncode == 0 and "新增并登记" in added_role.stdout,
          "add-role did not report a successful registered addition")
    fresh_collab = fresh_roles_project / "docs" / "collaboration"
    for relative in ("部门表.md", "会话启动清单.md", "路由表.md", "会话启动状态.json"):
        check(
            (incremental_collab / relative).read_bytes() == (fresh_collab / relative).read_bytes(),
            f"add-role did not converge to fresh scaffold truth: {relative}",
        )
    before_repeat = {
        relative: (incremental_collab / relative).read_bytes()
        for relative in ("部门表.md", "会话启动清单.md", "路由表.md", "会话启动状态.json")
    }
    repeated_add = run([
        sys.executable, str(SCAFFOLD), str(incremental_roles_project), "--add-roles", "dev",
    ])
    check("dev" in repeated_add.stdout and "已存在跳过" in repeated_add.stdout,
          "repeated add-role did not report an idempotent skip")
    check(all((incremental_collab / relative).read_bytes() == content
              for relative, content in before_repeat.items()),
          "repeated add-role changed already-converged derived truth")

    split_roster_project = make_project(root, "add-role-split-roster")
    scaffold(split_roster_project, "lead,do,review,dev")
    split_collab = split_roster_project / "docs" / "collaboration"
    split_registry = split_collab / "部门表.md"
    split_registry_text = split_registry.read_text(encoding="utf-8")
    split_registry.write_text(
        "\n".join(
            line for line in split_registry_text.splitlines()
            if not (line.startswith("|") and "`dev`" in line)
        ) + "\n",
        encoding="utf-8",
    )
    split_before = {
        relative: (split_collab / relative).read_bytes()
        for relative in ("部门表.md", "会话启动状态.json", "协议版本.json")
    }
    split_add = run([
        sys.executable, str(SCAFFOLD), str(split_roster_project), "--add-roles", "research",
    ], ok=False)
    check(("不完整" in split_add.stderr or "不一致" in split_add.stderr)
          and "Traceback" not in split_add.stderr
          and all((split_collab / relative).read_bytes() == content
                  for relative, content in split_before.items()),
          "add-role continued from or mutated a split registry/session department roster")

    malformed_session_project = make_project(root, "add-role-malformed-session")
    scaffold(malformed_session_project)
    malformed_collab = malformed_session_project / "docs" / "collaboration"
    malformed_state_path = malformed_collab / "会话启动状态.json"
    malformed_state = json.loads(malformed_state_path.read_text(encoding="utf-8"))
    malformed_state["departments"]["执行部"].pop("notification_mode")
    malformed_state_path.write_text(
        json.dumps(malformed_state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    malformed_before = malformed_state_path.read_bytes()
    malformed_add = run([
        sys.executable, str(SCAFFOLD), str(malformed_session_project), "--add-roles", "dev",
    ], ok=False)
    check(("不完整" in malformed_add.stderr or "格式无效" in malformed_add.stderr)
          and "Traceback" not in malformed_add.stderr
          and malformed_state_path.read_bytes() == malformed_before,
          "add-role raised a traceback or mutated a session item missing a required field")

    four_docs_project = make_project(root, "four-document-repair")
    scaffold(four_docs_project, "lead,do,review,dev")
    four_docs_collab = four_docs_project / "docs" / "collaboration"
    entry_docs = ("上岗引导.md", "岗位说明.md", "交接班文档.md", "收件箱.md")
    department_names = ("统筹部", "执行部", "检验部", "开发部")
    for department in department_names:
        for filename in entry_docs:
            (four_docs_collab / "部门" / department / filename).unlink()
    missing_entry_detected = run([
        sys.executable, str(SCAFFOLD), str(four_docs_project), "--add-roles", "do",
    ], ok=False)
    check("缺失" in missing_entry_detected.stderr or "不完整" in missing_entry_detected.stderr,
          "same-version collaboration accepted departments missing their four entry documents")
    repaired_entries = run([
        sys.executable, str(SCAFFOLD), str(four_docs_project), "--upgrade-collaboration",
    ])
    check(repaired_entries.stdout.startswith("UPGRADE_OK |"),
          "same-version upgrade did not repair missing department entry documents")
    for department in department_names:
        for filename in entry_docs:
            check((four_docs_collab / "部门" / department / filename).is_file(),
                  f"same-version upgrade did not restore {department}/{filename}")

    mistake_book = four_docs_collab / "错题集.md"
    preserved_handoff = four_docs_collab / "部门" / "统筹部" / "交接班文档.md"
    preserved_inbox = four_docs_collab / "部门" / "执行部" / "收件箱.md"
    custom_mistake = b"# custom mistake truth\n\nkeep this exact content\n"
    custom_handoff = b"# custom handoff truth\n\nkeep this exact content\n"
    custom_inbox = (
        "# custom inbox truth\n\n"
        "<!-- agent-team task index; use scripts/agent_team_task.py -->\n"
    ).encode("utf-8")
    mistake_book.write_bytes(custom_mistake)
    preserved_handoff.write_bytes(custom_handoff)
    preserved_inbox.write_bytes(custom_inbox)
    (four_docs_collab / "路由表.md").unlink()
    preserve_custom_truth = run([
        sys.executable, str(SCAFFOLD), str(four_docs_project), "--upgrade-collaboration",
    ])
    check(preserve_custom_truth.stdout.startswith("UPGRADE_OK |")
          and mistake_book.read_bytes() == custom_mistake
          and preserved_handoff.read_bytes() == custom_handoff
          and preserved_inbox.read_bytes() == custom_inbox,
          "upgrade overwrote an existing mistake book, handoff, or inbox truth")

    mistake_book.unlink()
    missing_mistake_detected = run([
        sys.executable, str(SCAFFOLD), str(four_docs_project), "--add-roles", "do",
    ], ok=False)
    check("缺失" in missing_mistake_detected.stderr or "不完整" in missing_mistake_detected.stderr,
          "same-version collaboration accepted a missing root mistake book")
    repaired_mistake = run([
        sys.executable, str(SCAFFOLD), str(four_docs_project), "--upgrade-collaboration",
    ])
    check(repaired_mistake.stdout.startswith("UPGRADE_OK |") and mistake_book.is_file(),
          "same-version upgrade did not restore a missing root mistake book")

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
    flat_guide.write_text(
        flat_guide.read_text(encoding="utf-8").replace(
            f"受管协议版本:{PROTOCOL_VERSION}", "受管协议版本:1.3.0",
        ),
        encoding="utf-8",
    )
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
    check(f"受管协议版本:{PROTOCOL_VERSION}" in flat_guide.read_text(encoding="utf-8"),
          "upgrade did not refresh project agent-guide")
    current_corrupt = json.loads(flat_original)
    current_corrupt["title"] = "bad\x00title"
    flat_task.write_text(json.dumps(current_corrupt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    denied_current = run([sys.executable, str(SCAFFOLD), str(flat_project), "--upgrade-collaboration"], ok=False)
    check("任务真值未通过完整性预检" in denied_current.stderr,
          "same-version upgrade no-op ignored corrupt TASK")
    flat_task.write_bytes(flat_original)
    duplicate_upgrade_text = json.dumps(json.loads(flat_original), ensure_ascii=False, indent=2).replace(
        "{\n", "{\n  \"title\": \"升级不得静默覆盖重复键\",\n", 1,
    ) + "\n"
    flat_task.write_text(duplicate_upgrade_text, encoding="utf-8")
    denied_duplicate_upgrade = run([
        sys.executable, str(SCAFFOLD), str(flat_project), "--upgrade-collaboration",
    ], ok=False)
    check("重复" in denied_duplicate_upgrade.stderr and "title" in denied_duplicate_upgrade.stderr,
          "upgrade preflight silently accepted a duplicate TASK JSON key")
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

    managed_script = collab / "scripts" / "agent_team_task.py"
    managed_script_original = managed_script.read_bytes()
    managed_script.write_text("#!/usr/bin/env python3\n# same-version managed drift\n", encoding="utf-8")
    drift_repaired = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    check(drift_repaired.stdout.startswith("UPGRADE_OK |")
          and managed_script.read_bytes() == managed_script_original,
          "same-version upgrade did not repair managed script content drift")

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

    rollback_project = make_project(root, "upgrade-directory-rollback")
    scaffold(rollback_project)
    rollback_collab = rollback_project / "docs" / "collaboration"
    rollback_tool = rollback_collab / "scripts" / "agent_team_task.py"
    rollback_task_id = enqueue(rollback_tool, "回滚目录真值")
    rollback_flat = rollback_collab / "tasks" / f"{rollback_task_id}.json"
    rollback_task_bytes = rollback_flat.read_bytes()
    rollback_queued = rollback_collab / "tasks" / "queued"
    rollback_claimed = rollback_collab / "tasks" / "claimed"
    rollback_queued.mkdir()
    rollback_claimed.mkdir()
    rollback_flat.rename(rollback_queued / rollback_flat.name)
    if os.name != "nt":
        rollback_queued.chmod(0o710)
        rollback_claimed.chmod(0o711)
    rollback_protocol_path = rollback_collab / "协议版本.json"
    rollback_protocol = json.loads(rollback_protocol_path.read_text(encoding="utf-8"))
    rollback_protocol["protocol_version"] = PREVIOUS_PROTOCOL_VERSION
    rollback_protocol_path.write_text(
        json.dumps(rollback_protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    rollback_protocol_bytes = rollback_protocol_path.read_bytes()
    rollback_guide = rollback_project / "docs" / "agent-guide.md"
    rollback_guide.write_text(
        rollback_guide.read_text(encoding="utf-8").replace(
            f"受管协议版本:{PROTOCOL_VERSION}", f"受管协议版本:{PREVIOUS_PROTOCOL_VERSION}",
        ),
        encoding="utf-8",
    )
    module_spec = importlib.util.spec_from_file_location("agent_team_scaffold_rollback_probe", SCAFFOLD)
    check(module_spec is not None and module_spec.loader is not None,
          "could not load scaffold module for rollback fault injection")
    scaffold_module = importlib.util.module_from_spec(module_spec)
    prior_dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        module_spec.loader.exec_module(scaffold_module)
    finally:
        sys.dont_write_bytecode = prior_dont_write_bytecode
    real_write_utf8_atomic = scaffold_module.write_utf8_atomic

    def fail_after_legacy_directories_removed(path, text, *, mode=None):
        if Path(path) == rollback_collab / "README.md":
            raise OSError("verify injected failure after legacy state directory removal")
        return real_write_utf8_atomic(path, text, mode=mode)

    scaffold_module.write_utf8_atomic = fail_after_legacy_directories_removed
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured_stdout), contextlib.redirect_stderr(captured_stderr):
            rollback_result = scaffold_module.run_upgrade(rollback_collab)
    finally:
        scaffold_module.write_utf8_atomic = real_write_utf8_atomic
    check(rollback_result == 6 and "已按回滚清单原子恢复" in captured_stderr.getvalue(),
          "injected post-migration failure did not take the verified rollback path")
    check(not rollback_flat.exists()
          and (rollback_queued / rollback_flat.name).read_bytes() == rollback_task_bytes
          and rollback_claimed.is_dir()
          and not any(rollback_claimed.iterdir())
          and rollback_protocol_path.read_bytes() == rollback_protocol_bytes,
          "rollback did not restore legacy task placement, empty state directory, or protocol bytes")
    if os.name != "nt":
        check(stat.S_IMODE(rollback_queued.stat().st_mode) == 0o710
              and stat.S_IMODE(rollback_claimed.stat().st_mode) == 0o711,
              "rollback did not restore exact legacy state directory permissions")
    backup_roots = sorted((rollback_collab / "升级备份").iterdir())
    check(bool(backup_roots), "rollback fault did not retain an upgrade backup")
    rollback_manifest = json.loads((backup_roots[-1] / "rollback-manifest.json").read_text(encoding="utf-8"))
    directory_manifest = {entry["target"]: entry for entry in rollback_manifest["directories"]}
    queued_relative = rollback_queued.relative_to(rollback_project).as_posix()
    claimed_relative = rollback_claimed.relative_to(rollback_project).as_posix()
    check(directory_manifest[queued_relative]["existed"] is True
          and directory_manifest[claimed_relative]["existed"] is True
          and (os.name == "nt" or directory_manifest[queued_relative]["mode"] == "0710")
          and (os.name == "nt" or directory_manifest[claimed_relative]["mode"] == "0711"),
          "rollback manifest omitted legacy state directories or their exact modes")


def main() -> int:
    compile_script(SCAFFOLD)
    verify_repository_contract()
    with tempfile.TemporaryDirectory(prefix="agent-team-verify-") as temp:
        root = Path(temp)
        verify_install_bundle_contract(root)
        project = make_project(root, "main")
        scaffold(project)
        verify_generated(project)
        verify_foundation_contract(root)
        verify_product_development_boundary(root)
        verify_tasks(project)
        verify_log_and_session(project, root)
        verify_temporary_executor(root)
        verify_resume_admission_guards(root)
        verify_upgrade_and_guards(root)
    print("VERIFY_OK | scaffold, task, temporary executor, tested-tree promotion, absorption, log, session, upgrade, migration, and path guards passed")
    return 0


def entrypoint() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--check-installed-copy":
        installed = Path(os.path.abspath(str(Path(sys.argv[2]).expanduser())))
        verify_installed_copy(installed)
        print(
            f"INSTALL_COPY_OK | {installed} | files:{len(RUNTIME_FILES)}"
            f" | source:{SOURCE_VERSION} | public:{PUBLIC_VERSION}"
        )
        return 0
    if len(sys.argv) != 1:
        raise VerifyError("usage: verify_agent_team.py [--check-installed-copy PATH]")
    return main()


if __name__ == "__main__":
    try:
        raise SystemExit(entrypoint())
    except VerifyError as exc:
        print(f"VERIFY_ERROR | {exc}", file=sys.stderr)
        raise SystemExit(1)
