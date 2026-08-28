#!/usr/bin/env python3
"""Black-box regression verifier for the Agent Team scaffold."""

from __future__ import annotations

import datetime as dt
import argparse
import contextlib
import hashlib
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
import zipfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCAFFOLD = Path(os.environ.get("AGENT_TEAM_SCAFFOLD", ROOT / "scripts" / "scaffold_team.py")).expanduser().resolve()
PUBLIC_VERSION = "2.1.0"
SOURCE_VERSION = "2.1.1"
PROTOCOL_VERSION = "1.5.1"
PREVIOUS_PROTOCOL_VERSION = "1.4.15"
IMMEDIATE_PREVIOUS_PROTOCOL_VERSION = "1.5.0"
TOKEN_AB_CANDIDATE_VERSION = "2.1.0"
TOKEN_AB_CANDIDATE_RUNTIME = "ea02df33bf675562ba89b6cc8d34d1a5da32f3754ad48153427bbf5aafd66e98"
LEGACY_2011_FIXTURE = ROOT / "tests" / "fixtures" / "agent-team-2.0.11-runtime"
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


class ReleaseAssetInvalid(VerifyError):
    """The public Latest assets are present but cannot be trusted."""


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    ok: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    args = list(args)
    if (
        len(args) >= 3
        and Path(args[1]).name == "agent_team_task.py"
        and args[2] in {"enqueue", "authorize"}
        and "--actor" not in args
    ):
        args += ["--actor", "统筹部/lead-thread"]
    if (
        len(args) >= 3
        and Path(args[1]).name == "agent_team_task.py"
        and args[2] == "rebuild-index"
        and "--actor" not in args
    ):
        args += ["--actor", "统筹部/lead-thread"]
    if (
        len(args) >= 3
        and Path(args[1]).name == "agent_team_session.py"
        and args[2] == "set-notification"
        and "--actor" not in args
    ):
        args += ["--actor", "统筹部/lead-thread"]
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


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_latest_release_assets(
    latest_json_path: Path,
    zip_path: Path,
    checksum_path: Path,
    repo: Path,
) -> str:
    """Verify that Latest assets exactly reproduce the runtime files at its tag."""
    try:
        release = json.loads(latest_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerifyError(f"cannot read Latest release metadata: {exc}") from exc

    tag = release.get("tagName") if isinstance(release, dict) else None
    assets = release.get("assets") if isinstance(release, dict) else None
    if not isinstance(tag, str) or not tag.strip() or not isinstance(assets, list):
        raise VerifyError("Latest release metadata is missing tagName or assets")

    asset_stem = f"agent-team-{tag.removeprefix('v')}-pure.zip"
    expected_assets = {
        asset_stem: zip_path,
        f"{asset_stem}.sha256": checksum_path,
    }
    asset_by_name = {
        asset.get("name"): asset
        for asset in assets
        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
    }
    missing = sorted(set(expected_assets) - set(asset_by_name))
    if missing:
        raise ReleaseAssetInvalid(f"Latest release is missing assets: {', '.join(missing)}")

    for name, path in expected_assets.items():
        if not path.is_file():
            raise VerifyError(f"downloaded Latest asset is unavailable: {name}")
        advertised = asset_by_name[name].get("digest")
        actual = f"sha256:{file_sha256(path)}"
        if advertised != actual:
            raise ReleaseAssetInvalid(
                f"Latest asset digest mismatch for {name}: advertised={advertised!r}, actual={actual}"
            )

    checksum_text = checksum_path.read_text(encoding="utf-8").strip()
    checksum_match = re.fullmatch(
        rf"([0-9a-f]{{64}})\s+\*?{re.escape(asset_stem)}",
        checksum_text,
    )
    if not checksum_match or checksum_match.group(1) != file_sha256(zip_path):
        raise ReleaseAssetInvalid("Latest checksum file does not verify the pure ZIP")

    try:
        with zipfile.ZipFile(zip_path) as archive:
            file_names = [name for name in archive.namelist() if not name.endswith("/")]
            if len(file_names) != len(set(file_names)) or sorted(file_names) != sorted(RUNTIME_FILES):
                raise ReleaseAssetInvalid("Latest pure ZIP does not contain exactly the five runtime files")
            for relative in RUNTIME_FILES:
                tagged = run(["git", "show", f"{tag}:{relative}"], cwd=repo).stdout.encode("utf-8")
                if archive.read(relative) != tagged:
                    raise ReleaseAssetInvalid(
                        f"Latest pure ZIP differs from tag {tag} at {relative}"
                    )
    except zipfile.BadZipFile as exc:
        raise ReleaseAssetInvalid("Latest pure ZIP is not a valid ZIP archive") from exc
    return tag


def verify_release_guard_branches(root: Path) -> None:
    repo = root / "release-guard-repo"
    repo.mkdir()
    run(["git", "init", "-q"], cwd=repo)
    run(["git", "config", "user.name", "Agent Team CI"], cwd=repo)
    run(["git", "config", "user.email", "ci@users.noreply.github.com"], cwd=repo)
    for relative in RUNTIME_FILES:
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"fixture:{relative}\n", encoding="utf-8")
    run(["git", "add", *RUNTIME_FILES], cwd=repo)
    run(["git", "commit", "-q", "-m", "fixture"], cwd=repo)
    fixture_tag = f"v{SOURCE_VERSION}"
    run(["git", "tag", fixture_tag], cwd=repo)

    assets_dir = root / "release-guard-assets"
    assets_dir.mkdir()
    asset_stem = f"agent-team-{SOURCE_VERSION}-pure.zip"
    zip_path = assets_dir / asset_stem
    checksum_path = assets_dir / f"{asset_stem}.sha256"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in RUNTIME_FILES:
            archive.write(repo / relative, relative)
    checksum_path.write_text(
        f"{file_sha256(zip_path)}  {asset_stem}\n",
        encoding="utf-8",
    )
    latest_json = assets_dir / "latest.json"

    def metadata(*, zip_digest: str | None = None, include_checksum: bool = True) -> dict[str, object]:
        listed = [{
            "name": zip_path.name,
            "digest": zip_digest or f"sha256:{file_sha256(zip_path)}",
        }]
        if include_checksum:
            listed.append({
                "name": checksum_path.name,
                "digest": f"sha256:{file_sha256(checksum_path)}",
            })
        return {"tagName": fixture_tag, "assets": listed}

    latest_json.write_text(json.dumps(metadata()), encoding="utf-8")
    check(
        verify_latest_release_assets(latest_json, zip_path, checksum_path, repo) == fixture_tag,
        "Latest release guard rejected a valid package",
    )

    for invalid in (
        metadata(include_checksum=False),
        metadata(zip_digest="sha256:" + "0" * 64),
    ):
        latest_json.write_text(json.dumps(invalid), encoding="utf-8")
        try:
            verify_latest_release_assets(latest_json, zip_path, checksum_path, repo)
        except ReleaseAssetInvalid:
            pass
        else:
            raise VerifyError("Latest release guard accepted missing or mismatched assets")

    latest_json.write_text(json.dumps(metadata()), encoding="utf-8")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in RUNTIME_FILES:
            content = (repo / relative).read_bytes()
            archive.writestr(relative, b"tampered\n" if relative == RUNTIME_FILES[0] else content)
    checksum_path.write_text(
        f"{file_sha256(zip_path)}  {asset_stem}\n",
        encoding="utf-8",
    )
    latest_json.write_text(json.dumps(metadata()), encoding="utf-8")
    try:
        verify_latest_release_assets(latest_json, zip_path, checksum_path, repo)
    except ReleaseAssetInvalid:
        pass
    else:
        raise VerifyError("Latest release guard accepted a self-consistent ZIP with wrong runtime content")


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
    readme_lower = readme.casefold()
    spec = (ROOT / "docs" / "spec.md").read_text(encoding="utf-8")
    candidate_manifest = json.loads((ROOT / "candidate-manifest.json").read_text(encoding="utf-8"))
    token_ab = json.loads((ROOT / "tests" / "token-ab-20260826.json").read_text(encoding="utf-8"))
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
        f"当前正式版：{PUBLIC_VERSION} · 适用于 Codex" in readme
        and "https://github.com/AidenXu-1/agent-team-skill/releases/latest" in readme
        and "只下载安装标记为 Latest 的正式版本" in readme
        and "安装到我的 Codex 全局 Skill 目录" in readme
        and "安装完成后告诉我实际安装版本" in readme
        and "等我确认后再创建团队" in readme,
        "README omitted the current product version or complete copyable installation prompts",
    )
    runtime_hashes = {
        relative: hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        for relative in RUNTIME_FILES
    }
    runtime_set_bytes = "".join(
        f"{runtime_hashes[relative]}  {relative}\n" for relative in RUNTIME_FILES
    ).encode("utf-8")
    runtime_set_sha256 = hashlib.sha256(runtime_set_bytes).hexdigest()
    expected_candidate_id = f"AT-{SOURCE_VERSION}-RC-{runtime_set_sha256[:12].upper()}"
    candidate_status = candidate_manifest.get("status") if isinstance(candidate_manifest, dict) else None
    base_commit = candidate_manifest.get("base_commit") if isinstance(candidate_manifest, dict) else None
    current_head = run(["git", "rev-parse", "HEAD"], cwd=ROOT).stdout.strip()
    base_is_commit = (
        isinstance(base_commit, str)
        and subprocess.run(
            ["git", "cat-file", "-e", f"{base_commit}^{{commit}}"], cwd=ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0
    )
    base_is_ancestor = (
        base_is_commit
        and subprocess.run(
            ["git", "merge-base", "--is-ancestor", base_commit, current_head], cwd=ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0
    )
    status_binding_valid = candidate_status == "source-candidate" and base_is_ancestor
    check(
        isinstance(candidate_manifest, dict)
        and set(candidate_manifest) == {
            "schema_version", "candidate_id", "status", "generated_on", "base_commit",
            "source_version", "protocol_version", "public_version_at_review", "runtime_files",
            "runtime_set_sha256", "runtime_set_sha256_algorithm",
        }
        and candidate_manifest.get("schema_version") == 1
        and candidate_manifest.get("candidate_id") == expected_candidate_id
        and candidate_status == "source-candidate"
        and status_binding_valid
        and candidate_manifest.get("source_version") == SOURCE_VERSION
        and candidate_manifest.get("protocol_version") == PROTOCOL_VERSION
        and candidate_manifest.get("public_version_at_review") == PUBLIC_VERSION
        and candidate_manifest.get("runtime_files") == runtime_hashes
        and candidate_manifest.get("runtime_set_sha256") == runtime_set_sha256,
        "candidate manifest is stale, ambiguous, or not bound to the five runtime files",
    )
    legacy_tokens = token_ab.get("legacy", {}) if isinstance(token_ab, dict) else {}
    candidate_tokens = token_ab.get("candidate", {}) if isinstance(token_ab, dict) else {}
    token_fixture = token_ab.get("fixture", {}) if isinstance(token_ab, dict) else {}
    token_conclusion = token_ab.get("conclusion", {}) if isinstance(token_ab, dict) else {}
    receipts_relative = token_ab.get("receipts") if isinstance(token_ab, dict) else None
    receipts_path = ROOT / receipts_relative if isinstance(receipts_relative, str) else ROOT / "missing"
    check(
        isinstance(receipts_relative, str)
        and not Path(receipts_relative).is_absolute()
        and ".." not in Path(receipts_relative).parts
        and receipts_path.is_file()
        and hashlib.sha256(receipts_path.read_bytes()).hexdigest() == token_ab.get("receipts_sha256"),
        "Token A/B receipt path or hash is invalid",
    )
    receipt_records = [
        json.loads(line) for line in receipts_path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    check(
        bool(receipt_records)
        and receipt_records[0].get("type") == "evidence.meta"
        and re.fullmatch(
            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
            receipt_records[0].get("source_root_thread_id", ""),
        )
        and receipt_records[0].get("prompt_sha256") == token_ab.get("prompt_sha256")
        and receipt_records[0].get("run_order") == token_ab.get("run_order"),
        "Token A/B receipt metadata is stale or reordered",
    )
    receipt_runs: dict[str, dict] = {}
    current_receipt_run: dict | None = None
    for record in receipt_records[1:]:
        if record.get("type") == "evidence.run":
            run_id = record.get("run_id")
            check(isinstance(run_id, str) and run_id not in receipt_runs,
                  "Token A/B receipt run identity is missing or duplicated")
            current_receipt_run = {**record, "tool_calls": 0, "thread_id": "", "usage": None}
            receipt_runs[run_id] = current_receipt_run
        elif record.get("type") == "thread.started":
            check(current_receipt_run is not None and not current_receipt_run["thread_id"],
                  "Token A/B receipt thread is unbound or duplicated")
            current_receipt_run["thread_id"] = record.get("thread_id")
        elif record.get("type") == "item.completed" and record.get("item", {}).get("type") == "command_execution":
            check(
                current_receipt_run is not None
                and record["item"].get("exit_code") == 0
                and record["item"].get("status") == "completed"
                and isinstance(record["item"].get("command"), str),
                "Token A/B receipt contains an invalid command completion",
            )
            current_receipt_run["tool_calls"] += 1
        elif record.get("type") == "turn.completed":
            check(current_receipt_run is not None and current_receipt_run["usage"] is None,
                  "Token A/B turn usage is unbound or duplicated")
            current_receipt_run["usage"] = record.get("usage")
    expected_order = token_ab.get("run_order")
    check(
        isinstance(expected_order, list)
        and list(receipt_runs) == expected_order
        and all(
            isinstance(run.get("thread_id"), str) and run["thread_id"]
            and isinstance(run.get("usage"), dict)
            for run in receipt_runs.values()
        ),
        "Token A/B receipt set is incomplete",
    )
    summary_samples = {
        sample["run_id"]: sample
        for variant in (legacy_tokens, candidate_tokens)
        for sample in variant.get("samples", [])
        if isinstance(sample, dict) and isinstance(sample.get("run_id"), str)
    }
    check(
        set(summary_samples) == set(receipt_runs)
        and all(
            summary_samples[run_id].get("thread_id") == receipt["thread_id"]
            and summary_samples[run_id].get("tool_calls") == receipt["tool_calls"]
            and all(
                summary_samples[run_id].get(field) == receipt["usage"].get(field)
                for field in (
                    "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens",
                )
            )
            for run_id, receipt in receipt_runs.items()
        ),
        "Token A/B summary no longer matches its JSONL receipts",
    )
    check(
        isinstance(token_ab, dict)
        and token_ab.get("schema_version") == 2
        and token_ab.get("status") == "controlled-two-pair-alternating"
        and token_ab.get("model") == "gpt-5.6-sol"
        and token_ab.get("reasoning_effort") == "low"
        and legacy_tokens.get("source_version") == "2.0.11"
        and legacy_tokens.get("runtime_set_sha256") == "171fae3f6cae4454a4cca5521a894e615431e323180d0344b7b2cf3eda4a28ec"
        and legacy_tokens.get("sample_count") == 2
        and legacy_tokens.get("input_tokens_mean") == 101929.0
        and legacy_tokens.get("tool_calls_mean") == 5.0
        and candidate_tokens.get("source_version") == TOKEN_AB_CANDIDATE_VERSION
        and candidate_tokens.get("runtime_set_sha256") == TOKEN_AB_CANDIDATE_RUNTIME
        and candidate_tokens.get("sample_count") == 2
        and candidate_tokens.get("input_tokens_mean") == 75915.0
        and candidate_tokens.get("tool_calls_mean") == 2.0
        and token_fixture.get("cold_history_loaded") == 0
        and token_fixture.get("current_tasks_loaded") == 1
        and token_fixture.get("legacy_history_tasks") == 927
        and token_fixture.get("legacy_total_tasks") == 928
        and token_fixture.get("candidate_total_tasks") == 928
        and token_fixture.get("synthetic_only") is True
        and token_fixture.get("builder") == "tests/build_token_ab_fixture.py"
        and token_fixture.get("builder_sha256")
        == hashlib.sha256((ROOT / token_fixture["builder"]).read_bytes()).hexdigest()
        and token_conclusion.get("input_tokens_percent") == -25.5
        and token_conclusion.get("pair_input_tokens_percent") == [-25.5, -25.5]
        and candidate_tokens.get("hot_context_bytes", 0) > legacy_tokens.get("hot_context_bytes", 0)
        and any(
            probe.get("status") == "invalid-comparison"
            and probe.get("thread_id") == "01a03eb4-5e2b-7602-80d1-a63d0e2321ca"
            and probe.get("tool_calls") == 8
            for probe in token_ab.get("rejected_probes", [])
        )
        and isinstance(token_ab.get("limitations"), list) and len(token_ab["limitations"]) >= 5,
        "historical Token A/B evidence is stale, selective, or no longer bound to its tested runtime",
    )
    check(
        all(term not in readme_lower for term in (
            "lulu", "gpt-5.6", "token 实测", "candidate-manifest.json",
            "reviewed-release-candidate", "runtime_set_sha256",
        ))
        and all(term not in readme for term in (
            "101,929", "75,915", "25.5%", "SHA-256", "协议版本", "TASK_STATE_OK",
        ))
        and re.search(r"\bS\d{1,2}\b", readme) is None,
        "README leaked project-specific experiments, model names, internal IDs, or release mechanics",
    )
    check(
        "https://github.com/AidenXu-1/agent-team-skill/releases/latest" in readme
        and "releases/download/" not in readme
        and ".zip" not in readme
        and ".sha256" not in readme_lower,
        "README installation entry is stale or exposes low-level package mechanics",
    )
    check(
        all(term in readme for term in (
            "## 安装提示", "## 怎么使用", "## 快捷指令",
            "## 协作资料为什么这样设计", "## 适合什么项目", "## 必要说明",
            "### 它会怎样向你汇报",
        ))
        and all(term in readme for term in (
            "`接班`", "`先接班，不要开始任务`", "`交班`", "`换班` / `换会话`",
            "`汇报进度`", "`先回答问题，不要改变当前任务`", "`继续当前任务`",
            "`这件事适合外包吗？`", "`外包` / `临时外包`",
            "不等于授权创建", "当前环境不能安全创建第二个执行会话",
        ))
        and all(term not in readme_lower for term in (
            "维护者", "运行文件", "公开主干", "pull request", "workflow", "fixture",
        ))
        and readme.count("\n## ") <= 7
        and "<details>" not in readme,
        "README is missing the plain-language product journey or has regrown developer documentation",
    )
    check(
        all(term in readme for term in (
            "部门表", "岗位说明", "上岗引导", "交接班文档", "收件箱",
            "任务记录", "错题集", "日志", "报告",
            "部门表回答“谁在做”", "任务记录回答“现在做什么”",
            "交接班文档回答“做到哪里”", "日志回答“发生过什么”",
            "报告回答“真正检查过什么”",
        ))
        and "同一件事只保留一份正式记录" in readme
        and "普通小任务不强制写长报告" in readme
        and "普通的一次性小问题不会全部塞进去" in readme,
        "README omitted the purpose and separation of generated collaboration materials",
    )
    check(
        set(reference_frontmatter) == {"title", "status"}
        and reference_frontmatter.get("status") == "legacy-maintenance-p2-blocked"
        and "TEMPORARY_EXECUTOR_P2_REQUIRED" in temporary_reference
        and "1.4 legacy temporary TASK" in temporary_reference
        and "pending-archives" in temporary_reference
        and "YAML frontmatter" in temporary_reference,
        "temporary executor cold reference is missing or carries redundant version metadata",
    )
    temporary_runtime = (ROOT / "scripts" / "temporary_executor_runtime.py").read_text(encoding="utf-8")
    check(
        "enforce_legacy_closeout_only" in temporary_runtime
        and "LEGACY_TEMPORARY_CLOSEOUT_ONLY" in temporary_runtime
        and "TEMPORARY_EXECUTOR_P2_REQUIRED" in temporary_runtime
        and "legacy-archive-recovery.json" in temporary_runtime,
        "temporary runtime freeze whitelist or protocol-1.5 creation boundary drifted",
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
        and all(f"S{index:02d}" in semantic_review for index in range(1, 48))
        and "本文件只保存稳定问题，不写某次执行结果" in semantic_review,
        "manual semantic release gate is missing or incomplete",
    )
    check(
        all(term in skill for term in (
            "统筹会话按项目阶段", "执行会话按端到端切片",
            "同一切片返工", "同一候选的审核复测",
            "旧候选或身份串入", "输入、缓存输入、输出和工具调用",
            "无法测量", "固定倍数阈值", "只建议换班",
        ))
        and all(term in readme for term in (
            "项目资料保存长期信息", "会话只专注眼前任务",
            "会话开始记混、反复翻旧资料", "你同意后，新会话带着现有进度接手",
            "旧资料需要时再查",
        ))
        and all(term in spec for term in (
            "会话生命周期", "完整任务生命周期 A/B", "不新增自动换班状态机",
        )),
        "session lifecycle guidance is missing, unmeasured, or able to masquerade as automatic switching",
    )
    check(
        all(term in skill for term in (
            "## 用户闸门与汇报", "需要你做什么", "还需注意",
            "不穷举场景", "对当前 TASK 的影响", "用户出口保持 `pending`", "`not_applicable`",
            "同一切片内继续", "普通问答自然回复", "不为凑格式制造空话",
            "临时提问/状态追问直接答并保留当前 TASK",
            "入口/操作顺序/预期结果/重点判断/已知限制",
            "默认不展开 TASK ID",
            "全项目同一时刻只有一个活动切片",
            "freeze-new-work",
            "同一 gate 跨两代连续 FAIL",
            "当前不可确认可用",
        ))
        and all(term in readme for term in (
            "结果 / 需要你做什么 / 还需注意",
            "专人干专活", "各部门分工协作，只干属于自己分内的事",
            "该你出手时才找你", "纯代码检查由团队内部完成",
            "压力大就换班", "并行任务招外包",
            "能和主线分开的独立任务", "能安全并行",
            "条件不够就直说", "普通问答不会套模板",
            "临时问一个问题", "不会打断正在做的事",
        ))
        and all(term in spec for term in (
            "稳定但不死板", "不穷举用户场景", "用户出口保持 `pending`", "`not_applicable`",
            "不得自动开启下一切片",
        )),
        "user-facing reporting contract is missing from the Skill or repository guide",
    )
    check(
        f"当前运行协议为 `{PROTOCOL_VERSION}`" in skill
        and "references/temporary-executor.md" in skill
        and "TEMPORARY_EXECUTOR_P2_REQUIRED" in skill
        and "文档修订号" not in readme
        and "文档修订号" not in temporary_reference,
        "SKILL did not keep temporary-executor rules on the cold path",
    )
    check(
        all(term in skill for term in (
            "系统级技术路径", "架构", "模块/数据/接口边界",
            "开工可行性复核", "正式实现、自测与集成", "`docs/decisions/code/`",
            "draft → proposed → accepted → superseded", "`accepted` 正文不可原地修改",
            "安全与测试只提交独立报告",
        )),
        "SKILL lost the durable product/development ownership, ADR status, or independent-review contract",
    )
    check(len(skill.encode("utf-8")) <= 17_000, "SKILL exceeded the 17 KB hot-context budget")
    check(
        "技术实现方式交开发部" not in skill
        and "开发部负责全部技术实现" not in skill
        and skill.count("本节只保留不可被项目覆盖削弱的职责合同") == 1,
        "SKILL reintroduced the old ownership regression or duplicated its hot responsibility contract",
    )
    workflow_lines = workflow.splitlines()
    try:
        on_index = workflow_lines.index("on:")
        jobs_index = workflow_lines.index("jobs:")
    except ValueError as exc:
        raise VerifyError("CI workflow is missing exact on/jobs sections") from exc
    trigger_block = "\n".join(workflow_lines[on_index + 1:jobs_index])
    check(
        "  push:" in trigger_block and "  pull_request:" in trigger_block
        and "  workflow_dispatch:" in trigger_block
        and all(field in trigger_block for field in (
            "release_version:", "reviewed_commit:", "reviewed_runtime_sha256:", "confirm_publish:",
        )),
        "CI must verify push/PR and expose a fully bound manual release dispatch",
    )
    check('python-version: ["3.9", "3.11"]' in workflow,
          "CI no longer verifies both Python 3.9 and 3.11")
    check(
        workflow.count("uses: actions/checkout@v7") == 2
        and workflow.count("uses: actions/setup-python@v6") == 2
        and workflow.count("fetch-depth: 0") == 2,
        "CI uses a deprecated action runtime or shallow history that breaks candidate ancestry",
    )
    check(
        "Publish explicitly authorized reviewed release" in workflow
        and "github.event_name == 'workflow_dispatch'" in workflow
        and "github.ref == 'refs/heads/main'" not in workflow
        and "needs: verify" in workflow
        and "environment: agent-team-public-release" in workflow
        and "contents: write" in workflow
        and "git archive --format=zip" in workflow
        and all(relative in workflow for relative in RUNTIME_FILES)
        and "reviewed-release-candidate" in workflow
        and "inputs.reviewed_commit" in workflow
        and "inputs.reviewed_runtime_sha256" in workflow
        and "inputs.confirm_publish" in workflow
        and "PUBLISH v${version}" in workflow
        and "agent-team-%s-pure.zip" in workflow
        and 'tag="v${version}"' in workflow
        and "gh release create" in workflow
        and "--draft" in workflow
        and "--check-release-assets" in workflow
        and "agent-team-fresh-clone" in workflow
        and "AGENT_TEAM_LEGACY_2011_ROOT" in workflow
        and "tests/fixtures/agent-team-2.0.11-runtime" in workflow
        and "fixture-manifest.json" in workflow
        and "gh release edit" in workflow
        and "--draft=false" in workflow
        and "--latest" in workflow
        and "draft_zip_digest" in workflow
        and "draft_target" in workflow
        and "targetCommitish" in workflow
        and "existing_release" in workflow
        and "draft_is_draft" in workflow
        and "draft_asset_count" in workflow
        and "Reusing exact reviewed draft release" in workflow
        and "Recovering unpublished draft onto runtime-identical reviewed commit" in workflow
        and 'git diff --quiet "${draft_target}" "${GITHUB_SHA}" -- \\' in workflow
        and 'gh release edit "${TAG}" \\' in workflow
        and '--target "${GITHUB_SHA}" \\' in workflow
        and 'gh release upload "${TAG}" \\' in workflow
        and '--clobber' in workflow
        and '| jq -r --arg zip' in workflow
        and '| jq -r --arg name' in workflow
        and '--jq --arg' not in workflow
        and "Tag appeared before publication" in workflow
        and "remote_tag_sha" not in workflow
        and 'releases/tags/${TAG}" --jq .target_commitish' not in workflow
        and "published_tag_sha" in workflow
        and "gh release download" in workflow
        and "sha256sum -c" in workflow
        and "published_install" in workflow
        and 'releases/latest" --jq .tag_name' in workflow
        and "published_zip_sha" in workflow
        and '--check-installed-copy "${published_install}"' in workflow
        and "/git/ref/tags/" in workflow
        and "github.event_name == 'push'" not in workflow
        and "gh release delete" not in workflow,
        "CI omitted the explicit reviewed versioned publication gate",
    )
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


def ensure_lead_registered(task_tool: Path, thread_id: str = "lead-thread") -> None:
    ensure_department_registered(task_tool, "统筹部", thread_id)


def ensure_department_registered(task_tool: Path, department: str, thread_id: str) -> None:
    collab = task_tool.parents[1]
    state_path = collab / "会话启动状态.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    item = state["departments"][department]
    if item["step"] == "registered":
        check(item["thread_id"] == thread_id, f"fixture {department} registered with unexpected thread")
        return
    check(item["step"] == "pending" and not item["thread_id"],
          f"test fixture {department} session is neither pending nor registered")
    session_tool = collab / "scripts" / "agent_team_session.py"
    for step in ("created", "onboarded", "registered"):
        run([
            sys.executable, str(session_tool), "mark", "--department", department, "--step", step,
            "--thread-id", thread_id, "--evidence", f"fixture-{step}",
        ])


def enqueue(task_tool: Path, title: str, auth: str = "none", evidence: str = "") -> str:
    ensure_lead_registered(task_tool)
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
        "scripts/agent_team_session.py", "scripts/agent_team_temporary.py", ".locks/dispatch-control.json",
        ".locks/slice-control.json", ".locks/legacy-closeout-index.json",
    ]
    for relative in required:
        check((collab / relative).is_file(), f"missing generated file: {relative}")
    check(not (collab / "读取路由规则.md").exists(), "obsolete reading rules generated")
    check(not (collab / "scripts" / "agent_team_read.py").exists(), "obsolete reader generated")
    protocol = json.loads((collab / "协议版本.json").read_text(encoding="utf-8"))
    check(protocol["protocol_version"] == PROTOCOL_VERSION, "unexpected protocol version")
    check(protocol.get("role_policy_overlays") == {}, "fresh scaffold registered unexpected role policy overlays")
    for script in (collab / "scripts").glob("*.py"):
        compile_script(script)
    task_tool = collab / "scripts" / "agent_team_task.py"
    run([sys.executable, str(task_tool), "rebuild-index", "--actor", "统筹部/bootstrap"])
    run([sys.executable, str(task_tool), "onboard-check", "--department", "执行部"])
    fresh_bundle = run([sys.executable, str(task_tool), "onboard-bundle", "--department", "执行部"])
    check(
        fresh_bundle.stdout.startswith(
            "ONBOARD_BUNDLE_OK | 执行部 | current_tasks=0 | recovery_tasks=0 | cold_history=not_loaded"
        )
        and "hot_context_bytes=" in fresh_bundle.stdout
        and "hot_context_warning=none" in fresh_bundle.stdout
        and "===== BEGIN 岗位说明.md =====" in fresh_bundle.stdout
        and "===== BEGIN 交接班文档.md =====" in fresh_bundle.stdout
        and "===== BEGIN 收件箱.md =====" in fresh_bundle.stdout,
        "read-only onboarding bundle omitted a hot entry or invented a cold task",
    )
    role_doc = collab / "部门" / "执行部" / "岗位说明.md"
    role_doc_bytes = role_doc.read_bytes()
    role_doc.write_bytes(role_doc_bytes + ("\n上下文告警探针" * 2_000).encode("utf-8"))
    oversized_bundle = run([sys.executable, str(task_tool), "onboard-bundle", "--department", "执行部"])
    role_doc.write_bytes(role_doc_bytes)
    check(
        "hot_context_warning=review_required" in oversized_bundle.stdout
        and "HOT_CONTEXT_WARNING | 执行部" in oversized_bundle.stdout
        and "内容未截断" in oversized_bundle.stdout,
        "oversized hot context was silently accepted or destructively truncated",
    )
    session_tool = collab / "scripts" / "agent_team_session.py"
    for step in ("created", "onboarded", "registered"):
        run([
            sys.executable, str(session_tool), "mark", "--department", "统筹部", "--step", step,
            "--thread-id", "lead-thread", "--evidence", f"fresh-bootstrap-{step}",
        ])
    run([sys.executable, str(task_tool), "onboard-check", "--department", "执行部"])
    guide = (project / "docs" / "agent-guide.md").read_text(encoding="utf-8")
    check(f"受管协议版本:{PROTOCOL_VERSION}" in guide and "任务真值" in guide,
          "project guide not refreshed")
    collaboration_readme = (collab / "README.md").read_text(encoding="utf-8")
    check(
        "agent_team_temporary.py" in collaboration_readme
        and "TEMPORARY_EXECUTOR_P2_REQUIRED" in collaboration_readme
        and "session-mark" not in collaboration_readme
        and "temporary_session=standby" not in collaboration_readme,
        "generated collaboration guide did not keep the temporary-executor rules cold",
    )
    check(
        all(term in collaboration_readme for term in (
            "不得依赖穷举场景", "对当前 TASK 的影响", "用户出口 `pending`",
            "纯代码或内部检查", "`not_applicable`", "不强制汇报",
            "临时提问、状态追问直接回答并保留当前 TASK",
            "结果 / 需要你做什么 / 还需注意",
            "入口、操作顺序、预期结果、重点判断和已知限制",
            "普通问答不套模板", "不为格式制造空话",
            "内部任务号、状态词、哈希、命令、日志和协议默认不展开",
            "后台及普通低影响测试照常自动执行",
            "明显妨碍用户正常使用设备",
            "前台独占或难以自行退出",
            "阶段性 Kickoff / 开发授权不能替代",
            "正常模式一次只推进一个切片、一个 owner、最多两个 gate",
            "不自动开启下一切片",
            "freeze-new-work",
            "同一 gate 跨两代连续 FAIL",
            "只保留已领取任务完成或安全停下、清账、核收、交接、换班和证据",
        )),
        "generated collaboration guide lost the concise user-reporting contract",
    )
    check("archive-request" not in collaboration_readme and "--archive-mode" not in collaboration_readme,
          "generated collaboration guide retained the rejected hard archive gate")
    for department in ("统筹部", "执行部", "检验部"):
        root = collab / "部门" / department
        check((root / "报告").is_dir() and (root / "日志").is_dir(), "department output directories missing")
        check(not (root / "报告" / "README.md").exists(), "duplicated report README generated")
        check(not list((root / "日志").glob("*.md")), "empty weekly log should be lazy")
        four_docs_bytes = sum(
            (root / name).stat().st_size
            for name in ("上岗引导.md", "岗位说明.md", "交接班文档.md", "收件箱.md")
        )
        check(four_docs_bytes <= 7_000,
              f"{department} four-document onboarding exceeded 7 KB: {four_docs_bytes}")
    inbox = (collab / "部门" / "执行部" / "收件箱.md").read_text(encoding="utf-8")
    lead_role_text = (collab / "部门" / "统筹部" / "岗位说明.md").read_text(encoding="utf-8")
    check("../../tasks/" in inbox, "inbox does not use stable clickable task path")
    check(
        all(term in lead_role_text for term in (
            "用户互动按意图、TASK 影响", "用户信息/体验判断/授权", "user_exit pending",
            "本切片验证后 not_applicable 继续", "临时问题直接答并保留 TASK",
            "结果 / 需要你做什么 / 还需注意",
            "第三段按需", "普通问答不套模板",
            "后台及普通低影响测试照常自动执行",
            "明显妨碍用户正常使用设备",
            "前台独占或难以自行退出",
            "一次只推进一个切片和一个执行 owner",
            "manual-degraded",
            "不得自动开启下一切片",
            "freeze-new-work",
            "同一 gate 跨两代连续 FAIL",
            "仅凭用户明确恢复证据运行 `unfreeze-new-work`",
        )),
        "generated lead role lost the concise user-reporting contract",
    )
    role_text = (collab / "部门" / "执行部" / "岗位说明.md").read_text(encoding="utf-8")
    bootstrap_text = (collab / "部门" / "执行部" / "上岗引导.md").read_text(encoding="utf-8")
    check(
        "../../tasks/TASK-*.json" in role_text
        and "项目总进度由统筹部维护" in role_text
        and "后台及普通低影响测试照常自动执行" in role_text
        and "阶段性 Kickoff / 开发授权不能替代" in role_text
        and "tasks/TASK-*.json" not in role_text.replace("../../tasks/TASK-*.json", ""),
        "slimmed role guide lost task truth or retained the wrong relative task path",
    )
    check("当前消息/文件就是第 1 份上岗入口" in bootstrap_text
          and "换会话 / 换班" in bootstrap_text and "不 fork 旧历史" in bootstrap_text,
          "slimmed bootstrap lost the explicit session-switch boundary")


def verify_default_minimal_software_team(root: Path) -> None:
    project = make_project(root, "default-minimal-software-team")
    run([
        sys.executable, str(SCAFFOLD), str(project),
        "--profile", "App / Web 最小协作", "--session-mode", "manual",
        "--foundation-file", "docs/spec.md",
    ])
    collab = project / "docs" / "collaboration"
    state = json.loads((collab / "会话启动状态.json").read_text(encoding="utf-8"))
    check(
        state["role_order"] == ["lead", "dev", "test"]
        and set(state["departments"]) == {"统筹部", "开发部", "测试部"}
        and not (collab / "部门" / "产品部").exists()
        and not (collab / "部门" / "设计部").exists()
        and not (collab / "部门" / "安全部").exists(),
        "software default expanded beyond the lead/dev/test three-layer minimum",
    )


def verify_stop_loss_control(root: Path) -> None:
    project = make_project(root, "stop-loss-control")
    scaffold(project)
    collab = project / "docs" / "collaboration"
    task_tool = collab / "scripts" / "agent_team_task.py"
    ensure_lead_registered(task_tool)
    result_file = project / "docs" / "stop-loss-result.txt"
    result_file.write_text("completed before freeze\n", encoding="utf-8")

    mode_before = run([sys.executable, str(task_tool), "work-mode"])
    check(mode_before.stdout.strip() == "WORK_MODE | normal | history:0",
          "fresh collaboration did not default to normal work mode")
    claimed = enqueue(task_tool, "冻结前已领取任务")
    run([sys.executable, str(task_tool), "claim", "--task-id", claimed, "--claimed-by", "do-thread"])
    queued = enqueue(task_tool, "冻结前待领取任务")
    gated = enqueue(task_tool, "冻结前待用户授权任务", "user_required", "awaiting-user-decision")
    resumable = task_id_from(run([
        sys.executable, str(task_tool), "enqueue", "--department", "检验部",
        "--from-department", "统筹部", "--title", "冻结前已阻断任务", "--node", "节点",
        "--details", "用于验证冻结时不可恢复", "--acceptance-exit", "恢复动作被明确拒绝",
        "--failure-path", "冻结旁路", "--authorization-state", "none",
    ]))
    run([sys.executable, str(task_tool), "claim", "--task-id", resumable, "--claimed-by", "review-thread"])
    run([sys.executable, str(task_tool), "block", "--task-id", resumable, "--reason", "等待止血验证"])
    queued_path = collab / "tasks" / f"{queued}.json"
    queued_before = queued_path.read_bytes()
    task_count_before = len(list((collab / "tasks").glob("TASK-*.json")))

    frozen = run([
        sys.executable, str(task_tool), "freeze-new-work",
        "--actor", "统筹部/lead-thread", "--evidence", "user-requested-p0-freeze",
    ])
    frozen_again = run([
        sys.executable, str(task_tool), "freeze-new-work",
        "--actor", "统筹部/lead-thread", "--evidence", "duplicate-freeze-call",
    ])
    check(
        frozen.stdout.startswith("WORK_FREEZE_OK | frozen | history:1")
        and "idempotent" in frozen_again.stdout,
        "freeze-new-work was not durable and idempotent",
    )
    denied_enqueue = run([
        sys.executable, str(task_tool), "enqueue", "--department", "执行部",
        "--from-department", "统筹部", "--title", "冻结后新任务", "--node", "节点",
        "--details", "不得创建", "--acceptance-exit", "明确拒绝", "--failure-path", "派单已冻结",
        "--authorization-state", "none", "--actor", "统筹部/lead-thread",
    ], ok=False)
    denied_claim = run([
        sys.executable, str(task_tool), "claim", "--task-id", queued, "--claimed-by", "do-thread",
    ], ok=False)
    denied_impact = run([
        sys.executable, str(task_tool), "declare-impact", "--task-id", queued,
        "--expected-revision", "1", "--write-path", "docs/blocked.txt", "--base-revision", "HEAD",
    ], ok=False)
    denied_expand = run([
        sys.executable, str(SCAFFOLD), str(project), "--add-roles", "security",
    ], ok=False)
    denied_resume = run([
        sys.executable, str(task_tool), "resume", "--task-id", resumable,
    ], ok=False)
    denied_authorize = run([
        sys.executable, str(task_tool), "authorize", "--task-id", gated,
        "--state", "user_confirmed", "--evidence", "must-not-open-work-during-freeze",
    ], ok=False)
    control_path = collab / ".locks" / "dispatch-control.json"
    frozen_control_before_upgrade = control_path.read_bytes()
    frozen_upgrade = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    check(
        all("P0_FREEZE_ACTIVE" in result.stderr for result in (
            denied_enqueue, denied_claim, denied_impact, denied_expand, denied_resume, denied_authorize,
        ))
        and frozen_upgrade.stdout.startswith(f"UPGRADE_NOT_NEEDED | protocol:{PROTOCOL_VERSION}")
        and control_path.read_bytes() == frozen_control_before_upgrade
        and len(list((collab / "tasks").glob("TASK-*.json"))) == task_count_before
        and queued_path.read_bytes() == queued_before,
        "P0 freeze allowed new work or mutated the queued task: "
        + " | ".join(result.stderr.strip() for result in (
            denied_enqueue, denied_claim, denied_impact, denied_expand, denied_resume, denied_authorize,
        )),
    )

    completed = run([
        sys.executable, str(task_tool), "complete", "--task-id", claimed,
        "--artifact", "docs/stop-loss-result.txt", "--verified", "冻结前领取内容已安全完成",
        "--unverified", "未启动任何新任务", "--mistake-check", "已检查止血边界",
    ])
    run([
        sys.executable, str(task_tool), "ack", "--task-id", claimed,
        "--acknowledged-by", "统筹部/lead-thread",
    ])
    queued_payload = json.loads(queued_path.read_text(encoding="utf-8"))
    resolved = run([
        sys.executable, str(task_tool), "resolve", "--task-id", queued,
        "--state", "abandoned", "--expected-revision", str(queued_payload["revision"]),
        "--actor", "统筹部/lead-thread", "--reason", "止血清账",
        "--evidence", "user-requested-p0-freeze",
    ])
    rejected = run([
        sys.executable, str(task_tool), "authorize", "--task-id", gated,
        "--state", "user_rejected", "--evidence", "freeze-cleanup-rejection",
    ])
    for task_id in (gated, resumable):
        payload = json.loads((collab / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))
        run([
            sys.executable, str(task_tool), "resolve", "--task-id", task_id,
            "--state", "abandoned", "--expected-revision", str(payload["revision"]),
            "--actor", "统筹部/lead-thread", "--reason", "止血清账",
            "--evidence", "user-requested-p0-freeze",
        ])
    check(completed.stdout.startswith("TASK_STATE_OK") and resolved.stdout.startswith("TASK_RESOLUTION_OK"),
          "freeze blocked safe completion, acknowledgement, or cleanup")
    check(rejected.stdout.startswith("TASK_AUTH_RECORDED"),
          "freeze blocked a user_rejected authorization cleanup record")

    wrong_actor = run([
        sys.executable, str(task_tool), "unfreeze-new-work", "--actor", "执行部/fake",
        "--user-confirmation", "user-approved-resume",
    ], ok=False)
    check("actor 必须匹配" in wrong_actor.stderr, "non-lead actor could unfreeze new work")
    unfrozen = run([
        sys.executable, str(task_tool), "unfreeze-new-work", "--actor", "统筹部/lead-thread",
        "--user-confirmation", "user-approved-resume",
    ])
    control = json.loads((collab / ".locks" / "dispatch-control.json").read_text(encoding="utf-8"))
    check(
        unfrozen.stdout.startswith("WORK_UNFREEZE_OK | normal | history:2")
        and control["mode"] == "normal"
        and [event["action"] for event in control["history"]] == ["freeze", "unfreeze"]
        and [event["evidence"] for event in control["history"]]
        == ["user-requested-p0-freeze", "user-approved-resume"],
        "unfreeze did not require the registered lead or preserve append-only evidence",
    )
    control_before_missing_probe = control_path.read_bytes()
    control_path.unlink()
    denied_missing_control_enqueue = run([
        sys.executable, str(task_tool), "enqueue", "--department", "执行部",
        "--from-department", "统筹部", "--title", "控制缺失后新任务", "--node", "节点",
        "--details", "不得创建", "--acceptance-exit", "明确拒绝", "--failure-path", "控制缺失",
        "--authorization-state", "none", "--actor", "统筹部/lead-thread",
    ], ok=False)
    denied_missing_control_expand = run([
        sys.executable, str(SCAFFOLD), str(project), "--add-roles", "security",
    ], ok=False)
    control_path.write_bytes(control_before_missing_probe)
    check(
        "派单控制缺失" in denied_missing_control_enqueue.stderr
        and "派单控制缺失" in denied_missing_control_expand.stderr,
        "missing dispatch control failed open for enqueue or team expansion",
    )
    resumed_intake = enqueue(task_tool, "用户解冻后的单一恢复任务")
    check(resumed_intake.startswith("TASK-"), "explicit unfreeze did not restore intake")


def verify_product_development_boundary(root: Path) -> None:
    deprecated = make_project(root, "deprecated-ai-role")
    denied = run([
        sys.executable, str(SCAFFOLD), str(deprecated), "--profile", "AI 产品",
        "--roles", "lead,product,design,dev,ai,test", "--session-mode", "manual",
        "--foundation-file", "docs/spec.md",
    ], ok=False)
    check(
        "已取消独立角色" in denied.stderr
        and "系统级技术规划归产品部" in denied.stderr
        and "代码与集成实现归开发部" in denied.stderr,
        "deprecated AI department was accepted or its replacement boundary regressed",
    )
    check(not (deprecated / "docs" / "collaboration").exists(), "failed deprecated-role scaffold left collaboration files")

    project = make_project(root, "ai-product-without-ai-department")
    scaffold(project, "lead,product,design,dev,test")
    collab = project / "docs" / "collaboration"
    check(not (collab / "部门" / "AI工程部").exists(), "AI department was generated")
    product = (collab / "部门" / "产品部" / "岗位说明.md").read_text(encoding="utf-8")
    development = (collab / "部门" / "开发部" / "岗位说明.md").read_text(encoding="utf-8")
    check(
        all(term in product for term in (
            "整个产品规划", "产品调研", "系统级技术实现路径", "整体架构", "模块/数据/接口边界",
            "技术选型方案", "迁移/回滚", "架构类 ADR/决策合同", "不写正式业务代码",
            "draft→proposed→accepted→superseded", "用户确认和证据齐全后才进入 accepted",
        )),
        "product ownership omitted research, system architecture, ADR, or no-production-code boundaries",
    )
    check(all(term in development for term in ("模型/API 接入", "Prompt", "RAG", "Agent 链路", "评测集")),
          "development role does not own the full AI implementation")
    check(
        all(term in development for term in (
            "开工前复核", "可行性", "函数、类、算法、代码组织", "提交证据、影响与优化方案",
            "经统筹退回产品部修订", "不静默修改", "系统级架构 ADR",
            "不得无授权重写系统级 ADR 的正文、状态或路线",
        )),
        "development role omitted feasibility review, code-level ownership, or architecture-change escalation",
    )
    check(
        "测试部或安全部做独立放行" in product
        and "最终质量、安全或发布背书" in development,
        "product or development role can still self-certify an independent gate",
    )

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


def verify_role_policy_upgrade_guard(root: Path) -> None:
    managed_project = make_project(root, "managed-role-policy-forward-upgrade")
    scaffold(managed_project, "lead,product,design,dev,test")
    managed_collab = managed_project / "docs" / "collaboration"
    product_relative = "部门/产品部/岗位说明.md"
    managed_product = managed_collab / product_relative
    legacy_template = """# 产品部岗位说明

## 负责什么

负责整个产品规划和 AI 行为验收目标；技术实现方式交开发部。

## 输出

产品方案/架构。

## 禁止写入

docs/decisions/ 技术决策定稿。
"""
    managed_product.write_text(legacy_template, encoding="utf-8")
    managed_protocol_path = managed_collab / "协议版本.json"
    managed_protocol = json.loads(managed_protocol_path.read_text(encoding="utf-8"))
    managed_protocol["protocol_version"] = PREVIOUS_PROTOCOL_VERSION
    managed_protocol["managed_files"][product_relative]["sha256"] = hashlib.sha256(
        managed_product.read_bytes()
    ).hexdigest()
    managed_protocol_path.write_text(
        json.dumps(managed_protocol, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    managed_state_path = managed_collab / "会话启动状态.json"
    managed_state = json.loads(managed_state_path.read_text(encoding="utf-8"))
    managed_state["protocol_version"] = PREVIOUS_PROTOCOL_VERSION
    managed_state_path.write_text(
        json.dumps(managed_state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    managed_guide = managed_project / "docs" / "agent-guide.md"
    managed_guide.write_text(
        managed_guide.read_text(encoding="utf-8").replace(
            f"受管协议版本:{PROTOCOL_VERSION}", f"受管协议版本:{PREVIOUS_PROTOCOL_VERSION}",
        ),
        encoding="utf-8",
    )
    managed_upgrade = run([
        sys.executable, str(SCAFFOLD), str(managed_project), "--upgrade-collaboration",
    ])
    upgraded_product = managed_product.read_text(encoding="utf-8")
    upgraded_lead = (managed_collab / "部门" / "统筹部" / "岗位说明.md").read_text(encoding="utf-8")
    upgraded_readme = (managed_collab / "README.md").read_text(encoding="utf-8")
    upgraded_protocol = json.loads(managed_protocol_path.read_text(encoding="utf-8"))
    check(
        managed_upgrade.stdout.startswith("UPGRADE_OK |")
        and "系统级技术实现路径" in upgraded_product
        and "架构类 ADR/决策合同" in upgraded_product
        and "技术实现方式交开发部" not in upgraded_product
        and "结果 / 需要你做什么 / 还需注意" in upgraded_lead
        and "内部任务号、状态词、哈希、命令、日志和协议默认不展开" in upgraded_readme
        and upgraded_protocol["protocol_version"] == PROTOCOL_VERSION
        and upgraded_protocol["role_policy_overlays"] == {},
        "managed legacy product policy did not forward-upgrade to the corrected responsibility contract",
    )

    custom_project = make_project(root, "custom-role-policy-fail-closed")
    scaffold(custom_project, "lead,product,design,dev,test")
    custom_collab = custom_project / "docs" / "collaboration"
    custom_product = custom_collab / product_relative
    custom_product.write_text(
        custom_product.read_text(encoding="utf-8")
        + "\n## 项目定制职责\n\n保留本项目已经确认的产品部行业合规规划职责。\n",
        encoding="utf-8",
    )
    custom_bytes = custom_product.read_bytes()
    custom_protocol_path = custom_collab / "协议版本.json"
    custom_protocol_bytes = custom_protocol_path.read_bytes()
    blocked = run([
        sys.executable, str(SCAFFOLD), str(custom_project), "--upgrade-collaboration",
    ], ok=False)
    check(
        "不能猜测项目想保留什么" in blocked.stderr
        and "--role-policy-overlay-file" in blocked.stderr
        and custom_product.read_bytes() == custom_bytes
        and custom_protocol_path.read_bytes() == custom_protocol_bytes
        and not (custom_collab / "升级备份").exists(),
        "custom role policy was not rejected before upgrade side effects",
    )
    overlay_file = root / "product-role-overlay.json"
    overlay_payload = {
        "product": {
            "schema_version": 1,
            "reviewed_base_contract_version": "product-dev-2",
            "authorization_evidence": "用户确认保留行业合规规划职责",
            "additions": [{
                "id": "industry-compliance-planning",
                "section": "mission",
                "text": "保留本项目已经确认的产品部行业合规规划职责。",
            }],
        },
    }
    overlay_file.write_text(json.dumps(overlay_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    preserved = run([
        sys.executable, str(SCAFFOLD), str(custom_project), "--upgrade-collaboration",
        "--role-policy-overlay-file", str(overlay_file),
    ])
    preserved_protocol = json.loads(custom_protocol_path.read_text(encoding="utf-8"))
    rendered_with_overlay = custom_product.read_text(encoding="utf-8")
    check(
        preserved.stdout.startswith("UPGRADE_OK |")
        and "ROLE_POLICY_OVERLAYS_APPLIED | product" in preserved.stdout
        and "系统级技术实现路径" in rendered_with_overlay
        and "行业合规规划职责" in rendered_with_overlay
        and "项目定制职责" in rendered_with_overlay
        and preserved_protocol["role_policy_overlays"] == overlay_payload
        and preserved_protocol["managed_files"][product_relative]["sha256"]
        == hashlib.sha256(custom_product.read_bytes()).hexdigest(),
        "explicit additive role policy overlay was not rendered and registered",
    )
    repeated = run([
        sys.executable, str(SCAFFOLD), str(custom_project), "--upgrade-collaboration",
    ])
    check(
        repeated.stdout.startswith("UPGRADE_NOT_NEEDED |")
        and custom_product.read_text(encoding="utf-8") == rendered_with_overlay,
        "registered role policy overlay did not remain stable on the next upgrade",
    )
    run([
        sys.executable, str(SCAFFOLD), str(custom_project), "--add-roles", "research",
    ])
    after_add_protocol = json.loads(custom_protocol_path.read_text(encoding="utf-8"))
    check(
        after_add_protocol["role_policy_overlays"] == overlay_payload
        and custom_product.read_text(encoding="utf-8") == rendered_with_overlay,
        "add-role transaction discarded a registered project role policy overlay",
    )
    custom_product.write_text(rendered_with_overlay + "\n未授权的直接修改\n", encoding="utf-8")
    registered_drift = run([
        sys.executable, str(SCAFFOLD), str(custom_project), "--upgrade-collaboration",
    ], ok=False)
    check(
        "不能猜测项目想保留什么" in registered_drift.stderr,
        "a registered overlay incorrectly allowed later direct role-policy edits",
    )


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
    gated_list = run([sys.executable, str(tool), "list"])
    check(f"{gated} | 待用户确认" in gated_list.stdout,
          "list mislabeled an authorization-gated task as claimable")
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
    hot_path_missing_department = run([
        sys.executable, str(tool), "enqueue", "--department", "执行部", "--from-department", "统筹部",
        "--title", "不应创建", "--node", "单节点", "--details", "测试",
        "--acceptance-exit", "可见", "--failure-path", "结构缺失", "--authorization-state", "none",
    ])
    created_while_history_corrupt = task_id_from(hot_path_missing_department)
    check("TASK_INDEX_STALE" in hot_path_missing_department.stderr,
          "hot-path enqueue did not warn that the derived inbox could not refresh")
    doctor_missing_department = run([sys.executable, str(tool), "doctor"], ok=False)
    check("任务字段缺失" in doctor_missing_department.stderr,
          "full-history doctor did not reject a missing canonical department")
    (collab / "tasks" / f"{created_while_history_corrupt}.json").unlink()
    schema_path.write_text(json.dumps(original, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    before = len(list((collab / "tasks").glob("TASK-*.json")))
    corrupt = collab / "tasks" / "TASK-20200101-BROKEN.json"
    corrupt.write_text("{broken", encoding="utf-8")
    hot_path_corrupt = run([
        sys.executable, str(tool), "enqueue", "--department", "执行部", "--from-department", "统筹部",
        "--title", "不应创建", "--node", "单节点", "--details", "测试",
        "--acceptance-exit", "可见", "--failure-path", "损坏", "--authorization-state", "none",
    ])
    created_while_json_corrupt = task_id_from(hot_path_corrupt)
    check("TASK_INDEX_STALE" in hot_path_corrupt.stderr,
          "corrupt cold history did not produce an explicit stale-index warning")
    doctor_corrupt = run([sys.executable, str(tool), "doctor"], ok=False)
    check("JSON 无效" in doctor_corrupt.stderr,
          "full-history doctor did not reject corrupt canonical history")
    (collab / "tasks" / f"{created_while_json_corrupt}.json").unlink()
    check(len(list((collab / "tasks").glob("TASK-*.json"))) == before + 1,
          "hot-path isolation lost or created unexpected TASK files")
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
status: pending
date: {dt.date.today().isoformat()}
related_task: {audit_id}
decision: 待定
tags: []
summary: 待定
---

# 审核草稿
""", encoding="utf-8")
    pending_report = run([
        sys.executable, str(tool), "complete", "--task-id", audit_id,
        "--artifact", str(report.relative_to(project)), "--report", str(report.relative_to(project)),
        "--verified", "仅完成草稿", "--unverified", "最终结论", "--mistake-check", "无命中",
    ], ok=False)
    check("status 必须为 final" in pending_report.stderr,
          "pending audit report crossed the completion gate")
    report.write_text(f"""---
type: audit_report
department: 检验部
target: Agent Team
status: final
date: {dt.date.today().isoformat()}
related_task: {audit_id}
decision: pass
tags: []
summary: 待填一句话结论
---

# 占位摘要
""", encoding="utf-8")
    placeholder_summary = run([
        sys.executable, str(tool), "complete", "--task-id", audit_id,
        "--artifact", str(report.relative_to(project)), "--report", str(report.relative_to(project)),
        "--verified", "仅完成占位报告", "--unverified", "真实结论", "--mistake-check", "无命中",
    ], ok=False)
    check("summary 仍是占位内容" in placeholder_summary.stderr,
          "placeholder audit summary crossed the completion gate")
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
    downgraded_state = dict(state)
    downgraded_state["protocol_version"] = PREVIOUS_PROTOCOL_VERSION
    state_path.write_text(json.dumps(downgraded_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    downgraded_bytes = state_path.read_bytes()
    rejected_downgrade = run([
        sys.executable, str(session), "set-notification", "--department", "执行部",
        "--mode", "auto", "--evidence", "must-reject-old-protocol",
    ], ok=False)
    check(
        "版本无效" in rejected_downgrade.stderr and state_path.read_bytes() == downgraded_bytes,
        "generated session tool wrote a downgraded protocol state",
    )
    state_path.write_text(state_text, encoding="utf-8")
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

    frozen_temporary = enqueue_dev("止血时不得推进的临时任务", "user_confirmed", "user-requested-temporary")
    frozen_path = collab / "tasks" / f"{frozen_temporary}.json"
    frozen_before = frozen_path.read_bytes()
    run([
        sys.executable, str(task_tool), "freeze-new-work", "--actor", "统筹部/lead-thread",
        "--evidence", "user-requested-p0-freeze",
    ])
    denied_frozen_temporary = run([
        sys.executable, str(temporary_tool), "preflight", "--task-id", frozen_temporary,
        "--parent-department", "开发部", "--write-path", "app/frozen.py",
    ], ok=False)
    denied_frozen_cleanup = run([
        sys.executable, str(temporary_tool), "cleanup", "--task-id", frozen_temporary,
        "--evidence", "must-not-delete-evidence-during-freeze",
    ], ok=False)
    denied_frozen_reconcile_cleanup = run([
        sys.executable, str(temporary_tool), "reconcile-cleanup", "--task-id", frozen_temporary,
    ], ok=False)
    check(
        all("P0_FREEZE_ACTIVE" in result.stderr for result in (
            denied_frozen_temporary, denied_frozen_cleanup, denied_frozen_reconcile_cleanup,
        ))
        and frozen_path.read_bytes() == frozen_before,
        "temporary executor bypassed P0 freeze, deleted evidence, or mutated its TASK",
    )
    control_path = collab / ".locks" / "dispatch-control.json"
    control_before = control_path.read_bytes()
    corrupted = json.loads(control_before.decode("utf-8"))
    corrupted["history"].insert(0, {
        "at": corrupted["history"][0]["at"], "action": "unfreeze",
        "actor": "统筹部/lead-thread", "evidence": "corrupted-order",
    })
    control_path.write_text(json.dumps(corrupted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    corrupt_history_denied = run([
        sys.executable, str(temporary_tool), "preflight", "--task-id", frozen_temporary,
        "--parent-department", "开发部", "--write-path", "app/frozen.py",
    ], ok=False)
    control_path.write_bytes(control_before)
    check("派单控制历史顺序无效" in corrupt_history_denied.stderr,
          "temporary executor accepted a corrupted freeze history")
    control_path.unlink()
    missing_control_denied = run([
        sys.executable, str(temporary_tool), "preflight", "--task-id", frozen_temporary,
        "--parent-department", "开发部", "--write-path", "app/frozen.py",
    ], ok=False)
    control_path.write_bytes(control_before)
    check("派单控制缺失" in missing_control_denied.stderr,
          "temporary executor failed open after dispatch control disappeared")
    run([
        sys.executable, str(task_tool), "unfreeze-new-work", "--actor", "统筹部/lead-thread",
        "--user-confirmation", "user-approved-test-resume",
    ])
    frozen_payload = json.loads(frozen_path.read_text(encoding="utf-8"))
    run([
        sys.executable, str(task_tool), "resolve", "--task-id", frozen_temporary,
        "--state", "abandoned", "--expected-revision", str(frozen_payload["revision"]),
        "--actor", "统筹部/lead-thread", "--reason", "止血门禁测试清账",
        "--evidence", "verification-cleanup",
    ])

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
    state_path = collab / "会话启动状态.json"
    audit_state_bytes = state_path.read_bytes()
    forged_audit_state = json.loads(audit_state_bytes)
    forged_audit_state["departments"]["测试部"]["role_id"] = "dev"
    state_path.write_text(json.dumps(forged_audit_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    forged_audit_task = run([
        sys.executable, str(temporary_tool), "record-integration-test", "--task-id", temporary,
        "--tested-base", tested_base, "--commit", delivery, "--test-definition", "compile and targeted regression",
        "--environment", "temporary verifier", "--evidence", report_relative, "--result", "pass",
        "--test-task-id", test_task, "--report", report_relative,
    ], ok=False)
    check("不属于审核层" in forged_audit_task.stderr,
          "a self-reported audit completion class bypassed the registered audit-role check")
    state_path.write_bytes(audit_state_bytes)
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
    ensure_lead_registered(task_tool)
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
          f"{PREVIOUS_PROTOCOL_VERSION} -> {PROTOCOL_VERSION} upgrade rewrote session step, evidence, previous thread, or operation truth")

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


def verify_transaction_recovery_attacks(root: Path) -> None:
    forged_project = make_project(root, "forged-add-role-marker")
    scaffold(forged_project, "lead,product,design,dev,test")
    forged_collab = forged_project / "docs" / "collaboration"
    truth_names = ("协议版本.json", "部门表.md", "会话启动清单.md", "路由表.md", "会话启动状态.json")
    truth_before = {name: (forged_collab / name).read_bytes() for name in truth_names}
    product = forged_collab / "部门" / "产品部"
    product_before = {
        entry.relative_to(product).as_posix(): entry.read_bytes()
        for entry in product.rglob("*") if entry.is_file()
    }
    operation_id = "ADD-20260719T170000-ABCDEF12"
    generated = {
        entry.name: hashlib.sha256(entry.read_bytes()).hexdigest()
        for entry in product.iterdir() if entry.is_file()
    }
    forged_marker = {
        "schema_version": 2,
        "kind": "add_roles",
        "operation_id": operation_id,
        "phase": "applying",
        "created_roles": ["product"],
        "originals": {
            name: truth_before[name].decode("utf-8") for name in truth_names
        },
        "generated_files": {"product": generated},
    }
    marker_path = forged_collab / ".add-roles-transaction.json"
    marker_path.write_text(json.dumps(forged_marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    denied = run([
        sys.executable, str(SCAFFOLD), str(forged_project), "--add-roles", "research",
    ], ok=False)
    check(
        denied.returncode == 10
        and all((forged_collab / name).read_bytes() == data for name, data in truth_before.items())
        and {
            entry.relative_to(product).as_posix(): entry.read_bytes()
            for entry in product.rglob("*") if entry.is_file()
        } == product_before
        and marker_path.exists(),
        "a forged add-role marker mutated truth or deleted an existing department",
    )

    crash_project = make_project(root, "durable-upgrade-crash-recovery")
    scaffold(crash_project)
    crash_collab = crash_project / "docs" / "collaboration"
    protocol_path = crash_collab / "协议版本.json"
    state_path = crash_collab / "会话启动状态.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    protocol["protocol_version"] = PREVIOUS_PROTOCOL_VERSION
    protocol_path.write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["protocol_version"] = PREVIOUS_PROTOCOL_VERSION
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    guide = crash_project / "docs" / "agent-guide.md"
    guide.write_text(guide.read_text(encoding="utf-8").replace(
        f"受管协议版本:{PROTOCOL_VERSION}", f"受管协议版本:{PREVIOUS_PROTOCOL_VERSION}",
    ), encoding="utf-8")
    module_spec = importlib.util.spec_from_file_location("agent_team_scaffold_crash_probe", SCAFFOLD)
    check(module_spec is not None and module_spec.loader is not None, "could not load scaffold for crash probe")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    real_write = module.write_utf8_atomic

    def terminate_after_marker(path, text, *, mode=None):
        if Path(path) == crash_collab / "README.md":
            raise SystemExit(99)
        return real_write(path, text, mode=mode)

    module.write_utf8_atomic = terminate_after_marker
    try:
        try:
            module.run_upgrade(crash_collab)
        except SystemExit as exc:
            check(exc.code == 99, "crash probe exited unexpectedly")
    finally:
        module.write_utf8_atomic = real_write
    check((crash_collab / ".upgrade-transaction.json").is_file(),
          "upgrade crash did not leave a durable recovery marker")
    recovered = run([sys.executable, str(SCAFFOLD), str(crash_project), "--upgrade-collaboration"])
    check(
        "UPGRADE_RECOVERY_OK" in recovered.stdout
        and "UPGRADE_OK" in recovered.stdout
        and not (crash_collab / ".upgrade-transaction.json").exists()
        and json.loads(protocol_path.read_text(encoding="utf-8"))["protocol_version"] == PROTOCOL_VERSION,
        "next invocation did not recover the interrupted upgrade before retrying",
    )


def verify_task_supersede(root: Path) -> None:
    project = make_project(root, "queued-task-supersede")
    scaffold(project)
    collab = project / "docs" / "collaboration"
    task_tool = collab / "scripts" / "agent_team_task.py"
    session_tool = collab / "scripts" / "agent_team_session.py"
    for step, extra in (
        ("created", ["--thread-id", "lead-thread"]),
        ("onboarded", ["--thread-id", "lead-thread"]),
        ("registered", ["--thread-id", "lead-thread"]),
    ):
        run([
            sys.executable, str(session_tool), "mark", "--department", "统筹部",
            "--step", step, "--evidence", f"verify-{step}", *extra,
        ])
    old_id = enqueue(task_tool, "已被后续主链取代的 queued 任务")
    queued_replacement = enqueue(task_tool, "尚未形成后续事实")
    replacement_id = enqueue(task_tool, "已形成的后续主链")
    run([sys.executable, str(task_tool), "claim", "--task-id", replacement_id, "--claimed-by", "do-thread"])
    old_path = collab / "tasks" / f"{old_id}.json"
    replacement_path = collab / "tasks" / f"{replacement_id}.json"
    old_before = json.loads(old_path.read_text(encoding="utf-8"))
    replacement_before = replacement_path.read_bytes()
    inbox_paths = sorted((collab / "部门").glob("*/收件箱.md"))

    def snapshot() -> tuple[bytes, bytes, dict[Path, bytes]]:
        return old_path.read_bytes(), replacement_path.read_bytes(), {path: path.read_bytes() for path in inbox_paths}

    def denied(extra: list[str], expected: str) -> None:
        before = snapshot()
        result = run([
            sys.executable, str(task_tool), "supersede", "--task-id", old_id,
            "--replacement-task", replacement_id, "--expected-revision", "1",
            "--expected-replacement-revision", "2", "--actor", "统筹部/lead-thread",
            "--reason", "被后续主链取代", "--evidence", "后续 TASK 已领取",
            *extra,
        ], ok=False)
        check(expected in result.stderr and snapshot() == before,
              f"supersede failure path mutated truth: {expected}")

    denied(["--replacement-task", "TASK-20990101-NOTFND"], "任务不存在")
    denied(["--actor", "开发部/dev-thread"], "actor 必须匹配")
    denied(["--expected-revision", "99"], "expected-revision")
    denied(["--expected-replacement-revision", "99"], "expected-replacement-revision")
    denied(["--replacement-task", queued_replacement, "--expected-replacement-revision", "1"], "未形成可审计后续事实")

    receipt = run([
        sys.executable, str(task_tool), "supersede", "--task-id", old_id,
        "--replacement-task", replacement_id, "--expected-revision", "1",
        "--expected-replacement-revision", "2", "--actor", "统筹部/lead-thread",
        "--reason", "被后续主链取代", "--evidence", "后续 TASK 已领取并形成执行事实",
    ])
    resolved = json.loads(old_path.read_text(encoding="utf-8"))
    preserved_fields = set(old_before) - {"resolution", "updated_at", "revision"}
    check(
        receipt.stdout.startswith("TASK_RESOLUTION_OK | state=superseded")
        and resolved["execution_state"] == "queued"
        and resolved["resolution"]["replacement_task_id"] == replacement_id
        and all(resolved[field] == old_before[field] for field in preserved_fields)
        and replacement_path.read_bytes() == replacement_before
        and old_id not in (collab / "部门" / "执行部" / "收件箱.md").read_text(encoding="utf-8"),
        "happy-path supersede did not preserve source truth, replacement bytes, or inbox semantics",
    )
    listed = run([sys.executable, str(task_tool), "list"])
    check(f"{old_id} | 已取代" in listed.stdout, "resolved queued task was not auditable in list output")
    for command in (
        ["claim", "--claimed-by", "do-thread"],
        ["authorize", "--state", "user_confirmed", "--evidence", "late-change"],
        ["declare-impact", "--expected-revision", "2", "--write-path", "app/x.py", "--base-revision", "HEAD"],
    ):
        rejected = run([sys.executable, str(task_tool), command[0], "--task-id", old_id, *command[1:]], ok=False)
        check("已收口" in rejected.stderr,
              f"resolved task re-entered {command[0]}: {rejected.stderr.strip()}")

    stale_inbox = collab / "部门" / "执行部" / "收件箱.md"
    stale_inbox.write_text(stale_inbox.read_text(encoding="utf-8") + f"\n- stale {old_id}\n", encoding="utf-8")
    wal = collab / ".locks" / "task-index-transaction.json"
    wal.write_text(json.dumps({
        "schema_version": 1, "kind": "task-index-refresh",
        "operation_id": "IDX-20260719T170000-ABCDEF12", "task_id": old_id,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    recovered = run([sys.executable, str(task_tool), "list"])
    check(
        "TASK_INDEX_RECOVERY_OK" in recovered.stdout
        and old_id not in stale_inbox.read_text(encoding="utf-8")
        and not wal.exists(),
        "task-index WAL did not rebuild a stale inbox from TASK truth",
    )

    legacy_project = make_project(root, "legacy-task-without-resolution")
    scaffold(legacy_project)
    legacy_collab = legacy_project / "docs" / "collaboration"
    legacy_tool = legacy_collab / "scripts" / "agent_team_task.py"
    legacy_id = enqueue(legacy_tool, "1.4.7 无 resolution 任务")
    legacy_path = legacy_collab / "tasks" / f"{legacy_id}.json"
    legacy_payload = json.loads(legacy_path.read_text(encoding="utf-8"))
    legacy_payload.pop("resolution", None)
    legacy_path.write_text(json.dumps(legacy_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    legacy_bytes = legacy_path.read_bytes()
    for name in ("协议版本.json", "会话启动状态.json"):
        path = legacy_collab / name
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["protocol_version"] = PREVIOUS_PROTOCOL_VERSION
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    guide = legacy_project / "docs" / "agent-guide.md"
    guide.write_text(guide.read_text(encoding="utf-8").replace(
        f"受管协议版本:{PROTOCOL_VERSION}", f"受管协议版本:{PREVIOUS_PROTOCOL_VERSION}",
    ), encoding="utf-8")
    upgraded = run([sys.executable, str(SCAFFOLD), str(legacy_project), "--upgrade-collaboration"])
    check(upgraded.stdout.startswith("UPGRADE_OK |") and legacy_path.read_bytes() == legacy_bytes,
          "1.4.7 task without resolution was rewritten or blocked during forward upgrade")
    malformed = json.loads(legacy_path.read_text(encoding="utf-8"))
    malformed["resolution"] = {"state": "superseded"}
    legacy_path.write_text(json.dumps(malformed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    malformed_bytes = legacy_path.read_bytes()
    for name in ("协议版本.json", "会话启动状态.json"):
        path = legacy_collab / name
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["protocol_version"] = PREVIOUS_PROTOCOL_VERSION
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    guide.write_text(guide.read_text(encoding="utf-8").replace(
        f"受管协议版本:{PROTOCOL_VERSION}", f"受管协议版本:{PREVIOUS_PROTOCOL_VERSION}",
    ), encoding="utf-8")
    backups_before = len(list((legacy_collab / "升级备份").iterdir()))
    malformed_denied = run([sys.executable, str(SCAFFOLD), str(legacy_project), "--upgrade-collaboration"], ok=False)
    check(
        "收口轴结构无效" in malformed_denied.stderr
        and legacy_path.read_bytes() == malformed_bytes
        and len(list((legacy_collab / "升级备份").iterdir())) == backups_before,
        "malformed resolution passed upgrade preflight or caused side effects",
    )


def verify_protocol_149_guards(root: Path) -> None:
    project = make_project(root, "protocol-149-guards")
    scaffold(project, "lead,do,review,research")
    collab = project / "docs" / "collaboration"
    task_tool = collab / "scripts" / "agent_team_task.py"
    session_tool = collab / "scripts" / "agent_team_session.py"
    temporary_tool = collab / "scripts" / "agent_team_temporary.py"
    non_git_help = run([sys.executable, str(temporary_tool), "--help"])
    non_git_pending = run([sys.executable, str(temporary_tool), "pending-archives"])
    check(
        "pending-archives" in non_git_help.stdout
        and non_git_pending.stdout.strip() == "NO_PENDING_THREAD_ARCHIVES",
        "non-Git read-only temporary commands still required eager Git discovery",
    )
    ensure_lead_registered(task_tool)

    actor_help_checks = [
        (task_tool, "enqueue", "--actor"),
        (task_tool, "authorize", "--actor"),
        (task_tool, "supersede", "--actor"),
        (task_tool, "resolve", "--actor"),
        (task_tool, "freeze-new-work", "--actor"),
        (task_tool, "unfreeze-new-work", "--actor"),
        (task_tool, "ack", "--acknowledged-by"),
        (session_tool, "set-notification", "--actor"),
        (session_tool, "retire", "--actor"),
    ]
    for tool, command, option in actor_help_checks:
        help_result = run([sys.executable, str(tool), command, "--help"])
        check(
            option in help_result.stdout and "统筹部/会话ID" in help_result.stdout,
            f"{command} help omitted the registered lead actor format",
        )

    wrong_actor = run([
        sys.executable, str(task_tool), "enqueue", "--department", "执行部",
        "--from-department", "统筹部", "--title", "伪造派单", "--node", "节点",
        "--details", "不得创建", "--acceptance-exit", "拒绝", "--failure-path", "身份不符",
        "--authorization-state", "none", "--actor", "执行部/fake",
    ], ok=False)
    check("actor 必须匹配" in wrong_actor.stderr, "non-lead enqueue actor was accepted")

    rejected = enqueue(task_tool, "用户拒绝后的普通任务", "user_required")
    run([
        sys.executable, str(task_tool), "authorize", "--task-id", rejected,
        "--state", "user_confirmed", "--evidence", "user-started",
    ])
    run([sys.executable, str(task_tool), "claim", "--task-id", rejected, "--claimed-by", "do-thread"])
    run([sys.executable, str(task_tool), "block", "--task-id", rejected, "--reason", "等待用户最终决定"])
    payload = json.loads((collab / "tasks" / f"{rejected}.json").read_text(encoding="utf-8"))
    resolved = run([
        sys.executable, str(task_tool), "resolve", "--task-id", rejected,
        "--state", "rejected_by_user", "--expected-revision", str(payload["revision"]),
        "--actor", "统筹部/lead-thread", "--reason", "用户明确停止",
        "--evidence", "user-message-rejected",
    ])
    resolved_payload = json.loads((collab / "tasks" / f"{rejected}.json").read_text(encoding="utf-8"))
    check(
        resolved.stdout.startswith("TASK_RESOLUTION_OK | state=rejected_by_user")
        and resolved_payload["execution_state"] == "blocked"
        and resolved_payload["resolution"]["state"] == "rejected_by_user"
        and resolved_payload["authorization_state"] == "user_rejected"
        and resolved_payload["authorization_evidence"] == "user-message-rejected",
        "user-rejected ordinary TASK did not close while preserving execution history",
    )

    registry = collab / "部门表.md"
    registry_before_rebuild = registry.read_bytes()
    registry.unlink()
    rebuilt = run([sys.executable, str(session_tool), "rebuild-registry"])
    check(
        rebuilt.stdout.startswith("SESSION_REGISTRY_OK")
        and registry.read_bytes() == registry_before_rebuild,
        "session registry was not reconstructed exactly from durable state",
    )

    for step in ("created", "onboarded", "registered"):
        run([
            sys.executable, str(session_tool), "mark", "--department", "研究部", "--step", step,
            "--thread-id", "research-thread", "--evidence", f"research-{step}",
        ])
    retired = run([
        sys.executable, str(session_tool), "retire", "--department", "研究部",
        "--actor", "统筹部/lead-thread",
        "--evidence", "host=test-host thread_id=research-thread archived=true",
    ])
    check(retired.stdout.startswith("SESSION_RETIRED"), "registered idle department could not retire safely")
    deactivated = run([
        sys.executable, str(SCAFFOLD), str(project), "--deactivate-roles", "research",
        "--deactivation-evidence", "user-confirmed-smaller-team",
    ])
    state = json.loads((collab / "会话启动状态.json").read_text(encoding="utf-8"))
    check(
        deactivated.stdout.startswith("DEACTIVATE_ROLES_OK")
        and state["departments"]["研究部"]["active"] is False
        and (collab / "部门" / "研究部").is_dir()
        and "研究部" not in (collab / "路由表.md").read_text(encoding="utf-8")
        and "已停用" in (collab / "部门表.md").read_text(encoding="utf-8"),
        "department deactivation lost history or left the role active",
    )
    inactive_enqueue = run([
        sys.executable, str(task_tool), "enqueue", "--department", "研究部",
        "--from-department", "统筹部", "--title", "停用后派单", "--node", "节点",
        "--details", "不得创建", "--acceptance-exit", "拒绝", "--failure-path", "部门停用",
        "--authorization-state", "none",
    ], ok=False)
    check("部门已停用" in inactive_enqueue.stderr, "inactive department accepted a new task")
    break_layers = run([
        sys.executable, str(SCAFFOLD), str(project), "--deactivate-roles", "do",
        "--deactivation-evidence", "user-confirmed",
    ], ok=False)
    check("三层最小结构" in break_layers.stderr, "deactivation broke the three-layer minimum")
    upgraded = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    upgraded_state = json.loads((collab / "会话启动状态.json").read_text(encoding="utf-8"))
    check(
        upgraded.stdout.startswith("UPGRADE_NOT_NEEDED")
        and upgraded_state["departments"]["研究部"]["active"] is False
        and "研究部" not in (collab / "路由表.md").read_text(encoding="utf-8"),
        "same-version maintenance reactivated a deactivated department",
    )
    reactivated = run([sys.executable, str(SCAFFOLD), str(project), "--add-roles", "research"])
    reactivated_state = json.loads((collab / "会话启动状态.json").read_text(encoding="utf-8"))
    check(
        "已重新启用部门" in reactivated.stdout
        and reactivated_state["departments"]["研究部"]["active"] is True
        and "研究部" in (collab / "路由表.md").read_text(encoding="utf-8"),
        "explicit add-roles could not reactivate a preserved department identity",
    )
    research_history = reactivated_state["departments"]["研究部"]["lifecycle_history"]
    check(
        [event["event"] for event in research_history] == ["retired", "deactivated", "reactivated"]
        and research_history[0]["thread_id"] == "research-thread"
        and research_history[1]["evidence"] == "user-confirmed-smaller-team",
        "retire/deactivate/reactivate evidence was overwritten instead of appended",
    )
    for step in ("created", "onboarded", "registered"):
        run([
            sys.executable, str(session_tool), "mark", "--department", "研究部", "--step", step,
            "--thread-id", "research-thread-v2", "--evidence", f"research-v2-{step}",
        ])
    registered_again = json.loads((collab / "会话启动状态.json").read_text(encoding="utf-8"))
    check(
        registered_again["departments"]["研究部"]["lifecycle_history"] == research_history,
        "new session registration erased preserved department lifecycle history",
    )
    doctor = run([sys.executable, str(task_tool), "doctor"])
    check(doctor.stdout.startswith("TASK_DOCTOR_OK"), "full-history doctor failed after legal resolution")


def verify_protocol_150_core(root: Path) -> None:
    def task_args(
        task_tool: Path, department: str, title: str, *, kind: str = "owner",
        slice_id: str = "", gate_type: str = "", required_gates: tuple[str, ...] = (),
    ) -> list[str]:
        args = [
            sys.executable, str(task_tool), "enqueue", "--actor", "统筹部/lead-thread",
            "--department", department, "--from-department", "统筹部",
            "--title", title, "--node", "2.1 单切片", "--details", "验证机械状态机",
            "--acceptance-exit", "最终出口可复验", "--failure-path", "身份或候选错误时拒绝",
            "--authorization-state", "none", "--task-kind", kind,
        ]
        if slice_id:
            args += ["--slice-id", slice_id]
        if gate_type:
            args += ["--gate-type", gate_type]
        for gate in required_gates:
            args += ["--required-gate", gate]
        return args

    def write_candidate(project: Path, candidate_id: str, label: str) -> tuple[Path, str]:
        artifact = project / "docs" / f"{label}.txt"
        artifact.write_text(f"candidate {candidate_id}\n", encoding="utf-8")
        artifact_sha = file_sha256(artifact)
        manifest = project / "docs" / f"{label}-manifest.json"
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "candidate_id": candidate_id,
            "artifact": {"path": artifact.relative_to(project).as_posix(), "sha256": artifact_sha, "kind": "file"},
            "source_revision": f"verify-{label}",
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return manifest, file_sha256(manifest)

    def write_gate_report(
        collab: Path, department: str, task_id: str, candidate_id: str, decision: str, label: str,
    ) -> Path:
        report = collab / "部门" / department / "报告" / f"{label}.md"
        report.write_text(f"""---
type: audit_report
department: {department}
target: Agent Team 2.1 candidate
status: final
date: {dt.date.today().isoformat()}
related_task: {task_id}
decision: {decision}
candidate_id: {candidate_id}
tags: [protocol-1.5]
summary: 已对指定候选执行独立反向探针并记录结论
---

# 独立审核

候选身份、失败路径和最终出口已按本次 verdict 复核。
""", encoding="utf-8")
        return report

    project = make_project(root, "protocol-150-stop-loss")
    scaffold(project, "lead,dev,test,security")
    collab = project / "docs" / "collaboration"
    task_tool = collab / "scripts" / "agent_team_task.py"
    session_tool = collab / "scripts" / "agent_team_session.py"
    for department, thread_id in (
        ("统筹部", "lead-thread"), ("开发部", "dev-thread"),
        ("测试部", "test-thread"), ("安全部", "security-thread"),
    ):
        ensure_department_registered(task_tool, department, thread_id)
    run([sys.executable, str(task_tool), "rebuild-index"])
    run([sys.executable, str(task_tool), "onboard-check", "--department", "开发部"])

    owner = task_id_from(run(task_args(
        task_tool, "开发部", "唯一 owner", required_gates=("test", "security"),
    )))
    slice_control_path = collab / ".locks" / "slice-control.json"
    slice_control = json.loads(slice_control_path.read_text(encoding="utf-8"))
    slice_id = slice_control["active_slice"]["slice_id"]
    phantom = run([
        sys.executable, str(task_tool), "claim", "--task-id", owner,
        "--claimed-by", "开发部/fake-thread-that-was-never-created",
    ], ok=False)
    check("当前已登记部门会话" in phantom.stderr, "phantom ordinary TASK identity was accepted")
    run([
        sys.executable, str(task_tool), "claim", "--task-id", owner,
        "--claimed-by", "开发部/dev-thread",
    ])
    second_owner = run(task_args(task_tool, "开发部", "不得出现的第二 owner"), ok=False)
    check("ACTIVE_SLICE_EXISTS" in second_owner.stderr, "a second active owner was accepted")

    run([
        sys.executable, str(session_tool), "begin-switch", "--department", "开发部",
        "--old-thread-id", "dev-thread", "--reason", "verify identity rebind",
    ])
    for step in ("created", "onboarded", "registered"):
        run([
            sys.executable, str(session_tool), "mark", "--department", "开发部", "--step", step,
            "--thread-id", "dev-thread-v2", "--evidence", f"verify-switch-{step}",
        ])
    drifted = run([
        sys.executable, str(task_tool), "block", "--task-id", owner,
        "--reason", "identity probe", "--actor", "开发部/dev-thread-v2",
    ], ok=False)
    check("rebind-owner" in drifted.stderr, "switched session silently inherited ordinary TASK ownership")
    owner_payload = json.loads((collab / "tasks" / f"{owner}.json").read_text(encoding="utf-8"))
    run([
        sys.executable, str(task_tool), "rebind-owner", "--task-id", owner,
        "--expected-revision", str(owner_payload["revision"]),
        "--actor", "开发部/dev-thread-v2", "--previous-actor", "开发部/dev-thread",
        "--evidence", "authorized switch and four-document onboarding",
    ])
    run([
        sys.executable, str(session_tool), "finish-switch", "--department", "开发部",
        "--new-thread-id", "dev-thread-v2",
        "--evidence", "host=verify thread_id=dev-thread archived=true",
    ])

    candidate_1 = "CAND-20260825-A10001"
    manifest_1, manifest_sha_1 = write_candidate(project, candidate_1, "candidate-a1")
    run([
        sys.executable, str(task_tool), "bind-candidate", "--task-id", owner,
        "--candidate-id", candidate_1, "--manifest", str(manifest_1.relative_to(project)),
        "--sha256", manifest_sha_1, "--actor", "开发部/dev-thread-v2",
    ])
    test_gate = task_id_from(run(task_args(
        task_tool, "测试部", "测试 gate", kind="gate", slice_id=slice_id, gate_type="test",
    )))
    security_gate = task_id_from(run(task_args(
        task_tool, "安全部", "安全 gate", kind="gate", slice_id=slice_id, gate_type="security",
    )))
    third_gate = run(task_args(
        task_tool, "测试部", "重复第三 gate", kind="gate", slice_id=slice_id, gate_type="test",
    ), ok=False)
    check("已存在" in third_gate.stderr or "最多两个" in third_gate.stderr,
          "a third or duplicate gate was accepted")
    run([sys.executable, str(task_tool), "claim", "--task-id", test_gate, "--claimed-by", "测试部/test-thread"])
    run([sys.executable, str(task_tool), "claim", "--task-id", security_gate, "--claimed-by", "安全部/security-thread"])
    wrong_candidate_report = write_gate_report(collab, "测试部", test_gate, candidate_1, "fail", "wrong-candidate")
    wrong_candidate = run([
        sys.executable, str(task_tool), "gate-verdict", "--task-id", test_gate,
        "--candidate-id", "CAND-20260825-BAD999", "--decision", "fail",
        "--report", str(wrong_candidate_report.relative_to(project)), "--evidence", "identity mismatch probe",
        "--actor", "测试部/test-thread",
    ], ok=False)
    check("唯一候选" in wrong_candidate.stderr, "gate verdict accepted a non-current candidate")
    report_fail_1 = write_gate_report(collab, "测试部", test_gate, candidate_1, "fail", "test-fail-1")
    run([
        sys.executable, str(task_tool), "gate-verdict", "--task-id", test_gate,
        "--candidate-id", candidate_1, "--decision", "fail",
        "--report", str(report_fail_1.relative_to(project)), "--evidence", "reverse probe failed generation 1",
        "--actor", "测试部/test-thread",
    ])
    candidate_2 = "CAND-20260825-A10002"
    manifest_2, manifest_sha_2 = write_candidate(project, candidate_2, "candidate-a2")
    run([
        sys.executable, str(task_tool), "bind-candidate", "--task-id", owner,
        "--candidate-id", candidate_2, "--manifest", str(manifest_2.relative_to(project)),
        "--sha256", manifest_sha_2, "--actor", "开发部/dev-thread-v2",
    ])
    security_fail_2 = write_gate_report(
        collab, "安全部", security_gate, candidate_2, "fail", "security-fail-2",
    )
    run([
        sys.executable, str(task_tool), "gate-verdict", "--task-id", security_gate,
        "--candidate-id", candidate_2, "--decision", "fail",
        "--report", str(security_fail_2.relative_to(project)), "--evidence", "security failed generation 2",
        "--actor", "安全部/security-thread",
    ])
    manifest_1_bytes = manifest_1.read_bytes()
    manifest_1.write_bytes(manifest_1_bytes + b"drift\n")
    historical_candidate_drift = run([sys.executable, str(task_tool), "doctor"], ok=False)
    check("候选 manifest SHA-256 不匹配" in historical_candidate_drift.stderr,
          "full-history doctor ignored an overwritten older candidate manifest")
    manifest_1.write_bytes(manifest_1_bytes)
    run([sys.executable, str(task_tool), "doctor"])
    report_fail_1_bytes = report_fail_1.read_bytes()
    report_fail_1.write_bytes(report_fail_1_bytes + b"\ntampered-after-verdict\n")
    historical_report_drift = run([sys.executable, str(task_tool), "doctor"], ok=False)
    check("审核报告内容已漂移" in historical_report_drift.stderr,
          "doctor accepted a gate report changed after its verdict")
    report_fail_1.write_bytes(report_fail_1_bytes)
    run([sys.executable, str(task_tool), "doctor"])
    reused_candidate = run([
        sys.executable, str(task_tool), "bind-candidate", "--task-id", owner,
        "--candidate-id", candidate_1, "--manifest", str(manifest_1.relative_to(project)),
        "--sha256", manifest_sha_1, "--actor", "开发部/dev-thread-v2",
    ], ok=False)
    check("全项目唯一" in reused_candidate.stderr,
          "a candidate ID from an older active generation was reused")
    report_fail_2 = write_gate_report(collab, "测试部", test_gate, candidate_2, "fail", "test-fail-2")
    module_spec = importlib.util.spec_from_file_location("agent_team_gate_crash_probe", task_tool)
    check(module_spec is not None and module_spec.loader is not None, "could not import generated task runtime")
    gate_module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(gate_module)
    original_store_dispatch = gate_module.store_dispatch_control

    def injected_dispatch_failure(payload: dict) -> None:
        raise OSError("injected crash between gate TASK and freeze control")

    gate_module.store_dispatch_control = injected_dispatch_failure
    try:
        with gate_module.task_lock():
            try:
                gate_module.cmd_gate_verdict(argparse.Namespace(
                    task_id=test_gate, candidate_id=candidate_2, decision="fail",
                    report=str(report_fail_2.relative_to(project)),
                    evidence="reverse probe failed generation 2", actor="测试部/test-thread",
                ))
            except OSError as exc:
                check("injected crash" in str(exc), "gate fault injection raised an unexpected error")
            else:
                raise VerifyError("gate fault injection unexpectedly completed")
    finally:
        gate_module.store_dispatch_control = original_store_dispatch
    transaction_path = collab / ".locks" / "gate-verdict-transaction.json"
    check(transaction_path.is_file(), "crashed gate verdict did not leave a recovery transaction")
    recovered = run([sys.executable, str(task_tool), "work-mode"])
    check(
        "GATE_VERDICT_RECOVERY_OK" in recovered.stdout and "WORK_MODE | frozen" in recovered.stdout
        and not transaction_path.exists(),
        "gate verdict recovery did not atomically converge TASK and freeze control",
    )
    dispatch = json.loads((collab / ".locks" / "dispatch-control.json").read_text(encoding="utf-8"))
    check(dispatch["mode"] == "frozen" and "AUTO_GATE_FAIL_X2" in dispatch["history"][-1]["evidence"],
          "automatic gate freeze was not durable")
    task_count = len(list((collab / "tasks").glob("TASK-*.json")))
    check(task_count == 3, "same-slice rework created replacement TASKs")
    denied_rework = run([
        sys.executable, str(task_tool), "bind-candidate", "--task-id", owner,
        "--candidate-id", "CAND-20260825-A10003", "--manifest", str(manifest_2.relative_to(project)),
        "--sha256", manifest_sha_2, "--actor", "开发部/dev-thread-v2",
    ], ok=False)
    check("P0_FREEZE_ACTIVE" in denied_rework.stderr, "automatic freeze allowed a third rework generation")
    metrics = run([
        sys.executable, str(task_tool), "record-metrics", "--slice-id", slice_id,
        "--source", "verified-host-export", "--input-tokens", "1200", "--output-tokens", "300",
        "--max-input-tokens", "800", "--tool-calls", "12", "--polls", "0",
        "--hot-context-bytes", "4096", "--actor", "统筹部/lead-thread",
    ])
    check("mode:manual-degraded | claims:none" in metrics.stdout,
          "manual host mode made an unsupported token-reduction claim")
    metrics_control = json.loads(slice_control_path.read_text(encoding="utf-8"))
    metric_seed = metrics_control["active_slice"]["metrics"][0]
    metrics_control["active_slice"]["metrics"] = [
        {**metric_seed, "source": f"bounded-seed-{index}"} for index in range(100)
    ]
    slice_control_path.write_text(
        json.dumps(metrics_control, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    run([
        sys.executable, str(task_tool), "record-metrics", "--slice-id", slice_id,
        "--source", "bounded-overflow", "--input-tokens", "1", "--output-tokens", "1",
        "--max-input-tokens", "1", "--tool-calls", "1", "--polls", "0",
        "--hot-context-bytes", "1", "--actor", "统筹部/lead-thread",
    ])
    bounded_metrics = json.loads(slice_control_path.read_text(encoding="utf-8"))["active_slice"]["metrics"]
    check(
        len(bounded_metrics) == 100 and bounded_metrics[-1]["source"] == "bounded-overflow",
        "101st metrics receipt corrupted or exceeded the bounded active history",
    )

    handoff = collab / "部门" / "开发部" / "交接班文档.md"
    handoff.write_text(
        handoff.read_text(encoding="utf-8")
        + f"\n- stale manual claim TASK-19990101-FAKE00 and cross-department {test_gate}\n",
        encoding="utf-8",
    )
    manual_note = run([
        sys.executable, str(task_tool), "onboard-check", "--department", "开发部",
    ])
    manual_bundle = run([
        sys.executable, str(task_tool), "onboard-bundle", "--department", "开发部",
    ])
    check(manual_note.stdout.startswith("ONBOARD_FRESH_OK"),
          "manual cold notes outside the machine block incorrectly invalidated freshness")
    check(
        "stale manual claim" not in manual_bundle.stdout
        and "===== BEGIN 交接班文档.md =====" in manual_bundle.stdout
        and "机器生成的当前切片" in manual_bundle.stdout,
        "onboarding bundle leaked stale manual handoff prose or omitted the machine block",
    )
    check(
        "current_tasks=1 | recovery_tasks=0" in manual_bundle.stdout
        and "===== BEGIN tasks/TASK-19990101-FAKE00.json =====" not in manual_bundle.stdout
        and f"===== BEGIN tasks/{test_gate}.json =====" not in manual_bundle.stdout,
        "manual text injected an unknown or cross-department TASK into onboarding identity",
    )
    handoff_text = handoff.read_text(encoding="utf-8")
    handoff.write_text(
        handoff_text.replace("<!-- agent-team current-slice:start -->",
                             "<!-- agent-team current-slice:start -->\n- forged stale machine claim", 1),
        encoding="utf-8",
    )
    stale = run([
        sys.executable, str(task_tool), "onboard-check", "--department", "开发部",
    ], ok=False)
    stale_bundle = run([
        sys.executable, str(task_tool), "onboard-bundle", "--department", "开发部",
    ], ok=False)
    check("HOT_STATE_STALE" in stale.stderr,
          "tampered machine-managed handoff block passed onboarding freshness")
    check("HOT_STATE_STALE" in stale_bundle.stderr,
          "tampered machine-managed handoff block passed bundled onboarding")
    run([sys.executable, str(task_tool), "rebuild-index"])
    run([sys.executable, str(task_tool), "onboard-check", "--department", "开发部"])

    happy = make_project(root, "protocol-150-happy-path")
    run(["git", "init", "-b", "main"], cwd=happy)
    run(["git", "config", "user.name", "Agent Team Verify"], cwd=happy)
    run(["git", "config", "user.email", "verify@example.invalid"], cwd=happy)
    run(["git", "add", "."], cwd=happy)
    run(["git", "commit", "-m", "foundation"], cwd=happy)
    scaffold(happy, "lead,dev,test")
    happy_collab = happy / "docs" / "collaboration"
    happy_tool = happy_collab / "scripts" / "agent_team_task.py"
    for department, thread_id in (("统筹部", "lead-thread"), ("开发部", "dev-thread"), ("测试部", "test-thread")):
        ensure_department_registered(happy_tool, department, thread_id)
    run([sys.executable, str(happy_tool), "rebuild-index"])
    too_many = run(task_args(
        happy_tool, "开发部", "三个 gates", required_gates=("test", "security", "finance"),
    ), ok=False)
    check("最多两个" in too_many.stderr or "已配置" in too_many.stderr,
          "owner accepted more than two gates")
    happy_owner = task_id_from(run(task_args(
        happy_tool, "开发部", "happy owner", required_gates=("test",),
    )))
    happy_control_path = happy_collab / ".locks" / "slice-control.json"
    happy_slice = json.loads(happy_control_path.read_text(encoding="utf-8"))["active_slice"]["slice_id"]
    run([sys.executable, str(happy_tool), "claim", "--task-id", happy_owner, "--claimed-by", "开发部/dev-thread"])
    temporary_tool = happy_collab / "scripts" / "agent_team_temporary.py"
    temporary_denied = run([
        sys.executable, str(temporary_tool), "preflight", "--task-id", happy_owner,
        "--parent-department", "开发部", "--write-path", "app/probe.py", "--base-revision", "HEAD",
    ], ok=False)
    check("TEMPORARY_EXECUTOR_P2_REQUIRED" in temporary_denied.stderr,
          "temporary executor bypassed the 1.5 single-owner boundary")
    happy_candidate = "CAND-20260825-H10001"
    happy_manifest, happy_manifest_sha = write_candidate(happy, happy_candidate, "happy-candidate")
    run([
        sys.executable, str(happy_tool), "freeze-new-work", "--actor", "统筹部/lead-thread",
        "--evidence", "initial candidate freeze whitelist probe",
    ])
    frozen_initial_candidate = run([
        sys.executable, str(happy_tool), "bind-candidate", "--task-id", happy_owner,
        "--candidate-id", happy_candidate, "--manifest", str(happy_manifest.relative_to(happy)),
        "--sha256", happy_manifest_sha, "--actor", "开发部/dev-thread",
    ], ok=False)
    check("P0_FREEZE_ACTIVE" in frozen_initial_candidate.stderr,
          "freeze whitelist allowed an initial candidate to advance work")
    run([
        sys.executable, str(happy_tool), "unfreeze-new-work", "--actor", "统筹部/lead-thread",
        "--user-confirmation", "resume happy-path candidate after whitelist probe",
    ])
    run([
        sys.executable, str(happy_tool), "bind-candidate", "--task-id", happy_owner,
        "--candidate-id", happy_candidate, "--manifest", str(happy_manifest.relative_to(happy)),
        "--sha256", happy_manifest_sha, "--actor", "开发部/dev-thread",
    ])
    happy_manifest_payload = json.loads(happy_manifest.read_text(encoding="utf-8"))
    happy_artifact = happy / happy_manifest_payload["artifact"]["path"]
    happy_artifact_bytes = happy_artifact.read_bytes()
    happy_artifact.write_bytes(happy_artifact_bytes + b"tampered\n")
    tampered_artifact_gate = run(task_args(
        happy_tool, "测试部", "tampered artifact gate", kind="gate",
        slice_id=happy_slice, gate_type="test",
    ), ok=False)
    check("artifact SHA-256" in tampered_artifact_gate.stderr,
          "gate creation accepted a candidate whose real artifact had drifted")
    happy_artifact.write_bytes(happy_artifact_bytes)
    happy_gate = task_id_from(run(task_args(
        happy_tool, "测试部", "happy gate", kind="gate", slice_id=happy_slice, gate_type="test",
    )))
    run([sys.executable, str(happy_tool), "claim", "--task-id", happy_gate, "--claimed-by", "测试部/test-thread"])
    happy_report = write_gate_report(happy_collab, "测试部", happy_gate, happy_candidate, "pass", "happy-pass")
    run([
        sys.executable, str(happy_tool), "gate-verdict", "--task-id", happy_gate,
        "--candidate-id", happy_candidate, "--decision", "pass",
        "--report", str(happy_report.relative_to(happy)), "--evidence", "all reverse probes passed",
        "--actor", "测试部/test-thread",
    ])
    run([
        sys.executable, str(happy_tool), "record-user-exit", "--task-id", happy_owner,
        "--candidate-id", happy_candidate, "--status", "not_applicable",
        "--evidence", "non-UI protocol verification", "--actor", "统筹部/lead-thread",
    ])
    run([
        sys.executable, str(happy_tool), "complete", "--task-id", happy_gate,
        "--actor", "测试部/test-thread", "--artifact", str(happy_report.relative_to(happy)),
        "--verified", "指定候选通过", "--unverified", "无", "--mistake-check", "无命中",
        "--report", str(happy_report.relative_to(happy)),
    ])
    run([
        sys.executable, str(happy_tool), "ack", "--task-id", happy_gate,
        "--acknowledged-by", "统筹部/lead-thread",
    ])
    run([
        sys.executable, str(happy_tool), "complete", "--task-id", happy_owner,
        "--actor", "开发部/dev-thread", "--artifact", str(happy_manifest.relative_to(happy)),
        "--verified", "唯一候选和最终出口已固定", "--unverified", "无",
        "--mistake-check", "未把局部 PASS 冒充其他项目可用",
    ])
    close_spec = importlib.util.spec_from_file_location("agent_team_slice_close_probe", happy_tool)
    check(close_spec is not None and close_spec.loader is not None,
          "could not import generated task runtime for slice-close fault injection")
    close_module = importlib.util.module_from_spec(close_spec)
    close_spec.loader.exec_module(close_module)
    original_close_store = close_module.store_slice_control

    def injected_slice_close_failure(payload: dict) -> None:
        raise OSError("injected crash between final TASK acknowledgement and slice close")

    close_module.store_slice_control = injected_slice_close_failure
    try:
        with close_module.task_lock():
            try:
                close_module.cmd_ack(argparse.Namespace(
                    task_id=happy_owner, acknowledged_by="统筹部/lead-thread",
                ))
            except OSError as exc:
                check("injected crash" in str(exc),
                      "slice-close fault injection raised an unexpected error")
            else:
                raise VerifyError("slice-close fault injection unexpectedly completed")
    finally:
        close_module.store_slice_control = original_close_store
    close_transaction = happy_collab / ".locks" / "slice-close-transaction.json"
    crashed_owner = json.loads((happy_collab / "tasks" / f"{happy_owner}.json").read_text(encoding="utf-8"))
    crashed_control = json.loads(happy_control_path.read_text(encoding="utf-8"))
    check(
        close_transaction.is_file()
        and crashed_owner["execution_state"] == "acknowledged"
        and crashed_control["active_slice"] is not None,
        "final acknowledgement crash did not preserve a recoverable slice-close transaction",
    )
    close_recovered = run([sys.executable, str(happy_tool), "work-mode"])
    check(
        "SLICE_CLOSE_RECOVERY_OK" in close_recovered.stdout
        and not close_transaction.exists()
        and json.loads(happy_control_path.read_text(encoding="utf-8"))["active_slice"] is None,
        "slice-close WAL did not recover the TASK/control split",
    )
    closed_control = json.loads(happy_control_path.read_text(encoding="utf-8"))
    inbox = (happy_collab / "部门" / "开发部" / "收件箱.md").read_text(encoding="utf-8")
    doctor = run([sys.executable, str(happy_tool), "doctor"])
    hot_only_list = run([sys.executable, str(happy_tool), "list"])
    cold_audit_list = run([sys.executable, str(happy_tool), "list", "--include-cold"])
    check(
        closed_control["active_slice"] is None and len(closed_control["history"]) == 1
        and happy_owner not in inbox and "host_runtime=manual-degraded" in doctor.stdout
        and hot_only_list.stdout.strip() == "NO_TASKS" and happy_owner in cold_audit_list.stdout,
        "happy slice did not close into cold history or leaked into the active inbox",
    )
    original_happy_history = list(closed_control["history"])
    closed_control["history"] = []
    happy_control_path.write_text(
        json.dumps(closed_control, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    ledger_owner = task_id_from(run(task_args(
        happy_tool, "开发部", "candidate ledger owner", required_gates=(),
    )))
    run([
        sys.executable, str(happy_tool), "claim", "--task-id", ledger_owner,
        "--claimed-by", "开发部/dev-thread",
    ])
    ledger_reuse = run([
        sys.executable, str(happy_tool), "bind-candidate", "--task-id", ledger_owner,
        "--candidate-id", happy_candidate, "--manifest", str(happy_manifest.relative_to(happy)),
        "--sha256", happy_manifest_sha, "--actor", "开发部/dev-thread",
    ], ok=False)
    check("全项目唯一" in ledger_reuse.stderr,
          "candidate ID became reusable after its bounded slice history was evicted")
    run([
        sys.executable, str(happy_tool), "block", "--task-id", ledger_owner,
        "--reason", "candidate ledger probe complete", "--actor", "开发部/dev-thread",
    ])
    ledger_payload = json.loads((happy_collab / "tasks" / f"{ledger_owner}.json").read_text(encoding="utf-8"))
    run([
        sys.executable, str(happy_tool), "resolve", "--task-id", ledger_owner,
        "--state", "abandoned", "--expected-revision", str(ledger_payload["revision"]),
        "--actor", "统筹部/lead-thread", "--reason", "candidate ledger probe complete",
        "--evidence", "permanent identity ledger retained prior candidate",
    ])
    restored_history_control = json.loads(happy_control_path.read_text(encoding="utf-8"))
    restored_history_control["history"] = original_happy_history + restored_history_control["history"]
    happy_control_path.write_text(
        json.dumps(restored_history_control, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    zero_gate_owner = task_id_from(run(task_args(
        happy_tool, "开发部", "zero gate immutable history", required_gates=(),
    )))
    run([
        sys.executable, str(happy_tool), "claim", "--task-id", zero_gate_owner,
        "--claimed-by", "开发部/dev-thread",
    ])
    zero_gate_candidate = "CAND-20260825-Z10001"
    zero_gate_manifest, zero_gate_manifest_sha = write_candidate(
        happy, zero_gate_candidate, "zero-gate-candidate",
    )
    run([
        sys.executable, str(happy_tool), "bind-candidate", "--task-id", zero_gate_owner,
        "--candidate-id", zero_gate_candidate,
        "--manifest", str(zero_gate_manifest.relative_to(happy)),
        "--sha256", zero_gate_manifest_sha, "--actor", "开发部/dev-thread",
    ])
    run([
        sys.executable, str(happy_tool), "record-user-exit", "--task-id", zero_gate_owner,
        "--candidate-id", zero_gate_candidate, "--status", "not_applicable",
        "--evidence", "non-UI zero-gate history probe", "--actor", "统筹部/lead-thread",
    ])
    run([
        sys.executable, str(happy_tool), "complete", "--task-id", zero_gate_owner,
        "--actor", "开发部/dev-thread", "--artifact", str(zero_gate_manifest.relative_to(happy)),
        "--verified", "zero-gate candidate fixed", "--unverified", "无",
        "--mistake-check", "未用任务完成冒充其他项目可用",
    ])
    run([
        sys.executable, str(happy_tool), "ack", "--task-id", zero_gate_owner,
        "--acknowledged-by", "统筹部/lead-thread",
    ])
    zero_gate_manifest_bytes = zero_gate_manifest.read_bytes()
    zero_gate_manifest.write_bytes(zero_gate_manifest_bytes + b"\ntampered-after-slice-close\n")
    zero_gate_history_drift = run([sys.executable, str(happy_tool), "doctor"], ok=False)
    check("候选 manifest SHA-256 不匹配" in zero_gate_history_drift.stderr,
          "doctor accepted a zero-gate candidate manifest changed after slice close")
    zero_gate_manifest.write_bytes(zero_gate_manifest_bytes)
    run([sys.executable, str(happy_tool), "doctor"])
    malformed_cold = happy_collab / "tasks" / "TASK-20260825-C0LD00.json"
    malformed_cold.write_text('{"broken":', encoding="utf-8")
    isolated_hot_list = run([sys.executable, str(happy_tool), "list"])
    isolated_onboard = run([
        sys.executable, str(happy_tool), "onboard-bundle", "--department", "开发部",
    ])
    explicit_cold_failure = run([
        sys.executable, str(happy_tool), "list", "--include-cold",
    ], ok=False)
    full_doctor_failure = run([sys.executable, str(happy_tool), "doctor"], ok=False)
    check(
        isolated_hot_list.stdout.strip() == "NO_TASKS"
        and isolated_onboard.stdout.startswith("ONBOARD_BUNDLE_OK")
        and explicit_cold_failure.returncode != 0 and full_doctor_failure.returncode != 0,
        "hot onboarding parsed cold history, or explicit full-history checks ignored corruption",
    )
    malformed_cold.unlink()
    no_op_upgrade = run([
        sys.executable, str(SCAFFOLD), str(happy), "--upgrade-collaboration",
    ])
    check(
        no_op_upgrade.stdout.startswith(f"UPGRADE_NOT_NEEDED | protocol:{PROTOCOL_VERSION}"),
        "current runtime rejected a valid cold schema-2 history during no-op upgrade validation",
    )

    history_project = make_project(root, "protocol-150-history-bound")
    scaffold(history_project)
    history_collab = history_project / "docs" / "collaboration"
    history_tool = history_collab / "scripts" / "agent_team_task.py"
    ensure_lead_registered(history_tool)
    run([sys.executable, str(history_tool), "rebuild-index"])
    control_path = history_collab / ".locks" / "dispatch-control.json"
    timestamp = dt.datetime.now().astimezone().isoformat(timespec="minutes")
    history_events = [
        {"at": timestamp, "action": "freeze" if index % 2 == 0 else "unfreeze",
         "actor": "统筹部/lead-thread", "evidence": f"history-bound-{index}"}
        for index in range(1000)
    ]
    control_path.write_text(json.dumps({
        "schema_version": 1, "mode": "normal", "updated_at": timestamp, "history": history_events,
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    run([
        sys.executable, str(history_tool), "freeze-new-work", "--actor", "统筹部/lead-thread",
        "--evidence", "history-bound-1000",
    ])
    run([
        sys.executable, str(history_tool), "unfreeze-new-work", "--actor", "统筹部/lead-thread",
        "--user-confirmation", "history-bound-1001",
    ])
    bounded = json.loads(control_path.read_text(encoding="utf-8"))
    check(len(bounded["history"]) == 1000 and bounded["history"][0]["action"] == "freeze",
          "1001st freeze transition corrupted or misaligned bounded history")
    run([sys.executable, str(history_tool), "doctor"])


def verify_protocol_150_migration(root: Path) -> None:
    missing_dispatch_project = make_project(root, "protocol-150-missing-dispatch")
    scaffold(missing_dispatch_project)
    missing_collab = missing_dispatch_project / "docs" / "collaboration"
    missing_protocol = missing_collab / "协议版本.json"
    missing_session = missing_collab / "会话启动状态.json"
    for path in (missing_protocol, missing_session):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["protocol_version"] = PREVIOUS_PROTOCOL_VERSION
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    missing_guide = missing_dispatch_project / "docs" / "agent-guide.md"
    missing_guide.write_text(missing_guide.read_text(encoding="utf-8").replace(
        f"受管协议版本:{PROTOCOL_VERSION}", f"受管协议版本:{PREVIOUS_PROTOCOL_VERSION}",
    ), encoding="utf-8")
    (missing_collab / ".locks" / "slice-control.json").unlink()
    (missing_collab / ".locks" / "hot-state.json").unlink(missing_ok=True)
    (missing_collab / ".locks" / "dispatch-control.json").unlink()
    missing_before = missing_protocol.read_bytes()
    missing_dispatch_upgrade = run([
        sys.executable, str(SCAFFOLD), str(missing_dispatch_project), "--upgrade-collaboration",
    ], ok=False)
    check(
        "P0_FREEZE_REQUIRED" in missing_dispatch_upgrade.stderr
        and missing_protocol.read_bytes() == missing_before
        and not (missing_collab / ".locks" / "dispatch-control.json").exists(),
        "upgrade silently recreated a normal dispatch control when the P0 freeze truth was missing",
    )

    project = make_project(root, "protocol-150-migration")
    scaffold(project)
    collab = project / "docs" / "collaboration"
    task_tool = collab / "scripts" / "agent_team_task.py"
    ensure_lead_registered(task_tool)
    legacy_task_id = enqueue(task_tool, "legacy 1.4.15 cold task")
    legacy_task_path = collab / "tasks" / f"{legacy_task_id}.json"
    legacy_payload = json.loads(legacy_task_path.read_text(encoding="utf-8"))
    for field in ("slice_id", "task_kind", "gate_type", "gate_attempts", "ownership_history"):
        legacy_payload.pop(field, None)
    legacy_payload["schema_version"] = 1
    legacy_task_path.write_text(
        json.dumps(legacy_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    slice_path = collab / ".locks" / "slice-control.json"
    slice_payload = json.loads(slice_path.read_text(encoding="utf-8"))
    slice_payload["active_slice"] = None
    slice_path.write_text(
        json.dumps(slice_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    run([sys.executable, str(task_tool), "rebuild-index"])
    run([
        sys.executable, str(task_tool), "freeze-new-work", "--actor", "统筹部/lead-thread",
        "--evidence", "migration-freeze",
    ])
    legacy_bytes = legacy_task_path.read_bytes()
    for name in ("协议版本.json", "会话启动状态.json"):
        path = collab / name
        payload = json.loads(path.read_text(encoding="utf-8"))
        if name == "协议版本.json":
            payload["protocol_version"] = PREVIOUS_PROTOCOL_VERSION
        else:
            payload["protocol_version"] = PREVIOUS_PROTOCOL_VERSION
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    guide = project / "docs" / "agent-guide.md"
    guide.write_text(
        guide.read_text(encoding="utf-8").replace(
            f"受管协议版本:{PROTOCOL_VERSION}", f"受管协议版本:{PREVIOUS_PROTOCOL_VERSION}",
        ),
        encoding="utf-8",
    )
    slice_path.unlink()
    (collab / ".locks" / "hot-state.json").unlink(missing_ok=True)
    missing_handoff = collab / "部门" / "执行部" / "交接班文档.md"
    missing_handoff.unlink()
    missing_output_directory = collab / "专项结论"
    check(not any(missing_output_directory.iterdir()), "migration reverse-probe output directory was not empty")
    missing_output_directory.rmdir()
    upgraded = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    check(upgraded.stdout.startswith("UPGRADE_OK |"), "frozen 1.4.15 project did not upgrade to 1.5.0")
    check(legacy_task_path.read_bytes() == legacy_bytes, "1.4.15 TASK bytes changed during 1.5 migration")
    legacy_reactivation = run([
        sys.executable, str(task_tool), "claim", "--task-id", legacy_task_id,
        "--claimed-by", "执行部/legacy-thread",
    ], ok=False)
    check("LEGACY_TASK_COLD_ONLY" in legacy_reactivation.stderr,
          "upgraded 1.4 history could be reactivated as a second owner")
    backup_roots = sorted((collab / "升级备份").iterdir())
    check(bool(backup_roots), "1.5 migration did not create a rollback backup")
    manifest = backup_roots[-1] / "rollback-manifest.json"
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    check(
        manifest_payload["schema_version"] == 2
        and manifest_payload["operation"] == "upgrade"
        and manifest_payload["source_protocol"] == PREVIOUS_PROTOCOL_VERSION
        and manifest_payload["target_protocol"] == PROTOCOL_VERSION,
        "rollback manifest omitted unique operation/source/target protocol identity",
    )
    generated_handoff_bytes = missing_handoff.read_bytes()
    missing_handoff.write_bytes(generated_handoff_bytes + "\n人工恢复判断：必须保留。\n".encode("utf-8"))
    handoff_rollback = run([
        sys.executable, str(SCAFFOLD), str(project), "--rollback-collaboration", str(manifest),
    ], ok=False)
    check(
        handoff_rollback.returncode == 9
        and "ROLLBACK_DENIED" in handoff_rollback.stderr
        and missing_handoff.read_bytes().endswith("人工恢复判断：必须保留。\n".encode("utf-8"))
        and not (collab / ".upgrade-transaction.json").exists()
        and json.loads((collab / "协议版本.json").read_text(encoding="utf-8"))["protocol_version"]
        == PROTOCOL_VERSION,
        "rollback did not fail closed before deleting post-upgrade manual handoff content",
    )
    missing_handoff.write_bytes(generated_handoff_bytes)
    migration_note = missing_output_directory / "migration-note.md"
    migration_note.write_text("post-upgrade evidence\n", encoding="utf-8")
    directory_rollback = run([
        sys.executable, str(SCAFFOLD), str(project), "--rollback-collaboration", str(manifest),
    ], ok=False)
    check(
        directory_rollback.returncode == 9
        and "ROLLBACK_DENIED" in directory_rollback.stderr
        and migration_note.read_text(encoding="utf-8") == "post-upgrade evidence\n"
        and not (collab / ".upgrade-transaction.json").exists()
        and json.loads((collab / "协议版本.json").read_text(encoding="utf-8"))["protocol_version"]
        == PROTOCOL_VERSION,
        "rollback did not reject an added child before mutating an upgrade-created directory",
    )
    migration_note.unlink()
    rolled_back = run([
        sys.executable, str(SCAFFOLD), str(project), "--rollback-collaboration", str(manifest),
    ])
    restored_protocol = json.loads((collab / "协议版本.json").read_text(encoding="utf-8"))
    check(
        rolled_back.stdout.startswith("ROLLBACK_OK |")
        and restored_protocol["protocol_version"] == PREVIOUS_PROTOCOL_VERSION
        and legacy_task_path.read_bytes() == legacy_bytes
        and not slice_path.exists()
        and not missing_handoff.exists()
        and not missing_output_directory.exists(),
        "explicit rollback did not restore exact 1.4.15 truth",
    )
    existing_backup_dirs = set((collab / "升级备份").iterdir())
    second_upgrade = run([
        sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration",
    ])
    new_backup_dirs = set((collab / "升级备份").iterdir()) - existing_backup_dirs
    check(len(new_backup_dirs) == 1, "second upgrade did not create exactly one backup generation")
    second_manifest = next(iter(new_backup_dirs)) / "rollback-manifest.json"
    check(
        second_upgrade.stdout.startswith("UPGRADE_OK |") and second_manifest != manifest,
        "second upgrade did not establish a distinct rollback generation",
    )
    stale_rollback = run([
        sys.executable, str(SCAFFOLD), str(project), "--rollback-collaboration", str(manifest),
    ], ok=False)
    check(
        "不属于当前升级代次" in stale_rollback.stderr
        and json.loads((collab / "协议版本.json").read_text(encoding="utf-8"))["protocol_version"]
        == PROTOCOL_VERSION,
        "an older rollback manifest was accepted after a newer upgrade generation",
    )
    second_rollback = run([
        sys.executable, str(SCAFFOLD), str(project), "--rollback-collaboration", str(second_manifest),
    ])
    check(
        second_rollback.stdout.startswith("ROLLBACK_OK |")
        and json.loads((collab / "协议版本.json").read_text(encoding="utf-8"))["protocol_version"]
        == PREVIOUS_PROTOCOL_VERSION,
        "current rollback generation was rejected after the stale-generation reverse probe",
    )

    claimed_project = make_project(root, "protocol-150-claimed-migration-denied")
    scaffold(claimed_project)
    claimed_collab = claimed_project / "docs" / "collaboration"
    claimed_tool = claimed_collab / "scripts" / "agent_team_task.py"
    ensure_lead_registered(claimed_tool)
    ensure_department_registered(claimed_tool, "执行部", "do-thread")
    run([sys.executable, str(claimed_tool), "rebuild-index"])
    claimed_task_id = enqueue(claimed_tool, "legacy claimed migration blocker")
    run([
        sys.executable, str(claimed_tool), "claim", "--task-id", claimed_task_id,
        "--claimed-by", "执行部/do-thread",
    ])
    run([
        sys.executable, str(claimed_tool), "freeze-new-work", "--actor", "统筹部/lead-thread",
        "--evidence", "claimed migration reverse probe",
    ])
    claimed_task_path = claimed_collab / "tasks" / f"{claimed_task_id}.json"
    claimed_payload = json.loads(claimed_task_path.read_text(encoding="utf-8"))
    for field in ("slice_id", "task_kind", "gate_type", "gate_attempts", "ownership_history"):
        claimed_payload.pop(field, None)
    claimed_payload["schema_version"] = 1
    claimed_task_path.write_text(
        json.dumps(claimed_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    claimed_bytes = claimed_task_path.read_bytes()
    for name in ("协议版本.json", "会话启动状态.json"):
        path = claimed_collab / name
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["protocol_version"] = PREVIOUS_PROTOCOL_VERSION
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    claimed_guide = claimed_project / "docs" / "agent-guide.md"
    claimed_guide.write_text(claimed_guide.read_text(encoding="utf-8").replace(
        f"受管协议版本:{PROTOCOL_VERSION}", f"受管协议版本:{PREVIOUS_PROTOCOL_VERSION}",
    ), encoding="utf-8")
    (claimed_collab / ".locks" / "slice-control.json").unlink()
    (claimed_collab / ".locks" / "hot-state.json").unlink(missing_ok=True)
    claimed_upgrade = run([
        sys.executable, str(SCAFFOLD), str(claimed_project), "--upgrade-collaboration",
    ], ok=False)
    check(
        "1.4 TASK 仍在 claimed" in claimed_upgrade.stderr
        and claimed_task_path.read_bytes() == claimed_bytes
        and json.loads((claimed_collab / "协议版本.json").read_text(encoding="utf-8"))["protocol_version"]
        == PREVIOUS_PROTOCOL_VERSION,
        "migration accepted or rewrote an in-flight legacy owner",
    )

    post_task = make_project(root, "protocol-150-rollback-denied")
    scaffold(post_task)
    post_collab = post_task / "docs" / "collaboration"
    post_tool = post_collab / "scripts" / "agent_team_task.py"
    ensure_lead_registered(post_tool)
    run([sys.executable, str(post_tool), "rebuild-index"])
    # Create a synthetic valid backup by taking this project's current tree through the same
    # frozen downgrade/upgrade path, then create a 1.5 TASK before requesting rollback.
    run([
        sys.executable, str(post_tool), "freeze-new-work", "--actor", "统筹部/lead-thread",
        "--evidence", "prepare rollback-denied probe",
    ])
    for name in ("协议版本.json", "会话启动状态.json"):
        path = post_collab / name
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["protocol_version"] = PREVIOUS_PROTOCOL_VERSION
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    post_guide = post_task / "docs" / "agent-guide.md"
    post_guide.write_text(post_guide.read_text(encoding="utf-8").replace(
        f"受管协议版本:{PROTOCOL_VERSION}", f"受管协议版本:{PREVIOUS_PROTOCOL_VERSION}",
    ), encoding="utf-8")
    (post_collab / ".locks" / "slice-control.json").unlink()
    (post_collab / ".locks" / "hot-state.json").unlink(missing_ok=True)
    run([sys.executable, str(SCAFFOLD), str(post_task), "--upgrade-collaboration"])
    post_manifest = sorted((post_collab / "升级备份").iterdir())[-1] / "rollback-manifest.json"
    run([
        sys.executable, str(post_tool), "unfreeze-new-work", "--actor", "统筹部/lead-thread",
        "--user-confirmation", "create post-upgrade task probe",
    ])
    created = enqueue(post_tool, "new 1.5 task blocks rollback")
    run([
        sys.executable, str(post_tool), "freeze-new-work", "--actor", "统筹部/lead-thread",
        "--evidence", "attempt explicit rollback after 1.5 task",
    ])
    denied = run([
        sys.executable, str(SCAFFOLD), str(post_task), "--rollback-collaboration", str(post_manifest),
    ], ok=False)
    check(
        created.startswith("TASK-") and (
            "活动切片" in denied.stderr
            or "已创建 1.5 TASK" in denied.stderr
            or "受管状态不完整" in denied.stderr
        ),
        "explicit rollback failed open after a new 1.5 TASK existed",
    )


def verify_protocol_150_atomic_guards(root: Path) -> None:
    project = make_project(root, "protocol-150-atomic-guards")
    scaffold(project)
    collab = project / "docs" / "collaboration"
    task_tool = collab / "scripts" / "agent_team_task.py"
    ensure_lead_registered(task_tool)
    run([sys.executable, str(task_tool), "rebuild-index"])

    slice_path = collab / ".locks" / "slice-control.json"
    slice_bytes = slice_path.read_bytes()
    slice_path.unlink()
    missing_slice = run([
        sys.executable, str(task_tool), "enqueue", "--actor", "统筹部/lead-thread",
        "--department", "执行部", "--from-department", "统筹部", "--title", "missing slice guard",
        "--node", "guard", "--details", "must fail", "--acceptance-exit", "rejected",
        "--failure-path", "slice control missing", "--authorization-state", "none",
    ], ok=False)
    slice_path.write_bytes(slice_bytes)
    check("切片控制缺失" in missing_slice.stderr and not list((collab / "tasks").glob("TASK-*.json")),
          "missing slice control failed open or left a TASK")

    tasks = collab / "tasks"
    safe_tasks = collab / "tasks-safe"
    outside = root / "outside-protocol-150-tasks"
    outside.mkdir()
    tasks.rename(safe_tasks)
    tasks.symlink_to(outside, target_is_directory=True)
    try:
        symlink_denied = run([
            sys.executable, str(task_tool), "enqueue", "--actor", "统筹部/lead-thread",
            "--department", "执行部", "--from-department", "统筹部", "--title", "symlink guard",
            "--node", "guard", "--details", "must fail", "--acceptance-exit", "rejected",
            "--failure-path", "tasks symlink", "--authorization-state", "none",
        ], ok=False)
        check("不安全" in symlink_denied.stderr and not any(outside.iterdir()),
              "1.5 enqueue wrote through a symlinked tasks root")
    finally:
        tasks.unlink()
        safe_tasks.rename(tasks)

    module_spec = importlib.util.spec_from_file_location("agent_team_enqueue_crash_probe", task_tool)
    check(module_spec is not None and module_spec.loader is not None, "could not import enqueue crash runtime")
    enqueue_module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(enqueue_module)
    original_store_slice = enqueue_module.store_slice_control

    def injected_slice_failure(payload: dict) -> None:
        raise OSError("injected crash between TASK and slice control")

    enqueue_module.store_slice_control = injected_slice_failure
    try:
        with enqueue_module.task_lock():
            try:
                enqueue_module.cmd_enqueue(argparse.Namespace(
                    actor="统筹部/lead-thread", department="执行部", from_department="统筹部",
                    title="enqueue crash owner", node="atomic guard", details="must recover",
                    acceptance_exit="one owner", failure_path=["slice control write fails"],
                    confirmation="无需额外确认", domain_stage="atomic guard", authorization_state="none",
                    authorization_evidence="", pointer=[], task_kind="owner", slice_id="", gate_type="",
                    required_gate=[],
                ))
            except OSError as exc:
                check("injected crash" in str(exc), "enqueue fault injection raised an unexpected error")
            else:
                raise VerifyError("enqueue fault injection unexpectedly completed")
    finally:
        enqueue_module.store_slice_control = original_store_slice
    enqueue_transaction = collab / ".locks" / "slice-enqueue-transaction.json"
    transaction_payload = json.loads(enqueue_transaction.read_text(encoding="utf-8"))
    crashed_task_id = transaction_payload["task"]["task_id"]
    recovered_enqueue = run([sys.executable, str(task_tool), "work-mode"])
    check(
        "SLICE_ENQUEUE_RECOVERY_OK" in recovered_enqueue.stdout and not enqueue_transaction.exists()
        and (collab / "tasks" / f"{crashed_task_id}.json").is_file(),
        "enqueue recovery did not converge TASK and slice control",
    )
    run([
        sys.executable, str(task_tool), "resolve", "--task-id", crashed_task_id,
        "--state", "abandoned", "--expected-revision", "1", "--actor", "统筹部/lead-thread",
        "--reason", "fault injection cleanup", "--evidence", "verified enqueue recovery",
    ])

    enqueue_args = [
        sys.executable, str(task_tool), "enqueue", "--actor", "统筹部/lead-thread",
        "--department", "执行部", "--from-department", "统筹部", "--title", "race owner",
        "--node", "race", "--details", "linearizable race", "--acceptance-exit", "one durable result",
        "--failure-path", "freeze wins", "--authorization-state", "none",
    ]
    freeze_args = [
        sys.executable, str(task_tool), "freeze-new-work", "--actor", "统筹部/lead-thread",
        "--evidence", "concurrent-freeze-probe",
    ]
    enqueue_process = subprocess.Popen(
        enqueue_args, cwd=project, text=True, encoding="utf-8",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    freeze_process = subprocess.Popen(
        freeze_args, cwd=project, text=True, encoding="utf-8",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    enqueue_stdout, enqueue_stderr = enqueue_process.communicate(timeout=30)
    freeze_stdout, freeze_stderr = freeze_process.communicate(timeout=30)
    check(freeze_process.returncode == 0 and freeze_stdout.startswith("WORK_FREEZE_OK"),
          f"concurrent freeze failed: {freeze_stderr}")
    check(
        (enqueue_process.returncode == 0 and enqueue_stdout.startswith("TASK_ENQUEUED"))
        or (enqueue_process.returncode == 2 and "P0_FREEZE_ACTIVE" in enqueue_stderr),
        "freeze/enqueue race was not linearizable",
    )
    denied_after = run(enqueue_args, ok=False)
    check("P0_FREEZE_ACTIVE" in denied_after.stderr, "post-race frozen state accepted new work")
    run([sys.executable, str(task_tool), "doctor"])
    check(
        "project-control.lock" in SCAFFOLD.read_text(encoding="utf-8")
        and "project-control.lock" in (ROOT / "scripts" / "temporary_executor_runtime.py").read_text(encoding="utf-8"),
        "scaffold/task/session/temporary runtimes did not converge on the project control lock",
    )


def verify_installed_2011_migration(root: Path, installed_root: Path) -> None:
    installed_root = installed_root.expanduser().resolve()
    fixture_manifest_path = installed_root / "fixture-manifest.json"
    check(fixture_manifest_path.is_file() and not fixture_manifest_path.is_symlink(),
          "2.0.11 fixture is missing its immutable fixture manifest")
    try:
        fixture_manifest = json.loads(fixture_manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerifyError(f"2.0.11 fixture manifest is unreadable: {exc}") from exc
    check(
        isinstance(fixture_manifest, dict)
        and set(fixture_manifest) == {
            "captured_on", "protocol_version", "runtime_files", "runtime_set_sha256",
            "runtime_set_sha256_algorithm", "schema_version", "source_kind", "source_version",
        }
        and fixture_manifest.get("schema_version") == 1
        and fixture_manifest.get("source_version") == "2.0.11"
        and fixture_manifest.get("protocol_version") == PREVIOUS_PROTOCOL_VERSION
        and fixture_manifest.get("source_kind") == "exact-installed-runtime-copy",
        "2.0.11 fixture manifest identity is invalid",
    )
    declared_files = fixture_manifest.get("runtime_files")
    check(isinstance(declared_files, dict) and set(declared_files) == set(RUNTIME_FILES),
          "2.0.11 fixture manifest does not bind the exact five runtime files")
    runtime_rows: list[str] = []
    for relative in RUNTIME_FILES:
        fixture_file = installed_root / relative
        check(fixture_file.is_file() and not fixture_file.is_symlink(),
              f"2.0.11 fixture runtime file is missing or unsafe: {relative}")
        digest = hashlib.sha256(fixture_file.read_bytes()).hexdigest()
        check(declared_files.get(relative) == digest,
              f"2.0.11 fixture runtime hash mismatch: {relative}")
        runtime_rows.append(f"{digest}  {relative}\n")
    runtime_set_sha256 = hashlib.sha256("".join(runtime_rows).encode("utf-8")).hexdigest()
    check(fixture_manifest.get("runtime_set_sha256") == runtime_set_sha256,
          "2.0.11 fixture runtime-set identity is stale")
    installed_skill = installed_root / "SKILL.md"
    installed_scaffold = installed_root / "scripts" / "scaffold_team.py"
    installed_temporary_runtime = installed_root / "scripts" / "temporary_executor_runtime.py"
    check(installed_skill.is_file() and installed_scaffold.is_file() and installed_temporary_runtime.is_file(),
          "2.0.11 fixture root does not contain the installed runtime files")
    installed_frontmatter = yaml_frontmatter(installed_skill.read_text(encoding="utf-8"), label="2.0.11 fixture")
    check(
        isinstance(installed_frontmatter.get("metadata"), dict)
        and installed_frontmatter["metadata"].get("version") == "2.0.11"
        and 'PROTOCOL_VERSION = "1.4.15"' in installed_scaffold.read_text(encoding="utf-8"),
        "legacy fixture is not the exact installed Agent-Team 2.0.11 / protocol 1.4.15 runtime",
    )

    project = make_project(root, "installed-2011-migration")
    (project / ".gitignore").write_text("/.agent-team/\n", encoding="utf-8")
    (project / "app").mkdir()
    (project / "app" / "base.py").write_text("VALUE = 'legacy'\n", encoding="utf-8")
    run(["git", "init", "-b", "main"], cwd=project)
    run(["git", "config", "user.name", "Agent Team Verify"], cwd=project)
    run(["git", "config", "user.email", "verify@example.invalid"], cwd=project)
    run(["git", "add", "."], cwd=project)
    run(["git", "commit", "-m", "legacy foundation"], cwd=project)
    run([
        sys.executable, str(installed_scaffold), str(project),
        "--profile", "软件项目", "--roles", "lead,dev,test", "--session-mode", "manual",
        "--foundation-file", "docs/spec.md",
    ])
    run(["git", "add", "."], cwd=project)
    run(["git", "commit", "-m", "installed 2.0.11 collaboration"], cwd=project)

    collab = project / "docs" / "collaboration"
    task_tool = collab / "scripts" / "agent_team_task.py"
    temporary_tool = collab / "scripts" / "agent_team_temporary.py"
    ensure_department_registered(task_tool, "统筹部", "lead-thread")
    ensure_department_registered(task_tool, "开发部", "dev-thread")
    legacy_task = task_id_from(run([
        sys.executable, str(task_tool), "enqueue", "--department", "开发部",
        "--from-department", "统筹部", "--title", "2.0.11 legacy temporary",
        "--node", "legacy migration", "--details", "verify exact installed migration",
        "--acceptance-exit", "legacy truth preserved", "--failure-path", "freeze on ambiguity",
        "--authorization-state", "user_confirmed",
        "--authorization-evidence", "fixture-authorized-temporary",
    ]))
    run([
        sys.executable, str(temporary_tool), "provision", "--task-id", legacy_task,
        "--parent-department", "开发部", "--executor-id", "legacy-2011-temp",
        "--display-name", "2.0.11 临时执行者", "--current-brief", "only app/legacy.py",
        "--client-key", "legacy-2011-client", "--scan-boundary-evidence", "fixture scope checked",
        "--base-revision", "HEAD", "--write-path", "app/legacy.py",
    ])
    run([
        sys.executable, str(temporary_tool), "pause", "--task-id", legacy_task,
        "--state", "blocked", "--reason", "prepare frozen migration fixture",
    ])
    run([
        sys.executable, str(task_tool), "freeze-new-work", "--actor", "统筹部/lead-thread",
        "--evidence", "exact 2.0.11 migration probe",
    ])
    onboarding_names = ("上岗引导.md", "岗位说明.md", "交接班文档.md", "收件箱.md")
    legacy_onboarding_bytes = sum(
        (collab / "部门" / "开发部" / name).stat().st_size for name in onboarding_names
    )
    legacy_task_path = collab / "tasks" / f"{legacy_task}.json"
    legacy_bytes = legacy_task_path.read_bytes()
    upgraded = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    closeout_index = json.loads(
        (collab / ".locks" / "legacy-closeout-index.json").read_text(encoding="utf-8")
    )
    check(
        upgraded.stdout.startswith("UPGRADE_OK |")
        and legacy_task_path.read_bytes() == legacy_bytes
        and legacy_task in closeout_index["task_ids"],
        "exact installed 2.0.11 migration rewrote or lost its blocked temporary executor",
    )
    recovery_bundle = run([
        sys.executable, str(task_tool), "onboard-bundle", "--department", "开发部",
    ])
    lead_recovery_bundle = run([
        sys.executable, str(task_tool), "onboard-bundle", "--department", "统筹部",
    ])
    check(
        "current_tasks=0 | recovery_tasks=1 | cold_history=not_loaded" in recovery_bundle.stdout
        and f"===== BEGIN tasks/{legacy_task}.json =====" in recovery_bundle.stdout
        and "冻结恢复任务" in recovery_bundle.stdout
        and "current_tasks=0 | recovery_tasks=1 | cold_history=not_loaded" in lead_recovery_bundle.stdout,
        "migrated legacy temporary blocker disappeared from department or lead onboarding",
    )
    closeout_index_path = collab / ".locks" / "legacy-closeout-index.json"
    closeout_index_bytes = closeout_index_path.read_bytes()
    tampered_index = json.loads(closeout_index_bytes)
    tampered_index["task_ids"] = []
    tampered_index["archive_recovery_task_ids"] = []
    closeout_index_path.write_text(
        json.dumps(tampered_index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tampered_index_unfreeze = run([
        sys.executable, str(task_tool), "unfreeze-new-work", "--actor", "统筹部/lead-thread",
        "--user-confirmation", "tampered index must fail closed",
    ], ok=False)
    closeout_index_path.write_bytes(closeout_index_bytes)
    check("LEGACY_CLOSEOUT_INDEX_INTEGRITY" in tampered_index_unfreeze.stderr,
          "a structurally valid but truncated closeout index allowed unfreeze")
    current_onboarding_bytes = sum(
        (collab / "部门" / "开发部" / name).stat().st_size for name in onboarding_names
    )
    denied_unfreeze = run([
        sys.executable, str(task_tool), "unfreeze-new-work", "--actor", "统筹部/lead-thread",
        "--user-confirmation", "probe must remain frozen until legacy closeout",
    ], ok=False)
    denied_resume = run([
        sys.executable, str(temporary_tool), "resume", "--task-id", legacy_task,
        "--evidence", "must not restore legacy development",
    ], ok=False)
    malformed_unrelated = collab / "tasks" / "TASK-20260825-C0LD99.json"
    malformed_unrelated.write_text('{"broken":', encoding="utf-8")
    hot_list = run([sys.executable, str(task_tool), "list"])
    malformed_unrelated.unlink()
    check(
        "LEGACY_TEMPORARY_CLOSEOUT_REQUIRED" in denied_unfreeze.stderr
        and (
            "P0_FREEZE_ACTIVE" in denied_resume.stderr
            or "P0_STOP_LOSS_ACTIVE" in denied_resume.stderr
            or "LEGACY_TEMPORARY_CLOSEOUT_ONLY" in denied_resume.stderr
        )
        and "LEGACY_TEMPORARY_CLOSEOUT_REQUIRED" in hot_list.stdout,
        "2.0.11 legacy temporary could resume, unfreeze, or force a full cold-history scan: "
        f"unfreeze={denied_unfreeze.stderr.strip()!r}; resume={denied_resume.stderr.strip()!r}; "
        f"list={hot_list.stdout.strip()!r}",
    )
    print(
        "LEGACY_2011_MIGRATION_OK | task_bytes_preserved=true | "
        f"four_docs_before={legacy_onboarding_bytes} | four_docs_after={current_onboarding_bytes} | "
        "migration_token_claim=not_inferred_from_bytes"
    )

    archive_project = make_project(root, "installed-2011-invalid-archive")
    (archive_project / ".gitignore").write_text("/.agent-team/\n", encoding="utf-8")
    (archive_project / "app").mkdir()
    (archive_project / "app" / "base.py").write_text("VALUE = 'archive'\n", encoding="utf-8")
    run(["git", "init", "-b", "main"], cwd=archive_project)
    run(["git", "config", "user.name", "Agent Team Verify"], cwd=archive_project)
    run(["git", "config", "user.email", "verify@example.invalid"], cwd=archive_project)
    run(["git", "add", "."], cwd=archive_project)
    run(["git", "commit", "-m", "legacy archive foundation"], cwd=archive_project)
    run([
        sys.executable, str(installed_scaffold), str(archive_project),
        "--profile", "软件项目", "--roles", "lead,dev,test", "--session-mode", "manual",
        "--foundation-file", "docs/spec.md",
    ])
    run(["git", "add", "."], cwd=archive_project)
    run(["git", "commit", "-m", "installed 2.0.11 archive fixture"], cwd=archive_project)
    archive_collab = archive_project / "docs" / "collaboration"
    archive_task_tool = archive_collab / "scripts" / "agent_team_task.py"
    archive_temporary_tool = archive_collab / "scripts" / "agent_team_temporary.py"
    ensure_department_registered(archive_task_tool, "统筹部", "lead-thread")
    ensure_department_registered(archive_task_tool, "开发部", "dev-thread")
    archive_task = task_id_from(run([
        sys.executable, str(archive_task_tool), "enqueue", "--department", "开发部",
        "--from-department", "统筹部", "--title", "2.0.11 invalid archived temporary",
        "--node", "legacy archive migration", "--details", "preserve invalid archived truth",
        "--acceptance-exit", "sidecar receipt verified", "--failure-path", "never rewrite legacy task",
        "--authorization-state", "user_confirmed", "--authorization-evidence", "fixture archive probe",
    ]))
    run([
        sys.executable, str(archive_temporary_tool), "provision", "--task-id", archive_task,
        "--parent-department", "开发部", "--executor-id", "legacy-archive-temp",
        "--display-name", "旧归档探针", "--current-brief", "only app/archive.py",
        "--client-key", "legacy-archive-client", "--scan-boundary-evidence", "archive scope checked",
        "--base-revision", "HEAD", "--write-path", "app/archive.py",
    ])
    archive_task_path = archive_collab / "tasks" / f"{archive_task}.json"
    archive_payload = json.loads(archive_task_path.read_text(encoding="utf-8"))
    rule_digest = archive_payload["temporary_executor"]["rule"]["digest"]
    run([
        sys.executable, str(archive_temporary_tool), "session-mark", "--task-id", archive_task,
        "--state", "active", "--thread-id", "legacy-archive-thread",
        "--rule-digest", rule_digest, "--evidence", "legacy session created",
    ])
    run([
        sys.executable, str(archive_temporary_tool), "pause", "--task-id", archive_task,
        "--state", "blocked", "--reason", "prepare invalid archived fixture",
    ])
    run([
        sys.executable, str(archive_temporary_tool), "abandon", "--task-id", archive_task,
        "--evidence", "fixture user abandoned legacy candidate",
    ])
    run([
        sys.executable, str(archive_temporary_tool), "cleanup", "--task-id", archive_task,
        "--evidence", "fixture workspace and branch removed",
    ])
    valid_legacy_receipt = "host=set_thread_archived thread_id=legacy-archive-thread archived=true"
    run([
        sys.executable, str(archive_temporary_tool), "session-mark", "--task-id", archive_task,
        "--state", "archived", "--evidence", valid_legacy_receipt,
    ])
    archive_payload = json.loads(archive_task_path.read_text(encoding="utf-8"))
    archive_payload["temporary_executor"]["temporary_session"].update(
        state="archived", evidence="legacy-archive-without-a-verifiable-host-receipt",
    )
    archive_task_path.write_text(
        json.dumps(archive_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    run([
        sys.executable, str(archive_task_tool), "freeze-new-work", "--actor", "统筹部/lead-thread",
        "--evidence", "invalid archived migration probe",
    ])
    invalid_archive_bytes = archive_task_path.read_bytes()
    archive_upgrade = run([
        sys.executable, str(SCAFFOLD), str(archive_project), "--upgrade-collaboration",
    ])
    recovery_path = archive_collab / ".locks" / "legacy-archive-recovery.json"
    recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
    pending_archive = run([sys.executable, str(archive_temporary_tool), "pending-archives"])
    check(
        archive_upgrade.stdout.startswith("UPGRADE_OK |")
        and archive_task_path.read_bytes() == invalid_archive_bytes
        and recovery["entries"][archive_task]["state"] == "pending"
        and "ARCHIVE_THREAD_REQUIRED:legacy-archive-thread" in pending_archive.stdout,
        "invalid legacy archived truth was rewritten, lost, or falsely certified during migration",
    )
    archive_receipt = "host=set_thread_archived thread_id=legacy-archive-thread archived=true"
    run([
        sys.executable, str(archive_temporary_tool), "session-mark", "--task-id", archive_task,
        "--state", "archived", "--evidence", archive_receipt,
    ])
    verified_recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
    check(
        archive_task_path.read_bytes() == invalid_archive_bytes
        and verified_recovery["entries"][archive_task]["state"] == "verified"
        and verified_recovery["entries"][archive_task]["receipt"] == archive_receipt,
        "host archive receipt did not settle the sidecar without rewriting legacy TASK bytes",
    )
    archive_module_spec = importlib.util.spec_from_file_location(
        "agent_team_legacy_recovery_required_probe", archive_task_tool,
    )
    check(archive_module_spec is not None and archive_module_spec.loader is not None,
          "could not import task runtime for recovery-required probe")
    archive_module = importlib.util.module_from_spec(archive_module_spec)
    archive_module_spec.loader.exec_module(archive_module)
    synthetic_terminal = json.loads(invalid_archive_bytes)
    synthetic_terminal["execution_state"] = "acknowledged"
    check(
        archive_module.legacy_temporary_terminal(
            synthetic_terminal, {}, archive_recovery_required=False,
        )
        and not archive_module.legacy_temporary_terminal(
            synthetic_terminal, {}, archive_recovery_required=True,
        ),
        "a required archive recovery entry could disappear and still be treated as terminal",
    )


def verify_long_thread_actor_identity(root: Path) -> None:
    project = make_project(root, "long-thread-actor-identity")
    scaffold(project)
    collab = project / "docs" / "collaboration"
    task_tool = collab / "scripts" / "agent_team_task.py"
    long_lead_thread_id = "l" * 300
    lead_actor = f"统筹部/{long_lead_thread_id}"
    ensure_lead_registered(task_tool, long_lead_thread_id)
    long_thread_id = "t" * 300
    ensure_department_registered(task_tool, "执行部", long_thread_id)
    run([sys.executable, str(task_tool), "rebuild-index", "--actor", lead_actor])
    task_id = task_id_from(run([
        sys.executable, str(task_tool), "enqueue", "--actor", lead_actor,
        "--department", "执行部", "--from-department", "统筹部",
        "--title", "300 字符 thread identity probe", "--node", "单节点",
        "--details", "完成确定性验证", "--acceptance-exit", "用户看到验证结果",
        "--failure-path", "错误输入被明确拒绝", "--confirmation", "无需额外确认",
        "--domain-stage", "实现验证", "--authorization-state", "none",
    ]))
    actor = f"执行部/{long_thread_id}"
    claimed = run([
        sys.executable, str(task_tool), "claim", "--task-id", task_id, "--claimed-by", actor,
    ])
    task = json.loads((collab / "tasks" / f"{task_id}.json").read_text(encoding="utf-8"))
    check(
        claimed.stdout.startswith("TASK_CLAIMED |")
        and task["claimed_by"] == actor
        and task["ownership_history"][-1]["actor"] == actor,
        "a valid 300-character thread ID could not bind its ordinary TASK owner identity",
    )
    frozen = run([
        sys.executable, str(task_tool), "freeze-new-work", "--actor", lead_actor,
        "--evidence", "300 字符统筹 thread 冻结探针",
    ])
    frozen_mode = run([sys.executable, str(task_tool), "work-mode"])
    temporary_tool = collab / "scripts" / "agent_team_temporary.py"
    temporary_spec = importlib.util.spec_from_file_location(
        "agent_team_long_actor_temporary_probe", temporary_tool,
    )
    check(temporary_spec is not None and temporary_spec.loader is not None,
          "could not import temporary runtime for long lead actor probe")
    temporary_module = importlib.util.module_from_spec(temporary_spec)
    temporary_spec.loader.exec_module(temporary_module)
    scaffold_spec = importlib.util.spec_from_file_location(
        "agent_team_long_actor_upgrade_probe", SCAFFOLD,
    )
    check(scaffold_spec is not None and scaffold_spec.loader is not None,
          "could not import scaffold runtime for long lead actor probe")
    scaffold_module = importlib.util.module_from_spec(scaffold_spec)
    scaffold_spec.loader.exec_module(scaffold_module)
    check(
        frozen.stdout.startswith("WORK_FREEZE_OK |")
        and "WORK_MODE | frozen" in frozen_mode.stdout
        and temporary_module.work_mode() == "frozen"
        and scaffold_module.collaboration_work_mode(collab) == "frozen",
        "a valid 300-character lead thread poisoned a dispatch-control reader",
    )
    unfrozen = run([
        sys.executable, str(task_tool), "unfreeze-new-work", "--actor", lead_actor,
        "--user-confirmation", "用户确认恢复 300 字符统筹 thread 探针",
    ])
    normal_mode = run([sys.executable, str(task_tool), "work-mode"])
    check(
        unfrozen.stdout.startswith("WORK_UNFREEZE_OK |")
        and "WORK_MODE | normal" in normal_mode.stdout
        and temporary_module.work_mode() == "normal"
        and scaffold_module.collaboration_work_mode(collab) == "normal",
        "a valid 300-character lead thread could not complete freeze/read/unfreeze/read",
    )


def verify_protocol_151_zero_gate_next_action(root: Path) -> None:
    project = make_project(root, "protocol-151-zero-gate-next-action")
    scaffold(project, "lead,dev,test")
    collab = project / "docs" / "collaboration"
    task_tool = collab / "scripts" / "agent_team_task.py"
    ensure_department_registered(task_tool, "统筹部", "lead-thread")
    ensure_department_registered(task_tool, "开发部", "dev-thread")
    ensure_department_registered(task_tool, "测试部", "test-thread")
    run([sys.executable, str(task_tool), "rebuild-index"])
    owner = task_id_from(run([
        sys.executable, str(task_tool), "enqueue", "--actor", "统筹部/lead-thread",
        "--department", "开发部", "--from-department", "统筹部",
        "--title", "零 gate 用户出口优先级", "--node", "2.1.1 next-action",
        "--details", "验证空审核集合在候选绑定后视为全部通过",
        "--acceptance-exit", "正常与冻结模式均先记录用户出口",
        "--failure-path", "错误建议解冻、恢复或继续审核时拒绝",
        "--authorization-state", "none", "--task-kind", "owner",
    ]))
    run([
        sys.executable, str(task_tool), "claim", "--task-id", owner,
        "--claimed-by", "开发部/dev-thread",
    ])
    candidate_id = "CAND-20260828-ZG0001"
    artifact = project / "docs" / "zero-gate-next-action.txt"
    artifact.write_text("zero gate candidate\n", encoding="utf-8")
    manifest = project / "docs" / "zero-gate-next-action-manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "candidate_id": candidate_id,
        "artifact": {
            "path": artifact.relative_to(project).as_posix(),
            "sha256": file_sha256(artifact),
            "kind": "file",
        },
        "source_revision": "verify-zero-gate-next-action",
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    run([
        sys.executable, str(task_tool), "bind-candidate", "--task-id", owner,
        "--candidate-id", candidate_id,
        "--manifest", str(manifest.relative_to(project)),
        "--sha256", file_sha256(manifest), "--actor", "开发部/dev-thread",
    ])

    normal_action = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    normal_bundle = run([
        sys.executable, str(task_tool), "onboard-bundle", "--department", "开发部",
    ])
    check(
        "first_blocker=USER_EXIT_PENDING" in normal_action.stdout
        and "allowed=record-user-exit" in normal_action.stdout
        and "user_decision=yes" in normal_action.stdout
        and "下一合法动作：`record-user-exit`" in normal_bundle.stdout,
        "normal zero-gate candidate did not prioritize record-user-exit in next-action/onboard",
    )

    run([
        sys.executable, str(task_tool), "wait", "--task-id", owner,
        "--reason", "等待用户给出零 gate 候选意见", "--actor", "开发部/dev-thread",
    ])
    normal_waiting_action = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    normal_waiting_bundle = run([
        sys.executable, str(task_tool), "onboard-bundle", "--department", "开发部",
    ])
    check(
        "state=waiting_input" in normal_waiting_action.stdout
        and "first_blocker=USER_EXIT_PENDING" in normal_waiting_action.stdout
        and "allowed=record-user-exit" in normal_waiting_action.stdout
        and "下一合法动作：`record-user-exit`" in normal_waiting_bundle.stdout,
        "normal waiting_input zero-gate owner suggested resume before record-user-exit",
    )

    run([
        sys.executable, str(task_tool), "freeze-new-work", "--actor", "统筹部/lead-thread",
        "--evidence", "零 gate 冻结优先级探针",
    ])
    frozen_action = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    frozen_bundle = run([
        sys.executable, str(task_tool), "onboard-bundle", "--department", "开发部",
    ])
    check(
        "first_blocker=P0_FREEZE_ACTIVE" in frozen_action.stdout
        and "allowed=record-user-exit" in frozen_action.stdout
        and "user_decision=yes" in frozen_action.stdout
        and "下一合法动作：`record-user-exit`" in frozen_bundle.stdout,
        "frozen zero-gate candidate suggested unfreeze before record-user-exit",
    )
    run([
        sys.executable, str(task_tool), "record-user-exit", "--task-id", owner,
        "--candidate-id", candidate_id, "--status", "needs_revision",
        "--evidence", "用户要求修订零 gate 候选", "--actor", "统筹部/lead-thread",
    ])
    frozen_revision = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    check(
        "allowed=unfreeze-new-work,next-action" in frozen_revision.stdout,
        "frozen waiting_input needs_revision did not return to the unfreeze boundary",
    )
    run([
        sys.executable, str(task_tool), "unfreeze-new-work", "--actor", "统筹部/lead-thread",
        "--user-confirmation", "测试夹具恢复零 gate 用户修订",
    ])
    normal_revision = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    check(
        "state=waiting_input" in normal_revision.stdout
        and "first_blocker=TASK_WAITING_INPUT" in normal_revision.stdout
        and "allowed=resume,next-action" in normal_revision.stdout,
        "normal waiting_input needs_revision did not return to resume before candidate binding",
    )


def protocol_151_edge_slice(
    root: Path, label: str, required_gates: tuple[str, ...],
) -> tuple[Path, Path, Path, str, str, dict[str, str], Path]:
    project = make_project(root, label)
    scaffold(project, "lead,dev,test,security")
    collab = project / "docs" / "collaboration"
    task_tool = collab / "scripts" / "agent_team_task.py"
    for department, thread_id in (
        ("统筹部", "lead-thread"), ("开发部", "dev-thread"),
        ("测试部", "test-thread"), ("安全部", "security-thread"),
    ):
        ensure_department_registered(task_tool, department, thread_id)
    run([sys.executable, str(task_tool), "rebuild-index"])
    enqueue_args = [
        sys.executable, str(task_tool), "enqueue", "--actor", "统筹部/lead-thread",
        "--department", "开发部", "--from-department", "统筹部",
        "--title", label, "--node", "2.1.1 next-action priority",
        "--details", "验证 next-action 的机械优先级",
        "--acceptance-exit", "只报告当前真实可执行动作",
        "--failure-path", "错误优先级被反向测试拒绝",
        "--authorization-state", "none", "--task-kind", "owner",
    ]
    for gate_type in required_gates:
        enqueue_args += ["--required-gate", gate_type]
    owner = task_id_from(run(enqueue_args))
    run([
        sys.executable, str(task_tool), "claim", "--task-id", owner,
        "--claimed-by", "开发部/dev-thread",
    ])
    slice_id = json.loads(
        (collab / ".locks" / "slice-control.json").read_text(encoding="utf-8")
    )["active_slice"]["slice_id"]
    candidate_id = "CAND-20260828-EDG001"
    artifact = project / "docs" / f"{label}-candidate.txt"
    artifact.write_text(f"{label} candidate\n", encoding="utf-8")
    manifest = project / "docs" / f"{label}-manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1,
        "candidate_id": candidate_id,
        "artifact": {
            "path": artifact.relative_to(project).as_posix(),
            "sha256": file_sha256(artifact), "kind": "file",
        },
        "source_revision": f"verify-{label}",
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    run([
        sys.executable, str(task_tool), "bind-candidate", "--task-id", owner,
        "--candidate-id", candidate_id,
        "--manifest", str(manifest.relative_to(project)),
        "--sha256", file_sha256(manifest), "--actor", "开发部/dev-thread",
    ])
    departments = {"test": ("测试部", "test-thread"), "security": ("安全部", "security-thread")}
    gates: dict[str, str] = {}
    for gate_type in required_gates:
        department, thread_id = departments[gate_type]
        gate = task_id_from(run([
            sys.executable, str(task_tool), "enqueue", "--actor", "统筹部/lead-thread",
            "--department", department, "--from-department", "统筹部",
            "--title", f"{label} {gate_type} gate", "--node", "2.1.1 next-action priority",
            "--details", "给当前候选独立结论", "--acceptance-exit", "报告固定",
            "--failure-path", "错误候选拒绝", "--authorization-state", "none",
            "--task-kind", "gate", "--slice-id", slice_id, "--gate-type", gate_type,
        ]))
        run([
            sys.executable, str(task_tool), "claim", "--task-id", gate,
            "--claimed-by", f"{department}/{thread_id}",
        ])
        gates[gate_type] = gate
    return project, collab, task_tool, owner, candidate_id, gates, manifest


def protocol_151_edge_report(
    collab: Path, task_id: str, candidate_id: str,
    department: str, decision: str, label: str,
) -> Path:
    report = collab / "部门" / department / "报告" / f"{label}.md"
    report.write_text(f"""---
type: audit_report
department: {department}
target: Agent Team 2.1.1 next-action priority
status: final
date: {dt.date.today().isoformat()}
related_task: {task_id}
decision: {decision}
candidate_id: {candidate_id}
tags: [protocol-1.5.1]
summary: 当前代候选独立审核结论
---

# 独立审核

当前代结论为 {decision}。
""", encoding="utf-8")
    return report


def verify_protocol_151_fail_and_ack_priorities(root: Path) -> None:
    fail_project, fail_collab, fail_tool, fail_owner, fail_candidate, fail_gates, _ = (
        protocol_151_edge_slice(root, "protocol-151-fail-priority", ("test", "security"))
    )
    fail_report = protocol_151_edge_report(
        fail_collab, fail_gates["test"], fail_candidate,
        "测试部", "fail", "fail-priority-test",
    )
    run([
        sys.executable, str(fail_tool), "gate-verdict", "--task-id", fail_gates["test"],
        "--candidate-id", fail_candidate, "--decision", "fail",
        "--report", str(fail_report.relative_to(fail_project)),
        "--evidence", "当前候选测试失败", "--actor", "测试部/test-thread",
    ])
    fail_claimed = run([
        sys.executable, str(fail_tool), "next-action", "--task-id", fail_owner,
    ])
    fail_claimed_allowed = re.search(r"allowed=([^|\n]+)", fail_claimed.stdout)
    check(
        "first_blocker=CURRENT_GATE_FAIL" in fail_claimed.stdout
        and fail_claimed_allowed is not None
        and "bind-candidate" in fail_claimed_allowed.group(1).split(",")
        and "gate-verdict" not in fail_claimed_allowed.group(1).split(","),
        "a current gate FAIL was hidden behind another claimed pending gate",
    )
    run([
        sys.executable, str(fail_tool), "block", "--task-id", fail_owner,
        "--reason", "失败候选等待恢复后返工", "--actor", "开发部/dev-thread",
    ])
    fail_blocked = run([
        sys.executable, str(fail_tool), "next-action", "--task-id", fail_owner,
    ])
    check(
        "first_blocker=TASK_BLOCKED" in fail_blocked.stdout
        and "allowed=resume,next-action" in fail_blocked.stdout,
        "a blocked owner with current gate FAIL skipped resume before candidate rebinding",
    )
    run([
        sys.executable, str(fail_tool), "freeze-new-work", "--actor", "统筹部/lead-thread",
        "--evidence", "失败候选冻结边界探针",
    ])
    fail_frozen = run([
        sys.executable, str(fail_tool), "next-action", "--task-id", fail_owner,
    ])
    check(
        "first_blocker=P0_FREEZE_ACTIVE" in fail_frozen.stdout
        and "allowed=unfreeze-new-work,next-action" in fail_frozen.stdout
        and "forbidden=resume,bind-candidate,claim,enqueue" in fail_frozen.stdout,
        "a frozen failed candidate continued gate work or exposed candidate binding",
    )

    ack_project, ack_collab, ack_tool, ack_owner, ack_candidate, ack_gates, ack_manifest = (
        protocol_151_edge_slice(root, "protocol-151-completed-ack", ("test",))
    )
    pass_report = protocol_151_edge_report(
        ack_collab, ack_gates["test"], ack_candidate,
        "测试部", "pass", "completed-ack-test",
    )
    run([
        sys.executable, str(ack_tool), "gate-verdict", "--task-id", ack_gates["test"],
        "--candidate-id", ack_candidate, "--decision", "pass",
        "--report", str(pass_report.relative_to(ack_project)),
        "--evidence", "当前候选测试通过", "--actor", "测试部/test-thread",
    ])
    run([
        sys.executable, str(ack_tool), "record-user-exit", "--task-id", ack_owner,
        "--candidate-id", ack_candidate, "--status", "not_applicable",
        "--evidence", "纯代码候选无需人工体验", "--actor", "统筹部/lead-thread",
    ])
    incomplete_gate_action = run([
        sys.executable, str(ack_tool), "next-action", "--task-id", ack_owner,
    ])
    check(
        "first_blocker=GATE_TASK_COMPLETION_REQUIRED" in incomplete_gate_action.stdout
        and f"target_task={ack_gates['test']}" in incomplete_gate_action.stdout
        and "allowed=complete,next-action" in incomplete_gate_action.stdout,
        "owner next-action suggested owner completion before the PASS gate TASK was completed",
    )
    run([
        sys.executable, str(ack_tool), "complete", "--task-id", ack_gates["test"],
        "--actor", "测试部/test-thread",
        "--artifact", str(pass_report.relative_to(ack_project)),
        "--report", str(pass_report.relative_to(ack_project)),
        "--verified", "当前代报告固定", "--unverified", "无",
        "--mistake-check", "未把 gate PASS 冒充发布",
    ])
    completed_gate = run([
        sys.executable, str(ack_tool), "next-action", "--task-id", ack_gates["test"],
    ])
    check(
        "state=completed" in completed_gate.stdout
        and "allowed=ack,next-action" in completed_gate.stdout,
        "normal completed gate did not expose ack as its next legal action",
    )
    bad_gate_ack = run([
        sys.executable, str(ack_tool), "ack", "--task-id", ack_gates["test"],
        "--acknowledged-by", "开发部/dev-thread",
    ], ok=False)
    check("必须匹配当前已登记统筹会话" in bad_gate_ack.stderr,
          "next-action ack reporting bypassed the actual ack actor constraint")
    run([
        sys.executable, str(ack_tool), "ack", "--task-id", ack_gates["test"],
        "--acknowledged-by", "统筹部/lead-thread",
    ])
    acknowledged_gate = run([
        sys.executable, str(ack_tool), "next-action", "--task-id", ack_gates["test"],
    ])
    check(
        "state=acknowledged" in acknowledged_gate.stdout
        and "first_blocker=TASK_TERMINAL" in acknowledged_gate.stdout
        and "allowed=next-action" in acknowledged_gate.stdout,
        "acknowledged gate was not treated as terminal",
    )
    run([
        sys.executable, str(ack_tool), "complete", "--task-id", ack_owner,
        "--actor", "开发部/dev-thread",
        "--artifact", str(ack_manifest.relative_to(ack_project)),
        "--verified", "当前候选及 gate 已固定", "--unverified", "无",
        "--mistake-check", "未把代码完成冒充用户体验",
    ])
    completed_owner = run([
        sys.executable, str(ack_tool), "next-action", "--task-id", ack_owner,
    ])
    check(
        "state=completed" in completed_owner.stdout
        and "allowed=ack,next-action" in completed_owner.stdout,
        "normal completed owner did not expose ack as its next legal action",
    )
    bad_owner_ack = run([
        sys.executable, str(ack_tool), "ack", "--task-id", ack_owner,
        "--acknowledged-by", "开发部/dev-thread",
    ], ok=False)
    check("必须匹配当前已登记统筹会话" in bad_owner_ack.stderr,
          "completed owner ack actor constraint was not enforced by the actual command")


def verify_protocol_151_authorization_priority(root: Path) -> None:
    for task_state in ("queued", "blocked", "waiting_input"):
        for authorization in ("user_required", "user_rejected"):
            label = f"protocol-151-auth-{task_state}-{authorization}"
            project = make_project(root, label)
            scaffold(project, "lead,dev,test")
            collab = project / "docs" / "collaboration"
            task_tool = collab / "scripts" / "agent_team_task.py"
            for department, thread_id in (
                ("统筹部", "lead-thread"), ("开发部", "dev-thread"), ("测试部", "test-thread"),
            ):
                ensure_department_registered(task_tool, department, thread_id)
            run([sys.executable, str(task_tool), "rebuild-index"])
            initial_authorization = authorization if task_state == "queued" else "none"
            enqueue_args = [
                sys.executable, str(task_tool), "enqueue", "--actor", "统筹部/lead-thread",
                "--department", "开发部", "--from-department", "统筹部",
                "--title", label, "--node", "2.1.1 authorization priority",
                "--details", "验证授权闸先于领取或恢复",
                "--acceptance-exit", "只报告真实授权动作",
                "--failure-path", "错误建议 claim 或 resume 时拒绝",
                "--authorization-state", initial_authorization, "--task-kind", "owner",
            ]
            if initial_authorization == "user_rejected":
                enqueue_args += ["--authorization-evidence", "用户已拒绝当前工作"]
            owner = task_id_from(run(enqueue_args))
            if task_state != "queued":
                run([
                    sys.executable, str(task_tool), "claim", "--task-id", owner,
                    "--claimed-by", "开发部/dev-thread",
                ])
                transition = "block" if task_state == "blocked" else "wait"
                run([
                    sys.executable, str(task_tool), transition, "--task-id", owner,
                    "--reason", f"进入 {task_state} 授权探针", "--actor", "开发部/dev-thread",
                ])
                run([
                    sys.executable, str(task_tool), "authorize", "--task-id", owner,
                    "--state", authorization, "--evidence", f"设置 {authorization} 授权闸",
                    "--actor", "统筹部/lead-thread",
                ])

            normal = run([
                sys.executable, str(task_tool), "next-action", "--task-id", owner,
            ])
            normal_allowed = re.search(r"allowed=([^|\n]+)", normal.stdout)
            expected_blocker = (
                "USER_AUTHORIZATION_REQUIRED" if authorization == "user_required" else "USER_REJECTED"
            )
            expected_action = "authorize" if authorization == "user_required" else "resolve"
            check(
                f"state={task_state}" in normal.stdout
                and f"first_blocker={expected_blocker}" in normal.stdout
                and normal_allowed is not None
                and normal_allowed.group(1).strip() == f"{expected_action},next-action"
                and "claim" not in normal_allowed.group(1).split(",")
                and "resume" not in normal_allowed.group(1).split(",")
                and ("user_decision=yes" in normal.stdout) == (authorization == "user_required"),
                f"normal {task_state} {authorization} authorization gate suggested an impossible state action",
            )

            run([
                sys.executable, str(task_tool), "freeze-new-work", "--actor", "统筹部/lead-thread",
                "--evidence", f"冻结 {task_state} {authorization} 授权探针",
            ])
            frozen = run([
                sys.executable, str(task_tool), "next-action", "--task-id", owner,
            ])
            frozen_allowed = re.search(r"allowed=([^|\n]+)", frozen.stdout)
            expected_frozen_actions = (
                "authorize,unfreeze-new-work,next-action"
                if authorization == "user_required" else "resolve,next-action"
            )
            check(
                f"first_blocker={expected_blocker}" in frozen.stdout
                and frozen_allowed is not None
                and frozen_allowed.group(1).strip() == expected_frozen_actions
                and "claim" not in frozen_allowed.group(1).split(",")
                and "resume" not in frozen_allowed.group(1).split(",")
                and (
                    ("unfreeze-new-work" in frozen_allowed.group(1).split(","))
                    == (authorization == "user_required")
                )
                and ("user_decision=yes" in frozen.stdout) == (authorization == "user_required"),
                f"frozen {task_state} {authorization} authorization gate escaped its cleanup whitelist",
            )
            if authorization == "user_rejected" or task_state == "blocked":
                if authorization == "user_required":
                    run([
                        sys.executable, str(task_tool), "authorize", "--task-id", owner,
                        "--state", "user_rejected", "--evidence", "用户在冻结中明确拒绝",
                        "--actor", "统筹部/lead-thread",
                    ])
                    rejected = run([
                        sys.executable, str(task_tool), "next-action", "--task-id", owner,
                    ])
                    check(
                        "first_blocker=USER_REJECTED" in rejected.stdout
                        and "allowed=resolve,next-action" in rejected.stdout,
                        "frozen user_required could not close through user_rejected and resolve",
                    )
                task_payload = json.loads(
                    (collab / "tasks" / f"{owner}.json").read_text(encoding="utf-8")
                )
                resolved = run([
                    sys.executable, str(task_tool), "resolve", "--task-id", owner,
                    "--state", "rejected_by_user", "--expected-revision", str(task_payload["revision"]),
                    "--actor", "统筹部/lead-thread", "--reason", "用户拒绝当前工作",
                    "--evidence", "冻结授权拒绝分支已确认",
                ])
                check(resolved.stdout.startswith("TASK_RESOLUTION_OK | state=rejected_by_user"),
                      "frozen user_rejected could not resolve without unfreezing")
            else:
                state_action = "claim" if task_state == "queued" else "resume"
                denied_args = [sys.executable, str(task_tool), state_action, "--task-id", owner]
                if state_action == "claim":
                    denied_args += ["--claimed-by", "开发部/dev-thread"]
                else:
                    denied_args += ["--actor", "开发部/dev-thread"]
                denied = run(denied_args, ok=False)
                check("P0_FREEZE_ACTIVE" in denied.stderr,
                      "frozen user_required allowed claim/resume before explicit unfreeze")
                run([
                    sys.executable, str(task_tool), "unfreeze-new-work",
                    "--actor", "统筹部/lead-thread",
                    "--user-confirmation", "用户确认继续，先显式解冻",
                ])
                after_unfreeze = run([
                    sys.executable, str(task_tool), "next-action", "--task-id", owner,
                ])
                check(
                    "first_blocker=USER_AUTHORIZATION_REQUIRED" in after_unfreeze.stdout
                    and "allowed=authorize,next-action" in after_unfreeze.stdout,
                    "confirmed path skipped the user_confirmed record after unfreezing",
                )
                run([
                    sys.executable, str(task_tool), "authorize", "--task-id", owner,
                    "--state", "user_confirmed", "--evidence", "用户明确确认继续",
                    "--actor", "统筹部/lead-thread",
                ])
                confirmed = run([
                    sys.executable, str(task_tool), "next-action", "--task-id", owner,
                ])
                check(
                    f"allowed={state_action}" in confirmed.stdout,
                    f"user_confirmed {task_state} did not expose {state_action}",
                )
                completed_action = run(denied_args)
                expected_receipt = "TASK_CLAIMED" if state_action == "claim" else "TASK_RESUMED"
                check(completed_action.stdout.startswith(expected_receipt),
                      f"confirmed path could not execute {state_action} after unfreeze and authorize")


def protocol_151_switch_session(
    collab: Path, department: str, old_thread: str, new_thread: str,
) -> None:
    session_tool = collab / "scripts" / "agent_team_session.py"
    run([
        sys.executable, str(session_tool), "begin-switch", "--department", department,
        "--old-thread-id", old_thread, "--reason", "next-action identity drift probe",
    ])
    for step in ("created", "onboarded", "registered"):
        run([
            sys.executable, str(session_tool), "mark", "--department", department,
            "--step", step, "--thread-id", new_thread, "--evidence", f"identity-{step}",
        ])


def verify_protocol_151_identity_priority(root: Path) -> None:
    for task_state in ("claimed", "blocked", "waiting_input"):
        project, collab, task_tool, owner, candidate, _, _ = protocol_151_edge_slice(
            root, f"protocol-151-owner-drift-{task_state}", (),
        )
        run([
            sys.executable, str(task_tool), "record-user-exit", "--task-id", owner,
            "--candidate-id", candidate, "--status", "not_applicable",
            "--evidence", "身份探针无需用户体验", "--actor", "统筹部/lead-thread",
        ])
        if task_state != "claimed":
            transition = "block" if task_state == "blocked" else "wait"
            run([
                sys.executable, str(task_tool), transition, "--task-id", owner,
                "--reason", f"进入 {task_state} 身份漂移探针", "--actor", "开发部/dev-thread",
            ])
        protocol_151_switch_session(collab, "开发部", "dev-thread", "dev-thread-v2")
        normal = run([
            sys.executable, str(task_tool), "next-action", "--task-id", owner,
        ])
        normal_allowed = re.search(r"allowed=([^|\n]+)", normal.stdout)
        check(
            "first_blocker=OWNER_REBIND_REQUIRED" in normal.stdout
            and normal_allowed is not None
            and normal_allowed.group(1).strip() == "rebind-owner,next-action",
            f"normal {task_state} owner identity drift exposed an action that its registered actor cannot run",
        )
        run([
            sys.executable, str(task_tool), "freeze-new-work", "--actor", "统筹部/lead-thread",
            "--evidence", f"冻结 {task_state} 身份漂移探针",
        ])
        frozen = run([
            sys.executable, str(task_tool), "next-action", "--task-id", owner,
        ])
        check(
            "first_blocker=OWNER_REBIND_REQUIRED" in frozen.stdout
            and "allowed=rebind-owner,next-action" in frozen.stdout,
            f"frozen {task_state} owner identity drift was hidden behind unfreeze",
        )

    pending_project, pending_collab, pending_tool, pending_owner, pending_candidate, _, _ = (
        protocol_151_edge_slice(root, "protocol-151-user-exit-before-rebind", ())
    )
    run([
        sys.executable, str(pending_tool), "block", "--task-id", pending_owner,
        "--reason", "等待用户纠偏", "--actor", "开发部/dev-thread",
    ])
    protocol_151_switch_session(pending_collab, "开发部", "dev-thread", "dev-thread-v2")
    pending = run([
        sys.executable, str(pending_tool), "next-action", "--task-id", pending_owner,
    ])
    check(
        "first_blocker=USER_EXIT_PENDING" in pending.stdout
        and "allowed=record-user-exit" in pending.stdout,
        "owner drift incorrectly hid a user-exit fact that the lead can still record",
    )
    run([
        sys.executable, str(pending_tool), "record-user-exit", "--task-id", pending_owner,
        "--candidate-id", pending_candidate, "--status", "needs_revision",
        "--evidence", "用户要求换班后修订", "--actor", "统筹部/lead-thread",
    ])
    revision = run([
        sys.executable, str(pending_tool), "next-action", "--task-id", pending_owner,
    ])
    check(
        "first_blocker=OWNER_REBIND_REQUIRED" in revision.stdout
        and "allowed=rebind-owner,next-action" in revision.stdout
        and "resume" not in re.search(r"allowed=([^|\n]+)", revision.stdout).group(1).split(","),
        "needs_revision skipped owner rebind and suggested resume to a drifted actor",
    )

    gate_project, gate_collab, gate_tool, gate_owner, _, gate_tasks, _ = protocol_151_edge_slice(
        root, "protocol-151-gate-drift", ("test", "security"),
    )
    protocol_151_switch_session(gate_collab, "测试部", "test-thread", "test-thread-v2")
    owner_view = run([
        sys.executable, str(gate_tool), "next-action", "--task-id", gate_owner,
    ])
    gate_view = run([
        sys.executable, str(gate_tool), "next-action", "--task-id", gate_tasks["test"],
    ])
    for output, label in ((owner_view.stdout, "owner"), (gate_view.stdout, "drifted gate")):
        allowed = re.search(r"allowed=([^|\n]+)", output)
        check(
            "first_blocker=OWNER_REBIND_REQUIRED" in output
            and allowed is not None
            and allowed.group(1).strip() == "rebind-owner,next-action"
            and "gate-verdict" not in allowed.group(1).split(","),
            f"{label} next-action recommended verdict with a drifted claimed gate actor",
        )
    run([
        sys.executable, str(gate_tool), "freeze-new-work", "--actor", "统筹部/lead-thread",
        "--evidence", "冻结 gate 身份漂移探针",
    ])
    frozen_owner_view = run([
        sys.executable, str(gate_tool), "next-action", "--task-id", gate_owner,
    ])
    check(
        "first_blocker=OWNER_REBIND_REQUIRED" in frozen_owner_view.stdout
        and "allowed=rebind-owner,next-action" in frozen_owner_view.stdout,
        "frozen owner query recommended a verdict for a drifted gate actor",
    )


def verify_protocol_151_state_retry_actor_identity(root: Path) -> None:
    project, collab, task_tool, owner, _, _, _ = protocol_151_edge_slice(
        root, "protocol-151-state-retry-actor", (),
    )
    session_tool = collab / "scripts" / "agent_team_session.py"
    run([
        sys.executable, str(task_tool), "block", "--task-id", owner,
        "--reason", "同一阻断原因", "--actor", "开发部/dev-thread",
    ])

    def switch_and_rebind(old_thread: str, new_thread: str) -> None:
        run([
            sys.executable, str(session_tool), "begin-switch", "--department", "开发部",
            "--old-thread-id", old_thread, "--reason", "verify state retry actor binding",
        ])
        for step in ("created", "onboarded", "registered"):
            run([
                sys.executable, str(session_tool), "mark", "--department", "开发部",
                "--step", step, "--thread-id", new_thread,
                "--evidence", f"verify-{new_thread}-{step}",
            ])
        payload = json.loads((collab / "tasks" / f"{owner}.json").read_text(encoding="utf-8"))
        run([
            sys.executable, str(task_tool), "rebind-owner", "--task-id", owner,
            "--expected-revision", str(payload["revision"]),
            "--actor", f"开发部/{new_thread}", "--previous-actor", f"开发部/{old_thread}",
            "--evidence", "authorized switch and four-document onboarding",
        ])
        run([
            sys.executable, str(session_tool), "finish-switch", "--department", "开发部",
            "--new-thread-id", new_thread,
            "--evidence", f"host=verify thread_id={old_thread} archived=true",
        ])

    switch_and_rebind("dev-thread", "dev-thread-v2")
    repeated_block = run([
        sys.executable, str(task_tool), "block", "--task-id", owner,
        "--reason", "同一阻断原因", "--actor", "开发部/dev-thread-v2",
    ], ok=False)
    check("动作签名冲突" in repeated_block.stderr,
          "a new owner session inherited the previous actor's block NOOP")
    run([
        sys.executable, str(task_tool), "resume", "--task-id", owner,
        "--actor", "开发部/dev-thread-v2",
    ])
    repeated_resume = run([
        sys.executable, str(task_tool), "resume", "--task-id", owner,
        "--actor", "开发部/dev-thread-v2",
    ])
    check(repeated_resume.stdout.startswith("TASK_STATE_NOOP"),
          "the same actor's identical resume retry did not remain a NOOP")
    run([
        sys.executable, str(task_tool), "freeze-new-work", "--actor", "统筹部/lead-thread",
        "--evidence", "verify frozen identical resume retry",
    ])
    frozen_resume = run([
        sys.executable, str(task_tool), "resume", "--task-id", owner,
        "--actor", "开发部/dev-thread-v2",
    ])
    check(frozen_resume.stdout.startswith("TASK_STATE_NOOP"),
          "an identical resume retry was rejected only because work became frozen")
    run([
        sys.executable, str(task_tool), "unfreeze-new-work", "--actor", "统筹部/lead-thread",
        "--user-confirmation", "verify resume retry fixture continues",
    ])

    switch_and_rebind("dev-thread-v2", "dev-thread-v3")
    inherited_resume = run([
        sys.executable, str(task_tool), "resume", "--task-id", owner,
        "--actor", "开发部/dev-thread-v3",
    ], ok=False)
    check("动作签名冲突" in inherited_resume.stderr,
          "a new owner session inherited the previous actor's resume NOOP")
    run([
        sys.executable, str(task_tool), "wait", "--task-id", owner,
        "--reason", "同一等待原因", "--actor", "开发部/dev-thread-v3",
    ])

    switch_and_rebind("dev-thread-v3", "dev-thread-v4")
    repeated_wait = run([
        sys.executable, str(task_tool), "wait", "--task-id", owner,
        "--reason", "同一等待原因", "--actor", "开发部/dev-thread-v4",
    ], ok=False)
    check("动作签名冲突" in repeated_wait.stderr,
          "a new owner session inherited the previous actor's wait NOOP")
    run([
        sys.executable, str(task_tool), "resume", "--task-id", owner,
        "--actor", "开发部/dev-thread-v4",
    ])
    final_resume = run([
        sys.executable, str(task_tool), "resume", "--task-id", owner,
        "--actor", "开发部/dev-thread-v4",
    ])
    check(final_resume.stdout.startswith("TASK_STATE_NOOP"),
          "the rebound actor's own resume retry did not remain a NOOP")


def verify_protocol_151_user_exit_supersession(root: Path) -> None:
    for prior_status in ("verified", "not_applicable"):
        project, collab, task_tool, owner, candidate, gates, _ = protocol_151_edge_slice(
            root, f"protocol-151-user-exit-supersede-{prior_status}", ("test",),
        )
        report = protocol_151_edge_report(
            collab, gates["test"], candidate, "测试部", "pass",
            f"user-exit-supersede-{prior_status}",
        )
        run([
            sys.executable, str(task_tool), "gate-verdict", "--task-id", gates["test"],
            "--candidate-id", candidate, "--decision", "pass",
            "--report", str(report.relative_to(project)),
            "--evidence", "当前候选测试通过", "--actor", "测试部/test-thread",
        ])
        prior_evidence = f"用户原先记录 {prior_status}"
        run([
            sys.executable, str(task_tool), "record-user-exit", "--task-id", owner,
            "--candidate-id", candidate, "--status", prior_status,
            "--evidence", prior_evidence, "--actor", "统筹部/lead-thread",
        ])
        revised = run([
            sys.executable, str(task_tool), "record-user-exit", "--task-id", owner,
            "--candidate-id", candidate, "--status", "needs_revision",
            "--evidence", "用户随后要求同一候选继续修订", "--actor", "统筹部/lead-thread",
        ])
        control = json.loads(
            (collab / ".locks" / "slice-control.json").read_text(encoding="utf-8")
        )
        active = control["active_slice"]
        request = active["revision_requests"][-1]
        superseded = request["superseded_user_exit"]
        action = run([
            sys.executable, str(task_tool), "next-action", "--task-id", owner,
        ])
        check(
            revised.stdout.startswith("USER_EXIT_RECORDED")
            and active["user_exit"]["status"] == "needs_revision"
            and request["status"] == "pending"
            and superseded["status"] == prior_status
            and superseded["evidence"] == prior_evidence
            and superseded["candidate_id"] == candidate
            and superseded["generation"] == 1
            and "first_blocker=USER_REVISION_READY" in action.stdout,
            f"{prior_status} user exit could not be superseded by a preserved user revision",
        )


def verify_protocol_151_completed_revision_reopen(root: Path) -> None:
    project, collab, task_tool, owner, candidate_1, gates, manifest_1 = protocol_151_edge_slice(
        root, "protocol-151-completed-revision-reopen", ("test",),
    )
    gate = gates["test"]
    report = protocol_151_edge_report(
        collab, gate, candidate_1, "测试部", "pass", "completed-revision-reopen-pass",
    )
    run([
        sys.executable, str(task_tool), "gate-verdict", "--task-id", gate,
        "--candidate-id", candidate_1, "--decision", "pass",
        "--report", str(report.relative_to(project)),
        "--evidence", "第一代当前候选测试通过", "--actor", "测试部/test-thread",
    ])
    run([
        sys.executable, str(task_tool), "record-user-exit", "--task-id", owner,
        "--candidate-id", candidate_1, "--status", "verified",
        "--evidence", "用户先确认第一代", "--actor", "统筹部/lead-thread",
    ])
    run([
        sys.executable, str(task_tool), "complete", "--task-id", gate,
        "--actor", "测试部/test-thread", "--artifact", str(report.relative_to(project)),
        "--report", str(report.relative_to(project)), "--verified", "第一代 gate 已固定",
        "--unverified", "无", "--mistake-check", "未把 PASS 冒充发布",
    ])
    run([
        sys.executable, str(task_tool), "ack", "--task-id", gate,
        "--acknowledged-by", "统筹部/lead-thread",
    ])
    run([
        sys.executable, str(task_tool), "complete", "--task-id", owner,
        "--actor", "开发部/dev-thread", "--artifact", str(manifest_1.relative_to(project)),
        "--verified", "第一代候选已固定", "--unverified", "无",
        "--mistake-check", "等待统筹最终核收",
    ])
    run([
        sys.executable, str(task_tool), "record-user-exit", "--task-id", owner,
        "--candidate-id", candidate_1, "--status", "needs_revision",
        "--evidence", "用户在切片关闭前撤回验收并要求修订", "--actor", "统筹部/lead-thread",
    ])
    action = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    check(
        "first_blocker=OWNER_REOPEN_REQUIRED" in action.stdout
        and f"target_task={owner}" in action.stdout
        and "allowed=resume,next-action" in action.stdout,
        "a completed owner with a user revision was not routed to reopen",
    )
    denied_ack = run([
        sys.executable, str(task_tool), "ack", "--task-id", owner,
        "--acknowledged-by", "统筹部/lead-thread",
    ], ok=False)
    check("用户修订" in denied_ack.stderr,
          "owner acknowledgement closed a slice after the user requested revision")
    run([
        sys.executable, str(task_tool), "resume", "--task-id", owner,
        "--actor", "开发部/dev-thread",
    ])

    candidate_2 = "CAND-20260829-RPN002"
    artifact_2 = project / "docs" / "completed-revision-candidate-2.txt"
    artifact_2.write_text("completed revision generation 2\n", encoding="utf-8")
    manifest_2 = project / "docs" / "completed-revision-manifest-2.json"
    manifest_2.write_text(json.dumps({
        "schema_version": 1,
        "candidate_id": candidate_2,
        "artifact": {
            "path": artifact_2.relative_to(project).as_posix(),
            "sha256": file_sha256(artifact_2), "kind": "file",
        },
        "source_revision": "verify-completed-revision-generation-2",
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    run([
        sys.executable, str(task_tool), "bind-candidate", "--task-id", owner,
        "--candidate-id", candidate_2, "--manifest", str(manifest_2.relative_to(project)),
        "--sha256", file_sha256(manifest_2), "--actor", "开发部/dev-thread",
    ])
    gate_action = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    check(
        "first_blocker=GATE_TASK_REOPEN_REQUIRED" in gate_action.stdout
        and f"target_task={gate}" in gate_action.stdout
        and "allowed=resume,next-action" in gate_action.stdout,
        "a next-generation owner did not route an acknowledged gate back to its original TASK",
    )
    run([
        sys.executable, str(task_tool), "resume", "--task-id", gate,
        "--actor", "测试部/test-thread",
    ])
    owner_payload = json.loads((collab / "tasks" / f"{owner}.json").read_text(encoding="utf-8"))
    gate_payload = json.loads((collab / "tasks" / f"{gate}.json").read_text(encoding="utf-8"))
    check(
        owner_payload["execution_state"] == "claimed"
        and gate_payload["execution_state"] == "claimed"
        and owner_payload["completion_history"][-1]["candidate_id"] == candidate_1
        and gate_payload["completion_history"][-1]["report"] == str(report.relative_to(project))
        and gate_payload["completion_history"][-1]["acknowledged_by"] == "统筹部/lead-thread"
        and not owner_payload["artifacts"]
        and not gate_payload["artifacts"],
        "reopen did not preserve prior completion evidence before clearing the live delivery fields",
    )

    report_2 = protocol_151_edge_report(
        collab, gate, candidate_2, "测试部", "pass", "completed-revision-reopen-pass-2",
    )
    run([
        sys.executable, str(task_tool), "gate-verdict", "--task-id", gate,
        "--candidate-id", candidate_2, "--decision", "pass",
        "--report", str(report_2.relative_to(project)),
        "--evidence", "第二代当前候选测试通过", "--actor", "测试部/test-thread",
    ])
    run([
        sys.executable, str(task_tool), "record-user-exit", "--task-id", owner,
        "--candidate-id", candidate_2, "--status", "verified",
        "--evidence", "用户确认第二代", "--actor", "统筹部/lead-thread",
    ])
    run([
        sys.executable, str(task_tool), "complete", "--task-id", gate,
        "--actor", "测试部/test-thread", "--artifact", str(report_2.relative_to(project)),
        "--report", str(report_2.relative_to(project)), "--verified", "第二代 gate 已固定",
        "--unverified", "无", "--mistake-check", "未把第二代 PASS 冒充发布",
    ])
    run([
        sys.executable, str(task_tool), "ack", "--task-id", gate,
        "--acknowledged-by", "统筹部/lead-thread",
    ])
    run([
        sys.executable, str(task_tool), "complete", "--task-id", owner,
        "--actor", "开发部/dev-thread", "--artifact", str(manifest_2.relative_to(project)),
        "--verified", "第二代候选已固定", "--unverified", "无",
        "--mistake-check", "等待统筹最终核收",
    ])
    run([
        sys.executable, str(task_tool), "ack", "--task-id", owner,
        "--acknowledged-by", "统筹部/lead-thread",
    ])
    closed = json.loads(
        (collab / ".locks" / "slice-control.json").read_text(encoding="utf-8")
    )
    close_entry = closed["history"][-1]
    check(
        closed["active_slice"] is None
        and close_entry["user_exit"]["candidate_id"] == candidate_2
        and close_entry["revision_requests"][-1]["source_candidate_id"] == candidate_1
        and close_entry["revision_requests"][-1]["status"] == "consumed"
        and close_entry["revision_requests"][-1]["evidence"] == "用户在切片关闭前撤回验收并要求修订",
        "slice close discarded the consumed user revision evidence from cold history",
    )
    forged = json.loads(
        (collab / ".locks" / "slice-control.json").read_text(encoding="utf-8")
    )
    forged["history"][-1]["revision_requests"][-1]["consumed_by_candidate_id"] = (
        "CAND-20260829-FORGED"
    )
    (collab / ".locks" / "slice-control.json").write_text(
        json.dumps(forged, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    forged_doctor = run([
        sys.executable, str(task_tool), "doctor",
    ], ok=False)
    check("冷历史已消费用户修订记录无效" in forged_doctor.stderr,
          "doctor accepted a forged consumed candidate identity in cold revision history")


def verify_protocol_151_gate_error_recovery_and_blocked_routing(root: Path) -> None:
    project, collab, task_tool, owner, candidate, gates, _ = protocol_151_edge_slice(
        root, "protocol-151-gate-error-recovery", ("test",),
    )
    gate = gates["test"]
    run([
        sys.executable, str(task_tool), "block", "--task-id", gate,
        "--reason", "gate 等待独立证据", "--actor", "测试部/test-thread",
    ])
    owner_action = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    check(
        "first_blocker=GATE_TASK_RESUME_REQUIRED" in owner_action.stdout
        and f"target_task={gate}" in owner_action.stdout
        and "allowed=resume,next-action" in owner_action.stdout,
        "owner next-action targeted itself or verdict while its only pending gate was blocked",
    )
    report = protocol_151_edge_report(
        collab, gate, candidate, "测试部", "pass", "blocked-gate-invalid-verdict",
    )
    denied = run([
        sys.executable, str(task_tool), "gate-verdict", "--task-id", gate,
        "--candidate-id", candidate, "--decision", "pass",
        "--report", str(report.relative_to(project)),
        "--evidence", "blocked gate 不得 verdict", "--actor", "测试部/test-thread",
    ], ok=False)
    marker = collab / ".locks" / "gate-verdict-transaction.json"
    after = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    check(
        "claimed gate" in denied.stderr
        and not marker.exists()
        and "HOT_STATE_BUSY" not in after.stdout,
        "an invalid gate verdict left a persistent transaction marker or blocked next-action",
    )


def verify_protocol_151_acknowledged_owner_revision_reopen(root: Path) -> None:
    project, collab, task_tool, owner, candidate, gates, manifest = protocol_151_edge_slice(
        root, "protocol-151-acknowledged-owner-revision", ("test",),
    )
    gate = gates["test"]
    report = protocol_151_edge_report(
        collab, gate, candidate, "测试部", "pass", "acknowledged-owner-revision-pass",
    )
    run([
        sys.executable, str(task_tool), "gate-verdict", "--task-id", gate,
        "--candidate-id", candidate, "--decision", "pass",
        "--report", str(report.relative_to(project)),
        "--evidence", "当前候选测试通过", "--actor", "测试部/test-thread",
    ])
    run([
        sys.executable, str(task_tool), "record-user-exit", "--task-id", owner,
        "--candidate-id", candidate, "--status", "verified",
        "--evidence", "用户先确认当前代", "--actor", "统筹部/lead-thread",
    ])
    run([
        sys.executable, str(task_tool), "complete", "--task-id", gate,
        "--actor", "测试部/test-thread", "--artifact", str(report.relative_to(project)),
        "--report", str(report.relative_to(project)), "--verified", "gate 已固定",
        "--unverified", "无", "--mistake-check", "等待统筹核收",
    ])
    run([
        sys.executable, str(task_tool), "ack", "--task-id", gate,
        "--acknowledged-by", "统筹部/lead-thread",
    ])
    run([
        sys.executable, str(task_tool), "complete", "--task-id", owner,
        "--actor", "开发部/dev-thread", "--artifact", str(manifest.relative_to(project)),
        "--verified", "owner 已固定", "--unverified", "无",
        "--mistake-check", "等待统筹核收",
    ])
    gate_path = collab / "tasks" / f"{gate}.json"
    legacy_gate = json.loads(gate_path.read_text(encoding="utf-8"))
    legacy_gate["execution_state"] = "completed"
    legacy_gate.pop("acknowledged_by", None)
    legacy_gate["revision"] += 1
    gate_path.write_text(
        json.dumps(legacy_gate, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    owner_path = collab / "tasks" / f"{owner}.json"
    acknowledged_owner = json.loads(owner_path.read_text(encoding="utf-8"))
    acknowledged_owner["execution_state"] = "acknowledged"
    acknowledged_owner["acknowledged_by"] = "统筹部/lead-thread"
    acknowledged_owner["revision"] += 1
    owner_path.write_text(
        json.dumps(acknowledged_owner, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    run([
        sys.executable, str(task_tool), "rebuild-index", "--actor", "统筹部/lead-thread",
    ])
    run([
        sys.executable, str(task_tool), "record-user-exit", "--task-id", owner,
        "--candidate-id", candidate, "--status", "needs_revision",
        "--evidence", "owner 已核收但 gate 未核收时用户要求返工",
        "--actor", "统筹部/lead-thread",
    ])
    action = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    check(
        "state=acknowledged" in action.stdout
        and "first_blocker=OWNER_REOPEN_REQUIRED" in action.stdout
        and f"target_task={owner}" in action.stdout
        and "allowed=resume,next-action" in action.stdout,
        "an acknowledged owner with an active user revision was hidden behind TASK_TERMINAL",
    )
    denied_gate_ack = run([
        sys.executable, str(task_tool), "ack", "--task-id", gate,
        "--acknowledged-by", "统筹部/lead-thread",
    ], ok=False)
    check("用户修订" in denied_gate_ack.stderr,
          "gate acknowledgement closed a slice after an acknowledged owner received a revision")
    run([
        sys.executable, str(task_tool), "resume", "--task-id", owner,
        "--actor", "开发部/dev-thread",
    ])
    reopened = json.loads((collab / "tasks" / f"{owner}.json").read_text(encoding="utf-8"))
    check(
        reopened["execution_state"] == "claimed"
        and reopened["completion_history"][-1]["from_state"] == "acknowledged"
        and reopened["completion_history"][-1]["acknowledged_by"] == "统筹部/lead-thread"
        and reopened["completion_history"][-1]["candidate_id"] == candidate,
        "acknowledged owner reopen did not preserve the prior acceptance evidence",
    )


def verify_protocol_151_gate_ack_order(root: Path) -> None:
    project, collab, task_tool, owner, candidate, gates, manifest = protocol_151_edge_slice(
        root, "protocol-151-gate-ack-order", ("test",),
    )
    gate = gates["test"]
    report = protocol_151_edge_report(
        collab, gate, candidate, "测试部", "pass", "gate-ack-order-pass",
    )
    run([
        sys.executable, str(task_tool), "gate-verdict", "--task-id", gate,
        "--candidate-id", candidate, "--decision", "pass",
        "--report", str(report.relative_to(project)),
        "--evidence", "当前候选测试通过", "--actor", "测试部/test-thread",
    ])
    run([
        sys.executable, str(task_tool), "record-user-exit", "--task-id", owner,
        "--candidate-id", candidate, "--status", "not_applicable",
        "--evidence", "纯协议探针无需真实用户出口", "--actor", "统筹部/lead-thread",
    ])
    run([
        sys.executable, str(task_tool), "complete", "--task-id", gate,
        "--actor", "测试部/test-thread", "--artifact", str(report.relative_to(project)),
        "--report", str(report.relative_to(project)), "--verified", "gate 已固定",
        "--unverified", "无", "--mistake-check", "等待统筹核收",
    ])
    action = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    check(
        "first_blocker=GATE_TASK_ACK_REQUIRED" in action.stdout
        and f"target_task={gate}" in action.stdout
        and "allowed=ack,next-action" in action.stdout,
        "owner next-action did not route a completed gate to acknowledgement",
    )
    premature_complete = run([
        sys.executable, str(task_tool), "complete", "--task-id", owner,
        "--actor", "开发部/dev-thread", "--artifact", str(manifest.relative_to(project)),
        "--verified", "owner 已固定", "--unverified", "无",
        "--mistake-check", "不得跳过 gate 核收",
    ], ok=False)
    check("尚未核收" in premature_complete.stderr,
          "owner completed before all required gate tasks were acknowledged")
    run([
        sys.executable, str(task_tool), "ack", "--task-id", gate,
        "--acknowledged-by", "统筹部/lead-thread",
    ])
    ready = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    check("first_blocker=NONE" in ready.stdout and "allowed=complete,next-action" in ready.stdout,
          "owner was not released after every required gate was acknowledged")
    run([
        sys.executable, str(task_tool), "complete", "--task-id", owner,
        "--actor", "开发部/dev-thread", "--artifact", str(manifest.relative_to(project)),
        "--verified", "owner 已固定", "--unverified", "无",
        "--mistake-check", "gate 已先核收",
    ])


def verify_protocol_151_user_revision(root: Path) -> None:
    def tree_snapshot(path: Path) -> dict[str, str]:
        return {
            item.relative_to(path).as_posix(): file_sha256(item)
            for item in sorted(path.rglob("*"))
            if item.is_file() and not item.is_symlink()
        }

    def task_args(
        task_tool: Path, department: str, title: str, *, kind: str = "owner",
        slice_id: str = "", gate_type: str = "", required_gates: tuple[str, ...] = (),
    ) -> list[str]:
        args = [
            sys.executable, str(task_tool), "enqueue", "--actor", "统筹部/lead-thread",
            "--department", department, "--from-department", "统筹部",
            "--title", title, "--node", "2.1.1 用户修订",
            "--details", "验证 PASS 后用户要求同切片修订的机械入口",
            "--acceptance-exit", "用户修订和新一代审核可复验",
            "--failure-path", "修订证据或候选身份错误时拒绝",
            "--authorization-state", "none", "--task-kind", kind,
        ]
        if slice_id:
            args += ["--slice-id", slice_id]
        if gate_type:
            args += ["--gate-type", gate_type]
        for gate in required_gates:
            args += ["--required-gate", gate]
        return args

    def write_candidate(project: Path, candidate_id: str, label: str) -> tuple[Path, str]:
        artifact = project / "docs" / f"{label}.txt"
        artifact.write_text(f"candidate {candidate_id}\n", encoding="utf-8")
        manifest = project / "docs" / f"{label}-manifest.json"
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "candidate_id": candidate_id,
            "artifact": {
                "path": artifact.relative_to(project).as_posix(),
                "sha256": file_sha256(artifact),
                "kind": "file",
            },
            "source_revision": f"verify-{label}",
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return manifest, file_sha256(manifest)

    def write_gate_report(
        collab: Path, department: str, task_id: str, candidate_id: str, label: str,
    ) -> Path:
        report = collab / "部门" / department / "报告" / f"{label}.md"
        report.write_text(f"""---
type: audit_report
department: {department}
target: Agent Team 2.1.1 candidate
status: final
date: {dt.date.today().isoformat()}
related_task: {task_id}
decision: pass
candidate_id: {candidate_id}
tags: [protocol-1.5.1]
summary: 当前代候选已完成独立审核
---

# 独立审核

当前代候选身份和验收出口通过。
""", encoding="utf-8")
        return report

    project = make_project(root, "protocol-151-user-revision")
    scaffold(project, "lead,dev,test,security")
    collab = project / "docs" / "collaboration"
    task_tool = collab / "scripts" / "agent_team_task.py"
    for department, thread_id in (
        ("统筹部", "lead-thread"), ("开发部", "dev-thread"),
        ("测试部", "test-thread"), ("安全部", "security-thread"),
    ):
        ensure_department_registered(task_tool, department, thread_id)
    run([sys.executable, str(task_tool), "rebuild-index"])

    owner = task_id_from(run(task_args(
        task_tool, "开发部", "PASS 后用户修订 owner", required_gates=("test", "security"),
    )))
    run([
        sys.executable, str(task_tool), "claim", "--task-id", owner,
        "--claimed-by", "开发部/dev-thread",
    ])
    slice_control_path = collab / ".locks" / "slice-control.json"
    slice_id = json.loads(slice_control_path.read_text(encoding="utf-8"))["active_slice"]["slice_id"]
    candidate_1 = "CAND-20260828-R10001"
    manifest_1, manifest_sha_1 = write_candidate(project, candidate_1, "revision-candidate-1")
    run([
        sys.executable, str(task_tool), "bind-candidate", "--task-id", owner,
        "--candidate-id", candidate_1, "--manifest", str(manifest_1.relative_to(project)),
        "--sha256", manifest_sha_1, "--actor", "开发部/dev-thread",
    ])
    gates = {}
    for department, thread_id, gate_type in (
        ("测试部", "test-thread", "test"), ("安全部", "security-thread", "security"),
    ):
        gate = task_id_from(run(task_args(
            task_tool, department, f"{gate_type} gate", kind="gate",
            slice_id=slice_id, gate_type=gate_type,
        )))
        gates[gate_type] = gate
        run([
            sys.executable, str(task_tool), "claim", "--task-id", gate,
            "--claimed-by", f"{department}/{thread_id}",
        ])
        report = write_gate_report(collab, department, gate, candidate_1, f"{gate_type}-pass-1")
        run([
            sys.executable, str(task_tool), "gate-verdict", "--task-id", gate,
            "--candidate-id", candidate_1, "--decision", "pass",
            "--report", str(report.relative_to(project)), "--evidence", f"{gate_type} pass generation 1",
            "--actor", f"{department}/{thread_id}",
        ])

    candidate_2 = "CAND-20260828-R10002"
    manifest_2, manifest_sha_2 = write_candidate(project, candidate_2, "revision-candidate-2")
    denied_without_revision = run([
        sys.executable, str(task_tool), "bind-candidate", "--task-id", owner,
        "--candidate-id", candidate_2, "--manifest", str(manifest_2.relative_to(project)),
        "--sha256", manifest_sha_2, "--actor", "开发部/dev-thread",
    ], ok=False)
    check("gate FAIL" in denied_without_revision.stderr,
          "PASS+PASS bound a second generation without a user revision record")

    run([
        sys.executable, str(task_tool), "block", "--task-id", owner,
        "--reason", "等待用户决定是否修订", "--actor", "开发部/dev-thread",
    ])
    normal_pending_snapshot = tree_snapshot(collab)
    normal_pending_1 = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    normal_pending_2 = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    normal_pending_allowed = re.search(r"allowed=([^|\n]+)", normal_pending_1.stdout)
    check(
        normal_pending_1.stdout == normal_pending_2.stdout
        and tree_snapshot(collab) == normal_pending_snapshot
        and "first_blocker=USER_EXIT_PENDING" in normal_pending_1.stdout
        and normal_pending_allowed is not None
        and normal_pending_allowed.group(1).strip() == "record-user-exit,next-action"
        and "user_decision=yes" in normal_pending_1.stdout,
        "normal blocked owner with PASS+PASS did not prioritize the user-exit fact without writes",
    )
    normal_pending_bundle = run([
        sys.executable, str(task_tool), "onboard-bundle", "--department", "开发部",
    ])
    check(
        "下一合法动作：`record-user-exit`" in normal_pending_bundle.stdout,
        "normal blocked PASS+PASS onboarding suggested resume before record-user-exit",
    )
    run([
        sys.executable, str(task_tool), "freeze-new-work", "--actor", "统筹部/lead-thread",
        "--evidence", "冻结期间只保全用户修订事实",
    ])
    frozen_pending_snapshot = tree_snapshot(collab)
    frozen_pending_1 = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    frozen_pending_2 = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    check(
        frozen_pending_1.stdout == frozen_pending_2.stdout
        and tree_snapshot(collab) == frozen_pending_snapshot
        and "first_blocker=P0_FREEZE_ACTIVE" in frozen_pending_1.stdout
        and "allowed=record-user-exit" in frozen_pending_1.stdout
        and "forbidden=resume,bind-candidate,claim,enqueue" in frozen_pending_1.stdout
        and "user_decision=yes" in frozen_pending_1.stdout,
        "frozen blocked owner with PASS+PASS did not prioritize the user-exit fact without writes",
    )
    frozen_pending_bundle = run([
        sys.executable, str(task_tool), "onboard-bundle", "--department", "开发部",
    ])
    check(
        "下一合法动作：`record-user-exit`" in frozen_pending_bundle.stdout,
        "frozen blocked PASS+PASS onboarding suggested an action before record-user-exit",
    )
    revision = run([
        sys.executable, str(task_tool), "record-user-exit", "--task-id", owner,
        "--candidate-id", candidate_1, "--status", "needs_revision",
        "--evidence", "用户要求补齐三个同范围缺口", "--actor", "统筹部/lead-thread",
    ])
    check(revision.stdout.startswith("USER_EXIT_RECORDED"),
          "a registered lead could not record a candidate-bound user revision")
    frozen_revision_snapshot = tree_snapshot(collab)
    frozen_revision_noop = run([
        sys.executable, str(task_tool), "record-user-exit", "--task-id", owner,
        "--candidate-id", candidate_1, "--status", "needs_revision",
        "--evidence", "用户要求补齐三个同范围缺口", "--actor", "统筹部/lead-thread",
    ])
    check(
        frozen_revision_noop.stdout.startswith("USER_EXIT_NOOP")
        and tree_snapshot(collab) == frozen_revision_snapshot,
        "frozen evidence recording was not idempotent",
    )
    revision_control = json.loads(slice_control_path.read_text(encoding="utf-8"))
    dispatch_after_revision = json.loads(
        (collab / ".locks" / "dispatch-control.json").read_text(encoding="utf-8")
    )
    check(
        all(
            attempt["decision"] == "pass"
            for gate_task_id in revision_control["active_slice"]["gate_tasks"].values()
            for attempt in json.loads(
                (collab / "tasks" / f"{gate_task_id}.json").read_text(encoding="utf-8")
            )["gate_attempts"]
        )
        and all("AUTO_GATE_FAIL_X2" not in event["evidence"] for event in dispatch_after_revision["history"]),
        "needs_revision was misclassified as a gate FAIL or continuous-failure freeze",
    )
    after_revision = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    check(
        "allowed=unfreeze-new-work,next-action" in after_revision.stdout
        and "forbidden=resume,bind-candidate,claim,enqueue" in after_revision.stdout
        and "user_decision=yes" in after_revision.stdout,
        "frozen needs_revision did not require unfreeze before candidate binding",
    )
    frozen_bind = run([
        sys.executable, str(task_tool), "bind-candidate", "--task-id", owner,
        "--candidate-id", candidate_2, "--manifest", str(manifest_2.relative_to(project)),
        "--sha256", manifest_sha_2, "--actor", "开发部/dev-thread",
    ], ok=False)
    frozen_resume = run([
        sys.executable, str(task_tool), "resume", "--task-id", owner,
        "--actor", "开发部/dev-thread",
    ], ok=False)
    check(
        "P0_FREEZE_ACTIVE" in frozen_bind.stderr and "P0_FREEZE_ACTIVE" in frozen_resume.stderr,
        "frozen mode allowed candidate binding or resume after a valid user revision",
    )
    run([
        sys.executable, str(task_tool), "unfreeze-new-work", "--actor", "统筹部/lead-thread",
        "--user-confirmation", "测试夹具允许消费用户修订",
    ])
    normal_revision_blocked = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    check(
        "first_blocker=TASK_BLOCKED" in normal_revision_blocked.stdout
        and "allowed=resume,next-action" in normal_revision_blocked.stdout
        and "bind-candidate" not in re.search(
            r"allowed=([^|\n]+)", normal_revision_blocked.stdout,
        ).group(1).split(","),
        "normal blocked needs_revision skipped resume or allowed candidate binding too early",
    )
    blocked_revision_bind = run([
        sys.executable, str(task_tool), "bind-candidate", "--task-id", owner,
        "--candidate-id", candidate_2, "--manifest", str(manifest_2.relative_to(project)),
        "--sha256", manifest_sha_2, "--actor", "开发部/dev-thread",
    ], ok=False)
    check("只允许 claimed owner" in blocked_revision_bind.stderr,
          "an unfreezed but blocked owner bound the user-revision candidate")
    run([
        sys.executable, str(task_tool), "resume", "--task-id", owner,
        "--actor", "开发部/dev-thread",
    ])
    normal_revision_claimed = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    check(
        "first_blocker=USER_REVISION_READY" in normal_revision_claimed.stdout
        and "allowed=bind-candidate" in normal_revision_claimed.stdout,
        "normal claimed needs_revision did not allow the next candidate after resume",
    )
    reused_id = run([
        sys.executable, str(task_tool), "bind-candidate", "--task-id", owner,
        "--candidate-id", candidate_1, "--manifest", str(manifest_2.relative_to(project)),
        "--sha256", manifest_sha_2, "--actor", "开发部/dev-thread",
    ], ok=False)
    reused_manifest = run([
        sys.executable, str(task_tool), "bind-candidate", "--task-id", owner,
        "--candidate-id", candidate_2, "--manifest", str(manifest_1.relative_to(project)),
        "--sha256", manifest_sha_1, "--actor", "开发部/dev-thread",
    ], ok=False)
    old_artifact_bytes = (project / "docs" / "revision-candidate-1.txt").read_bytes()
    (project / "docs" / "revision-candidate-1.txt").write_bytes(old_artifact_bytes + b"tampered\n")
    tampered_old = run([
        sys.executable, str(task_tool), "bind-candidate", "--task-id", owner,
        "--candidate-id", candidate_2, "--manifest", str(manifest_2.relative_to(project)),
        "--sha256", manifest_sha_2, "--actor", "开发部/dev-thread",
    ], ok=False)
    (project / "docs" / "revision-candidate-1.txt").write_bytes(old_artifact_bytes)
    check(
        "新的 candidate-id" in reused_id.stderr
        and "candidate_id" in reused_manifest.stderr
        and "artifact SHA-256" in tampered_old.stderr,
        "old candidate identity, manifest path, or modified artifact was accepted for a new generation",
    )
    bound = run([
        sys.executable, str(task_tool), "bind-candidate", "--task-id", owner,
        "--candidate-id", candidate_2, "--manifest", str(manifest_2.relative_to(project)),
        "--sha256", manifest_sha_2, "--actor", "开发部/dev-thread",
    ])
    control = json.loads(slice_control_path.read_text(encoding="utf-8"))
    active = control["active_slice"]
    check(
        bound.stdout.startswith("CANDIDATE_BOUND")
        and active["candidate"]["candidate_id"] == candidate_2
        and active["candidate"]["generation"] == 2
        and active["user_exit"]["status"] == "pending",
        "a valid PASS+PASS user revision did not bind generation 2 and reset its user exit",
    )

    before_next_action = tree_snapshot(collab)
    next_action_1 = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    next_action_2 = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    check(
        next_action_1.stdout == next_action_2.stdout
        and tree_snapshot(collab) == before_next_action,
        "next-action was not a deterministic zero-write query",
    )
    check(
        "first_blocker=GATES_PENDING" in next_action_1.stdout
        and "allowed=gate-verdict,wait,block,next-action" in next_action_1.stdout,
        "next-action did not expose the first generation-2 gate blocker",
    )

    run([
        sys.executable, str(task_tool), "wait", "--task-id", owner,
        "--reason", "等待第二代 gate 收尾", "--actor", "开发部/dev-thread",
    ])
    normal_waiting_gate = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    check(
        "state=waiting_input" in normal_waiting_gate.stdout
        and "first_blocker=GATES_PENDING" in normal_waiting_gate.stdout
        and "allowed=gate-verdict" in normal_waiting_gate.stdout
        and "resume" not in re.search(r"allowed=([^|\n]+)", normal_waiting_gate.stdout).group(1).split(","),
        "normal waiting_input owner hid a claimed pending gate behind resume",
    )
    run([
        sys.executable, str(task_tool), "freeze-new-work", "--actor", "统筹部/lead-thread",
        "--evidence", "验证冻结 waiting_input 仍能收尾已领取 gate",
    ])
    frozen_waiting_gate = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    check(
        "state=waiting_input" in frozen_waiting_gate.stdout
        and "first_blocker=P0_FREEZE_ACTIVE" in frozen_waiting_gate.stdout
        and "allowed=gate-verdict" in frozen_waiting_gate.stdout
        and "resume" not in re.search(r"allowed=([^|\n]+)", frozen_waiting_gate.stdout).group(1).split(","),
        "frozen waiting_input owner hid a claimed pending gate behind resume or unfreeze",
    )
    run([
        sys.executable, str(task_tool), "unfreeze-new-work", "--actor", "统筹部/lead-thread",
        "--user-confirmation", "测试夹具恢复 waiting_input gate",
    ])
    run([
        sys.executable, str(task_tool), "resume", "--task-id", owner,
        "--actor", "开发部/dev-thread",
    ])

    run([
        sys.executable, str(task_tool), "block", "--task-id", owner,
        "--reason", "冻结时等待第二代 gate", "--actor", "开发部/dev-thread",
    ])
    run([
        sys.executable, str(task_tool), "block", "--task-id", gates["test"],
        "--reason", "测试 gate 自身阻断", "--actor", "测试部/test-thread",
    ])
    normal_gate_snapshot = tree_snapshot(collab)
    normal_owner_gate = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    normal_blocked_gate = run([
        sys.executable, str(task_tool), "next-action", "--task-id", gates["test"],
    ])
    normal_claimed_gate = run([
        sys.executable, str(task_tool), "next-action", "--task-id", gates["security"],
    ])
    normal_blocked_gate_allowed = re.search(r"allowed=([^|\n]+)", normal_blocked_gate.stdout)
    check(
        "allowed=gate-verdict" in normal_owner_gate.stdout
        and "allowed=gate-verdict" in normal_claimed_gate.stdout
        and normal_blocked_gate_allowed is not None
        and "gate-verdict" not in normal_blocked_gate_allowed.group(1).split(",")
        and "allowed=resume,next-action" in normal_blocked_gate.stdout
        and tree_snapshot(collab) == normal_gate_snapshot,
        "normal gate routing hid a claimed gate or allowed a blocked gate to verdict",
    )
    run([
        sys.executable, str(task_tool), "freeze-new-work", "--actor", "统筹部/lead-thread",
        "--evidence", "验证冻结时仍能收尾已领取 gate",
    ])
    frozen_gate_snapshot = tree_snapshot(collab)
    frozen_owner_gate = run([
        sys.executable, str(task_tool), "next-action", "--task-id", owner,
    ])
    frozen_blocked_gate = run([
        sys.executable, str(task_tool), "next-action", "--task-id", gates["test"],
    ])
    frozen_claimed_gate = run([
        sys.executable, str(task_tool), "next-action", "--task-id", gates["security"],
    ])
    blocked_gate_allowed = re.search(r"allowed=([^|\n]+)", frozen_blocked_gate.stdout)
    check(
        "allowed=gate-verdict" in frozen_owner_gate.stdout
        and "allowed=gate-verdict" in frozen_claimed_gate.stdout
        and blocked_gate_allowed is not None
        and "gate-verdict" not in blocked_gate_allowed.group(1).split(",")
        and "forbidden=resume,bind-candidate,claim,enqueue" in frozen_blocked_gate.stdout
        and tree_snapshot(collab) == frozen_gate_snapshot,
        "frozen gate routing hid a claimed gate or allowed a blocked gate to verdict",
    )
    run([
        sys.executable, str(task_tool), "unfreeze-new-work", "--actor", "统筹部/lead-thread",
        "--user-confirmation", "测试夹具恢复第二代 gate",
    ])
    run([
        sys.executable, str(task_tool), "resume", "--task-id", owner,
        "--actor", "开发部/dev-thread",
    ])
    run([
        sys.executable, str(task_tool), "resume", "--task-id", gates["test"],
        "--actor", "测试部/test-thread",
    ])

    handoff = collab / "部门" / "开发部" / "交接班文档.md"
    handoff.write_text(
        handoff.read_text(encoding="utf-8") + "\n旧的人工当前任务：继续 generation 1。\n",
        encoding="utf-8",
    )
    bundle = run([
        sys.executable, str(task_tool), "onboard-bundle", "--department", "开发部",
    ])
    check(
        "旧的人工当前任务" not in bundle.stdout
        and "候选：`CAND-20260828-R10002` · generation `2`" in bundle.stdout
        and "下一合法动作：`gate-verdict`" in bundle.stdout,
        "onboard-bundle leaked stale manual current-task prose or omitted current machine truth",
    )

    first_block = run([
        sys.executable, str(task_tool), "block", "--task-id", owner,
        "--reason", "等待用户补图", "--actor", "开发部/dev-thread",
    ])
    blocked_snapshot = tree_snapshot(collab)
    repeated_block = run([
        sys.executable, str(task_tool), "block", "--task-id", owner,
        "--reason", "等待用户补图", "--actor", "开发部/dev-thread",
    ])
    check(
        first_block.stdout.startswith("TASK_BLOCKED")
        and repeated_block.stdout.startswith("TASK_STATE_NOOP")
        and tree_snapshot(collab) == blocked_snapshot,
        "an identical block retry mutated state instead of returning a safe NOOP",
    )
    conflicting_block = run([
        sys.executable, str(task_tool), "block", "--task-id", owner,
        "--reason", "另一条原因", "--actor", "开发部/dev-thread",
    ], ok=False)
    check("冲突" in conflicting_block.stderr, "a conflicting block retry was not rejected")
    run([
        sys.executable, str(task_tool), "resume", "--task-id", owner,
        "--actor", "开发部/dev-thread",
    ])
    resumed_snapshot = tree_snapshot(collab)
    repeated_resume = run([
        sys.executable, str(task_tool), "resume", "--task-id", owner,
        "--actor", "开发部/dev-thread",
    ])
    check(
        repeated_resume.stdout.startswith("TASK_STATE_NOOP")
        and tree_snapshot(collab) == resumed_snapshot,
        "an identical resume retry mutated state instead of returning a safe NOOP",
    )
    run([
        sys.executable, str(task_tool), "wait", "--task-id", owner,
        "--reason", "等待用户选择", "--actor", "开发部/dev-thread",
    ])
    waiting_snapshot = tree_snapshot(collab)
    repeated_wait = run([
        sys.executable, str(task_tool), "wait", "--task-id", owner,
        "--reason", "等待用户选择", "--actor", "开发部/dev-thread",
    ])
    check(
        repeated_wait.stdout.startswith("TASK_STATE_NOOP")
        and tree_snapshot(collab) == waiting_snapshot,
        "an identical wait retry mutated state instead of returning a safe NOOP",
    )
    conflicting_wait = run([
        sys.executable, str(task_tool), "wait", "--task-id", owner,
        "--reason", "等待另一选择", "--actor", "开发部/dev-thread",
    ], ok=False)
    check("冲突" in conflicting_wait.stderr, "a conflicting wait retry was not rejected")
    run([
        sys.executable, str(task_tool), "resume", "--task-id", owner,
        "--actor", "开发部/dev-thread",
    ])

    preserved_generation_1 = {
        path: path.read_bytes() for path in (
            project / "docs" / "revision-candidate-1.txt",
            manifest_1,
            collab / "部门" / "测试部" / "报告" / "test-pass-1.md",
            collab / "部门" / "安全部" / "报告" / "security-pass-1.md",
        )
    }

    def owner_complete(*, ok: bool) -> subprocess.CompletedProcess[str]:
        return run([
            sys.executable, str(task_tool), "complete", "--task-id", owner,
            "--actor", "开发部/dev-thread",
            "--artifact", str(manifest_2.relative_to(project)),
            "--verified", "候选身份固定", "--unverified", "无",
            "--mistake-check", "已检查", "--report", "不适用",
        ], ok=ok)

    pending_denied = owner_complete(ok=False)
    check("全部 gate" in pending_denied.stderr,
          "generation 2 completed while both current-generation gates were pending")
    generation_2_reports: dict[str, Path] = {}
    for index, (department, thread_id, gate_type) in enumerate((
        ("测试部", "test-thread", "test"), ("安全部", "security-thread", "security"),
    )):
        gate = gates[gate_type]
        report = write_gate_report(collab, department, gate, candidate_2, f"{gate_type}-pass-2")
        generation_2_reports[gate_type] = report
        run([
            sys.executable, str(task_tool), "gate-verdict", "--task-id", gate,
            "--candidate-id", candidate_2, "--decision", "pass",
            "--report", str(report.relative_to(project)),
            "--evidence", f"{gate_type} pass generation 2",
            "--actor", f"{department}/{thread_id}",
        ])
        if index == 0:
            mixed_generation_denied = owner_complete(ok=False)
            check("全部 gate" in mixed_generation_denied.stderr,
                  "one generation-2 PASS combined with one generation-1 PASS completed the owner")

    user_exit_pending_denied = owner_complete(ok=False)
    check("用户最终出口" in user_exit_pending_denied.stderr,
          "a user-experience slice completed while generation-2 user_exit was pending")
    mismatch = run([
        sys.executable, str(task_tool), "record-user-exit", "--task-id", owner,
        "--candidate-id", candidate_1, "--status", "verified",
        "--evidence", "错误代次", "--actor", "统筹部/lead-thread",
    ], ok=False)
    check("候选身份不匹配" in mismatch.stderr,
          "record-user-exit accepted evidence for an old candidate generation")
    verified = run([
        sys.executable, str(task_tool), "record-user-exit", "--task-id", owner,
        "--candidate-id", candidate_2, "--status", "verified",
        "--evidence", "用户确认第二代体验通过", "--actor", "统筹部/lead-thread",
    ])
    verified_snapshot = tree_snapshot(collab)
    verified_noop = run([
        sys.executable, str(task_tool), "record-user-exit", "--task-id", owner,
        "--candidate-id", candidate_2, "--status", "verified",
        "--evidence", "用户确认第二代体验通过", "--actor", "统筹部/lead-thread",
    ])
    check(
        verified.stdout.startswith("USER_EXIT_RECORDED")
        and verified_noop.stdout.startswith("USER_EXIT_NOOP")
        and tree_snapshot(collab) == verified_snapshot,
        "an identical user-exit retry mutated state instead of returning a safe NOOP",
    )
    conflicting_exit = run([
        sys.executable, str(task_tool), "record-user-exit", "--task-id", owner,
        "--candidate-id", candidate_2, "--status", "verified",
        "--evidence", "另一份体验证据", "--actor", "统筹部/lead-thread",
    ], ok=False)
    check("冲突" in conflicting_exit.stderr, "a conflicting user-exit retry was not rejected")
    for department, thread_id, gate_type in (
        ("测试部", "test-thread", "test"), ("安全部", "security-thread", "security"),
    ):
        gate = gates[gate_type]
        report = generation_2_reports[gate_type]
        run([
            sys.executable, str(task_tool), "complete", "--task-id", gate,
            "--actor", f"{department}/{thread_id}",
            "--artifact", str(report.relative_to(project)),
            "--verified", "第二代审核通过", "--unverified", "无",
            "--mistake-check", "已检查", "--report", str(report.relative_to(project)),
        ])
        run([
            sys.executable, str(task_tool), "ack", "--task-id", gate,
            "--acknowledged-by", "统筹部/lead-thread",
        ])
    completed = owner_complete(ok=True)
    check(completed.stdout.startswith("TASK_STATE_OK"),
          "generation 2 did not complete after two fresh PASS verdicts and verified user exit")
    check(
        all(path.read_bytes() == data for path, data in preserved_generation_1.items()),
        "binding or completing generation 2 rewrote generation-1 candidate or PASS evidence",
    )


def verify_protocol_151_active_migration(root: Path) -> None:
    project = make_project(root, "protocol-151-active-migration")
    scaffold(project, "lead,dev,test,security")
    collab = project / "docs" / "collaboration"
    task_tool = collab / "scripts" / "agent_team_task.py"
    for department, thread_id in (
        ("统筹部", "lead-thread"), ("开发部", "dev-thread"),
        ("测试部", "test-thread"), ("安全部", "security-thread"),
    ):
        ensure_department_registered(task_tool, department, thread_id)
    run([sys.executable, str(task_tool), "rebuild-index"])
    owner = task_id_from(run([
        sys.executable, str(task_tool), "enqueue", "--actor", "统筹部/lead-thread",
        "--department", "开发部", "--from-department", "统筹部",
        "--title", "1.5.0 活动切片迁移", "--node", "协议升级",
        "--details", "保全活动 owner、候选和 gate 报告",
        "--acceptance-exit", "升级后活动切片可继续",
        "--failure-path", "任何证据字节变化即失败", "--authorization-state", "none",
        "--task-kind", "owner", "--required-gate", "test", "--required-gate", "security",
    ]))
    run([
        sys.executable, str(task_tool), "claim", "--task-id", owner,
        "--claimed-by", "开发部/dev-thread",
    ])
    slice_path = collab / ".locks" / "slice-control.json"
    slice_id = json.loads(slice_path.read_text(encoding="utf-8"))["active_slice"]["slice_id"]
    candidate_id = "CAND-20260828-M15100"
    artifact = project / "docs" / "migration-candidate.txt"
    artifact.write_text("migration candidate\n", encoding="utf-8")
    manifest = project / "docs" / "migration-candidate-manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": 1, "candidate_id": candidate_id,
        "artifact": {"path": artifact.relative_to(project).as_posix(),
                     "sha256": file_sha256(artifact), "kind": "file"},
        "source_revision": "protocol-1.5.0-active-fixture",
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    run([
        sys.executable, str(task_tool), "bind-candidate", "--task-id", owner,
        "--candidate-id", candidate_id, "--manifest", str(manifest.relative_to(project)),
        "--sha256", file_sha256(manifest), "--actor", "开发部/dev-thread",
    ])
    gate_paths: list[Path] = []
    task_paths = [collab / "tasks" / f"{owner}.json"]
    for department, thread_id, gate_type in (
        ("测试部", "test-thread", "test"), ("安全部", "security-thread", "security"),
    ):
        gate = task_id_from(run([
            sys.executable, str(task_tool), "enqueue", "--actor", "统筹部/lead-thread",
            "--department", department, "--from-department", "统筹部",
            "--title", f"migration {gate_type} gate", "--node", "协议升级",
            "--details", "迁移前审核证据", "--acceptance-exit", "独立 PASS",
            "--failure-path", "证据错误即拒绝", "--authorization-state", "none",
            "--task-kind", "gate", "--slice-id", slice_id, "--gate-type", gate_type,
        ]))
        run([
            sys.executable, str(task_tool), "claim", "--task-id", gate,
            "--claimed-by", f"{department}/{thread_id}",
        ])
        report = collab / "部门" / department / "报告" / f"migration-{gate_type}-pass.md"
        report.write_text(f"""---
type: audit_report
department: {department}
target: protocol 1.5.0 active migration
status: final
date: {dt.date.today().isoformat()}
related_task: {gate}
decision: pass
candidate_id: {candidate_id}
tags: [migration]
summary: 迁移前当前代独立审核通过
---

# 迁移前审核

当前候选通过。
""", encoding="utf-8")
        run([
            sys.executable, str(task_tool), "gate-verdict", "--task-id", gate,
            "--candidate-id", candidate_id, "--decision", "pass",
            "--report", str(report.relative_to(project)), "--evidence", f"{gate_type} pass",
            "--actor", f"{department}/{thread_id}",
        ])
        task_paths.append(collab / "tasks" / f"{gate}.json")
        gate_paths.append(report)
    run([
        sys.executable, str(task_tool), "freeze-new-work", "--actor", "统筹部/lead-thread",
        "--evidence", "1.5.0 到 1.5.1 迁移冻结",
    ])
    for task_path in task_paths:
        payload = json.loads(task_path.read_text(encoding="utf-8"))
        payload.pop("state_action_receipt", None)
        task_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    preserved = {
        path: path.read_bytes() for path in [artifact, manifest, *gate_paths, *task_paths]
    }
    protocol_path = collab / "协议版本.json"
    session_path = collab / "会话启动状态.json"
    for path in (protocol_path, session_path, collab / ".locks" / "legacy-closeout-index.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["protocol_version"] = IMMEDIATE_PREVIOUS_PROTOCOL_VERSION
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    slice_payload = json.loads(slice_path.read_text(encoding="utf-8"))
    slice_payload["protocol_version"] = IMMEDIATE_PREVIOUS_PROTOCOL_VERSION
    active = slice_payload["active_slice"]
    active.pop("revision_requests")
    active["user_exit"].pop("candidate_id")
    active["user_exit"].pop("generation")
    slice_path.write_text(json.dumps(slice_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    guide = project / "docs" / "agent-guide.md"
    guide.write_text(guide.read_text(encoding="utf-8").replace(
        f"受管协议版本:{PROTOCOL_VERSION}", f"受管协议版本:{IMMEDIATE_PREVIOUS_PROTOCOL_VERSION}",
    ), encoding="utf-8")

    upgraded = run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    migrated = json.loads(slice_path.read_text(encoding="utf-8"))
    check(
        upgraded.stdout.startswith("UPGRADE_OK |")
        and migrated["protocol_version"] == PROTOCOL_VERSION
        and migrated["active_slice"]["slice_id"] == slice_id
        and migrated["active_slice"]["candidate"]["candidate_id"] == candidate_id
        and migrated["active_slice"]["revision_requests"] == []
        and migrated["active_slice"]["user_exit"]["candidate_id"] == candidate_id
        and all(path.read_bytes() == data for path, data in preserved.items()),
        "1.5.0 active slice did not migrate in place without rewriting TASK/candidate/gate evidence",
    )
    first_manifest = sorted((collab / "升级备份").iterdir())[-1] / "rollback-manifest.json"
    immediate = run([
        sys.executable, str(SCAFFOLD), str(project), "--rollback-collaboration", str(first_manifest),
    ])
    check(
        immediate.stdout.startswith("ROLLBACK_OK |")
        and json.loads(protocol_path.read_text(encoding="utf-8"))["protocol_version"]
        == IMMEDIATE_PREVIOUS_PROTOCOL_VERSION
        and json.loads(slice_path.read_text(encoding="utf-8"))["active_slice"].get("revision_requests") is None,
        "immediate 1.5.1 rollback did not restore the exact 1.5.0 active slice",
    )
    existing_backups = set((collab / "升级备份").iterdir())
    run([sys.executable, str(SCAFFOLD), str(project), "--upgrade-collaboration"])
    second_manifest = next(iter(set((collab / "升级备份").iterdir()) - existing_backups)) / "rollback-manifest.json"
    run([
        sys.executable, str(task_tool), "block", "--task-id", owner,
        "--reason", "旧 1.5.0 TASK 首次新协议状态动作", "--actor", "开发部/dev-thread",
    ])
    migrated_owner = json.loads((collab / "tasks" / f"{owner}.json").read_text(encoding="utf-8"))
    repeated_block = run([
        sys.executable, str(task_tool), "block", "--task-id", owner,
        "--reason", "旧 1.5.0 TASK 首次新协议状态动作", "--actor", "开发部/dev-thread",
    ])
    check(
        migrated_owner["state_action_receipt"]["action"] == "block"
        and repeated_block.stdout.startswith("TASK_STATE_NOOP"),
        "a migrated 1.5.0 TASK without a receipt did not safely acquire one on its first transition",
    )
    run([
        sys.executable, str(task_tool), "record-user-exit", "--task-id", owner,
        "--candidate-id", candidate_id, "--status", "needs_revision",
        "--evidence", "用户要求迁移后修订", "--actor", "统筹部/lead-thread",
    ])
    denied = run([
        sys.executable, str(SCAFFOLD), str(project), "--rollback-collaboration", str(second_manifest),
    ], ok=False)
    check(
        "ROLLBACK_DENIED" in denied.stderr
        and json.loads(protocol_path.read_text(encoding="utf-8"))["protocol_version"] == PROTOCOL_VERSION,
        "rollback remained open after the first protocol-1.5.1 state mutation",
    )


def main() -> int:
    compile_script(SCAFFOLD)
    verify_repository_contract()
    with tempfile.TemporaryDirectory(prefix="agent-team-verify-") as temp:
        root = Path(temp)
        verify_release_guard_branches(root)
        verify_install_bundle_contract(root)
        project = make_project(root, "main")
        scaffold(project)
        verify_generated(project)
        verify_default_minimal_software_team(root)
        verify_foundation_contract(root)
        verify_protocol_150_core(root)
        verify_protocol_151_zero_gate_next_action(root)
        verify_protocol_151_fail_and_ack_priorities(root)
        verify_protocol_151_authorization_priority(root)
        verify_protocol_151_identity_priority(root)
        verify_protocol_151_state_retry_actor_identity(root)
        verify_protocol_151_user_exit_supersession(root)
        verify_protocol_151_completed_revision_reopen(root)
        verify_protocol_151_gate_error_recovery_and_blocked_routing(root)
        verify_protocol_151_acknowledged_owner_revision_reopen(root)
        verify_protocol_151_gate_ack_order(root)
        verify_protocol_151_user_revision(root)
        verify_protocol_151_active_migration(root)
        verify_protocol_150_migration(root)
        verify_protocol_150_atomic_guards(root)
        verify_long_thread_actor_identity(root)
        legacy_fixture_root = Path(os.environ.get("AGENT_TEAM_LEGACY_2011_ROOT", LEGACY_2011_FIXTURE))
        verify_installed_2011_migration(root, legacy_fixture_root)
    print(
        "VERIFY_OK | 2.1 single-slice, registered identity, candidate generations, gate freeze, "
        "freshness, cold index, migration/rollback, unified-lock races, path guards, "
        "manual host boundary, bounded history, "
        "scaffold, package, and foundation guards passed"
    )
    return 0


def entrypoint() -> int:
    if len(sys.argv) == 6 and sys.argv[1] == "--check-release-assets":
        latest_json = Path(sys.argv[2]).expanduser().resolve()
        zip_path = Path(sys.argv[3]).expanduser().resolve()
        checksum_path = Path(sys.argv[4]).expanduser().resolve()
        repo = Path(sys.argv[5]).expanduser().resolve()
        tag = verify_latest_release_assets(latest_json, zip_path, checksum_path, repo)
        print(f"LATEST_RELEASE_ASSETS_OK | tag:{tag} | files:{len(RUNTIME_FILES)}")
        return 0
    if len(sys.argv) == 3 and sys.argv[1] == "--check-installed-copy":
        installed = Path(os.path.abspath(str(Path(sys.argv[2]).expanduser())))
        verify_installed_copy(installed)
        print(
            f"INSTALL_COPY_OK | {installed} | files:{len(RUNTIME_FILES)}"
            f" | source:{SOURCE_VERSION} | public:{PUBLIC_VERSION}"
        )
        return 0
    if len(sys.argv) != 1:
        raise VerifyError(
            "usage: verify_agent_team.py [--check-installed-copy PATH] "
            "[--check-release-assets LATEST_JSON ZIP CHECKSUM REPO]"
        )
    return main()


if __name__ == "__main__":
    try:
        raise SystemExit(entrypoint())
    except ReleaseAssetInvalid as exc:
        print(f"LATEST_RELEASE_ASSETS_INVALID | {exc}", file=sys.stderr)
        raise SystemExit(3)
    except VerifyError as exc:
        print(f"VERIFY_ERROR | {exc}", file=sys.stderr)
        raise SystemExit(1)
