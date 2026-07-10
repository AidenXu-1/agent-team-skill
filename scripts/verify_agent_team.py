#!/usr/bin/env python3
"""Verify generated agent-team collaboration docs preserve the node-gate protocol."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import os
import re
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCAFFOLD = ROOT / "scripts" / "scaffold_team.py"
RESEARCH_SCRIPT = ROOT / "scripts" / "agent_team_research.py"
SPEC_TEXT = """# Spec

## 目标与用户需求

为目标用户交付一个可运行产品。

## MVP 功能范围

- 完成核心功能。

## 验收标准

- 核心流程可运行且失败路径可见。
"""

ROLES = "lead,research,planning,do,product,design,dev,ai,growth,test,security,finance"
MINIMAL_FOUNDATION_ARGS = [
    "--foundation-goal", "为课程学员建立可执行的互联网 AI 产品开发协作流程。",
    "--foundation-deliverable", "交付课程讲义、练习项目和验收清单，范围限定为第一期课程。",
    "--foundation-audience", "课程学员、讲师和负责最终验收的项目负责人。",
    "--foundation-acceptance", "讲义完整、练习可以运行、失败路径有说明，并由负责人按清单验收。",
    "--foundation-resources", "已有课程素材、示例项目、讲师时间和本地测试环境。",
    "--foundation-risks", "通过同伴复核、示例实跑和发布前检查避免内容遗漏与错误。",
]

REQUIRED = {
    "docs/collaboration/README.md": [
        "节点式推进与用户确认闸",
        "会话启动清单",
        "不得声称会话已创建",
        "必须用户确认 / 可自主推进 / 可自主推进但必须汇报",
        "自主推进停止条件",
        "统筹部节点卡",
        "日志收据与读取边界",
        "错题集防复发",
        "短唤醒",
        "通知能力",
        "上岗/接班",
        "主场景为互联网 AI 产品",
        "lead,research,planning,do,review",
        "用户明确确认\"体验 OK / 可以进测试\"后",
        "默认直接帮用户打开 App",
        "入口 / 重点 / 建议试法 / 判断口径",
        "统筹已按三类节点判断可自主推进",
        "设计可视化确认",
        "OpenDesign",
        "设计意图预览",
        "真实 UI 验收",
        "成果 / 判断点 / 建议 / 风险 / 下一步",
        "功能方向 OK 不等于 UI 通过",
        "用户不想处理 OpenDesign",
        "帮忙安装 / 启动 / 授权 / 注册 MCP / 重载或新开会话",
        "最小排障顺序",
        "UI 未确认前只能推进实现评估 / 技术可行性",
        "验收出口与失败路径",
        "必测失败路径",
        "审核层独立不等于盲审充分",
        "worker / UI / 用户最终出口",
        "只测底层不算通过",
        "自设计反向探针",
        "是否触发子 Agent 盲审 / 抽检",
        "纯代码 / 质量 / 异常路径问题可自主派开发部返工",
        "用户确认正式收口后",
        "git status --short",
        "共享错题集",
    ],
    "docs/collaboration/会话启动清单.md": [
        "启动前硬闸",
        "互联网 AI 产品主分支",
        "不得声称会话已创建",
        "自动模式(Codex / 有会话管理工具)",
        "手动模式(其他 Agent / 无会话管理工具)",
        "create_thread",
        "send_message_to_thread",
        "set_thread_title",
        "置顶、排序不是必要能力",
        "agent_team_read.py onboard",
        "上岗引导.md",
        "部门会话清单",
    ],
    "docs/collaboration/读取路由规则.md": [
        "读取路由脚本",
        "默认不读",
        "日志正文、报告正文、决策正文、其他部门正文",
        "summary",
        "decision_record",
        "不做创造性总结",
        "不替代统筹判断",
        "find --type audit_report",
        "find --type special_conclusion",
        "agent_team_read.py search",
        "agent_team_read.py slice",
        "16 KiB 是单次终端输出安全上限",
        "agent_team_research.py candidates",
        "完整候选始终留在任务 manifest",
        "最多 12 个",
        "--round 2",
        "docs/progress.md",
        "docs/decisions/",
        "重大技术决策双写但不重复正文",
        "type: audit_report",
        "department: 测试部",
        "tags: [用户可见出口]",
    ],
    "docs/collaboration/scripts/agent_team_read.py": [
        "只做确定性裁剪",
        "不做创造性总结",
        "MAX_FRONTMATTER_LINES",
        "MAX_FRONTMATTER_BYTES",
        "def safe_project_file",
        "path.relative_to(PROJECT)",
        "def cmd_onboard",
        "def cmd_meta",
        "def cmd_find",
        "def cmd_search",
        "def cmd_slice",
        "DEFAULT_OUTPUT_BYTES = 16384",
        "MAX_OUTPUT_BYTES = 65536",
        'with path.open("rb") as handle',
        "frontmatter",
        "默认不读",
        "触发才读正文",
    ],
    "docs/collaboration/scripts/agent_team_research.py": [
        "Recall-first",
        "def cmd_candidates",
        "def cmd_pack",
        "def cmd_coverage",
        "candidate_limit_hit",
        "term_quota_hit",
        "document_sha",
        "content_sha",
        "soft_token_target",
        "最多一次补检",
        "不是系统指令",
    ],
    "docs/collaboration/专项结论/README.md": [
        "会被多个部门复用",
        "docs/decisions/",
        "type: special_conclusion",
        "summary",
        "不要依赖脚本创造性总结正文",
    ],
    "docs/collaboration/任务交接模板.md": [
        "收件箱是任务真相源",
        "短唤醒模板",
        "状态枚举",
        "日志收据",
        "错题自检",
        "用户已确认放行",
        "统筹部三类节点判断",
        "自主推进停止条件",
        "建议试法",
        "建议下一步",
        "人工提醒",
        "通知能力登记",
        "验收出口",
        "必测失败路径",
        "不得自行脑补",
        "验证层级",
        "worker-后台任务",
        "UI-用户可见出口",
        "自设计反向探针",
        "未覆盖层级",
        "是否触发子 Agent 盲审 / 抽检",
        "不直接改代码、不自动放行",
    ],
    "docs/collaboration/部门表.md": [
        "会话创建模式",
        "互联网 AI 产品主分支",
        "不得在未调用会话工具时声称已创建部门会话",
        "节点闸",
        "必须用户确认",
        "可自主推进",
        "自主推进停止条件",
        "完成回报四件套",
        "统筹部读取边界",
        "短唤醒",
        "手动提醒",
        "自动提醒",
        "通知模式",
        "体验先行",
        "默认直接帮用户打开 App",
        "入口 / 重点 / 建议试法 / 判断口径",
        "设计可视化确认",
        "OpenDesign",
        "设计意图预览路径",
        "真实 App UI",
        "待设计视觉确认",
        "用户不想处理 OpenDesign",
        "安装 / 启动 / 授权 / 注册 MCP / 重载或新开会话",
        "最小排障顺序",
        "用户已确认放行",
        "验收出口",
        "必测失败路径",
        "审核层独立不等于盲审充分",
        "happy path",
        "engine/API/helper",
        "worker / UI / 用户最终出口",
        "自设计反向探针",
        "是否触发子 Agent 盲审 / 抽检",
    ],
    "docs/collaboration/部门/统筹部/岗位说明.md": [
        "用节点卡向用户汇报",
        "三类节点判断",
        "流程性、技术性、无争议调度由统筹部专业推进",
        "默认不读部门产出正文",
        "不在产品感知、功能取舍、设计判断、重大风险节点替用户拍板",
        "默认直接帮用户打开 App",
        "先展示设计意图预览",
        "功能方向 OK 不等于 UI 通过",
        "真实 App",
        "验收出口",
        "必测失败路径",
        "不得自行脑补",
    ],
    "docs/collaboration/部门/设计部/岗位说明.md": [
        "OpenDesign",
        "可编辑 artifact",
        "本地 HTML + PNG 截图",
        "设计意图预览路径",
        "不得声称等同真实 App UI",
        "不得只交文字说明",
        "没有 active project",
        "用户不想处理 OpenDesign",
        "权限不足",
        "连接失败",
        "重载或新开会话",
        "最小排障顺序",
    ],
    "docs/collaboration/部门/开发部/收件箱.md": [
        "任务真相源",
        "[回报]",
        "如何体验 / 查看",
        "设计意图预览路径",
        "OpenDesign 状态",
        "建议试法",
        "日志收据",
        "错题自检",
        "测试部不得直接派开发返工",
        "手动通知",
        "验证层级",
        "用户可见出口",
        "自设计反向探针",
        "未覆盖层级",
        "是否触发子 Agent 盲审 / 抽检",
    ],
    "docs/collaboration/部门/AI工程部/岗位说明.md": [
        "Prompt/RAG/Agent",
        "评测集与基线",
        "推理成本",
        "降级",
        "不保存 API Key",
        "app/ai/",
        "跨边界集成由开发部落盘",
    ],
    "docs/collaboration/部门/开发部/岗位说明.md": [
        "负责整体集成",
        "已设 AI工程部时不写 `app/ai/`",
    ],
    "docs/collaboration/部门/研究部/岗位说明.md": [
        "资料收集",
        "事实核验",
        "不把未核实信息当事实",
    ],
    "docs/collaboration/部门/策划部/岗位说明.md": [
        "可执行方案",
        "验收节点",
        "非软件或混合项目",
    ],
    "docs/collaboration/部门/测试部/岗位说明.md": [
        "不代替用户体验功能",
        "体验 OK / 可以进测试",
        "完成回报四件套",
        "不得直接返工或放行",
        "由统筹部节点卡同步后判断是否可自主派开发返工",
        "手动通知",
        "happy path",
        "worker/UI/用户最终出口",
        "只测 engine/API/helper 层",
        "自设计一个反向探针",
        "验证层级",
    ],
    "docs/collaboration/部门/测试部/上岗引导.md": [
        "轻量路由卡",
        "agent_team_read.py onboard --dept 测试部",
        "默认不读日志正文、报告正文、决策正文、其他部门正文",
        "脚本直接返回",
        "裁剪接班包",
        "不做创造性总结",
        "人工模式",
        "自动模式",
    ],
    "docs/collaboration/部门/测试部/报告/README.md": [
        "不是所有任务都需要正式报告",
        "type: work_report",
        "summary",
        "不要依赖脚本创造性总结正文",
    ],
    "docs/collaboration/部门/安全部/岗位说明.md": [
        "大阶段完成、上线或外发前",
        "结论回统筹部",
        "不自动触发返工或放行",
    ],
    "docs/collaboration/部门/财务部/岗位说明.md": [
        "成本核算",
        "MVP 或第二版上线前",
        "不自动卡死发布",
    ],
    "docs/collaboration/部门/测试部/把关报告/README.md": [
        "审核报告",
        "兼容旧称“把关报告”",
        "type: audit_report",
        "summary",
        "审核层独立不等于盲审充分",
        "验证层级",
        "engine",
        "adapter-service",
        "worker-后台任务",
        "UI-用户可见出口",
        "打包态",
        "未覆盖层级",
        "用户可见出口",
        "必测失败路径",
        "自设计反向探针",
        "是否触发子 Agent 盲审 / 抽检",
        "连续 3 轮无阻断通过",
        "不直接改代码、不自动放行",
    ],
}

FORBIDDEN = [
    "把关打回时重新激活执行部门返工",
    "任一关不通过 → 经统筹部打回",
    "不通过经统筹部打回对应执行部门",
    "安全部、财务部**方案阶段就前置介入**",
    "直接通知测试部开始测试",
    "统筹部通读部门日志正文",
    "风险可控时派开发实现",
    "开发评估完成,我判断风险可控,已派开发实现",
    "只验证 engine 层即可通过",
    "只跑 happy path 即可通过",
]

OPENDESIGN_FAILURE_MATRIX = [
    "未安装 / 未运行 OpenDesign",
    "MCP 未热加载",
    "无 active project",
    "权限不足",
    "连接失败",
    "用户不想处理 OpenDesign",
    "帮忙安装 / 启动 / 授权 / 注册 MCP / 重载或新开会话",
    "不得卡住",
    "兜底预览方案",
    "设计说明文档路径",
    "设计意图预览路径",
    "真实 UI 验收",
    "OpenDesign 当前状态",
    "后续恢复条件",
]

PRESSURE_SCENARIO_REQUIRED = [
    "场景 18: 底层通过但用户可见出口丢信息",
    "场景 19: 盲审/抽检该触发但未触发",
    "验收出口",
    "必测失败路径",
    "worker-后台任务 / UI-用户可见出口",
    "验证层级",
    "自设计反向探针",
    "是否触发子 Agent 盲审 / 抽检",
    "不直接改代码、不自动放行",
    "场景 20: 读取路由脚本防止长文误读",
    "场景 21: 专项结论升格与检索",
    "场景 5A: 测试不通过涉及取舍",
    "纯代码 / 质量 / 异常路径",
    "可自行派开发部返工",
    "用户确认正式收口后",
    "git status --short",
    "默认不读日志正文、报告正文、决策正文、其他部门正文",
    "agent_team_read.py onboard",
    "frontmatter",
    "summary",
    "search",
    "slice",
    "输出总预算",
    "场景 26: docs 符号链接不得越界写入",
    "场景 27: 并发增设部门保持一致",
    "场景 28: 接班包有界且不漏最新待办",
    "场景 29: 地基语义闸与事务回滚",
    "场景 30: 高召回研究模式不被精排静默截断",
    "场景 31: Unicode 与文本编码失败必须显式",
    "扩词只能增加",
    "完整候选 manifest",
    "target-tokens",
    "--round 2",
]


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)


def fail(message: str) -> int:
    print(f"FAIL: {message}", file=sys.stderr)
    return 1


def main() -> int:
    compile_result = run([sys.executable, "-m", "py_compile", str(SCAFFOLD), str(RESEARCH_SCRIPT)], ROOT)
    if compile_result.returncode != 0:
        print(compile_result.stderr, file=sys.stderr)
        return fail("scaffold_team.py does not compile")

    temp_root = Path(tempfile.mkdtemp(prefix="agent-team-verify-"))
    try:
        target = temp_root / "project"
        (target / "docs").mkdir(parents=True)
        (target / "docs" / "spec.md").write_text(SPEC_TEXT)

        scaffold_result = run(
            [
                sys.executable,
                str(SCAFFOLD),
                str(target),
                "--roles",
                ROLES,
                "--profile",
                "verification",
                "--session-mode",
                "manual",
            ],
            ROOT,
        )
        if scaffold_result.returncode != 0:
            print(scaffold_result.stdout)
            print(scaffold_result.stderr, file=sys.stderr)
            return fail("scaffold generation failed")

        duplicate_target = temp_root / "duplicate-project"
        (duplicate_target / "docs").mkdir(parents=True)
        (duplicate_target / "docs" / "spec.md").write_text(SPEC_TEXT)
        missing_layer_target = temp_root / "missing-layer-project"
        (missing_layer_target / "docs").mkdir(parents=True)
        (missing_layer_target / "docs" / "spec.md").write_text(SPEC_TEXT)
        unconfirmed_mode_target = temp_root / "unconfirmed-mode-project"
        unconfirmed_mode_target.mkdir()
        invalid_mode_target = temp_root / "invalid-mode-project"
        invalid_mode_target.mkdir()
        missing_foundation_target = temp_root / "missing-foundation-project"
        missing_foundation_target.mkdir()
        allow_only_target = temp_root / "allow-only-project"
        allow_only_target.mkdir()
        empty_markdown_target = temp_root / "empty-markdown-project"
        (empty_markdown_target / "docs").mkdir(parents=True)
        (empty_markdown_target / "docs" / "empty.md").write_text("")
        random_markdown_target = temp_root / "random-markdown-project"
        (random_markdown_target / "docs").mkdir(parents=True)
        (random_markdown_target / "docs" / "random.md").write_text("x" * 220)
        empty_spec_target = temp_root / "empty-spec-project"
        (empty_spec_target / "docs").mkdir(parents=True)
        (empty_spec_target / "docs" / "spec.md").write_text("")
        incomplete_add_target = temp_root / "incomplete-add-project"
        (incomplete_add_target / "docs" / "collaboration").mkdir(parents=True)
        locale_target = temp_root / "locale-project"
        (locale_target / "docs").mkdir(parents=True)
        (locale_target / "docs" / "spec.md").write_text(SPEC_TEXT)
        file_target = temp_root / "target-is-file"
        file_target.write_text("not a directory")
        default_product_target = temp_root / "default-product-project"
        (default_product_target / "docs").mkdir(parents=True)
        (default_product_target / "docs" / "spec.md").write_text(SPEC_TEXT)
        english_spec_target = temp_root / "english-spec-project"
        (english_spec_target / "docs").mkdir(parents=True)
        (english_spec_target / "docs" / "spec.md").write_text(
            "# Product Spec\n\n## Product Goal and User Requirements\nBuild a useful AI product for target users.\n\n"
            "## Scope and Deliverables\nDeliver the MVP feature set and integration.\n\n"
            "## Acceptance Criteria\nThe core flow and failure paths are verifiably complete.\n"
        )
        minimal_foundation_target = temp_root / "minimal-foundation-project"
        minimal_foundation_target.mkdir()
        missing_minimal_args_target = temp_root / "missing-minimal-args-project"
        missing_minimal_args_target.mkdir()
        keyword_spam_target = temp_root / "keyword-spam-project"
        (keyword_spam_target / "docs").mkdir(parents=True)
        (keyword_spam_target / "docs" / "random.md").write_text("user scope acceptance " * 40)
        docs_file_target = temp_root / "docs-is-file-project"
        docs_file_target.mkdir()
        (docs_file_target / "docs").write_text("not a directory")
        rollback_target = temp_root / "rollback-project"
        (rollback_target / "docs" / "agent-guide.md").mkdir(parents=True)
        (rollback_target / "docs" / "spec.md").write_text(SPEC_TEXT)
        symlink_target = temp_root / "docs-symlink-project"
        symlink_target.mkdir()
        symlink_outside = temp_root / "docs-symlink-outside"
        symlink_outside.mkdir()
        (symlink_target / "docs").symlink_to(symlink_outside, target_is_directory=True)

        duplicate_result = run(
            [
                sys.executable,
                str(SCAFFOLD),
                str(duplicate_target),
                "--roles",
                "lead,do,do,review",
                "--profile",
                "verification",
                "--session-mode",
                "manual",
                "--allow-without-foundation",
            ],
            ROOT,
        )
        if duplicate_result.returncode == 0 or "重复角色" not in duplicate_result.stderr:
            return fail("duplicate roles are not rejected clearly")
        if (duplicate_target / "docs" / "collaboration").exists():
            return fail("duplicate-role failure left a partial collaboration layer")

        missing_layer_result = run(
            [
                sys.executable,
                str(SCAFFOLD),
                str(missing_layer_target),
                "--roles",
                "lead,do",
                "--profile",
                "verification",
                "--session-mode",
                "manual",
                "--allow-without-foundation",
            ],
            ROOT,
        )
        if missing_layer_result.returncode == 0 or "缺少三层框架" not in missing_layer_result.stderr:
            return fail("teams missing one of the three layers are not rejected clearly")
        if (missing_layer_target / "docs" / "collaboration").exists():
            return fail("missing-layer failure left a partial collaboration layer")

        help_result = run([sys.executable, str(SCAFFOLD), "--help"], ROOT)
        if help_result.returncode != 0:
            return fail("scaffold help failed")
        for forbidden_mode in ("pending", "待判断", "尚未确认"):
            if forbidden_mode in help_result.stdout:
                return fail(f"scaffold help still exposes a third session mode: {forbidden_mode}")

        unconfirmed_mode_result = run(
            [
                sys.executable,
                str(SCAFFOLD),
                str(unconfirmed_mode_target),
                "--roles",
                "lead,do,review",
                "--profile",
                "verification",
                "--allow-without-foundation",
            ],
            ROOT,
        )
        if unconfirmed_mode_result.returncode == 0 or "未确认会话创建模式" not in unconfirmed_mode_result.stderr:
            return fail("missing session mode is not rejected clearly")
        if (unconfirmed_mode_target / "docs" / "collaboration").exists():
            return fail("missing-session-mode failure left a partial collaboration layer")

        invalid_mode_result = run(
            [
                sys.executable,
                str(SCAFFOLD),
                str(invalid_mode_target),
                "--roles",
                "lead,do,review",
                "--profile",
                "verification",
                "--session-mode",
                "pending",
                "--allow-without-foundation",
            ],
            ROOT,
        )
        if invalid_mode_result.returncode == 0 or "invalid choice" not in invalid_mode_result.stderr:
            return fail("third session mode is still accepted")
        if (invalid_mode_target / "docs" / "collaboration").exists():
            return fail("invalid-session-mode failure left a partial collaboration layer")

        missing_foundation_result = run(
            [
                sys.executable,
                str(SCAFFOLD),
                str(missing_foundation_target),
                "--roles",
                "lead,do,review",
                "--profile",
                "verification",
                "--session-mode",
                "manual",
            ],
            ROOT,
        )
        if missing_foundation_result.returncode == 0 or "未找到 docs/spec.md" not in missing_foundation_result.stderr:
            return fail("missing foundation is not rejected clearly")
        if (missing_foundation_target / "docs" / "collaboration").exists():
            return fail("missing-foundation failure left a partial collaboration layer")

        allow_only_result = run(
            [
                sys.executable,
                str(SCAFFOLD),
                str(allow_only_target),
                "--roles",
                "lead,do,review",
                "--profile",
                "verification",
                "--session-mode",
                "manual",
                "--allow-without-foundation",
            ],
            ROOT,
        )
        if allow_only_result.returncode == 0 or "不能只创建无地基协作层" not in allow_only_result.stderr:
            return fail("allow-without-foundation alone is not rejected for an empty project")
        if (allow_only_target / "docs" / "collaboration").exists():
            return fail("allow-only failure left a partial collaboration layer")

        empty_markdown_result = run(
            [
                sys.executable, str(SCAFFOLD), str(empty_markdown_target),
                "--roles", "lead,do,review", "--profile", "verification",
                "--session-mode", "manual", "--allow-without-foundation",
            ],
            ROOT,
        )
        if empty_markdown_result.returncode == 0 or (empty_markdown_target / "docs" / "collaboration").exists():
            return fail("an empty markdown file is still accepted as a usable project foundation")

        for shell_target in (random_markdown_target, empty_spec_target):
            shell_result = run(
                [
                    sys.executable, str(SCAFFOLD), str(shell_target),
                    "--roles", "lead,do,review", "--profile", "verification",
                    "--session-mode", "manual", "--allow-without-foundation",
                ],
                ROOT,
            )
            if shell_result.returncode == 0 or (shell_target / "docs" / "collaboration").exists():
                return fail(f"an empty-shell foundation is accepted: {shell_target.name}")

        incomplete_add_result = run(
            [sys.executable, str(SCAFFOLD), str(incomplete_add_target), "--add-roles", "research"],
            ROOT,
        )
        if incomplete_add_result.returncode == 0 or (incomplete_add_target / "docs" / "collaboration" / "部门" / "研究部").exists():
            return fail("add-roles mutates an incomplete collaboration layer")

        locale_env = os.environ.copy()
        locale_env.update({"LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"})
        locale_result = subprocess.run(
            [sys.executable, str(SCAFFOLD), str(locale_target), "--roles", "lead,do,review", "--profile", "verification", "--session-mode", "manual"],
            cwd=ROOT, text=True, capture_output=True, check=False, env=locale_env,
        )
        progress_path = locale_target / "docs" / "progress.md"
        if locale_result.returncode != 0 or not progress_path.is_file() or progress_path.stat().st_size == 0:
            return fail("scaffold is not stable under a non-UTF-8 process locale")

        file_target_result = run(
            [sys.executable, str(SCAFFOLD), str(file_target), "--roles", "lead,do,review", "--session-mode", "manual"],
            ROOT,
        )
        if file_target_result.returncode == 0 or "目标路径不是目录" not in file_target_result.stderr or "Traceback" in file_target_result.stderr:
            return fail("a file target is not rejected cleanly")

        default_product_result = run(
            [sys.executable, str(SCAFFOLD), str(default_product_target), "--profile", "internet product", "--session-mode", "manual"],
            ROOT,
        )
        if default_product_result.returncode != 0:
            return fail("default internet product scaffold failed")
        for department in ("产品部", "设计部", "开发部", "测试部"):
            if not (default_product_target / "docs" / "collaboration" / "部门" / department).is_dir():
                return fail(f"default internet product scaffold missing department: {department}")

        english_spec_result = run(
            [sys.executable, str(SCAFFOLD), str(english_spec_target), "--profile", "english internet product", "--session-mode", "manual"],
            ROOT,
        )
        if english_spec_result.returncode != 0:
            return fail("a structured English product spec is rejected as unusable foundation")

        orphan_dir = default_product_target / "docs" / "collaboration" / "部门" / "自动化部"
        orphan_dir.mkdir()
        orphan_result = run([sys.executable, str(SCAFFOLD), str(default_product_target), "--add-roles", "auto"], ROOT)
        if orphan_result.returncode == 0 or "状态不一致" not in orphan_result.stderr:
            return fail("add-roles accepts a department directory missing from the registry")

        repair_target = temp_root / "repair-add-project"
        (repair_target / "docs").mkdir(parents=True)
        (repair_target / "docs" / "spec.md").write_text(SPEC_TEXT)
        repair_scaffold = run(
            [sys.executable, str(SCAFFOLD), str(repair_target), "--roles", "lead,dev,ai,test", "--profile", "repair", "--session-mode", "manual"],
            ROOT,
        )
        if repair_scaffold.returncode != 0:
            return fail("could not create add-roles repair fixture")
        shutil.rmtree(repair_target / "docs" / "collaboration" / "部门" / "AI工程部")
        repair_result = run([sys.executable, str(SCAFFOLD), str(repair_target), "--add-roles", "ai"], ROOT)
        repair_registry = (repair_target / "docs" / "collaboration" / "部门表.md").read_text()
        if repair_result.returncode != 0 or repair_registry.count("`ai`") != 1 or not (repair_target / "docs" / "collaboration" / "部门" / "AI工程部").is_dir():
            return fail("add-roles duplicates registry state instead of repairing a missing department directory")

        concurrent_target = temp_root / "concurrent-add-project"
        (concurrent_target / "docs").mkdir(parents=True)
        (concurrent_target / "docs" / "spec.md").write_text(SPEC_TEXT)
        concurrent_scaffold = run(
            [sys.executable, str(SCAFFOLD), str(concurrent_target), "--roles", "lead,do,review", "--session-mode", "manual"],
            ROOT,
        )
        if concurrent_scaffold.returncode != 0:
            return fail("could not create concurrent add-roles fixture")
        processes = [
            subprocess.Popen(
                [sys.executable, str(SCAFFOLD), str(concurrent_target), "--add-roles", role],
                cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            for role in ("research", "planning")
        ]
        concurrent_results = [process.communicate() + (process.returncode,) for process in processes]
        for role, (stdout, stderr, returncode) in zip(("research", "planning"), concurrent_results):
            if returncode not in {0, 7}:
                return fail(f"concurrent add-roles returned unexpected status for {role}: {returncode} {stderr}")
            if returncode == 7 and "另一个 agent-team" not in stderr:
                return fail("concurrent add-roles lock failure is not explained cleanly")
        for role, department in (("research", "研究部"), ("planning", "策划部")):
            registry = (concurrent_target / "docs" / "collaboration" / "部门表.md").read_text()
            if f"`{role}`" not in registry:
                retry = run([sys.executable, str(SCAFFOLD), str(concurrent_target), "--add-roles", role], ROOT)
                if retry.returncode != 0:
                    return fail(f"could not retry lock-rejected add-role: {role}")
            registry = (concurrent_target / "docs" / "collaboration" / "部门表.md").read_text()
            directory_exists = (concurrent_target / "docs" / "collaboration" / "部门" / department).is_dir()
            if directory_exists != (f"`{role}`" in registry):
                return fail(f"concurrent add-roles leaves directory/registry mismatch: {role}")

        minimal_foundation_result = run(
            [
                sys.executable,
                str(SCAFFOLD),
                str(minimal_foundation_target),
                "--roles",
                "lead,research,planning,do,review",
                "--profile",
                "non-software verification",
                "--session-mode",
                "manual",
                "--allow-without-foundation",
                "--create-minimal-foundation",
                *MINIMAL_FOUNDATION_ARGS,
            ],
            ROOT,
        )
        if minimal_foundation_result.returncode != 0:
            print(minimal_foundation_result.stdout)
            print(minimal_foundation_result.stderr, file=sys.stderr)
            return fail("minimal business foundation generation failed")
        for relative in (
            "docs/overview.md",
            "docs/progress.md",
            "docs/agent-guide.md",
            "docs/collaboration/README.md",
        ):
            if not (minimal_foundation_target / relative).exists():
                return fail(f"minimal foundation path missing: {relative}")
        minimal_overview = (minimal_foundation_target / "docs" / "overview.md").read_text()
        if "通用最小业务地基" not in minimal_overview:
            return fail("minimal foundation overview does not identify the business foundation")

        missing_minimal_args_result = run(
            [
                sys.executable, str(SCAFFOLD), str(missing_minimal_args_target),
                "--roles", "lead,do,review", "--session-mode", "manual",
                "--allow-without-foundation", "--create-minimal-foundation",
            ],
            ROOT,
        )
        if missing_minimal_args_result.returncode == 0 or "必须提供真实、非占位内容" not in missing_minimal_args_result.stderr:
            return fail("minimal foundation creation accepts missing structured business inputs")
        if (missing_minimal_args_target / "docs").exists():
            return fail("missing minimal foundation inputs mutate the project")

        keyword_spam_result = run(
            [
                sys.executable, str(SCAFFOLD), str(keyword_spam_target),
                "--roles", "lead,do,review", "--session-mode", "manual", "--allow-without-foundation",
            ],
            ROOT,
        )
        if keyword_spam_result.returncode == 0 or (keyword_spam_target / "docs" / "collaboration").exists():
            return fail("keyword spam is accepted as a usable project foundation")

        docs_file_result = run(
            [
                sys.executable, str(SCAFFOLD), str(docs_file_target),
                "--roles", "lead,do,review", "--session-mode", "manual",
                "--allow-without-foundation", "--create-minimal-foundation", *MINIMAL_FOUNDATION_ARGS,
            ],
            ROOT,
        )
        if docs_file_result.returncode == 0 or "docs 必须是项目内普通目录" not in docs_file_result.stderr or "Traceback" in docs_file_result.stderr:
            return fail("a file-shaped docs path is not rejected cleanly")

        rollback_result = run(
            [sys.executable, str(SCAFFOLD), str(rollback_target), "--roles", "lead,do,review", "--session-mode", "manual"],
            ROOT,
        )
        if rollback_result.returncode == 0 or (rollback_target / "docs" / "collaboration").exists():
            return fail("rollback fixture unexpectedly succeeds or leaves collaboration")
        if (rollback_target / "docs" / "progress.md").exists():
            return fail("failed scaffold leaves a newly-created progress foundation file")

        symlink_result = run(
            [
                sys.executable, str(SCAFFOLD), str(symlink_target),
                "--roles", "lead,do,review", "--session-mode", "manual",
                "--allow-without-foundation", "--create-minimal-foundation", *MINIMAL_FOUNDATION_ARGS,
            ],
            ROOT,
        )
        if symlink_result.returncode == 0 or "docs 必须是项目内普通目录" not in symlink_result.stderr:
            return fail("scaffold follows a docs symlink outside the project")
        if any(symlink_outside.iterdir()):
            return fail("docs symlink rejection still writes outside the project")
        for relative in (
            "docs/collaboration/部门/研究部/岗位说明.md",
            "docs/collaboration/部门/策划部/岗位说明.md",
        ):
            role_text = (minimal_foundation_target / relative).read_text()
            for legacy in ("docs/spec.md", "app/ 代码"):
                if legacy in role_text:
                    return fail(f"non-software role still contains software-specific wording: {relative} -> {legacy}")

        for relative in (
            "docs/collaboration/部门/策划部/岗位说明.md",
            "docs/collaboration/部门/增长运营部/岗位说明.md",
        ):
            role_text = (target / relative).read_text()
            writable_section = ""
            lines = role_text.splitlines()
            for index, line in enumerate(lines):
                if line.strip() == "## 可写文件 / 目录":
                    for candidate in lines[index + 1:]:
                        if candidate.startswith("## "):
                            break
                        if candidate.strip():
                            writable_section += candidate + "\n"
                    break
            if "docs/progress.md" in writable_section:
                return fail(f"non-lead role can still write project progress: {relative}")

        registry_text = (target / "docs" / "collaboration" / "部门表.md").read_text()
        for row in registry_text.splitlines():
            if "| 执行层 |" in row or "| 审核层 |" in row:
                parts = [part.strip() for part in row.strip().strip("|").split("|")]
                if len(parts) >= 7 and "docs/progress.md" in parts[6]:
                    return fail(f"non-lead registry row can still write project progress: {row}")

        for relative, needles in REQUIRED.items():
            path = target / relative
            if not path.exists():
                return fail(f"missing generated file: {relative}")
            text = path.read_text()
            for needle in needles:
                if needle not in text:
                    return fail(f"{relative} missing required text: {needle}")

        read_router = target / "docs" / "collaboration" / "scripts" / "agent_team_read.py"
        read_router_text = read_router.read_text()
        frontmatter_body = read_router_text.split("def frontmatter", 1)[1].split("def metadata_files", 1)[0]
        if "read_text(path)" in frontmatter_body:
            return fail("read router frontmatter parser still reads the whole markdown file")
        for required_guard in ("MAX_FRONTMATTER_LINES", "MAX_FRONTMATTER_BYTES"):
            if required_guard not in frontmatter_body:
                return fail(f"read router frontmatter parser missing guard: {required_guard}")
        onboard_result = run([sys.executable, str(read_router), "onboard", "--dept", "测试部"], target)
        if onboard_result.returncode != 0:
            print(onboard_result.stdout)
            print(onboard_result.stderr, file=sys.stderr)
            return fail("read router onboard failed")
        for needle in (
            "你是: 测试部",
            "本次接班包",
            "岗位核心:",
            "交接摘要:",
            "当前待办正文:",
            "默认不读:",
            "日志正文",
            "报告正文",
            "触发才读正文:",
            "无结构化待办",
        ):
            if needle not in onboard_result.stdout:
                return fail(f"read router onboard output missing: {needle}")

        role_path = target / "docs" / "collaboration" / "部门" / "测试部" / "岗位说明.md"
        role_backup = role_path.read_text()
        role_path.unlink()
        role_path.symlink_to(temp_root / "outside-linked.md")
        unsafe_onboard = run([sys.executable, str(read_router), "onboard", "--dept", "测试部"], target)
        role_path.unlink()
        role_path.write_text(role_backup)
        if "缺失或路径非法，禁止直接读取" not in unsafe_onboard.stdout:
            return fail("onboard presents a symlinked required file as safe to read")

        long_dept = "A" * 12000
        bounded_error = run(
            [sys.executable, str(read_router), "onboard", "--dept", long_dept, "--max-output-bytes", "1024"],
            target,
        )
        if len(bounded_error.stderr.encode("utf-8")) > 1024:
            return fail("read router error output bypasses the configured byte budget")

        inbox_path = target / "docs" / "collaboration" / "部门" / "测试部" / "收件箱.md"
        inbox_backup = inbox_path.read_text()
        inbox_path.write_text(("x" * 140000) + "\n## [紧急] LATEST_AFTER_128K\n- 任务详情:检查尾部最新任务。\n")
        tail_onboard = run([sys.executable, str(read_router), "onboard", "--dept", "测试部"], target)
        inbox_path.write_text(inbox_backup)
        if "LATEST_AFTER_128K" not in tail_onboard.stdout or "检查尾部最新任务" not in tail_onboard.stdout:
            return fail("onboard misses the newest structured task after the old 128 KiB prefix")

        conclusion = target / "docs" / "collaboration" / "专项结论" / "2026-07-02-用户可见出口-专项结论.md"
        conclusion.write_text("""---
type: special_conclusion
department: 统筹部
target: 用户可见出口
status: active
date: 2026-07-02
related_task: 测试部收件箱#demo
decision: 多部门复用
tags: [用户可见出口, worker, UI]
summary: 涉及用户可见文案时必须测到 worker/UI/最终出口。
---

# 用户可见出口专项结论

正文不应被 find 命令默认读取。
""")
        find_result = run(
            [
                sys.executable,
                str(read_router),
                "find",
                "--type",
                "special_conclusion",
                "--tag",
                "用户可见出口",
            ],
            target,
        )
        if find_result.returncode != 0:
            print(find_result.stdout)
            print(find_result.stderr, file=sys.stderr)
            return fail("read router find failed")
        for needle in (
            "2026-07-02-用户可见出口-专项结论.md",
            "type: special_conclusion",
            "summary: 涉及用户可见文案时必须测到 worker/UI/最终出口。",
        ):
            if needle not in find_result.stdout:
                return fail(f"read router find output missing: {needle}")
        if "正文不应被 find 命令默认读取" in find_result.stdout:
            return fail("read router find leaked markdown body instead of frontmatter only")

        meta_result = run(
            [
                sys.executable,
                str(read_router),
                "meta",
                "docs/collaboration/专项结论/2026-07-02-用户可见出口-专项结论.md",
            ],
            target,
        )
        if meta_result.returncode != 0 or "target: 用户可见出口" not in meta_result.stdout:
            return fail("read router meta did not return frontmatter fields")

        outside_file = temp_root / "outside-frontmatter.md"
        outside_file.write_text("""---
type: special_conclusion
summary: should not be readable from outside project
---

# Outside
""")
        outside_meta_result = run(
            [sys.executable, str(read_router), "meta", str(outside_file)],
            target,
        )
        if outside_meta_result.returncode == 0 or "路径非法" not in outside_meta_result.stderr:
            return fail("read router meta allows paths outside the project")

        malformed = target / "docs" / "collaboration" / "专项结论" / "2026-07-02-畸形元数据.md"
        malformed.write_text("---\n" + "\n".join(f"k{i}: v{i}" for i in range(240)) + "\n# no closing marker\n")
        malformed_meta_result = run(
            [
                sys.executable,
                str(read_router),
                "meta",
                "docs/collaboration/专项结论/2026-07-02-畸形元数据.md",
            ],
            target,
        )
        if malformed_meta_result.returncode == 0 or "无有效受限元数据" not in malformed_meta_result.stdout:
            return fail("read router meta does not reject oversized or unterminated frontmatter")

        partial_malformed = target / "docs" / "collaboration" / "专项结论" / "2026-07-02-半畸形元数据.md"
        partial_malformed.write_text("""---
type audit_report_without_colon
department: 测试部
status: blocked
summary: this partial metadata must be rejected
---

# Partially malformed metadata

正文不应被读取,也不应因剩余字段进入 find 结果。
""")
        partial_meta_result = run(
            [
                sys.executable,
                str(read_router),
                "meta",
                "docs/collaboration/专项结论/2026-07-02-半畸形元数据.md",
            ],
            target,
        )
        if partial_meta_result.returncode == 0 or "无有效受限元数据" not in partial_meta_result.stdout:
            return fail("read router meta accepts partially malformed frontmatter")
        partial_find_result = run(
            [sys.executable, str(read_router), "find", "--status", "blocked"],
            target,
        )
        if "2026-07-02-半畸形元数据.md" in partial_find_result.stdout:
            return fail("read router find includes partially malformed frontmatter")

        near_match = target / "docs" / "collaboration" / "专项结论" / "2026-07-02-近似字段不应命中.md"
        near_match.write_text("""---
type: audit_report_extra
department: 测试部
target: 用户可见出口
status: unblocked
date: 2026-07-02
related_task: 测试部收件箱#demo
decision: 近似字段探针
tags: [用户可见出口-extra, blocked-extra]
summary: 近似字段不能被 find 当成精确命中。
---

# 近似字段不应命中

正文不应被读取。
""")
        near_type_result = run(
            [sys.executable, str(read_router), "find", "--type", "audit_report", "--status", "blocked"],
            target,
        )
        if "2026-07-02-近似字段不应命中.md" in near_type_result.stdout:
            return fail("read router find uses substring matching for type/status filters")
        near_tag_result = run(
            [sys.executable, str(read_router), "find", "--tag", "用户可见出口"],
            target,
        )
        if "2026-07-02-近似字段不应命中.md" in near_tag_result.stdout:
            return fail("read router find uses substring matching for tag filters")

        bom = target / "docs" / "collaboration" / "专项结论" / "2026-07-02-BOM.md"
        bom.write_bytes(b"\xef\xbb\xbf---\ntype: special_conclusion\nsummary: bom accepted\n---\nBODY\n")
        bom_result = run([sys.executable, str(read_router), "meta", str(bom.relative_to(target))], target)
        if bom_result.returncode != 0 or "summary: bom accepted" not in bom_result.stdout:
            return fail("read router does not accept UTF-8 BOM safely")

        duplicate = target / "docs" / "collaboration" / "专项结论" / "2026-07-02-重复键.md"
        duplicate.write_text("---\ntype: special_conclusion\nstatus: blocked\nstatus: active\nsummary: duplicate\n---\n")
        duplicate_result = run([sys.executable, str(read_router), "meta", str(duplicate.relative_to(target))], target)
        if duplicate_result.returncode == 0:
            return fail("read router silently accepts duplicate metadata keys")

        unknown = target / "docs" / "collaboration" / "专项结论" / "2026-07-02-未知字段.md"
        unknown.write_text("---\ntype: special_conclusion\nsecret_internal_field: should-not-print\nsummary: unknown\n---\n")
        unknown_result = run([sys.executable, str(read_router), "meta", str(unknown.relative_to(target))], target)
        if unknown_result.returncode == 0 or "should-not-print" in unknown_result.stdout:
            return fail("read router accepts or prints unknown metadata fields")

        external = temp_root / "outside-linked.md"
        external.write_text("---\ntype: special_conclusion\ntags: [outside]\nsummary: OUTSIDE_SECRET_METADATA\n---\n")
        link = target / "docs" / "collaboration" / "专项结论" / "outside-linked.md"
        link.symlink_to(external)
        link_result = run([sys.executable, str(read_router), "find", "--tag", "outside"], target)
        if "OUTSIDE_SECRET_METADATA" in link_result.stdout:
            return fail("read router find follows a symlink outside the project")

        real_dir = target / "docs" / "real-materials"
        real_dir.mkdir()
        (real_dir / "article.md").write_text("INTERMEDIATE_LINK_SECRET\n")
        linked_dir = target / "docs" / "linked-materials"
        linked_dir.symlink_to(real_dir, target_is_directory=True)
        intermediate_result = run(
            [sys.executable, str(read_router), "search", "docs/linked-materials/article.md", "--query", "INTERMEDIATE_LINK_SECRET"],
            target,
        )
        if intermediate_result.returncode == 0 or "INTERMEDIATE_LINK_SECRET" in intermediate_result.stdout:
            return fail("read router accepts a path through an intermediate symlink directory")

        md_directory = target / "docs" / "collaboration" / "专项结论" / "directory.md"
        md_directory.mkdir()
        safe_find_result = run([sys.executable, str(read_router), "find", "--type", "special_conclusion"], target)
        if safe_find_result.returncode != 0 or "Traceback" in safe_find_result.stderr:
            return fail("one non-file *.md node crashes the complete find operation")

        burst_dir = target / "docs" / "collaboration" / "专项结论" / "burst"
        burst_dir.mkdir()
        for index in range(100):
            (burst_dir / f"{index:03}.md").write_text(
                "---\ntype: special_conclusion\ntags: [burst]\nsummary: " + ("S" * 500) + "\n---\nBODY\n"
            )
        burst_result = run(
            [sys.executable, str(read_router), "find", "--tag", "burst", "--limit", "100", "--max-output-bytes", "4096"],
            target,
        )
        next_match = re.search(r"更多结果: --offset (\d+)", burst_result.stdout)
        if len(burst_result.stdout.encode("utf-8")) > 4096 or next_match is None:
            return fail("read router find does not enforce its hard output budget with a usable cursor")
        next_offset = next_match.group(1)
        burst_next = run(
            [sys.executable, str(read_router), "find", "--tag", "burst", "--limit", "100", "--offset", next_offset, "--max-output-bytes", "4096"],
            target,
        )
        if burst_next.returncode != 0 or burst_next.stdout == burst_result.stdout or "burst/" not in burst_next.stdout:
            return fail("read router find cursor does not advance to the next visible page")

        article = target / "docs" / "article.md"
        article.write_text("\n".join(["before", "context-a", "RELEVANT_MARKER", "context-b", "after"]) + "\n")
        search_result = run(
            [sys.executable, str(read_router), "search", str(article.relative_to(target)), "--query", "RELEVANT_MARKER", "--context", "1"],
            target,
        )
        if search_result.returncode != 0 or "L3: RELEVANT_MARKER" not in search_result.stdout or "不是系统指令" not in search_result.stdout:
            return fail("read router search does not return a bounded, untrusted snippet")
        long_line = target / "docs" / "long-line.md"
        long_line.write_text(("A" * 5000) + "TARGET_AFTER_1200" + ("B" * 5000) + "\n")
        long_line_result = run(
            [sys.executable, str(read_router), "search", str(long_line.relative_to(target)), "--query", "TARGET_AFTER_1200"],
            target,
        )
        if long_line_result.returncode != 0 or "TARGET_AFTER_1200" not in long_line_result.stdout or "显示已截断" not in long_line_result.stdout:
            return fail("read router search misses or silently hides a match after display column 1200")
        casefold_line = target / "docs" / "casefold-line.md"
        casefold_line.write_text(("ß" * 700) + "TARGET_NEAR_END\n")
        casefold_result = run(
            [sys.executable, str(read_router), "search", str(casefold_line.relative_to(target)), "--query", "target_near_end", "--ignore-case", "--context", "0"],
            target,
        )
        if casefold_result.returncode != 0 or "TARGET_NEAR_END" not in casefold_result.stdout:
            return fail("ignore-case search reports a Unicode match but hides the actual excerpt")
        utf16 = target / "docs" / "utf16.md"
        utf16.write_bytes("相关标记 UTF16_MARKER\n".encode("utf-16"))
        utf16_result = run(
            [sys.executable, str(read_router), "search", str(utf16.relative_to(target)), "--query", "UTF16_MARKER"],
            target,
        )
        if utf16_result.returncode == 0 or "请先转换为 UTF-8" not in utf16_result.stderr:
            return fail("read router silently searches unsupported UTF-16 text")
        slice_result = run(
            [sys.executable, str(read_router), "slice", str(article.relative_to(target)), "--start-line", "2", "--end-line", "4"],
            target,
        )
        if slice_result.returncode != 0 or "L2: context-a" not in slice_result.stdout or "L4: context-b" not in slice_result.stdout:
            return fail("read router slice does not return the requested line range")

        research_router = target / "docs" / "collaboration" / "scripts" / "agent_team_research.py"
        research_a = target / "docs" / "research-a.md"
        research_b = target / "docs" / "research-b.md"
        research_a.write_text(
            "# 高可用设计\n\n主节点不可用时自动切换到备用实例，确保服务持续可用。\n\n"
            "## 限制与反例\n\n如果备用实例也不可用，系统会显示降级提示而不是声称无中断。\n"
        )
        research_b.write_text("# 其他内容\n\n本文只介绍界面颜色和按钮间距。\n")
        candidate_result = run(
            [
                sys.executable, str(research_router), "candidates",
                "--task-id", "recall-demo",
                "--query", "系统故障如何保证服务不中断",
                "--expand", "备用实例",
                "--expand", "故障转移",
                "--path", "docs/research-a.md",
                "--path", "docs/research-b.md",
            ],
            target,
        )
        candidate_id_match = re.search(r"id=([0-9a-f]{16})", candidate_result.stdout)
        if candidate_result.returncode != 0 or candidate_id_match is None or "research-a.md" not in candidate_result.stdout:
            return fail("research retrieval does not create a recall-first candidate manifest")
        candidate_id = candidate_id_match.group(1)
        manifest_path = target / "docs" / "collaboration" / ".retrieval" / "recall-demo" / "manifest.json"
        initial_manifest = json.loads(manifest_path.read_text())
        initial_candidate_ids = {item["id"] for item in initial_manifest["candidates"]}
        pack_result = run(
            [
                sys.executable, str(research_router), "pack",
                "--task-id", "recall-demo", "--ids", candidate_id,
                "--target-tokens", "1200", "--max-output-bytes", "8192",
            ],
            target,
        )
        if pack_result.returncode != 0 or "自动切换到备用实例" not in pack_result.stdout or "estimated_tokens" not in pack_result.stdout:
            return fail("research retrieval cannot create a bounded evidence pack from candidate ids")
        coverage_result = run(
            [sys.executable, str(research_router), "coverage", "--task-id", "recall-demo"],
            target,
        )
        for needle in ("files_scanned: 2", "term_hits:", "candidate_limit_hit:", "unsupported_files:"):
            if coverage_result.returncode != 0 or needle not in coverage_result.stdout:
                return fail(f"research coverage report missing: {needle}")
        changed_query_result = run(
            [
                sys.executable, str(research_router), "candidates",
                "--task-id", "recall-demo", "--round", "2",
                "--query", "已被改写的问题", "--expand", "备用实例",
                "--path", "docs/research-a.md",
            ],
            target,
        )
        if changed_query_result.returncode == 0 or "原始 query 不变" not in changed_query_result.stderr:
            return fail("research supplement allows the original query to drift")
        supplement_result = run(
            [
                sys.executable, str(research_router), "candidates",
                "--task-id", "recall-demo", "--round", "2",
                "--query", "系统故障如何保证服务不中断",
                "--expand", "限制与反例",
                "--max-candidates", "1",
                "--path", "docs/research-a.md",
                "--path", "docs/research-b.md",
            ],
            target,
        )
        if supplement_result.returncode != 0 or "第二轮已完成" not in supplement_result.stdout:
            return fail("research retrieval does not support one controlled counter-evidence supplement")
        supplemented_manifest = json.loads(manifest_path.read_text())
        if not initial_candidate_ids.issubset({item["id"] for item in supplemented_manifest["candidates"]}):
            return fail("research supplement permanently deletes first-round candidates under a lower cap")
        if "限制与反例" not in supplemented_manifest["coverage"]["term_hits"]:
            return fail("research coverage does not accumulate second-round query evidence")
        repeated_supplement = run(
            [
                sys.executable, str(research_router), "candidates",
                "--task-id", "recall-demo", "--round", "2",
                "--query", "系统故障如何保证服务不中断", "--expand", "相反结论",
                "--path", "docs/research-a.md",
            ],
            target,
        )
        if repeated_supplement.returncode == 0 or "最多一次补检" not in repeated_supplement.stderr:
            return fail("research retrieval allows more than one supplement round")

        research_locale_env = os.environ.copy()
        research_locale_env.update({"LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"})
        research_locale_result = subprocess.run(
            [
                sys.executable, str(research_router), "candidates",
                "--task-id", "locale-research", "--query", "备用实例",
                "--path", "docs/research-a.md",
            ],
            cwd=target, text=True, capture_output=True, check=False, env=research_locale_env,
        )
        if research_locale_result.returncode != 0 or "research-a.md" not in research_locale_result.stdout:
            return fail("generated research router is not stable under a non-UTF-8 process locale")

        outside_dept = target / "docs" / "outside-dept"
        outside_dept.mkdir()
        (outside_dept / "收件箱.md").write_text("## [待办] OUTSIDE_TASK_SECRET\n")
        registry_path = target / "docs" / "collaboration" / "部门表.md"
        original_registry = registry_path.read_text()
        registry_path.write_text(
            original_registry + "\n| 执行层 | ../../outside-dept | `evil` | 待登记 | 人工 | probe | none | none | 待启用 |\n"
        )
        traversal_result = run([sys.executable, str(read_router), "onboard", "--dept", "evil"], target)
        registry_path.write_text(original_registry)
        if traversal_result.returncode == 0 or "OUTSIDE_TASK_SECRET" in traversal_result.stdout:
            return fail("read router onboard trusts a path-traversal department name")

        add_role_result = run([sys.executable, str(SCAFFOLD), str(target), "--add-roles", "auto"], ROOT)
        if add_role_result.returncode != 0:
            return fail("transactional add-roles failed on a valid collaboration layer")
        if not (target / "docs" / "collaboration" / "部门" / "自动化部" / "岗位说明.md").is_file():
            return fail("transactional add-roles did not create the requested department")
        if "`auto`" not in registry_path.read_text() or "自动化部 (`auto`)" not in (target / "docs" / "collaboration" / "会话启动清单.md").read_text():
            return fail("transactional add-roles did not update registry/startup routing")

        all_text = "\n".join(
            path.read_text() for path in (target / "docs" / "collaboration").rglob("*.md")
            if path.is_file() and not path.is_symlink()
        )
        all_text += "\n" + (ROOT / "SKILL.md").read_text()
        all_text += "\n" + (target / "docs" / "agent-guide.md").read_text()
        for needle in FORBIDDEN:
            if needle in all_text:
                return fail(f"forbidden legacy wording remains: {needle}")
        for needle in OPENDESIGN_FAILURE_MATRIX:
            if needle not in all_text:
                return fail(f"OpenDesign failure matrix missing required text: {needle}")

        pressure_text = (ROOT / "tests" / "pressure_scenarios.md").read_text()
        for needle in PRESSURE_SCENARIO_REQUIRED:
            if needle not in pressure_text:
                return fail(f"pressure scenarios missing required text: {needle}")

        vibe_skill = ROOT.parent / "vibe-project-foundation" / "SKILL.md"
        if vibe_skill.exists():
            vibe_text = vibe_skill.read_text()
            for needle in (
                "适用性硬闸",
                "只适用于全新、长期维护的可运行软件 / 互联网产品 / Vibe Coding 项目",
                "不得创建 `app/`、`design/`、`docs/spec.md` 这套软件地基",
                "转用对应业务地基 / 现有目录结构 / `agent-team` 的最小业务地基",
            ):
                if needle not in vibe_text:
                    return fail(f"vibe-project-foundation applicability gate missing required text: {needle}")
            vibe_template_requirements = {
                "templates/docs/agent-guide.md": [
                    "OpenDesign 没有 active project",
                    "要求用户在 OpenDesign 内创建或点进项目",
                    "用户不想处理 OpenDesign",
                    "本地 HTML + PNG",
                    "Figma",
                    "可打开图片预览",
                ],
                "templates/design/README.md": [
                    "无 active project",
                    "没有 active project",
                    "创建或点进项目",
                    "用户不想处理 OpenDesign",
                    "兜底预览",
                ],
                "templates/design/ui/README.md": [
                    "无 active project",
                    "没有 active project",
                    "创建或点进项目",
                    "用户不想处理 OpenDesign",
                    "兜底预览",
                ],
            }
            for relative, needles in vibe_template_requirements.items():
                path = vibe_skill.parent / relative
                if not path.exists():
                    return fail(f"vibe-project-foundation template missing: {relative}")
                text = path.read_text()
                for needle in needles:
                    if needle not in text:
                        return fail(f"vibe-project-foundation {relative} missing required text: {needle}")

        print("PASS: agent-team generated docs preserve node-gate protocol")
        return 0
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
