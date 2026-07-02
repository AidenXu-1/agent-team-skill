#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为项目创建多会话协作层(按部门组织,三层框架:管理 / 执行 / 审核)。"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path


# 三层框架:management(管理层) / execution(执行层) / audit(审核层)
LAYER_CN = {
    "management": "管理层",
    "execution": "执行层",
    "audit": "审核层",
}


# 角色库:每个角色带 layer 字段。最小盘默认 lead,do,review(每层各一个)。
ROLE_DEFS = {
    # ============ 管理层 ============
    "lead": {
        "name": "统筹部",
        "layer": "management",
        "mission": "判断阶段、拆分验收节点、把任务写入部门收件箱、维护项目总进度与跨部门沟通;派单时强制写清验收出口和必测失败路径;读取统筹部收件箱中的结构化回报,核验日志收据存在,按三类节点判断:必须用户确认 / 可自主推进 / 可自主推进但必须汇报,并用节点卡向用户汇报。产品感知和重大边界让用户拍板;流程性、技术性、无争议调度由统筹部专业推进。收到 UI / 交互 / 视觉 / 页面布局 / 用户体验路径类设计回报时,先展示设计意图预览,再给用户成果 / 判断点 / 建议 / 风险 / 下一步短节点卡;不得把设计预览说成真实 App UI。用户已体验 OK 后,测试部发现纯代码 / 质量 / 异常路径问题时,先向用户同步节点卡,随后可自主派开发部返工;涉及体验取舍、范围变化、成本/安全/发布、方案选择或重大事项时才停下等用户确认。",
        "not_responsible": "不亲自做执行层的活;不替审核层做单项把关(把关由各审核部门做,它只做总汇总);不自动对外放行;不把建议下一步当成用户已同意;不在产品感知、功能取舍、设计判断、重大风险节点替用户拍板;默认不读部门产出正文、长日志、测试证据全文或代码 diff;不把纯文字设计说明当作可视化设计确认;不把功能方向 OK 当成视觉/交互已通过;派单缺验收出口或必测失败路径时,不得要求接收部门自行脑补。",
        "inputs": "项目目标, 统筹部收件箱回报, 日志收据, 验收出口, 必测失败路径, 必要的项目总进度;异常时才读取最小必要正文",
        "outputs": "派给各部门的任务(写进对方收件箱,含验收出口和必测失败路径), 三类节点卡汇报, 项目总进度汇总, 三关汇总后的放行建议",
        "can_write": "项目总进度文档, 部门表.md, 各部门收件箱(仅派发任务)",
        "cannot_write": "各部门的产出物, 其他部门岗位边界, 不替审核层改把关结论",
        "confirm": "产品体验、用户感知、功能取舍、界面设计、交互流程、视觉呈现、设计可视化预览、MVP 边界、产品路线、上线发布、外发交付、成本明显增加、隐私/安全/云端/密钥/授权风险, 以及大阶段收口或对外放行",
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
        "can_write": "docs/overview.md 方案/流程小节, deliverables/ 或业务计划目录, 本部门记忆文件;阶段计划建议写入统筹部收件箱,由统筹部汇总到项目总进度",
        "cannot_write": "未经确认的最终范围, 其他部门产出物, 与业务无关的软件目录",
        "confirm": "方案定稿, 范围变化, 排期/预算变化, 新增关键交付物或删减核心交付物",
    },
    "product": {
        "name": "产品部",
        "layer": "execution",
        "mission": "定需求、做产品方案与架构、排优先级、写并维护 docs/spec.md、冻结 MVP;必要时做技术选型实验并据结果定方案;把上线反馈转化为下一轮迭代需求。",
        "not_responsible": "不画最终视觉;不写代码;不替审核层做验收;发现实现/方案落地问题→经统筹转对应部门。",
        "inputs": "用户需求, docs/overview.md, docs/roadmap.md, 统筹部提供的项目进度摘要, 上线反馈",
        "outputs": "docs/spec.md, 产品方案/架构, 优先级排序, MVP 边界, 迭代需求",
        "can_write": "docs/spec.md, docs/mvp.md, docs/overview.md",
        "cannot_write": "app/ 代码, design/ 定稿视觉, docs/decisions/ 技术决策定稿",
        "confirm": "Spec v1 冻结, MVP 范围变化, 删除核心功能, 涉及隐私/付款/授权",
    },
    "design": {
        "name": "设计部",
        "layer": "execution",
        "mission": "把 spec 转成设计规范、界面布局、交互流程、视觉规范,打磨体验。凡涉及 UI、交互、视觉呈现、页面布局、设计稿、用户体验路径的节点,必须提供用户可直接判断的设计意图预览;优先使用 OpenDesign 等专用设计工具生成可编辑 artifact。设计意图预览不得声称等同真实 App UI;真实 UI 验收以运行中的 App / 真实路由 / 构建或打包态截图为准。未安装 / 未运行 OpenDesign、MCP 未热加载、无 active project、权限不足或连接失败时,主动询问用户是否需要帮忙安装 / 启动 / 授权 / 注册 MCP / 重载或新开会话;用户不想处理 OpenDesign 时,按用户偏好直接用本地 HTML + PNG 截图、Figma 或可打开图片兜底。",
        "not_responsible": "不定义需求;不写代码;不擅自增删功能;发现需求问题→经统筹转产品部;不得因为能使用 OpenDesign 就扩大需求范围、做完整 UI 重设计、品牌升级或开发实现;不得只交文字说明、ASCII 线框、Markdown 表格或抽象结论。",
        "inputs": "docs/spec.md, design/references/, 用户审美偏好",
        "outputs": "design/ui/, design/references/, 设计规范, 页面状态清单, 设计意图预览路径(OpenDesign artifact / 本地 HTML / PNG 截图 / Figma / 可打开图片), OpenDesign 状态说明(如使用兜底方案)",
        "can_write": "design/, docs/spec.md 中明确的设计小节(需说明)",
        "cannot_write": "app/ 业务逻辑, docs/decisions/ 技术决策, docs/spec.md 的 MVP 范围",
        "confirm": "视觉方向定稿, 交互方向定稿, 页面流程大改, 增加新页面或新主流程;用户只确认功能方向 OK 但 UI 未确认时,不能视为设计通过",
    },
    "dev": {
        "name": "开发部",
        "layer": "execution",
        "mission": "依据 spec 和设计稿实现功能(前后端一体)、对接 API、技术落地、自测后交付。",
        "not_responsible": "不改需求和设计;不做最终质量背书;发现方案问题→经统筹转产品部。",
        "inputs": "docs/spec.md, docs/decisions/, docs/conventions.md, design/ 已确认材料",
        "outputs": "app/, 自测结果, 技术实现说明, commit",
        "can_write": "app/, docs/conventions.md, 必要时 docs/decisions/",
        "cannot_write": "未经确认的大范围 docs/spec.md 改动, design/ 定稿, 其他部门岗位边界",
        "confirm": "新增依赖, 改技术栈, 改认证/权限/支付/密钥, 删除数据, 大重构",
    },
    "data": {
        "name": "数据部",
        "layer": "execution",
        "mission": "处理数据来源、采集、清洗、字段定义、导入导出和数据质量。",
        "not_responsible": "不写 UI;不绕过安全部评估的平台风险采集;不擅自处理敏感数据。",
        "inputs": "docs/overview.md 或 docs/spec.md, 数据样例, 平台规则, 用户提供的数据文件",
        "outputs": "数据字段说明, 数据质量检查, 导入导出方案",
        "can_write": "docs/overview.md 数据小节, research/ 或 data/ 数据说明, scratch/ 数据实验",
        "cannot_write": "正式实现代码(除非作为开发任务), 未脱敏敏感数据",
        "confirm": "采集个人信息, 使用外部数据源, 保存敏感字段, 变更核心数据结构",
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
        "can_write": "docs/overview.md 指标/运营小节, operations/ 运营方案;运营进度建议写入统筹部收件箱,由统筹部汇总到项目总进度",
        "cannot_write": "与业务无关的软件代码, 未确认的对外发布材料",
        "confirm": "商业化方案, 对外承诺, 增长实验上线",
    },
    # ============ 审核层(把关层,三维度:质量 / 风险 / 成本,≥1) ============
    "review": {
        "name": "检验部",
        "layer": "audit",
        "mission": "独立把关执行层成果(质量关):在统筹部确认可进入把关后亲自检验/运行,凭自己实际观察到的结果判断是否符合要求、是否真的可用、有无明显风险——不靠执行部转述的结论。审核层独立不等于盲审充分,必须覆盖派单验收出口和必测失败路径;凡涉及用户看到/提示/错误文案/进度/状态/弹窗/结果摘要/导出文件名/打包态窗口,必须测到 worker/UI/用户最终出口,只测 engine/API/helper 层不算通过。每个关键风险至少自设计一个反向探针。团队未单独拆出安全部/财务部时,兼做风险与成本的轻量把关。",
        "not_responsible": "不替执行部做事;不继承执行部的长上下文;不为了通过而降低标准;不把执行部说的“已完成/已验证”当作证据;不只沿执行部门 happy path 重跑一遍;不因底层 engine/API 通过就判定用户可见出口通过。",
        "inputs": "验收标准, 验收出口, 必测失败路径, 成果, 复现/检验方式, 变更摘要(仅用于定位,不作为通过依据)",
        "outputs": "把关报告/ 下的报告(必须附自己检验的证据:实际输出/结果/截图/复现步骤,并写明验证层级、用户可见出口、自设计反向探针、未覆盖层级、是否触发子 Agent 盲审/抽检), 问题清单, 是否通过建议",
        "can_write": "把关报告/",
        "cannot_write": "执行部的产出物(除非用户明确授权), 验收标准本身",
        "confirm": "是否允许直接修复, 是否通过把关, 是否进入下一步;结论写回统筹部后必须等待用户确认",
    },
    "test": {
        "name": "测试部",
        "layer": "audit",
        "mission": "质量关:只在用户明确确认“体验 OK / 可以进测试”后介入,依据 spec/方案检测执行层产出——代码相关验证、功能回归、异常场景、打包、日志和边界情况。亲自运行,出测试报告;结论回统筹部,不直接触发返工或放行;由统筹部节点卡同步后判断是否可自主派开发返工。测试必须覆盖派单验收出口和必测失败路径;凡涉及用户看到/提示/错误文案/进度/状态/弹窗/结果摘要/导出文件名/打包态窗口,必须测到 worker/UI/用户最终出口,不能只测 engine/API/helper 层。每个关键风险至少自设计一个反向探针。",
        "not_responsible": "不代替用户体验功能;不判断是否顺手、是否符合用户预期;只判专业质量这一关,不碰安全合规与成本;不改代码;不采信开发部转述的“已通过”;不只沿开发部 happy path 重跑一遍;不因底层 engine/API 通过就判定用户可见出口通过。",
        "inputs": "docs/spec.md, 验收标准, 验收出口, 必测失败路径, 可运行的产出, 复现方式, 变更摘要(仅用于定位)",
        "outputs": "把关报告/ 测试报告(附自己跑出的证据:实际输出/测试结果/截图/复现步骤,并写明验证层级、用户可见出口、自设计反向探针、未覆盖层级、是否触发子 Agent 盲审/抽检), bug 清单, 是否通过建议",
        "can_write": "把关报告/",
        "cannot_write": "app/ 代码, 验收标准本身",
        "confirm": "用户明确确认体验 OK / 可以进测试后才开始;测试结论出来后只回统筹部,不得直接返工或放行;涉及体验取舍、范围变化、成本/安全/发布、方案选择或重大事项时由统筹部请用户确认",
    },
    "security": {
        "name": "安全部",
        "layer": "audit",
        "mission": "风险关:在大阶段完成、上线或外发前,或涉及隐私、上传、权限、密钥、授权、第三方平台、生产配置等风险时介入,评估数据/法务/合规/第三方平台(封号、授权、费用、频率)/认证权限/密钥/隐私/生产配置等风险,出风险报告与合规清单;结论回统筹部,不自动触发返工或放行。",
        "not_responsible": "不判功能 bug;不评估成本是否划算(平台费用是否值归财务);不实现业务功能;不保存密钥;不替用户授权;不降低安全要求换速度。",
        "inputs": "docs/spec.md, docs/decisions/, 第三方平台文档, 环境变量示例, 权限/授权设计",
        "outputs": "把关报告/ 风险报告 + 合规清单, 可做/不可做边界, 替代方案",
        "can_write": "把关报告/, docs/decisions/ 安全/平台相关 ADR, docs/spec.md 风险小节",
        "cannot_write": ".env 真值, 生产配置, app/ 代码(除非用户授权), 账号凭证",
        "confirm": "处理敏感数据, 上线生产, 改权限/认证/密钥/支付, 涉及登录态/授权/爬取/批量操作",
    },
    "finance": {
        "name": "财务部",
        "layer": "audit",
        "mission": "成本关:在成本核算、成本影响中大的功能规划、MVP 或第二版上线前、大功能板块完成时介入,评估和计算各环节成本;超支或成本过高时主动预警、给降本建议,并经统筹上报用户。成本只监控,不自动卡死发布。",
        "not_responsible": "不碰技术质量与安全;不替用户做最终花钱决定;不自动阻断发布(只预警+上报,花钱由用户拍板)。",
        "inputs": "docs/spec.md, 方案/技术选型, 第三方费用与计费规则, 预算上限",
        "outputs": "把关报告/ 成本测算与预算追踪, 超支预警与降本建议",
        "can_write": "把关报告/, docs/spec.md 成本小节",
        "cannot_write": "app/ 代码, 账号凭证, 未经用户确认的付费动作",
        "confirm": "超出预算阈值(预警上报), 引入付费项, 重大成本结构变化",
    },
}


def md_escape(text: str) -> str:
    return text.replace("\n", " ").strip()


def iso_week_info(today: dt.date) -> tuple[str, str, str]:
    """返回 (ISO周标签, 本周一, 本周日)。"""
    iso_year, iso_week, _ = today.isocalendar()
    monday = today - dt.timedelta(days=today.weekday())
    sunday = monday + dt.timedelta(days=6)
    return f"{iso_year}-W{iso_week:02d}", monday.isoformat(), sunday.isoformat()


def read_router_script() -> str:
    return r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent-team 读取路由器。

只做确定性裁剪:读部门路由、列必读文件、提取 Markdown frontmatter。
不做创造性总结、不替代统筹判断、不替代审核结论、不自动放行。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


COLLAB = Path(__file__).resolve().parents[1]
PROJECT = COLLAB.parents[1]
MAX_FRONTMATTER_LINES = 200
MAX_FRONTMATTER_BYTES = 32768


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def parse_table_row(line: str) -> list[str]:
    return [part.strip().strip("`") for part in line.strip().strip("|").split("|")]


def departments() -> list[dict[str, str]]:
    registry = COLLAB / "部门表.md"
    if not registry.exists():
        return []
    rows: list[dict[str, str]] = []
    for line in read_text(registry).splitlines():
        if not line.startswith("|") or "---" in line or "角色 ID" in line:
            continue
        parts = parse_table_row(line)
        if len(parts) < 9:
            continue
        rows.append({
            "layer": parts[0],
            "department": parts[1],
            "role_id": parts[2],
            "thread_id": parts[3],
            "notify_mode": parts[4],
            "mission": parts[5],
            "can_write": parts[6],
            "cannot_write": parts[7],
            "status": parts[8],
        })
    return rows


def find_department(name: str) -> dict[str, str] | None:
    for row in departments():
        if row["department"] == name or row["role_id"] == name:
            return row
    return None


def frontmatter(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        first = handle.readline()
        if first != "---\n":
            return {}
        lines: list[str] = []
        bytes_seen = len(first.encode("utf-8"))
        for raw in handle:
            bytes_seen += len(raw.encode("utf-8"))
            if len(lines) >= MAX_FRONTMATTER_LINES or bytes_seen > MAX_FRONTMATTER_BYTES:
                return {}
            if raw.strip() == "---":
                break
            lines.append(raw.rstrip("\n"))
        else:
            return {}
    if not lines:
        return {}
    meta: dict[str, str] = {}
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in raw or stripped.startswith("-"):
            return {}
        key, value = raw.split(":", 1)
        if not key.strip():
            return {}
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta


def metadata_files() -> list[Path]:
    roots = [
        COLLAB / "专项结论",
        COLLAB / "部门",
        PROJECT / "docs" / "decisions",
    ]
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(path for path in root.rglob("*.md") if path.name != "README.md")
    return sorted(files)


def yaml_list(value: str) -> list[str]:
    stripped = value.strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return [stripped] if stripped else []
    inner = stripped[1:-1].strip()
    if not inner:
        return []
    return [item.strip().strip('"').strip("'") for item in inner.split(",") if item.strip()]


def field_matches(meta: dict[str, str], key: str, expected: str | None) -> bool:
    if not expected:
        return True
    return meta.get(key, "") == expected


def tag_matches(meta: dict[str, str], expected: str | None) -> bool:
    if not expected:
        return True
    return expected in yaml_list(meta.get("tags", ""))


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT))
    except ValueError:
        return str(path)


def project_path(raw_path: str) -> Path | None:
    path = (PROJECT / raw_path).resolve()
    try:
        path.relative_to(PROJECT)
    except ValueError:
        return None
    return path


def pending_titles(inbox: Path) -> list[str]:
    if not inbox.exists():
        return []
    titles = []
    in_comment = False
    for line in read_text(inbox).splitlines():
        stripped = line.strip()
        if stripped.startswith("<!--"):
            in_comment = True
        if not in_comment and (line.startswith("## [待办]") or line.startswith("## [紧急]")):
            titles.append(line.lstrip("# ").strip())
        if stripped.endswith("-->"):
            in_comment = False
    return titles


def cmd_onboard(args: argparse.Namespace) -> int:
    row = find_department(args.dept)
    if row is None:
        print(f"未在部门表找到部门: {args.dept}", file=sys.stderr)
        return 2
    dept_dir = COLLAB / "部门" / row["department"]
    required = [
        dept_dir / "岗位说明.md",
        dept_dir / "交接班文档.md",
        dept_dir / "收件箱.md",
        COLLAB / "错题集.md",
    ]
    if row["layer"] == "管理层":
        required.append(PROJECT / "docs" / "progress.md")
    print(f"你是: {row['department']}")
    print(f"层级: {row['layer']}")
    print(f"角色ID: {row['role_id']}")
    print(f"会话ID: {row['thread_id']}")
    print(f"通知模式: {row['notify_mode']}")
    print("\n本次必读:")
    for path in required:
        print(f"- {rel(path)}")
    print("\n默认不读:")
    for item in ("日志正文", "报告正文", "决策正文", "其他部门正文", "代码 diff", "测试证据全文"):
        print(f"- {item}")
    print("\n触发才读正文:")
    print("- 摘要不足 / 路径异常 / 结论冲突 / 涉及放行、返工、安全、费用、发布、用户可见质量 / 用户要求查证据 / 当前任务明确依赖正文")
    titles = pending_titles(dept_dir / "收件箱.md")
    print("\n当前待办标题:")
    if titles:
        for title in titles:
            print(f"- {title}")
    else:
        print("- 无结构化待办标题")
    return 0


def cmd_meta(args: argparse.Namespace) -> int:
    path = project_path(args.path)
    if path is None:
        print(f"路径超出项目范围: {args.path}", file=sys.stderr)
        return 2
    if not path.exists():
        print(f"文件不存在: {args.path}", file=sys.stderr)
        return 2
    meta = frontmatter(path)
    if not meta:
        print("无 frontmatter")
        return 1
    for key in sorted(meta):
        print(f"{key}: {meta[key]}")
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    found = 0
    for path in metadata_files():
        meta = frontmatter(path)
        if not meta:
            continue
        checks = {
            "type": args.type,
            "department": args.department,
            "target": args.target,
            "status": args.status,
        }
        matched = True
        for key, expected in checks.items():
            if not field_matches(meta, key, expected):
                matched = False
                break
        if not tag_matches(meta, args.tag):
            matched = False
        if not matched:
            continue
        found += 1
        print(f"- {rel(path)}")
        for key in ("type", "department", "target", "status", "decision", "tags", "summary"):
            if key in meta:
                print(f"  {key}: {meta[key]}")
    if found == 0:
        print("未找到匹配的元数据文件")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="agent-team 读取路由器")
    sub = parser.add_subparsers(dest="cmd", required=True)
    onboard = sub.add_parser("onboard", help="返回部门身份、必读文件和默认阅读边界")
    onboard.add_argument("--dept", required=True)
    onboard.set_defaults(func=cmd_onboard)
    meta = sub.add_parser("meta", help="只读取一个 Markdown 文件的 frontmatter")
    meta.add_argument("path")
    meta.set_defaults(func=cmd_meta)
    find = sub.add_parser("find", help="按 frontmatter 查报告、审核报告、专项结论、决策记录")
    find.add_argument("--type")
    find.add_argument("--department")
    find.add_argument("--target")
    find.add_argument("--status")
    find.add_argument("--tag")
    find.set_defaults(func=cmd_find)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
'''


# ---- 每部门文件(在 部门/<部门名>/ 下) -------------------------------------

def role_markdown(key: str, role: dict[str, str], date: str) -> str:
    layer_cn = LAYER_CN.get(role.get("layer", ""), "")
    return f"""# {role['name']}岗位说明

> 角色 ID:`{key}` ·所在层:{layer_cn} ·创建日期:{date}

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

## 必须请用户确认的节点

{role['confirm']}

## 节点式推进与确认闸

- 每个功能 / 环节都必须拆成明确的验收节点;一个节点完成后,执行层和审核层先停止推进并把结果回到统筹部。
- 统筹部收到回报后按三类节点判断:必须用户确认 / 可自主推进 / 可自主推进但必须汇报。
- 产品感知、功能取舍、设计判断、重大风险和大阶段收口必须用户确认;流程性、技术性、无争议调度可由统筹部自主推进。
- "建议下一步"只能作为建议,不能默认视为用户已同意;统筹自主推进也必须保留简短汇报。
- 没有明确写"用户已确认"或"统筹已按三类节点判断可自主推进"的任务,接收部门应暂停并回统筹部核对。
- 节点状态只使用:待用户体验 / 待设计视觉确认 / 设计视觉通过 / 用户体验通过 / 用户要求返工 / 可进入测试 / 测试通过 / 测试不通过 / 可进入下一节点 / 用户已确认放行。

## 统筹部三类节点决策

- **必须用户确认**:影响产品体验、用户感知、功能取舍、界面设计、交互流程、视觉呈现、MVP 边界、产品路线、上线发布、外发交付、成本明显增加、隐私/安全/云端/密钥/授权风险。
- **统筹可以自主推进**:用户不适合判断、且不改变产品体验和重大边界的流程性 / 技术性节点,如边界确认后派设计草案、设计草案后派开发评估、设计视觉 / 交互已确认且技术评估风险可控时派开发正式实现、用户体验 OK 后派测试质量关、测试发现纯代码 / 质量 / 异常路径问题后派开发返工、日志/交接/共享错题集/进度记录/轻量验证/用户确认收口后的 commit 存档;UI 未确认前只能推进实现评估 / 技术可行性。
- **可以自主推进但必须汇报**:开发评估完成且风险可控、体验 OK 后已派测试、测试发现代码层问题且已派开发返工、测试无 P0/P1/P2 阻断准备收口、安全/财务本节点未触发等,用简短节点卡告诉用户“已推进到哪一步”。
- **自主推进停止条件**:结论明显不确定、部门判断冲突、需要牺牲体验/范围/成本/速度、要新增依赖/云端/联网/模型/成本、改变用户已确认方向、进入可运行功能体验、UI 视觉确认、发布/打包/外发、大阶段收口。

## 验收出口、失败路径与反向探针

- 统筹部派单必须写清 `验收出口` 和 `必测失败路径`;缺失时接收部门应回统筹部补齐,不得自行脑补。
- `验收出口` 指用户最终在哪里看到结果 / 提示 / 错误 / 状态 / 弹窗 / 结果摘要 / 导出文件名 / 打包态窗口。凡涉及用户可见内容,不得只写 engine / API / helper 层。
- `必测失败路径` 至少列 1-3 个打破 happy path 的失败、异常或边界场景。
- 审核层独立不等于盲审充分;审核部门不能只沿执行部门 happy path 重跑一遍。
- 凡是用户看到 / 提示 / 错误文案 / 进度 / 状态 / 弹窗 / 结果摘要 / 导出文件名 / 打包态窗口的验收,必须测到 worker / UI / 用户最终出口;只测底层不算通过。
- 每个关键风险至少有一个自设计反向探针。
- 审核/测试报告必须写明:验证层级(engine / adapter-service / worker-后台任务 / UI-用户可见出口 / 打包态 / 未覆盖层级)、用户可见出口、自设计反向探针、未覆盖层级、是否触发子 Agent 盲审 / 抽检。
- 盲审/抽检触发条件:同一功能链连续多轮无阻断通过(默认 3 轮,可解释调整)、链路跨 engine→worker→UI→用户出口、涉及错误文案/状态/发布/打包/安全/费用等高风险、用户或统筹感觉结论依据不足。
- 触发后子 Agent 只做盲审/抽检结论回报,不直接改代码、不自动放行。

## 设计可视化确认节点

- 凡涉及 UI、交互、视觉呈现、页面布局、设计稿、用户体验路径的节点,设计部必须提供用户可直接判断的设计意图预览,不能只交文字说明、ASCII 线框、Markdown 表格或抽象结论。
- 设计意图预览用于判断方向、布局、信息层级和交互感觉,不得声称等同真实 App UI;真实 UI 验收必须来自运行中的 App / 真实路由 / 构建或打包态截图。
- 设计部优先使用 OpenDesign 等专用设计工具生成可编辑设计产物或 artifact;如果当前会话未热加载 OpenDesign MCP、OpenDesign 没有 active project、权限不足、工具连接失败,必须明确说明失败原因。
- OpenDesign 不可用时,设计部必须用本地 HTML + PNG 截图、Figma、可打开图片预览等方式兜底,保证用户看到设计意图,但不得承诺这就是最终真实 UI。
- 设计部回报四件套中的产出路径必须同时包含设计说明文档路径和设计意图预览路径;若使用兜底方案,必须写清 OpenDesign 当前状态和后续恢复条件。
- OpenDesign 接入顺序:先确认本机 OpenDesign App 是否运行,再确认 daemon 健康状态;不要假设默认端口一定是 7456,应从 OpenDesign 日志或本机监听端口确认实际 daemon URL;确认后注册 open-design MCP。当前会话可能无法热加载新 MCP,必要时提示用户重载 / 新开会话。
- 若 OpenDesign artifact 写入提示没有 active project,要求用户在 OpenDesign 内创建或点进项目,或改用本地 HTML / PNG 兜底交付,不能卡住节点。
- 若发现未安装 / 未运行 OpenDesign、权限不足、连接失败或 MCP 未热加载,设计部必须主动询问用户是否需要帮忙安装 / 启动 / 授权 / 注册 MCP / 重载或新开会话;用户不想处理 OpenDesign 或不愿意重载 / 新开会话时,不得卡住,应按用户偏好直接选择本地 HTML + PNG、Figma 或可打开图片预览。
- 最小排障顺序:查 App 是否运行 → 查监听端口或日志里的 daemon URL → 查 MCP 是否已注册 / 当前会话是否已热加载 → 查 active project → 查权限 / 连接错误;任一步失败都同时给兜底预览方案。
- 统筹部收到设计回报后,先展示设计意图预览,再给用户成果 / 判断点 / 建议 / 风险 / 下一步短节点卡,不要把长技术说明或设计正文直接丢给用户判断。
- 用户确认设计视觉或交互方向前,统筹部不得派开发部进入正式实现;如果用户只反馈功能方向 OK 但 UI 未确认,只能推进功能可行性或技术评估。功能方向 OK 不等于 UI 通过;开发完成后的 UI 通过判断必须回到真实 App / 真实路由 / 构建或打包态截图。
- OpenDesign 只是增强设计表达和可视化确认能力,不代表自动进入完整 UI 重设计、品牌升级或开发实现。

## App 体验节点与专业验证分工

- 可运行功能完成后,先交给用户体验;进入“需要用户体验 App”的节点时,统筹部默认直接帮用户打开 App,不先问“要不要打开”。
- 如果当前环境能启动 / 打开 App,统筹部直接打开;如果启动失败,说明失败原因、已尝试命令和用户可手动打开的入口。
- 统筹部必须给用户一张短体验卡,格式固定为:入口 / 重点 / 建议试法 / 判断口径。
- 体验卡里的“建议试法”给 2-4 个具体操作;“判断口径”只要求用户回复“体验 OK”或指出哪里不顺。
- 用户只判断体验是否顺手、是否符合预期、流程是否对;用户不负责代码质量、异常覆盖、打包、隐私、安全、成本等专业验证。
- 用户体验不通过时,先按体验反馈返工;用户明确确认"体验 OK / 可以进测试"后,测试部才介入专业质量关。

## 完成回报四件套

- 部门完成节点后,必须把 `[回报]` 写入统筹部收件箱,并包含四件套:产出路径、验证结果(含未验证项)、日志收据、错题自检。
- 设计节点的产出路径必须同时包含设计说明文档路径和设计意图预览路径;只给文字说明或表格时,统筹部不得视为完整回报。
- 日志收据只是一条可机器式核验的归档索引,格式包含:日志文件、节点ID、索引行。部门自己负责日志内容质量;统筹部只核验索引存在和可倒查。
- 错题自检必须说明已检查哪些相关错题、是否命中、如何处理。`../../错题集.md` 是跨部门共享错题集,只收会让多个部门反复踩坑的流程错误;部门局部问题写本部门日志或交接,不另建部门错题集。
- 四件套缺任一项,统筹部不得视为完整回报,不得进入下一环节。

## 统筹部读取边界

- 统筹部的主入口是统筹部收件箱,不是各部门正文。统筹部根据收件箱回报写节点卡,只核验日志收据存在。
- 默认不得读取部门产出正文、完整日志、测试证据全文或代码 diff,以保护多会话隔离。
- 只有收件箱回报不足、日志收据不存在/指针错误、多部门结论冲突、或用户明确要求复核正文时,才读取最小必要范围,优先只读结论 / 验证 / 风险三段。

## 跨部门流转规则(混合路由)

- **澄清类直连**:不改任何已确认产物、不下裁决、不推进状态、问清就能继续干的事(如“这个字段能为空吗?”),直接写对方 `收件箱.md`,并发一句短唤醒。
- **要改东西 / 要裁决 / 要变状态 → 经统筹部**:返工、放行、进入下一阶段、改变需求或设计范围、审核结论、阻断、是否上线、状态升级、增删部门。
- 口诀:**“只问一句、不改东西” → 直连;“要改 / 要裁决 / 要变状态” → 经统筹。**
- 通知分两种:
  - 自动模式:本部门在 `../../部门表.md` 的通知模式为"自动",后续默认直接调用会话发送工具发一句短唤醒。
  - 人工模式:本部门通知模式为"人工",写完收件箱后默认直接给用户可复制的短唤醒,请用户手动通知目标部门查看收件箱。
- 通知能力只在上岗/接班时登记一次;若工具能力后来变化,请用户通知统筹部更新 `../../部门表.md`,不要每次任务完成都重新探测。
- 自动模式实际调用失败时,本次回退为人工提醒,并请用户通知统筹部更新 `../../部门表.md`。
- 通知只允许表达三类状态:有新任务 / 任务已完成 / 遇到阻断;任务全文、报告全文和长上下文只写 `收件箱.md`。

## 会话使用规则

- 本岗位对应一个长期会话。**新会话用 `上岗引导.md` 启动**(先接班再干活)。
- **接班**:最小读取 `../../部门表.md` 确认身份 + `交接班文档.md` 恢复状态 + `../../错题集.md` + `收件箱.md` 最新待办;除非任务要求接班或状态不明,不扫全局。
- **手上只做一件**(在"交接班文档 · 进行中"),干活时不刷收件箱;一件做完才去收件箱取下一件。
- **交班**:发跨部门消息前、完成可交付工作后、压缩前自动交班;更新 `交接班文档.md`,档案级事件追加 `日志/<本周>.md`,完成回报必须带产出路径、验证结果、日志收据、错题自检;新错题进 `../../错题集.md`。
- 会话过长 / 偏离职责 / 质量下降 → 新建会话并更新 `../../部门表.md`。
"""


def bootstrap_markdown(key: str, role: dict[str, str]) -> str:
    layer_cn = LAYER_CN.get(role.get("layer", ""), "")
    return f"""# {role['name']} 上岗引导

> 定位:轻量路由卡。先裁剪上下文,再读必要文件;不要把本文件当长制度手册。
> 手动模式:新开一个会话当本部门,把下面整段粘进去即可。
> 自动模式(Codex 等有会话工具):由工具自动发送本段作为初始化消息。

```
你现在是【{role['name']}】(角色 ID:{key} ·所在层:{layer_cn})。

## 第一步:读取路由(先裁剪上下文,别急着扫全局)
先运行固定读取路由脚本:

python3 docs/collaboration/scripts/agent_team_read.py onboard --dept {role['name']}

脚本只返回本部门身份、通知模式、必读文件、当前待办标题和默认阅读边界。它不做创造性总结、不替代统筹判断、不替代审核结论、不自动放行。

## 第二步:只读脚本返回的必读文件
- 先读岗位说明、交接班文档、收件箱、错题集;统筹部按脚本提示读项目级进度。
- 默认不读日志正文、报告正文、决策正文、其他部门正文、代码 diff、测试证据全文。
- 只有摘要不足、路径异常、结论冲突、涉及放行/返工/安全/费用/发布/用户可见质量、用户要求查证据、当前任务明确依赖正文时,才读取最小必要正文。

## 本部门职责边界
负责:{role['mission']}
不负责:{role['not_responsible']}
只能写:{role['can_write']}
禁止写:{role['cannot_write']}
必须停下来问用户:{role['confirm']}

## 干活纪律
- 手上只做一件:正在做的那件写在 `交接班文档.md` 的“进行中”。干活时不刷收件箱。
- 一件做完,才去收件箱取下一件:取出即移进“进行中”,并在收件箱删掉待办正文或保留一行指针。
- 任务详情、背景、输入、输出、报告路径、确认点只认收件箱;会话工具消息只是短唤醒。
- 节点完成后把 `[回报]` 写入统筹部收件箱,必须带四件套:产出路径、验证结果、日志收据、错题自检。

## 通知模式
- 自动模式:本部门在 `../../部门表.md` 的通知模式为“自动”时,默认直接调用会话发送工具发短唤醒。
- 人工模式:通知模式为“人工”或工具发送失败时,写完收件箱后提醒用户手动通知目标部门。
- 不要每次任务完成都重新探测工具能力;能力变化时请用户通知统筹部更新部门表。
```
"""


def state_markdown(key: str, role: dict[str, str], date: str) -> str:
    return f"""# {role['name']} · 交接班文档

> 角色 ID:`{key}` ·最近更新:{date}
> 这是本部门的**当前状态**(给接班的人看),不是流水账。接班先读这里恢复;会话变长或交接前回来更新成最新。
> 铁律:从这里删掉的旧内容,必须先追加到 `日志/<本周>.md`(只增不改),绝不直接丢。

## 进行中(在办)

> 当前手上正在做的**那一件**(从收件箱取来的)。干活只看这里,不刷收件箱。

_(待填:正在做什么、做到哪、关键中间结论、相关产出路径)_

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

> 创建日期:{date}
> 本部门的**任务真相源**:任务详情、背景、输入、输出、报告路径、确认点、节点状态都写这里。通知只做短唤醒,不复制任务全文或报告全文;按 `../../部门表.md` 登记的通知模式执行,人工模式时由用户手动提醒对应部门查看收件箱。
> 用法:
> - 只在**接班、或做完一件去取下一件时**读这里;**干活途中不刷**(免得新任务冲掉手上的活)。
> - 取出一条 → 移进 `交接班文档.md` 的"进行中" → 在本文件里**删掉待办正文**(必要时保留一行指针)。
> - 节点完成后,先更新本部门交接和产出文件,再把 `[回报]` 写入统筹部收件箱,最后按本部门通知模式提醒统筹部查看;自动模式发一句短唤醒,人工模式提醒用户手动通知。
> - `[回报]` 必须包含四件套:产出路径、验证结果、日志收据、错题自检;缺任一项,统筹部不得视为完整回报。
> - 谁能往这里写:澄清类问题任何部门可直接写;返工/放行/进入下一阶段/状态升级/审核结论/阻断类必须经统筹部。统筹部按三类节点判断是否必须用户确认、可自主推进、或可自主推进但必须汇报;用户已体验 OK 后的纯代码 / 质量 / 异常路径返工可由统筹部节点卡同步后自主派发。
> - 派单必须包含 `验收出口` 和 `必测失败路径`;缺失时接收部门应回统筹部补齐,不得自行脑补。
> - 节点状态只使用:待用户体验 / 待设计视觉确认 / 设计视觉通过 / 用户体验通过 / 用户要求返工 / 可进入测试 / 测试通过 / 测试不通过 / 可进入下一节点 / 用户已确认放行。

<!-- [待办] 模板:
## [待办] 来自:统筹部 · YYYY-MM-DD HH:MM · 节点:节点名
- 当前状态:待用户体验 / 待设计视觉确认 / 设计视觉通过 / 用户体验通过 / 用户要求返工 / 可进入测试 / 测试通过 / 测试不通过 / 可进入下一节点 / 用户已确认放行
- 任务详情:(这次只做什么节点,不要写下一节点)
- 背景:(只写必要背景)
- 输入 / 关联:(文件、入口、数据、上游结论)
- 要求输出:(产出物 + 报告路径)
- 验收节点:(用户要验收什么)
- 验收出口:(用户最终在哪里看到结果 / 提示 / 错误 / 状态 / 弹窗 / 结果摘要 / 导出文件名 / 打包态窗口;涉及用户可见内容时不得只写 engine/API/helper 层)
- 必测失败路径:(至少 1-3 个打破 happy path 的失败 / 异常 / 边界场景)
- 确认点:(完成后需要用户决定什么)
- 禁止事项:(未满足统筹部三类节点判断前不得自动返工 / 进入下一节点 / 派给其他部门 / 放行;测试部不得直接派开发返工,必须回统筹部;必须用户确认的节点不得越过用户;验收出口或必测失败路径缺失时不得自行脑补,应回统筹补齐)
-->

<!-- [回报] 模板(写入统筹部收件箱):
## [回报] 来自:{role['name']} · YYYY-MM-DD HH:MM · 节点:节点名
- 当前状态:(从状态枚举中选一个)
- 这次做出的成果:
- 如何体验 / 查看:(App 打开方式、入口、报告路径或产出路径;若需要用户体验 App,必须足够让统筹部直接打开)
- 设计说明文档路径:(设计节点必填;非设计节点写不适用)
- 设计意图预览路径:(设计节点必填,OpenDesign artifact / 本地 HTML / PNG 截图 / Figma / 可打开图片;非设计节点写不适用)
- OpenDesign 状态:(设计节点必填;正常 / 未热加载 MCP / 无 active project / 权限不足 / 连接失败 / 已兜底,以及恢复条件)
- 建议用户重点体验 / 查看:
- 建议试法:(2-4 个具体操作;没有 App 体验则写不适用)
- 关键证据:
- 验证结果:(已验证什么;未验证什么)
- 验证层级:(engine / adapter-service / worker-后台任务 / UI-用户可见出口 / 打包态 / 未覆盖层级)
- 用户可见出口:(用户最终在哪里看到结果 / 提示 / 错误 / 状态 / 弹窗 / 结果摘要 / 导出文件名 / 打包态窗口)
- 自设计反向探针:(至少说明一个打破 happy path 的探针;没有则写未覆盖并说明原因)
- 未覆盖层级:(必须明写;没有则写无)
- 是否触发子 Agent 盲审 / 抽检:(未触发 / 已触发;触发依据:连续 3 轮无阻断通过 / 跨 engine→worker→UI→用户出口 / 错误文案或状态高风险 / 发布打包安全费用高风险 / 用户或统筹觉得依据不足)
- 日志收据:
  - 文件:`docs/collaboration/部门/{role['name']}/日志/<ISO周>.md`
  - 节点ID:`<PROJECT-YYYYMMDD-ROLE-001>`
  - 索引行:`YYYY-MM-DD HH:MM · <节点ID> · 类型 · 做了什么,为什么重要 → 产出路径`
- 错题自检:
  - 已检查:
  - 结果:无命中 / 命中 X,已按正确做法处理
- 已知问题和未完成项:
- 需要统筹部请用户决定:
- 统筹部推进判断:(必须用户确认 / 可自主推进 / 可自主推进但必须汇报)
- 建议下一步:(只作为建议,不代表已获授权)
-->
"""


def weekly_log_markdown(key: str, role: dict[str, str], week_label: str, start: str, end: str) -> str:
    return f"""---
部门: {role['name']}
角色ID: {key}
覆盖: {start} ~ {end}
阶段: []
摘要: (待填:本卷主要发生了什么)
---

# {role['name']} · 日志 · {week_label}

> 只增不改的部门历史。按 ISO 周分卷、懒创建(新一周第一次有事才建那卷)。最新在最上方。
> **只记档案级事件,不按操作记**:决策 / 弃案 / 错题 / 高风险 / 从交接班文档删下的旧内容。routine 不记(详细 WHAT 在 git / 产出里)。
> **两档**:
> - 默认**一行索引**:`时间 · 节点ID · 类型 · 做了什么(带一句为什么) → 指针(相对路径 / git commit)`
> - 要追责/复盘的**大事**(决策/弃案/错题/高风险)才升级成**结构块**。
> 倒查:扫各卷 frontmatter(覆盖/阶段/摘要)锁文件 → grep 条目头 → 顺指针回 git / 产出拿原文。

<!-- 一行索引示例:
- {start} 10:30 · PROJECT-YYYYMMDD-{key.upper()}-001 · 改 · 视频007开场改钩子,因完播偏低 → 产出/视频007/脚本.md
-->

<!-- 结构块示例(只给大事;详细 WHAT 在 git,这里重点是 git 没有的 WHY):
## {start} · 决策 · 开场结构改为钩子
- 为什么:复盘 完播 38% vs 62%
- 选项与取舍:三段式(数据劣)/双开场AB(产能不够)→ 选钩子
- 结果/验证:送待审,待把关 → 产出/视频007/脚本.md(git: a1b2c3)
-->
"""


def reports_readme_markdown(role: dict[str, str], date: str) -> str:
    return f"""# 审核报告

> 创建日期:{date}
> 说明:本目录兼容旧称“把关报告”,规则中统一理解为审核层报告。{role['name']}(审核层)的审核报告放这里。**每份必须附本部门自己跑出来/查出来的证据**:实际输出 / 测试结果 / 截图 / 复现步骤 / 风险或成本测算依据。
> 不采信执行部门转述的"已完成";结论凭本部门亲自观察到的结果。审核层独立不等于盲审充分,不能只沿执行部门 happy path 重跑一遍;凡涉及用户看到 / 提示 / 错误文案 / 进度 / 状态 / 弹窗 / 结果摘要 / 导出文件名 / 打包态窗口,必须测到 worker / UI / 用户最终出口。审核层结论只回统筹部,不自动返工、放行或进入下一节点。
> 文件命名:`YYYY-MM-DD-对象-审核报告.md`。正文必须带 YAML frontmatter,便于 `scripts/agent_team_read.py find` 只读元数据定位候选。

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
summary: 待填一句话结论,供脚本返回;不要依赖脚本创造性总结正文。
---

## 把关对象 / 切片
## 把关标准(本关维度:质量 / 风险 / 成本;软件项目质量关即 docs/spec.md)
## 验证层级
- engine:
- adapter-service:
- worker-后台任务:
- UI-用户可见出口:
- 打包态:
- 未覆盖层级:
## 用户可见出口
- 用户最终在哪里看到结果 / 提示 / 错误 / 状态 / 弹窗 / 结果摘要 / 导出文件名 / 打包态窗口:
## 必测失败路径
- (至少 1-3 个打破 happy path 的失败 / 异常 / 边界场景)
## 自设计反向探针
- (每个关键风险至少一个;说明探针如何证明不是只跑 happy path)
## 是否触发子 Agent 盲审 / 抽检
- 结论:未触发 / 已触发
- 触发依据:连续 3 轮无阻断通过 / 跨 engine→worker→UI→用户出口 / 错误文案或状态高风险 / 发布打包安全费用高风险 / 用户或统筹觉得依据不足
- 子 Agent 只做盲审/抽检结论回报,不直接改代码、不自动放行
## 我怎么查的(命令 / 步骤 / 测算口径)
## 实际看到的(输出 / 截图 / 测试结果 / 风险点 / 成本数字)
## 问题清单
## 结论:通过 / 不通过 + 理由(成本关:是否超阈值,需上报用户的事项)
## 需要用户决定
-->
"""


def work_reports_readme_markdown(role: dict[str, str], date: str) -> str:
    return f"""# 报告

> 创建日期:{date}
> 不是所有任务都需要正式报告。默认用收件箱回报 + 交接班 + 日志索引闭环;只有复杂研究、设计、方案、架构、数据分析、阶段总结或用户决策材料才在这里写工作报告。
> 文件命名:`YYYY-MM-DD-对象-报告类型.md`。正文必须带 YAML frontmatter,便于读取路由脚本只读元数据检索。

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
summary: 待填一句话摘要,供脚本返回;不要依赖脚本创造性总结正文。
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
> 文件命名:`YYYY-MM-DD-对象-专项结论.md`。正文必须带 YAML frontmatter,便于读取路由脚本只读元数据检索。

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
summary: 待填一句话结论,供脚本返回;不要依赖脚本创造性总结正文。
---

## 结论
## 适用范围
## 不适用范围
## 证据 / 来源
## 后续引用方式
-->
"""


def reading_rules_markdown(date: str) -> str:
    return f"""# 读取路由规则

> 创建日期:{date}
> 目标:让部门会话默认吃短上下文,只有触发条件成立才扩大阅读范围。

## 固定入口

新会话或接班时,先运行:

```bash
python3 docs/collaboration/scripts/agent_team_read.py onboard --dept 【部门名】
```

要查报告、审核报告、专项结论、关键决策时,优先只查 YAML 元数据:

```bash
python3 docs/collaboration/scripts/agent_team_read.py find --type audit_report --status blocked
python3 docs/collaboration/scripts/agent_team_read.py find --type special_conclusion --tag 用户可见出口
python3 docs/collaboration/scripts/agent_team_read.py meta docs/collaboration/专项结论/示例.md
```

脚本只读取结构化字段和人工预写的 `summary`,不做创造性总结、不替代统筹判断、不替代审核结论、不自动放行。

## 默认阅读边界

- 默认读:读取路由脚本输出、岗位说明、交接班文档、收件箱、错题集。
- 默认不读:日志正文、报告正文、决策正文、其他部门正文、代码 diff、测试证据全文。
- 触发才读正文:摘要不足、路径异常、结论冲突、涉及放行/返工/安全/费用/发布/用户可见质量、用户要求查证据、当前任务明确依赖正文。

## 项目级文件

- `docs/progress.md`:项目级总状态,由统筹部维护。统筹部接班默认读;新增部门/新会话上岗、任务背景不足、用户问整体进度、阶段切换、收口、发布前、多部门冲突时才读。执行层和审核层平时不默认读。
- `docs/decisions/`:正式关键决策。重大技术决策双写但不重复正文:部门日志写一行索引,`docs/decisions/` 写正式决策正文。

## 元数据字段

报告、审核报告、专项结论、关键决策统一使用:

```yaml
---
type: audit_report
department: 测试部
target: 待填
status: blocked
date: {date}
related_task: 待填
decision: 不通过
tags: [用户可见出口]
summary: 待填一句话摘要。
---
```

`type` 只使用以下基础类型:

- `work_report`:执行层复杂研究、设计、方案、架构、数据分析等工作报告。
- `audit_report`:审核层质量、风险、成本审核报告。
- `special_conclusion`:会被多个部门复用的专项结论。
- `decision_record`:长期改变项目规则、架构、依赖、安全或发布方式的关键决策记录,正式正文放 `docs/decisions/`。
"""


# ---- 顶层共享文件 ----------------------------------------------------------

def cuoti_markdown(date: str) -> str:
    return f"""# 错题集

> 创建日期:{date}
> AI 犯过的错 + 正确做法。**每次接班必读**,避免重复踩同一个坑。
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


def registry_rows(roles: list[str]) -> list[str]:
    rows = []
    for key in roles:
        role = ROLE_DEFS[key]
        layer_cn = LAYER_CN.get(role.get("layer", ""), "")
        rows.append(
            f"| {layer_cn} | {role['name']} | `{key}` | 待登记 | 待登记 | {md_escape(role['mission'])} | {md_escape(role['can_write'])} | {md_escape(role['cannot_write'])} | 待启用 |"
        )
    return rows


REGISTRY_RULES_MARKER = "\n\n## 使用规则"


def registry_markdown(roles: list[str], profile: str, date: str, session_mode: str) -> str:
    rows = registry_rows(roles)
    return f"""# 部门表

> 把“岗位”绑到“具体会话”。会话 ID 后续补登。
> 三层框架:管理层(统筹) / 执行层(产出) / 审核层(质量·风险·成本把关)。
> 文件夹按部门组织:每个部门的东西都在 `部门/<部门名>/` 里。

## 团队诊断摘要

- 项目类型:{profile}
- 创建日期:{date}
- 会话创建模式:{session_mode}

## 部门列表

| 层 | 部门 | 角色 ID | 会话 ID | 通知模式 | 负责 | 可写范围 | 禁止写入 | 状态 |
|----|------|---------|---------|----------|------|----------|----------|------|
{chr(10).join(rows)}

## 使用规则

- 三层框架必须齐全:至少有 1 个管理层(统筹部)、1 个执行层、1 个审核层。
- 启动前必须先确认项目最终交付物和会话创建模式;不得默认互联网产品开发,不得在未调用会话工具时声称已创建部门会话。
- 新增 / 删除 / 替换部门前必须让用户确认;之后用脚本 `--add-roles` 增量补建,并回来更新本表与相关流转规则。
- 会话 ID / 通知模式变更只改本表,不动历史。通知模式只用:待登记 / 自动 / 人工。
- 自动会话模式:当前 Agent 必须实际调用会话管理工具创建部门会话,把返回的会话 ID 写入本表,并把 `上岗引导.md` 发给对应会话;工具不可用或调用失败时,回退手动模式并明说未创建会话。
- 手动会话模式:用户自行创建部门窗口,本 skill 只生成部门文件、上岗引导和 `会话启动清单.md`;会话 ID 保持待登记,不得声称已创建会话。
- 每个部门:状态看 `部门/<部门>/交接班文档.md`,历史看 `部门/<部门>/日志/`,待办看 `部门/<部门>/收件箱.md`。
- 审核层各部门保持独立,亲自把关,不继承执行部门上下文、也不采信其转述结论;把关报告在 `部门/<部门>/把关报告/`。
- 验收出口:统筹派单必须写验收出口和必测失败路径;缺失时接收部门回统筹补齐,不得自行脑补。凡涉及用户看到 / 提示 / 错误文案 / 进度 / 状态 / 弹窗 / 结果摘要 / 导出文件名 / 打包态窗口,验收出口必须覆盖 worker / UI / 用户最终出口,不得只写 engine/API/helper 层。
- 反向探针:审核层独立不等于盲审充分;审核部门不能只沿执行部门 happy path 重跑一遍。每个关键风险至少有一个自设计反向探针;审核/测试报告必须写验证层级、用户可见出口、自设计反向探针、未覆盖层级、是否触发子 Agent 盲审 / 抽检。
- 盲审/抽检触发:同一功能链连续多轮无阻断通过(默认 3 轮,可解释调整)、链路跨 engine→worker→UI→用户出口、涉及错误文案/状态/发布/打包/安全/费用等高风险、用户或统筹感觉结论依据不足时触发;子 Agent 只做盲审/抽检结论回报,不直接改代码、不自动放行。
- 三类节点闸:每个功能 / 环节按验收节点推进;部门完成节点后先回报统筹部。统筹部按必须用户确认 / 可自主推进 / 可自主推进但必须汇报三类判断。
- 必须用户确认:产品体验、用户感知、功能取舍、界面设计、交互流程、视觉呈现、MVP 边界、产品路线、上线发布、外发交付、成本明显增加、隐私/安全/云端/密钥/授权风险。
- 可自主推进:不改变产品体验和重大边界的流程性 / 技术性节点,如派设计草案、派开发评估、设计视觉 / 交互已确认且风险可控时派开发正式实现、用户体验 OK 后派测试、测试发现纯代码 / 质量 / 异常路径问题后派开发返工、日志/交接/共享错题集/进度记录/轻量验证/用户确认收口后的 commit 存档;UI 未确认前只能推进实现评估 / 技术可行性。
- 自主推进但必须汇报:开发评估完成、设计已确认且已派开发正式实现、已派测试、测试发现代码层问题且已派开发返工、测试无 P0/P1/P2 阻断准备收口、安全/财务本节点未触发等,统筹部必须用简短节点卡告知用户。
- 自主推进停止条件:结论明显不确定、部门判断冲突、需要牺牲体验/范围/成本/速度、新增依赖/云端/联网/模型/成本、改变用户已确认方向、进入可运行功能体验、UI 视觉确认、发布/打包/外发、大阶段收口。
- 设计可视化确认:凡涉及 UI、交互、视觉呈现、页面布局、设计稿、用户体验路径的节点,设计部不能只交文字说明、ASCII 线框、Markdown 表格或抽象结论;必须提供用户可直接判断的设计意图预览。设计预览不得声称等同真实 App UI;真实 UI 验收以运行中的 App / 真实路由 / 构建或打包态截图为准。
- OpenDesign 接入:先确认本机 OpenDesign App 是否运行,再确认 daemon 健康状态;不要假设默认端口一定是 7456,应从日志或监听端口确认实际 daemon URL;当前会话无法热加载 MCP 时,提示用户重载 / 新开会话。没有 active project 时要求用户创建或点进项目,或使用兜底交付。
- OpenDesign 恢复引导:未安装 / 未运行 OpenDesign、权限不足、连接失败或 MCP 未热加载时,主动询问用户是否需要帮忙安装 / 启动 / 授权 / 注册 MCP / 重载或新开会话;用户不想处理 OpenDesign 或不愿意重载 / 新开会话时,按用户偏好直接走本地 HTML + PNG、Figma 或图片预览。
- OpenDesign 最小排障顺序:App 是否运行 → 监听端口或日志里的 daemon URL → MCP 是否注册 / 当前会话是否热加载 → active project → 权限 / 连接错误;任一步失败都同时给兜底预览方案。
- 设计回报路径:设计节点的完成回报必须同时包含设计说明文档路径和设计意图预览路径;若用兜底方案,写清 OpenDesign 当前状态和恢复条件。
- 设计确认闸:统筹部收到设计回报后先展示设计意图预览,再给成果 / 判断点 / 建议 / 风险 / 下一步短节点卡。用户确认设计视觉或交互方向前,不得派开发部正式实现;用户只确认功能方向 OK 不等于 UI 通过。开发完成后的 UI 通过判断必须回到真实 App / 真实路由 / 构建或打包态截图。
- 完成回报四件套:产出路径、验证结果、日志收据、错题自检;缺任一项,统筹部不得视为完成闭环。
- 统筹部读取边界:先读统筹部收件箱,只核验日志收据存在;默认不读部门产出正文、长日志、测试证据全文或代码 diff。
- 状态枚举:待用户体验 / 待设计视觉确认 / 设计视觉通过 / 用户体验通过 / 用户要求返工 / 可进入测试 / 测试通过 / 测试不通过 / 可进入下一节点 / 用户已确认放行。
- 路由:澄清类直连对方收件箱;裁决/返工/放行/进入下一阶段/审核结论/阻断/状态升级/增删部门经统筹部。
- 短唤醒:通知只允许表达有新任务 / 任务已完成 / 遇到阻断;任务全文、报告全文和长上下文只写收件箱。
- 通知能力登记:每个部门会话上岗/接班时只判定一次并登记到本表;后续按登记的自动/人工模式执行,不要每次任务完成都重新探测工具。
- 自动提醒:本部门通知模式为自动时,后续默认直接调用会话发送工具发短唤醒,不要每次重新探测能力。
- 人工提醒:本部门通知模式为人工时,写完收件箱后默认直接给用户可复制短句,请用户手动提醒目标部门查看收件箱。
- 失败回退:自动模式实际发送失败时,本次回退为人工提醒,并请用户通知统筹部更新本表。
- 体验先行:可运行功能先给用户体验;进入“需要用户体验 App”的节点时,统筹部默认直接帮用户打开 App,不先问“要不要打开”,并给入口 / 重点 / 建议试法 / 判断口径四项体验卡。用户明确确认体验 OK / 可以进测试后,统筹部可自主派测试部做专业质量关,并用简短节点卡汇报。测试不通过时,纯代码 / 质量 / 异常路径问题由统筹部节点卡同步后自主派开发返工;涉及体验取舍、范围变化、成本/安全/发布、方案选择或重大事项时等用户确认。
- 放行:三关(质量/风险/成本)通过后,统筹部给出放行建议,**标记完成/对外发布由用户拍板**。用户确认正式收口后,统筹部检查 `git status --short`;若只有本节点相关变更可 commit 存档,否则先说明无关变更并请用户决定。
- 成本:财务部只监控+预警上报,不自动卡死发布;花钱由用户决定。
- 任何部门要写超出可写范围的文件,先请求用户确认。
"""


def handoff_template_markdown() -> str:
    return """# 任务交接模板

> 把某个**具体任务**派给另一个部门时,照这个填,写进对方的 `收件箱.md`。收件箱是任务真相源;通知只做短唤醒,不要复制任务全文、报告全文或长上下文。按本部门在 `部门表.md` 登记的通知模式执行;人工模式时把短唤醒交给用户手动转发。
> 注意:这是“派活给已存在的部门”;让一个新会话**上岗成为某部门**,用那个部门的 `上岗引导.md`。

## 节点式推进

- 每个功能 / 环节都拆成验收节点;一次派单只派一个节点。
- 节点完成后,执行层/审核层先停止推进,把 `[回报]` 写入统筹部收件箱。
- `[回报]` 必须包含四件套:产出路径、验证结果(含未验证项)、日志收据、错题自检。缺任一项,统筹部不得视为完成闭环。
- 统筹部只读统筹部收件箱回报来写节点卡;日志收据只做存在性核验,默认不读部门产出正文、长日志、测试证据全文或代码 diff。
- 统筹部按三类节点判断:必须用户确认 / 可自主推进 / 可自主推进但必须汇报。
- 必须用户确认:产品体验、用户感知、功能取舍、UI / 交互 / 视觉、MVP 边界、产品路线、上线发布、外发交付、成本明显增加、隐私/安全/云端/密钥/授权风险。
- 可自主推进:不改变产品体验和重大边界的流程性 / 技术性节点,如派设计草案、派开发评估、设计视觉 / 交互已确认且风险可控时派开发正式实现、用户体验 OK 后派测试、测试发现纯代码 / 质量 / 异常路径问题后派开发返工、常规日志/交接/轻量验证、用户确认收口后的 commit 存档;UI 未确认前只能推进实现评估 / 技术可行性。
- 自主推进但必须汇报:开发评估完成、设计已确认且已派开发正式实现、已派测试、测试发现代码层问题且已派开发返工、测试无 P0/P1/P2 阻断准备收口、安全/财务未触发等,必须简短告知用户。
- 自主推进停止条件:明显不确定、部门冲突、牺牲体验/范围/成本/速度、新增依赖/云端/联网/模型/成本、改变已确认方向、进入体验/视觉确认/发布打包外发/大阶段收口。
- "建议下一步"只作为建议,不等于用户已同意。

## 验收出口与必测失败路径

- 统筹派单必须写 `验收出口` 和 `必测失败路径`;缺失时接收部门应回统筹补齐,不要自行脑补。
- `验收出口`:用户最终在哪里看到结果 / 提示 / 错误 / 状态 / 弹窗 / 结果摘要 / 导出文件名 / 打包态窗口。凡涉及用户可见内容,不得只写 engine / API / helper 层。
- `必测失败路径`:至少 1-3 个打破 happy path 的失败、异常或边界场景。
- 审核层独立不等于盲审充分;审核部门不能只沿执行部门 happy path 重跑一遍。
- 凡是用户看到 / 提示 / 错误文案 / 进度 / 状态 / 弹窗 / 结果摘要 / 导出文件名 / 打包态窗口的验收,必须测到 worker / UI / 用户最终出口;只测底层不算通过。
- 每个关键风险至少有一个自设计反向探针。
- 盲审/抽检触发条件:同一功能链连续多轮无阻断通过(默认 3 轮,可解释调整)、链路跨 engine→worker→UI→用户出口、涉及错误文案/状态/发布/打包/安全/费用等高风险、用户或统筹感觉结论依据不足。
- 触发后子 Agent 只做盲审/抽检结论回报,不直接改代码、不自动放行。

## 设计可视化确认

- UI、交互、视觉呈现、页面布局、设计稿、用户体验路径节点必须交付设计意图预览;不得只交文字说明、ASCII 线框、Markdown 表格或抽象结论。
- 设计意图预览用于判断方向、布局、信息层级和交互感觉,不得声称等同真实 App UI;真实 UI 验收以运行中的 App / 真实路由 / 构建或打包态截图为准。
- 优先使用 OpenDesign 等专用设计工具生成可编辑 artifact。OpenDesign 接入前先确认 App 是否运行,再确认 daemon 健康状态;不要假设端口一定是 7456,从日志或本机监听端口确认实际 daemon URL。
- 当前会话未热加载 OpenDesign MCP、没有 active project、权限不足或工具连接失败时,设计部必须写清失败原因,并用本地 HTML + PNG 截图、Figma、可打开图片预览等方式兜底。
- OpenDesign artifact 写入提示没有 active project 时,要求用户在 OpenDesign 内创建或点进项目,或使用兜底交付,不能卡住节点。
- 未安装 / 未运行 OpenDesign、权限不足、连接失败或 MCP 未热加载时,主动询问用户是否需要帮忙安装 / 启动 / 授权 / 注册 MCP / 重载或新开会话;用户不想处理 OpenDesign 或不愿意重载 / 新开会话时,按用户偏好直接走本地 HTML + PNG、Figma 或图片预览。
- OpenDesign 最小排障顺序:App 是否运行 → 监听端口或日志里的 daemon URL → MCP 是否注册 / 当前会话是否热加载 → active project → 权限 / 连接错误;任一步失败都同时给兜底预览方案。
- 设计回报的产出路径必须同时包含设计说明文档路径和设计意图预览路径;兜底方案要写清 OpenDesign 当前状态和后续恢复条件。
- 统筹部收到设计回报后,先展示设计意图预览,再给成果 / 判断点 / 建议 / 风险 / 下一步短节点卡。
- 用户确认设计视觉或交互方向前,不得派开发部正式实现;用户只确认功能方向 OK 但 UI 未确认时,只能推进功能可行性或技术评估。开发完成后的 UI 通过判断必须回到真实 App / 真实路由 / 构建或打包态截图。

## 先判断走哪条路(混合路由)

- **澄清类 → 直连**:不改任何已确认产物、不下裁决、不推进状态,对方回一句你就能继续(如“这个字段能为空吗?”)。直接写对方收件箱,不必经统筹。
- **裁决 / 返工 / 改需求设计范围 / 审核结论 / 阻断 / 放行 / 进入下一阶段 / 状态升级 / 增删部门 → 经统筹部**:这些会改变状态或重排优先级,统一由统筹部派发。
- 口诀:**只问一句、不改东西 → 直连;要改 / 要裁决 / 要变状态 → 经统筹。**

## 短唤醒模板 / 人工提醒模板

只允许三类状态:

```text
【统筹部→测试部】有新任务，请读取本部门收件箱最新待办。
【测试部→统筹部】任务已完成，请查看统筹部收件箱最新回报。
【开发部→统筹部】遇到阻断，请查看统筹部收件箱最新回报。
```

## 通知能力登记

通知能力只在部门会话上岗/接班时判断一次,登记到 `部门表.md` 的“通知模式”列,后续不要每次任务完成都重新探测工具。

登记规则:

- `自动`:本部门会话有可调用的会话发送工具(如 `send_message_to_thread`)。
- `人工`:本部门会话没有发送工具、搜不到工具或无法使用工具。
- `待登记`:新会话未判断,接班时必须先判断并回报统筹部登记。

后续实际通知时不再重新探测能力:

- 自动模式:默认直接调用会话发送工具发短唤醒。
- 人工模式:默认直接对用户给出下面的可复制提醒句。
- 自动模式实际发送失败时,本次回退为人工提醒,并请用户通知统筹部更新 `部门表.md`。

人工提醒时对用户说:

```text
我已把内容写入【目标部门】收件箱。请你手动提醒【目标部门】:有新任务/任务已完成/遇到阻断,请查看收件箱最新内容。
```

## 状态枚举

只能使用:

```text
待用户体验
待设计视觉确认
设计视觉通过
用户体验通过
用户要求返工
可进入测试
测试通过
测试不通过
可进入下一节点
用户已确认放行
```

```markdown
## [待办] 来自:【你的部门】 · YYYY-MM-DD HH:MM ·【澄清类直连 / 经统筹】· 节点:节点名

## 当前状态

(从状态枚举里选一个)

## 任务详情

(这次只做什么节点,不要写下一节点)

## 背景

(只给必要背景,不要复制无关长上下文)

## 输入 / 关联

- (相关物料 / 文件 / 选题号等)

## 要求输出

- (交付物 + 报告路径)

## 验收节点

- (本节点完成后用户 / 统筹应看什么)

## 验收出口

- (用户最终在哪里看到结果 / 提示 / 错误 / 状态 / 弹窗 / 结果摘要 / 导出文件名 / 打包态窗口;涉及用户可见内容时不得只写 engine/API/helper 层)

## 必测失败路径

- (至少 1-3 个打破 happy path 的失败 / 异常 / 边界场景)

## 确认点

- (完成后需要用户决定什么)

## 禁止事项

- 未满足统筹部三类节点判断前,不得进入下一节点 / 返工 / 派给其他部门 / 放行 / 状态升级;凡属必须用户确认的节点,必须等用户明确确认。
- 验收出口或必测失败路径缺失时,接收部门应回统筹补齐,不得自行脑补。
```

## 回报模板(写入统筹部收件箱)

```markdown
## [回报] 来自:【你的部门】 · YYYY-MM-DD HH:MM · 节点:节点名

## 当前状态

(从状态枚举里选一个)

## 这次做出的成果

-

## 如何体验 / 查看

- App 打开方式 / 入口 / 报告路径 / 产出路径:
- 设计说明文档路径:(设计节点必填)
- 设计意图预览路径:(设计节点必填,OpenDesign artifact / 本地 HTML / PNG 截图 / Figma / 可打开图片)
- OpenDesign 状态:(设计节点必填;若兜底,写清失败原因和恢复条件)

## 体验卡素材

- 入口:
- 重点:
- 建议试法:
- 判断口径:

## 建议用户重点体验 / 查看

-

## 建议试法

- (需要用户体验 App 时,给 2-4 个具体操作;否则写不适用)

## 关键证据

-

## 验证结果

- 已验证:
- 未验证:

## 验证层级

- engine:
- adapter-service:
- worker-后台任务:
- UI-用户可见出口:
- 打包态:
- 未覆盖层级:

## 用户可见出口

- (用户最终在哪里看到结果 / 提示 / 错误 / 状态 / 弹窗 / 结果摘要 / 导出文件名 / 打包态窗口)

## 自设计反向探针

- (每个关键风险至少一个;说明如何打破 happy path)

## 未覆盖层级

- (必须明写;没有则写无)

## 是否触发子 Agent 盲审 / 抽检

- 结论:未触发 / 已触发
- 触发依据:连续 3 轮无阻断通过 / 跨 engine→worker→UI→用户出口 / 错误文案或状态高风险 / 发布打包安全费用高风险 / 用户或统筹觉得依据不足
- 限制:子 Agent 只做盲审/抽检结论回报,不直接改代码、不自动放行

## 日志收据

- 文件:`docs/collaboration/部门/【你的部门】/日志/<ISO周>.md`
- 节点ID:`<PROJECT-YYYYMMDD-ROLE-001>`
- 索引行:`YYYY-MM-DD HH:MM · <节点ID> · 类型 · 做了什么,为什么重要 → 产出路径`

## 错题自检

- 已检查:
- 结果:无命中 / 命中 X,已按正确做法处理

## 已知问题和未完成项

-

## 需要统筹部请用户决定

-

## 建议下一步

- (只作为建议,不代表已获授权)
```
"""


def session_startup_markdown(roles: list[str], session_mode: str, date: str) -> str:
    rows = []
    for index, key in enumerate(roles, start=1):
        role = ROLE_DEFS[key]
        rows.append(
            f"| {index:02d} | {role['name']} | `{key}` | 部门/{role['name']}/上岗引导.md | 待登记 | 待登记 |"
        )
    return f"""# 会话启动清单

> 创建日期:{date}
> 会话创建模式:{session_mode}
> 用途:把“部门文件已创建”和“部门会话已创建”分清楚。没有实际调用会话管理工具创建窗口时,不得声称会话已创建。

## 启动前硬闸

- 先确认项目最终交付物 / 目标,再决定部门;不得默认互联网产品开发。
- 先确认会话创建模式,再搭建协作层:
  - `自动`:Codex 等有会话管理工具的 Agent 负责创建部门会话。
  - `手动`:用户先手动创建各部门会话窗口,Agent 只生成文件和上岗引导。
- 创建 `docs/collaboration/`、新增/删除/替换部门、首次创建部门会话、改变跨会话路由或通知模式前,必须先让用户确认;已登记为自动/人工的短唤醒按 `部门表.md` 执行,不每次重复确认。

## 自动模式(Codex / 有会话管理工具)

执行顺序:

1. 用 `tool_search` 搜索 `create_thread`、`send_message_to_thread`、`set_thread_title` 三类会话工具;置顶、排序不是必要能力。
2. 对下表每个部门实际调用会话创建工具;标题建议用序号前缀保持侧栏顺序。
3. 把对应 `上岗引导.md` 的全文作为初始化消息发给该会话。
4. 把返回的会话 ID 写入 `部门表.md`,并把通知模式登记为 `自动`。
5. 如果任一步工具不可用或失败,立刻回退手动模式,把未完成项告诉用户;不得说“已创建会话”。

## 手动模式(其他 Agent / 无会话管理工具)

执行顺序:

1. 用户按下表手动创建各部门会话窗口。
2. 给每个窗口粘贴对应 `上岗引导.md`。
3. 部门会话先接班:运行 `python3 docs/collaboration/scripts/agent_team_read.py onboard --dept 【部门名】`,再只读脚本返回的必读文件。
4. 部门会话只汇报职责 / 阶段 / 进行中 / 收件箱 / 待确认问题,不要立刻做业务任务。
5. 后续交接全部写对应部门文件夹里的 `收件箱.md`;会话消息只做短唤醒。

## 部门会话清单

| 顺序 | 部门 | 角色 ID | 上岗引导 | 会话 ID | 通知模式 |
|------|------|---------|----------|---------|----------|
{chr(10).join(rows)}

## 手动上岗提醒模板

```text
请打开本项目的 docs/collaboration/部门/【部门名】/上岗引导.md,按里面的顺序接班。先只汇报:你的职责、当前阶段、进行中、收件箱是否有待办、待确认问题。不要开始业务任务。
```
"""


def append_session_startup_rows(collab: Path, roles: list[str], date: str) -> None:
    startup = collab / "会话启动清单.md"
    if not startup.exists() or not roles:
        return
    rows = []
    for key in roles:
        role = ROLE_DEFS[key]
        rows.append(f"- {role['name']} (`{key}`): `部门/{role['name']}/上岗引导.md` · 会话 ID 待登记 · 通知模式待登记")
    text = startup.read_text()
    addition = "\n\n## 增量新增部门 · " + date + "\n\n" + "\n".join(rows) + "\n"
    startup.write_text(text.rstrip() + addition)


def readme_markdown(date: str) -> str:
    return f"""# 多会话协作层

> 创建日期:{date}
> 这里管理“会话 = 部门”的协作机制。**三层框架 + 按部门组织**:每个部门的东西都在自己的文件夹里。

## 三层框架(任何需要团队协作的项目都适用)

- **管理层 · 统筹部**:拆解目标、维护节点、调度各部门、维护总进度;读取统筹部收件箱中的结构化回报,核验日志收据存在,按三类节点判断必须用户确认 / 可自主推进 / 可自主推进但必须汇报。产品感知和重大边界让用户拍板;流程性、技术性、无争议调度由统筹部专业推进。
- **执行层(产出层,≥1)**:产出实际成果。软件项目常见拆成产品 / 设计 / 开发;非软件项目先按交付物判断,可用研究 / 策划 / 执行,再按需加数据 / 自动化 / 内容 / 增长运营。
- **审核层(把关层,≥1,三维度)**:质量关(检验部/测试部)、风险关(安全部)、成本关(财务部)。各判各的关,亲自验证、凭自己看到的证据下结论。未拆全时由检验部兼任三关轻量把关。

最小盘 = 统筹 + 1 执行 + 1 审核(`lead,do,review`)。软件常见盘 = `lead,product,design,dev,test`;非软件常见盘 = `lead,research,planning,do,review`。三层必须齐全,内部拆几个按需求定。

## 结构

```
├── README.md            本说明
├── 部门表.md            层 / 部门 ↔ 会话ID 路由表(共享)
├── 会话启动清单.md       自动/手动创建部门会话的步骤和上岗入口
├── 读取路由规则.md       默认读什么、默认不读什么、何时读正文
├── 错题集.md            跨部门共享错题 + 正确做法,接班必读
├── 任务交接模板.md       把任务派给别部门的模板(含路由判断,共享)
├── 专项结论/            多部门复用的结论,用 YAML 元数据检索
├── scripts/
│   └── agent_team_read.py 读取路由器:只裁剪上下文,不替代判断
└── 部门/
    └── <部门名>/
        ├── 岗位说明.md       职责与边界(静态,含所在层与路由规则)
        ├── 上岗引导.md       轻量路由卡:先运行读取路由脚本
        ├── 交接班文档.md      当前状态(含“进行中”=在办那一件)
        ├── 收件箱.md         待办队列(没开始的任务,处理即清)
        ├── 报告/             非默认产物;复杂方案/研究/总结才写
        └── 日志/<ISO周>.md   历史档案,按周分卷
            (审核层部门另有 把关报告/,标题为审核报告)
```

## 读取路由:先裁剪,再判断是否读正文

- 新会话或接班先运行 `python3 docs/collaboration/scripts/agent_team_read.py onboard --dept 【部门名】`。
- 脚本只返回本部门身份、通知模式、必读文件、当前待办标题和默认阅读边界;不做创造性总结、不替代统筹判断、不替代审核结论、不自动放行。
- 报告、审核报告、专项结论、关键决策都必须带 YAML frontmatter,其中 `summary` 是人工预写的一句话摘要。脚本只读取这个字段,不读正文后自动总结。
- 默认不读日志正文、报告正文、决策正文、其他部门正文、代码 diff、测试证据全文。
- 只有摘要不足、路径异常、结论冲突、涉及放行/返工/安全/费用/发布/用户可见质量、用户要求查证据、当前任务明确依赖正文时,才读取最小必要正文。
- 查元数据优先用读取路由脚本,如 `agent_team_read.py find --type audit_report --status blocked`、`agent_team_read.py find --type special_conclusion --tag 用户可见出口`。

## 报告与专项结论

- 不是所有部门都必须产出正式报告;默认用收件箱回报、交接班更新、日志索引和必要产出路径闭环。
- 审核层必须产出审核报告;旧目录名 `把关报告/` 兼容保留,但语义统一为审核层报告。
- 执行层只有遇到复杂研究、设计、方案、架构、数据分析时才产出工作报告。
- 统筹部只有需要阶段总结、决策材料、用户判断材料时才产出报告。
- 只影响一个任务、一个部门的专项结论放在对应报告正文;会被多个部门复用的结论升格到 `专项结论/`;长期改变项目规则、架构、依赖、安全或发布方式的结论升级到 `docs/decisions/`。
- 重大技术决策双写但不重复正文:部门日志写一行索引,`docs/decisions/` 写正式决策正文。

## 三套记忆,各司其职

- **会话启动清单**(启动分流):区分自动创建会话和手动创建窗口。没有实际调用会话管理工具创建窗口时,不得声称会话已创建。

- **交接班文档**(热,覆盖更新):本部门当前状态,接班先读。其中“进行中”= 手上在办的那一件。
- **收件箱**(任务/回报真相源,处理即清):任务详情、背景、输入、输出、报告路径、确认点、节点状态都写这里。统筹部只从自己的收件箱读取部门回报来写节点卡;通知只做短唤醒,不复制任务全文或报告全文。只在接班 / 取下一件时读,干活途中不刷。
- **日志**(冷,只增不改,按周分卷):只做历史收据和倒查索引,不复制长报告。部门完成节点时写单行索引,并把“日志收据”放进统筹部回报。
- **共享错题集**(跨部门防复发):只收用户纠正或审核层发现的可复发流程错误。部门局部坑放本部门日志 / 交接,不要把共享错题集变成每部门流水账。

## 接班 / 交班:让记忆转起来

- **接班(读档,接手即做)**:读 `部门表.md` + 本部门 `交接班文档.md` + `错题集.md` + `收件箱.md` → 汇报恢复到的状态。
- **交班(写档,分层触发)**:
  - 硬节点自动:发跨部门消息前、完成可交付工作后。
  - 压缩 / 换会话前:先交班再压,在途推理也倒进日志(标 [进行中])。
  - 随时手动。
  - **交班 ≠ git commit**:交班只写记忆文件,commit 是入版本库。用户确认正式收口后,统筹部应检查 `git status --short`;若工作区只包含本节点相关变更,可执行 commit 存档;若有无关或用户未确认变更,先向用户说明并等待确认。

## 收件箱怎么转(防止新任务冲掉手上的活)

- 手上只做一件(交接班文档 · 进行中),**干活时不刷收件箱**。
- 一件做完,才去收件箱取下一件:取出 → 写进“进行中” → 收件箱删掉它。
- 节点完成后先更新本部门交接和产出文件,再把 `[回报]` 写入统筹部收件箱,最后按本部门在 `部门表.md` 登记的通知模式提醒统筹部查看;自动模式发一句短唤醒,人工模式提醒用户手动通知。
- `[回报]` 必须包含四件套:产出路径、验证结果(含未验证项)、日志收据、错题自检。缺任一项,统筹部不得视为完整回报。
- 统筹部处理完一条 `[回报]` 并向用户汇报后,应把它转为日志/progress/交接指针并从收件箱移除或移到已处理归档区;不要让收件箱变成第二份 progress。
- 新任务到了就在收件箱排队等;带 `[紧急]` 的可插队。

## 验收出口与失败路径

- 统筹派单必须写清 `验收出口` 和 `必测失败路径`;缺失时接收部门应回统筹部补齐,不得自行脑补。
- `验收出口`:用户最终在哪里看到结果 / 提示 / 错误 / 状态 / 弹窗 / 结果摘要 / 导出文件名 / 打包态窗口。凡涉及用户可见内容,不得只写 engine / API / helper 层。
- `必测失败路径`:至少 1-3 个打破 happy path 的失败、异常或边界场景。
- 审核层独立不等于盲审充分;审核部门不能只沿执行部门 happy path 重跑一遍。
- 凡是用户看到 / 提示 / 错误文案 / 进度 / 状态 / 弹窗 / 结果摘要 / 导出文件名 / 打包态窗口的验收,必须测到 worker / UI / 用户最终出口;只测底层不算通过。
- 每个关键风险至少有一个自设计反向探针。
- 审核/测试报告必须写明:验证层级(engine / adapter-service / worker-后台任务 / UI-用户可见出口 / 打包态 / 未覆盖层级)、用户可见出口、自设计反向探针、未覆盖层级、是否触发子 Agent 盲审 / 抽检。
- 盲审/抽检触发条件:同一功能链连续多轮无阻断通过(默认 3 轮,可解释调整)、链路跨 engine→worker→UI→用户出口、涉及错误文案/状态/发布/打包/安全/费用等高风险、用户或统筹感觉结论依据不足。
- 触发后子 Agent 只做盲审/抽检结论回报,不直接改代码、不自动放行。

## 节点式推进与用户确认闸

- 每个功能 / 环节都必须拆成明确验收节点;一次只推进一个节点。
- 部门完成节点后先停止推进并回报统筹部;统筹部再按三类节点判断:必须用户确认 / 可自主推进 / 可自主推进但必须汇报。
- 必须用户确认:影响产品体验、用户感知、功能取舍、界面设计、交互流程、视觉呈现、MVP 边界、产品路线、上线发布、外发交付、成本明显增加、隐私/安全/云端/密钥/授权风险。
- 可自主推进:用户不适合判断、且不改变产品体验和重大边界的流程性 / 技术性节点,如产品边界确认后派设计部做最小交互草案、设计草案完成后派开发部做实现评估、设计视觉 / 交互已确认且技术评估风险可控时派开发正式实现、用户体验 OK 后派测试部做质量关、测试发现纯代码 / 质量 / 异常路径问题后派开发返工、日志/交接/共享错题集/进度记录/轻量验证/用户确认收口后的 commit 存档;UI 未确认前只能推进实现评估 / 技术可行性。
- 可自主推进但必须汇报:开发评估完成且风险可控、设计已确认且已派开发正式实现、你体验 OK 后已派测试、测试发现代码层问题且已派开发返工、测试无 P0/P1/P2 阻断准备收口、安全/财务本节点未触发等,统筹部用简短节点卡告诉用户“我已经推进到哪一步”。
- 自主推进停止条件:结论明显不确定、部门之间判断冲突、需要牺牲体验/范围/成本/速度、要新增依赖/云端/联网/模型/成本、改变用户之前确认过的产品方向、进入可运行功能体验、UI 视觉确认、发布/打包/外发、大阶段收口。
- "建议下一步"只能作为建议,不能默认视为用户已同意。
- 没有明确写"用户已确认"或"统筹已按三类节点判断可自主推进"的任务,接收部门应暂停并回统筹部核对。
- 节点状态只使用:待用户体验 / 待设计视觉确认 / 设计视觉通过 / 用户体验通过 / 用户要求返工 / 可进入测试 / 测试通过 / 测试不通过 / 可进入下一节点 / 用户已确认放行。

## 设计可视化确认

- 凡涉及 UI、交互、视觉呈现、页面布局、设计稿、用户体验路径的节点,设计部必须提供用户可直接判断的设计意图预览;不得只交文字说明、ASCII 线框、Markdown 表格或抽象结论。
- 设计意图预览用于判断方向、布局、信息层级和交互感觉,不得声称等同真实 App UI;真实 UI 验收必须来自运行中的 App / 真实路由 / 构建或打包态截图。
- 优先用 OpenDesign 等专用设计工具生成可编辑设计产物或 artifact。OpenDesign 接入要先确认本机 App 是否运行,再确认 daemon 健康状态;不要假设默认端口一定是 7456,应从 OpenDesign 日志或本机监听端口确认实际 daemon URL。
- 如果当前会话未热加载 OpenDesign MCP、OpenDesign 没有 active project、权限不足、工具连接失败,设计部必须明确说明失败原因,并用本地 HTML + PNG 截图、Figma、可打开图片预览等方式兜底。
- 若 OpenDesign artifact 写入提示没有 active project,要求用户在 OpenDesign 内创建或点进项目,或直接使用本地 HTML / PNG 兜底交付;不能因此卡住节点。
- 未安装 / 未运行 OpenDesign、权限不足、连接失败或 MCP 未热加载时,设计部必须主动询问用户是否需要帮忙安装 / 启动 / 授权 / 注册 MCP / 重载或新开会话;用户不想处理 OpenDesign 或不愿意重载 / 新开会话时,按用户偏好直接走本地 HTML + PNG、Figma 或图片预览。
- OpenDesign 最小排障顺序:App 是否运行 → 监听端口或日志里的 daemon URL → MCP 是否注册 / 当前会话是否热加载 → active project → 权限 / 连接错误;任一步失败都同时给兜底预览方案。
- 设计部回报四件套中的产出路径必须同时包含设计说明文档路径和设计意图预览路径;若使用兜底方案,也要写清 OpenDesign 当前状态和后续恢复条件。
- 统筹部收到设计回报后,必须先展示设计意图预览,再给用户短节点卡。结构建议:成果 / 判断点 / 建议 / 风险 / 下一步。不要把长技术说明或设计正文直接丢给用户判断。
- 用户确认设计视觉或交互方向前,统筹部不得派开发部进入正式实现。如果用户只反馈功能方向 OK 但 UI 未确认,只能推进功能可行性或技术评估;功能方向 OK 不等于 UI 通过。
- OpenDesign 只是增强设计表达和可视化确认能力,不代表自动扩大需求范围、完整 UI 重设计、品牌升级或开发实现。

## 统筹部节点卡

统筹部给用户汇报每个节点时,只讲关键问题、推进判断和用户要做的决策。普通节点用六行卡:

```markdown
节点:
状态:
成果:
风险:
推进判断:
请确认:
```

复杂节点(可运行功能、体验节点、测试不通过、方向选择、成本风险、发布前节点)用完整卡:

```markdown
节点:
状态:
本次成果:
体验入口:
体验卡:
- 入口:
- 重点:
- 建议试法:
- 判断口径:
已验证:
风险 / 遗留:
需要用户确认:
```

节点卡信息来源以统筹部收件箱回报为主,不复制部门报告正文。

设计节点要先展示设计意图预览,再给短节点卡:

```markdown
成果:
判断点:
建议:
风险:
下一步:
```

进入“需要用户体验 App”的节点时,统筹部默认直接帮用户打开 App,不先问“要不要打开”。如果能打开,打开后给体验卡;如果打开失败,说明失败原因、已尝试命令和可手动打开的入口。体验卡只保留四项:入口 / 重点 / 建议试法 / 判断口径,其中建议试法给 2-4 个具体操作,判断口径只要求用户回复“体验 OK”或指出哪里不顺。

## 验收出口与盲审抽检

- 统筹派单必须写验收出口和必测失败路径;缺失时接收部门回统筹补齐,不得自行脑补。
- 验收出口是用户最终在哪里看到结果 / 提示 / 错误 / 状态 / 弹窗 / 结果摘要 / 导出文件名 / 打包态窗口;涉及用户可见内容时不得只写 engine / API / helper 层。
- 必测失败路径至少列 1-3 个打破 happy path 的失败、异常或边界场景。
- 审核层独立不等于盲审充分;审核部门不能只沿执行部门 happy path 重跑一遍。
- 凡是用户看到 / 提示 / 错误文案 / 进度 / 状态 / 弹窗 / 结果摘要 / 导出文件名 / 打包态窗口的验收,必须测到 worker / UI / 用户最终出口;只测底层不算通过。
- 每个关键风险至少有一个自设计反向探针。
- 审核/测试报告必须写验证层级(engine / adapter-service / worker-后台任务 / UI-用户可见出口 / 打包态 / 未覆盖层级)、用户可见出口、自设计反向探针、未覆盖层级、是否触发子 Agent 盲审 / 抽检。
- 盲审/抽检触发条件:同一功能链连续多轮无阻断通过(默认 3 轮,可解释调整)、链路跨 engine→worker→UI→用户出口、涉及错误文案/状态/发布/打包/安全/费用等高风险、用户或统筹感觉结论依据不足。触发后子 Agent 只做盲审/抽检结论回报,不直接改代码、不自动放行。

## 日志收据与读取边界

- 部门回报必须提供日志收据:日志文件、节点ID、索引行。
- 统筹部只用节点ID / 索引行做存在性核验,不审核部门日志正文质量。
- 默认不读取部门产出正文、完整日志、测试证据全文或代码 diff,以保护多会话隔离。
- 只有收件箱回报不足、日志收据不存在/指针错误、多部门结论冲突、或用户明确要求复核正文时,才读取最小必要范围,优先只读结论 / 验证 / 风险三段,并在节点卡里说明原因。

## 错题集防复发

- 每次接班必须读错题集。
- 节点完成回报必须写“错题自检”:已检查哪些相关错题、是否命中、如何处理。
- 用户纠正或审核层发现可复发流程错误时,必须写入共享错题集;普通一次性 bug 不进错题集。部门局部坑写本部门日志 / 交接,不另建部门错题集。
- 统筹部收到回报时检查是否有错题自检、是否明显违反既有错题、是否需要新增错题。

## 跨部门路由(混合)

- **澄清类直连**:不改产物、不裁决、不推进状态、问清就能继续的事,直接写对方收件箱,并发一句短唤醒。
- **经统筹部**:返工、放行、进入下一阶段、改变需求或设计范围、审核结论、阻断、是否上线、状态升级、增删部门。
- 口诀:只问一句、不改东西 → 直连;要改 / 要裁决 / 要变状态 → 经统筹。

## 短唤醒 / 人工提醒

通知只允许表达三类状态:有新任务 / 任务已完成 / 遇到阻断。任务全文、报告全文和长上下文只写收件箱。

```text
【统筹部→测试部】有新任务，请读取本部门收件箱最新待办。
【测试部→统筹部】任务已完成，请查看统筹部收件箱最新回报。
【开发部→统筹部】遇到阻断，请查看统筹部收件箱最新回报。
```

两种执行方式:

- 通知能力只在部门会话上岗/接班时登记一次,后续不要每次任务完成都重新探测工具。
- 自动模式:本部门通知模式为自动时,默认直接调用会话发送工具发短唤醒。
- 人工模式:本部门通知模式为人工时,默认直接把短唤醒交给用户,请用户手动提醒目标部门查看收件箱。
- 自动模式实际发送失败时,本次回退为人工提醒,并请用户通知统筹部更新 `部门表.md`。

## 用户体验 / 测试 / 把关流程

1. 可运行功能完成后,先由统筹部默认直接帮用户打开 App,不先问“要不要打开”;打开后给短体验卡:入口 / 重点 / 建议试法 / 判断口径。
   - 入口:从哪里进。
   - 重点:这次主要看什么。
   - 建议试法:2-4 个具体操作。
   - 判断口径:用户只需回复“体验 OK”或指出哪里不顺。
2. 用户只判断体验是否顺手、是否符合预期、流程是否对;用户体验不 OK 时,先按体验反馈返工,不进入测试部。
3. 用户明确确认"体验 OK / 可以进测试"后,测试部才做专业质量关:代码相关验证、功能回归、异常场景、打包验证、日志和边界情况、bug 清单和复现步骤。
4. 测试部结论写回统筹部收件箱,由统筹部按节点卡向用户汇报后分流:纯代码 / 质量 / 异常路径问题可自主派开发部返工;涉及体验取舍、范围变化、成本/安全/发布、方案选择或重大事项时停下等用户确认;任何情况都不得自动放行。
5. 安全部只在大阶段完成、上线或外发前,或涉及隐私、上传、权限、密钥、授权、第三方平台、生产配置等风险时介入。
6. 财务部只在成本核算、成本影响中大的功能规划、MVP 或第二版上线前、大功能板块完成时介入。成本关只预警 + 上报,不自动阻断发布。
7. 三关全通过 → 统筹部汇总给出**放行建议** → **用户拍板**标记完成 / 对外发布。用户确认正式收口后,统筹部检查 `git status --short`;若只有本节点相关变更,执行 commit 存档,否则先说明无关变更并请用户决定是否拆分提交。

## 协作原则

- 不是角色扮演,而是岗位制度。前一个部门的输出,是后一个部门的输入。
- 审核层独立:亲自把关、凭自己看到的证据下结论,不采信执行部门转述。
- 默认单线程推进,减少多会话通信;多部门只在关键节点介入。后台巡检若存在,只能提醒和汇报,不能自动派单或推进状态。
- 先从最小可用团队(三层各一个)开始,有明确需求理由再在某层内拆部门。
"""


def append_agent_guide(target: Path) -> None:
    guide = target / "docs" / "agent-guide.md"
    if not guide.exists():
        return
    text = guide.read_text()
    if "docs/collaboration/" in text:
        return
    addition = """

## 多会话协作(三层框架)

如果项目启用了 `docs/collaboration/`,团队按**三层框架**组织:管理层(统筹部)/ 执行层(产出)/ 审核层(质量·风险·成本把关)。新会话用 `docs/collaboration/部门/<本部门>/上岗引导.md` 启动:先运行 `python3 docs/collaboration/scripts/agent_team_read.py onboard --dept 【部门名】` 裁剪上下文,再只读脚本返回的必读文件。默认不读日志正文、报告正文、决策正文、其他部门正文、代码 diff、测试证据全文;只有摘要不足、路径异常、结论冲突、涉及放行/返工/安全/费用/发布/用户可见质量、用户要求查证据、当前任务明确依赖正文时,才读取最小必要正文。**手上只做一件(交接班文档·进行中),干活时不刷收件箱**;做完才取下一件。发跨部门消息前 / 完成可交付工作后 / 压缩前**交班**:更新本部门 `交接班文档.md`,形成产出/报告/设计稿/spec/代码交付/把关结论/阶段建议时追加 `日志/<本周>.md` 单行索引,并在回报里提供日志收据;跨部门可复发流程错题进共享 `错题集.md`,部门局部坑进本部门日志/交接。**交班 ≠ git commit**;用户确认正式收口后,统筹部检查 `git status --short`,仅本节点相关变更可 commit 存档,有无关变更先请用户决定。

**三类节点闸**:每个功能 / 环节拆成验收节点;部门完成节点后先停下回报统筹部。统筹部按三类判断:必须用户确认 / 可自主推进 / 可自主推进但必须汇报。产品体验、用户感知、功能取舍、UI / 交互 / 视觉、MVP 边界、产品路线、上线发布、外发交付、成本明显增加、隐私/安全/云端/密钥/授权风险必须用户确认;不改变体验和重大边界的流程性 / 技术性节点可自主推进;用户体验 OK 后派测试、测试发现纯代码 / 质量 / 异常路径问题后派开发返工、开发评估完成、测试无 P0/P1/P2 阻断、安全/财务未触发等节点必须简短汇报。自主推进停止条件:明显不确定、部门冲突、牺牲体验/范围/成本/速度、新增依赖/云端/联网/模型/成本、改变已确认方向、进入体验/视觉确认/发布打包外发/大阶段收口。"建议下一步"不等于授权。
**收件箱**:任务详情、背景、输入、输出、报告路径、确认点和节点状态只写对应部门 `收件箱.md`;通知只做短唤醒,只表达有新任务 / 任务已完成 / 遇到阻断。通知能力在上岗/接班时登记到 `部门表.md`,后续按登记模式执行;人工模式直接请用户手动提醒,自动模式直接调用工具发送。自动发送失败时回退为人工提醒,并请用户通知统筹部更新登记。
**完成回报四件套**:产出路径、验证结果、日志收据、错题自检。统筹部先读统筹部收件箱,只核日志收据存在,默认不读部门产出正文、长日志、测试证据全文或代码 diff;只有回报不足、收据错误、部门结论冲突或用户要求时,才读取最小必要正文。
**路由(混合)**:澄清类(不改产物、不裁决、不推进状态)直接写对方收件箱;裁决/返工/改需求设计范围/审核结论/阻断/放行/进入下一阶段/状态升级/增删部门经统筹部。
**体验与测试**:可运行功能先给用户体验。进入“需要用户体验 App”的节点时,统筹部默认直接帮用户打开 App,不先问“要不要打开”;打开后给短体验卡:入口 / 重点 / 建议试法 / 判断口径。建议试法给 2-4 个具体操作,判断口径只要求用户回复“体验 OK”或指出哪里不顺。用户明确确认体验 OK / 可以进测试后,统筹部可自主派测试部介入专业质量关,并用简短节点卡汇报。测试不通过时,测试部只回统筹部;统筹部节点卡同步后分流:纯代码 / 质量 / 异常路径问题可自主派开发返工,涉及体验取舍、范围变化、成本/安全/发布、方案选择或重大事项才等用户确认。
**设计可视化确认**:凡涉及 UI、交互、视觉呈现、页面布局、设计稿、用户体验路径的节点,设计部必须提供用户可直接判断的设计意图预览,不能只交文字说明、ASCII 线框、Markdown 表格或抽象结论。设计预览不得声称等同真实 App UI;真实 UI 验收必须来自运行中的 App / 真实路由 / 构建或打包态截图。优先用 OpenDesign 等专用设计工具生成可编辑 artifact;当前会话未热加载 OpenDesign MCP、没有 active project、权限不足或工具连接失败时,必须写清失败原因,主动询问用户是否需要帮忙安装 / 启动 / 授权 / 注册 MCP / 重载或新开会话。用户不想处理 OpenDesign 或不愿意重载 / 新开会话时,按用户偏好直接用本地 HTML + PNG 截图、Figma、可打开图片兜底。OpenDesign 接入先确认 App 是否运行,再确认 daemon 健康状态;不要假设端口一定是 7456,应从日志或监听端口确认实际 daemon URL。统筹部收到设计回报后,先展示设计意图预览,再给成果 / 判断点 / 建议 / 风险 / 下一步短节点卡。用户确认设计视觉或交互方向前,不得派开发部正式实现;用户只确认功能方向 OK 不等于 UI 通过;开发完成后的 UI 通过判断必须回到真实 App / 真实路由 / 构建或打包态截图。
**放行**:审核层三关通过后,统筹部给出放行建议,标记完成/对外发布由用户拍板。
**成本**:财务部只在成本节点介入并预警上报,不自动卡死发布。

### 与地基记忆文件的分工(避免重复记)

启用协作层后,按下面分工,别两头重复:

- **优先级**:本节覆盖上方“完成标准”里对 `docs/progress.md` / `docs/handoff.md` 的通用要求,避免部门和项目级记忆重复写。
- **部门级(各部门自己维护)**:本部门 `交接班文档`(当前状态/换班)、`日志`(部门历史)。日常“做到哪、为什么、踩了坑”都记这里。
- **项目级总进度 → 只由统筹部维护**:`docs/progress.md` 是各部门状态的汇总,由统筹部从各部门交接班文档汇总后单写;**其他部门不直接写 `docs/progress.md`**。
- **`docs/handoff.md` 已被部门级交接班文档取代**:启用多会话后不再单独维护(整个项目状态 = `部门表` + 各部门 `交接班文档`)。
- **最终把关以审核层为准,但不能替用户拍板**:执行部门“完成=已验证”只是交检前自检;审核层结论必须回统筹部收件箱。无 P0/P1/P2 阻断时统筹部可建议通过并准备收口,但正式收口、进入下一大阶段、对外发布仍由用户拍板;用户确认正式收口后,统筹部可按本节点相关变更执行 commit 存档。
- **确认节点**:agent-guide 的改动分级(A/B/C)是全局底线,各部门岗位说明里的确认节点是本部门细化,冲突从严;没有明确写“用户已确认”或“统筹已按三类节点判断可自主推进”的任务,接收部门暂停并回统筹部核对。
"""
    guide.write_text(text.rstrip() + addition + "\n")


# ---- 最小业务地基 ------------------------------------------------------------

def has_usable_foundation(target: Path) -> bool:
    docs = target / "docs"
    if not docs.exists():
        return False
    if any((docs / name).exists() for name in ("spec.md", "overview.md", "progress.md", "agent-guide.md")):
        return True
    return any(path.is_file() and "collaboration" not in path.parts for path in docs.rglob("*.md"))


def ensure_core_docs(target: Path, date: str) -> None:
    docs = target / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    progress = docs / "progress.md"
    if not progress.exists():
        progress.write_text(f"""# 项目进度

> 创建日期:{date}

## 当前阶段

- 已有项目地基,正在搭建多会话协作层。

## 已完成

- _(待补)_

## 进行中

- 搭建多会话协作层。

## 下一步

- 由统筹部根据团队配置派发第一个验收节点。
""")
    guide = docs / "agent-guide.md"
    if not guide.exists():
        guide.write_text(f"""# Agent 协作指南

> 创建日期:{date}

## 文件分工

- `docs/spec.md` 或 `docs/overview.md`:项目目标、交付物、边界、验收标准。
- `docs/progress.md`:项目级进度摘要,启用协作层后由统筹部维护。
- `docs/collaboration/`:多会话部门协作层。
""")


def create_minimal_foundation(target: Path, profile: str, date: str) -> None:
    """Create a small, domain-neutral foundation when no dedicated business foundation exists."""
    docs = target / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    overview = docs / "overview.md"
    if not overview.exists():
        overview.write_text(f"""# 项目概览

> 创建日期:{date}
> 地基类型:通用最小业务地基

## 目标业务

{profile}

## 最终交付物

_(待补:这个项目最终要交付什么)_

## 服务对象 / 使用对象

_(待补:谁会使用、接收或验收这个交付物)_

## 边界

- 做:
- 不做:

## 验收标准

- _(待补:用户如何判断交付物可用 / 合格)_
""")
    progress = docs / "progress.md"
    if not progress.exists():
        progress.write_text(f"""# 项目进度

> 创建日期:{date}

## 当前阶段

- 地基已创建,待根据业务目标补齐交付物、验收标准和部门协作层。

## 已完成

- 创建通用最小业务地基。

## 进行中

- 搭建多会话协作层。

## 下一步

- 由统筹部根据团队配置派发第一个验收节点。
""")
    guide = docs / "agent-guide.md"
    if not guide.exists():
        guide.write_text(f"""# Agent 协作指南

> 创建日期:{date}

## 地基说明

本项目使用通用最小业务地基。若后续发现更适合的行业/业务专用地基,先向用户确认迁移范围,再调整目录与部门权限。

## 文件分工

- `docs/overview.md`:目标、交付物、对象、边界、验收标准。
- `docs/progress.md`:项目级进度摘要,启用协作层后由统筹部维护。
- `docs/collaboration/`:多会话部门协作层。
""")


# ---- 建部门 ----------------------------------------------------------------

def create_department(depts_root: Path, key: str, role: dict[str, str], date: str,
                      week_label: str, week_start: str, week_end: str) -> None:
    d = depts_root / role["name"]
    d.mkdir(parents=True)
    (d / "岗位说明.md").write_text(role_markdown(key, role, date))
    (d / "上岗引导.md").write_text(bootstrap_markdown(key, role))
    (d / "交接班文档.md").write_text(state_markdown(key, role, date))
    (d / "收件箱.md").write_text(inbox_markdown(key, role, date))
    reports_dir = d / "报告"
    reports_dir.mkdir(parents=True)
    (reports_dir / "README.md").write_text(work_reports_readme_markdown(role, date))
    log_dir = d / "日志"
    log_dir.mkdir(parents=True)
    (log_dir / f"{week_label}.md").write_text(
        weekly_log_markdown(key, role, week_label, week_start, week_end)
    )
    if role.get("layer") == "audit":
        reports = d / "把关报告"
        reports.mkdir(parents=True)
        (reports / "README.md").write_text(reports_readme_markdown(role, date))


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
        default="lead,do,review",
        help="逗号分隔的角色 ID(默认通用最小盘:lead,do,review = 统筹/执行/检验)",
    )
    parser.add_argument(
        "--add-roles",
        default="",
        help="增量模式:在已有 docs/collaboration/ 上追加这些角色 ID(只建缺失部门并更新部门表)",
    )
    parser.add_argument("--allow-without-foundation", action="store_true", help="允许缺少 docs/spec.md,用于非软件/自定义业务地基")
    parser.add_argument(
        "--create-minimal-foundation",
        action="store_true",
        help="在没有专用业务地基时创建通用最小业务地基(docs/overview.md, docs/progress.md, docs/agent-guide.md)",
    )
    return parser.parse_args()


def validate_roles(roles: list[str], *, require_layers: bool = False) -> int | None:
    duplicates = sorted({role for role in roles if roles.count(role) > 1})
    if duplicates:
        print(f"重复角色: {', '.join(duplicates)}。请每个角色只传一次。", file=sys.stderr)
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


def run_add_roles(collab: Path, add_roles: list[str]) -> int:
    """增量模式:在已有协作层追加部门。"""
    if not collab.exists():
        print("docs/collaboration/ 不存在,无法增量。请先用 --roles 正常创建。", file=sys.stderr)
        return 3
    err = validate_roles(add_roles)
    if err:
        return err

    today = dt.date.today()
    date = today.isoformat()
    week_label, week_start, week_end = iso_week_info(today)
    depts_root = collab / "部门"
    depts_root.mkdir(parents=True, exist_ok=True)

    created, skipped = [], []
    for key in add_roles:
        role = ROLE_DEFS[key]
        if (depts_root / role["name"]).exists():
            skipped.append(key)
            continue
        create_department(depts_root, key, role, date, week_label, week_start, week_end)
        created.append(key)

    # 把新建部门追加进部门表(保留用户已填的会话ID/状态,不整表重写)
    if created:
        registry = collab / "部门表.md"
        if registry.exists():
            text = registry.read_text()
            rows = registry_rows(created)
            new_block = "\n" + "\n".join(rows)
            idx = text.find(REGISTRY_RULES_MARKER)
            if idx != -1:
                text = text[:idx] + new_block + text[idx:]
            else:
                text = text.rstrip() + new_block + "\n"
            registry.write_text(text)
        append_session_startup_rows(collab, created, date)

    print(f"增量更新协作层: {collab}")
    if created:
        print("已新增部门:")
        for key in created:
            role = ROLE_DEFS[key]
            print(f"- {LAYER_CN.get(role.get('layer', ''), '')} · {role['name']} ({key})")
    if skipped:
        print(f"已存在跳过: {', '.join(skipped)}")
    print("提醒:回来更新受影响的部门间流转规则(谁与谁直连、谁的把关结果进谁收件箱)。")
    return 0


def main() -> int:
    args = parse_args()
    target = Path(args.target).expanduser().resolve()
    if not target.exists():
        print(f"目标目录不存在: {target}", file=sys.stderr)
        return 1

    collab = target / "docs" / "collaboration"

    # 增量模式
    add_roles = [item.strip() for item in args.add_roles.split(",") if item.strip()]
    if add_roles:
        return run_add_roles(collab, add_roles)

    # 全新创建模式
    if args.session_mode is None:
        print("未确认会话创建模式。请先确认 auto(工具自动创建会话) 或 manual(用户手动创建窗口),再传 --session-mode。", file=sys.stderr)
        return 5
    missing_spec = not (target / "docs" / "spec.md").exists()
    if missing_spec and not args.allow_without_foundation:
        print(
            "未找到 docs/spec.md。若这是互联网产品/Vibe Coding 项目,请先使用对应产品地基;若是其他业务场景,请先使用适用的专用业务地基,或在用户确认后加 --allow-without-foundation --create-minimal-foundation 创建通用最小业务地基。",
            file=sys.stderr,
        )
        return 2
    if missing_spec and args.allow_without_foundation and not args.create_minimal_foundation and not has_usable_foundation(target):
        print(
            "当前项目没有可用地基。请先使用适用的业务地基,或在用户确认后加 --create-minimal-foundation 创建通用最小业务地基;不能只创建无地基协作层。",
            file=sys.stderr,
        )
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
    week_label, week_start, week_end = iso_week_info(today)
    if missing_spec and args.create_minimal_foundation:
        create_minimal_foundation(target, args.profile, date)
    else:
        ensure_core_docs(target, date)

    # 顶层共享文件
    collab.mkdir(parents=True)
    (collab / "README.md").write_text(readme_markdown(date))
    (collab / "部门表.md").write_text(registry_markdown(roles, args.profile, date, args.session_mode))
    (collab / "会话启动清单.md").write_text(session_startup_markdown(roles, args.session_mode, date))
    (collab / "错题集.md").write_text(cuoti_markdown(date))
    (collab / "任务交接模板.md").write_text(handoff_template_markdown())
    (collab / "读取路由规则.md").write_text(reading_rules_markdown(date))
    special_dir = collab / "专项结论"
    special_dir.mkdir(parents=True)
    (special_dir / "README.md").write_text(special_conclusion_readme(date))
    scripts_dir = collab / "scripts"
    scripts_dir.mkdir(parents=True)
    read_router = scripts_dir / "agent_team_read.py"
    read_router.write_text(read_router_script())
    read_router.chmod(0o755)

    # 按部门组织
    depts_root = collab / "部门"
    depts_root.mkdir(parents=True)
    for key in roles:
        create_department(depts_root, key, ROLE_DEFS[key], date, week_label, week_start, week_end)

    append_agent_guide(target)

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


if __name__ == "__main__":
    raise SystemExit(main())
