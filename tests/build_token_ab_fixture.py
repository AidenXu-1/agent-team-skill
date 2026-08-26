#!/usr/bin/env python3
"""Build paired synthetic 2.0.11 and 2.1 onboarding fixtures in a caller-owned temp root."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LEGACY_RUNTIME = ROOT / "tests" / "fixtures" / "agent-team-2.0.11-runtime"
CURRENT_SCAFFOLD = ROOT / "scripts" / "scaffold_team.py"
PROMPT = (
    "你是执行部的新接班人。第一步直接读取 docs/collaboration/部门/执行部/上岗引导.md，"
    "不要先搜索文件；随后严格按上岗引导完成接班。除上岗引导要求的机械校验外，不修改文件，"
    "不扫描 tasks 目录，不展开冷历史或日志，不领取或执行任务。最后仅返回 department、mode、"
    "task_title、unverified。"
)


def run(args: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(
        args,
        cwd=cwd or ROOT,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def task_id(receipt: str) -> str:
    parts = [part.strip() for part in receipt.split("|")]
    if len(parts) < 2 or parts[0] != "TASK_ENQUEUED":
        raise RuntimeError(f"unexpected enqueue receipt: {receipt!r}")
    return parts[1]


def register_sessions(project: Path) -> None:
    tool = project / "docs" / "collaboration" / "scripts" / "agent_team_session.py"
    for department, thread_id in (
        ("统筹部", "lead-token-ab"),
        ("执行部", "do-token-ab"),
        ("检验部", "review-token-ab"),
    ):
        for step in ("created", "onboarded", "registered"):
            run([
                sys.executable, str(tool), "mark", "--department", department,
                "--step", step, "--thread-id", thread_id,
                "--evidence", "token-ab-synthetic-session",
            ])


def seed_history(source: Path) -> None:
    task_tool = source / "docs" / "collaboration" / "scripts" / "agent_team_task.py"
    seed_id = task_id(run([
        sys.executable, str(task_tool), "enqueue",
        "--actor", "统筹部/lead-token-ab",
        "--department", "执行部", "--from-department", "统筹部",
        "--title", "历史样本", "--node", "synthetic-history",
        "--details", "已完成的合成历史任务", "--acceptance-exit", "只读记录完整",
        "--failure-path", "冻结并保留现场", "--authorization-state", "none",
    ]))
    run([
        sys.executable, str(task_tool), "claim", "--task-id", seed_id,
        "--claimed-by", "执行部/do-token-ab",
    ])
    run([
        sys.executable, str(task_tool), "complete", "--task-id", seed_id,
        "--external-artifact", "https://example.invalid/synthetic-history",
        "--verified", "synthetic-history-complete", "--unverified", "none",
        "--mistake-check", "no-user-data",
    ])
    run([
        sys.executable, str(task_tool), "ack", "--task-id", seed_id,
        "--acknowledged-by", "统筹部/lead-token-ab",
    ])
    tasks = source / "docs" / "collaboration" / "tasks"
    seed = json.loads((tasks / f"{seed_id}.json").read_text(encoding="utf-8"))
    number = 1
    while len(list(tasks.glob("TASK-*.json"))) < 927:
        synthetic_id = f"TASK-20260826-{number:06X}"
        number += 1
        target = tasks / f"{synthetic_id}.json"
        if target.exists():
            continue
        payload = dict(seed)
        payload["task_id"] = synthetic_id
        payload["title"] = f"历史样本 {number - 1:03d}"
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    run([sys.executable, str(task_tool), "rebuild-index"])
    run([sys.executable, str(task_tool), "doctor"])


def enqueue_current(project: Path, *, current: bool) -> str:
    task_tool = project / "docs" / "collaboration" / "scripts" / "agent_team_task.py"
    args = [
        sys.executable, str(task_tool), "enqueue",
        "--actor", "统筹部/lead-token-ab",
        "--department", "执行部", "--from-department", "统筹部",
        "--title", "当前只读接班任务", "--node", "token-ab-current",
        "--details", "只读接班并汇报任务目标与未验证边界",
        "--acceptance-exit", "输出固定三行摘要且不修改文件",
        "--failure-path", "入口过期则停止并报告", "--authorization-state", "none",
    ]
    if current:
        args += ["--task-kind", "owner"]
    return task_id(run(args))


def hot_context_bytes(project: Path, task: str) -> int:
    department = project / "docs" / "collaboration" / "部门" / "执行部"
    paths = [
        department / "上岗引导.md", department / "岗位说明.md",
        department / "交接班文档.md", department / "收件箱.md",
        project / "docs" / "collaboration" / "tasks" / f"{task}.json",
    ]
    return sum(path.stat().st_size for path in paths)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_root", type=Path)
    args = parser.parse_args()
    output_root = args.output_root.expanduser().resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError("output_root must be absent or empty")
    output_root.mkdir(parents=True, exist_ok=True)
    source = output_root / "source"
    (source / "docs").mkdir(parents=True)
    (source / "docs" / "spec.md").write_text(
        "# Token A/B 隔离项目\n\n只验证大型合成历史下的只读接班。\n",
        encoding="utf-8",
    )
    run([
        sys.executable, str(LEGACY_RUNTIME / "scripts" / "scaffold_team.py"), str(source),
        "--profile", "token-ab", "--roles", "lead,do,review", "--session-mode", "manual",
        "--foundation-file", "docs/spec.md",
    ])
    register_sessions(source)
    seed_history(source)
    legacy = output_root / "legacy"
    candidate = output_root / "candidate"
    shutil.copytree(source, legacy)
    shutil.copytree(source, candidate)
    candidate_task_tool = candidate / "docs" / "collaboration" / "scripts" / "agent_team_task.py"
    run([
        sys.executable, str(candidate_task_tool), "freeze-new-work",
        "--actor", "统筹部/lead-token-ab", "--evidence", "token-ab-upgrade-freeze",
    ])
    run([sys.executable, str(CURRENT_SCAFFOLD), str(candidate), "--upgrade-collaboration"])
    run([
        sys.executable, str(candidate_task_tool), "unfreeze-new-work",
        "--actor", "统筹部/lead-token-ab", "--user-confirmation", "token-ab-isolated-run",
    ])
    legacy_task = enqueue_current(legacy, current=False)
    candidate_task = enqueue_current(candidate, current=True)
    legacy_tool = legacy / "docs" / "collaboration" / "scripts" / "agent_team_task.py"
    run([sys.executable, str(legacy_tool), "rebuild-index"])
    run([sys.executable, str(candidate_task_tool), "rebuild-index", "--actor", "统筹部/lead-token-ab"])
    runtime_set_sha = json.loads((ROOT / "candidate-manifest.json").read_text(encoding="utf-8"))[
        "runtime_set_sha256"
    ]
    metadata = {
        "schema_version": 1,
        "prompt": PROMPT,
        "prompt_sha256": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
        "legacy": {
            "path": str(legacy), "task_id": legacy_task,
            "total_tasks": len(list((legacy / "docs" / "collaboration" / "tasks").glob("TASK-*.json"))),
            "hot_context_bytes": hot_context_bytes(legacy, legacy_task),
            "runtime_set_sha256": "171fae3f6cae4454a4cca5521a894e615431e323180d0344b7b2cf3eda4a28ec",
        },
        "candidate": {
            "path": str(candidate), "task_id": candidate_task,
            "total_tasks": len(list((candidate / "docs" / "collaboration" / "tasks").glob("TASK-*.json"))),
            "hot_context_bytes": hot_context_bytes(candidate, candidate_task),
            "runtime_set_sha256": runtime_set_sha,
        },
    }
    (output_root / "fixture-metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_root / "result-schema.json").write_text(
        json.dumps({
            "type": "object",
            "properties": {
                "department": {"type": "string"}, "mode": {"type": "string"},
                "task_title": {"type": "string"},
                "unverified": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["department", "mode", "task_title", "unverified"],
            "additionalProperties": False,
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"TOKEN_AB_FIXTURE_OK | {output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
