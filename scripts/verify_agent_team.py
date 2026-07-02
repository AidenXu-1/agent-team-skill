#!/usr/bin/env python3
"""Verify generated agent-team collaboration docs preserve the node-gate protocol."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCAFFOLD = ROOT / "scripts" / "scaffold_team.py"

ROLES = "lead,research,planning,do,product,design,dev,growth,test,security,finance"

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
        "非软件常见盘 = `lead,research,planning,do,review`",
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
        "不得默认互联网产品开发",
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
        "def project_path",
        "path.relative_to(PROJECT)",
        "def cmd_onboard",
        "def cmd_meta",
        "def cmd_find",
        'with path.open("r", encoding="utf-8", errors="replace") as handle',
        "frontmatter",
        "默认不读",
        "触发才读正文",
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
        "不得默认互联网产品开发",
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
        "脚本只返回",
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
]


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)


def fail(message: str) -> int:
    print(f"FAIL: {message}", file=sys.stderr)
    return 1


def main() -> int:
    compile_result = run([sys.executable, "-m", "py_compile", str(SCAFFOLD)], ROOT)
    if compile_result.returncode != 0:
        print(compile_result.stderr, file=sys.stderr)
        return fail("scaffold_team.py does not compile")

    temp_root = Path(tempfile.mkdtemp(prefix="agent-team-verify-"))
    try:
        target = temp_root / "project"
        (target / "docs").mkdir(parents=True)
        (target / "docs" / "spec.md").write_text("# Spec\n")

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
        (duplicate_target / "docs" / "spec.md").write_text("# Spec\n")
        missing_layer_target = temp_root / "missing-layer-project"
        (missing_layer_target / "docs").mkdir(parents=True)
        (missing_layer_target / "docs" / "spec.md").write_text("# Spec\n")
        unconfirmed_mode_target = temp_root / "unconfirmed-mode-project"
        unconfirmed_mode_target.mkdir()
        invalid_mode_target = temp_root / "invalid-mode-project"
        invalid_mode_target.mkdir()
        missing_foundation_target = temp_root / "missing-foundation-project"
        missing_foundation_target.mkdir()
        allow_only_target = temp_root / "allow-only-project"
        allow_only_target.mkdir()
        minimal_foundation_target = temp_root / "minimal-foundation-project"
        minimal_foundation_target.mkdir()

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
            "本次必读:",
            "默认不读:",
            "日志正文",
            "报告正文",
            "触发才读正文:",
            "无结构化待办标题",
        ):
            if needle not in onboard_result.stdout:
                return fail(f"read router onboard output missing: {needle}")

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
        if outside_meta_result.returncode == 0 or "路径超出项目范围" not in outside_meta_result.stderr:
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
        if malformed_meta_result.returncode == 0 or "无 frontmatter" not in malformed_meta_result.stdout:
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
        if partial_meta_result.returncode == 0 or "无 frontmatter" not in partial_meta_result.stdout:
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

        all_text = "\n".join(path.read_text() for path in (target / "docs" / "collaboration").rglob("*.md"))
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
