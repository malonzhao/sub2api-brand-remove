#!/usr/bin/env python3
"""对上游 sub2api 工作副本执行去品牌改写。

改写规则：
  1. 删除前端三处指向上游仓库的可点击 GitHub 入口（首页页脚、用量页页脚、右上角下拉导航），
     并删除随之失效的 githubUrl 常量（前端开启了 noUnusedLocals，留下常量会让构建失败）。
  2. 把后端在线更新的仓库常量改写为目标仓库，使版本检测、回滚列表与产物下载回流到目标仓库。

每一处改写都遵循「改前必须命中 → 执行 → 改后必须清零」的三段式；任一断言失败即以非零码退出，
让流水线红灯，而不是产出一个漏改的版本。

用法：
    debrand.py --upstream <上游工作副本路径> --repo <owner/repo>
    debrand.py --upstream <上游工作副本路径> --verify-dist
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

UPSTREAM_REPO = "Wei-Shaw/sub2api"
UPSTREAM_URL = f"https://github.com/{UPSTREAM_REPO}"
UPSTREAM_DOCKER_IMAGE = "weishaw/sub2api"
MANIFEST_NAME = ".debrand-manifest.json"

# 改写目标文件（相对上游工作副本根目录）
HOME_VIEW = "frontend/src/views/HomeView.vue"
KEY_USAGE_VIEW = "frontend/src/views/KeyUsageView.vue"
APP_HEADER = "frontend/src/components/layout/AppHeader.vue"
VERSION_BADGE = "frontend/src/components/common/VersionBadge.vue"
UPDATE_SERVICE = "backend/internal/service/update_service.go"

EXPECTED_CHANGED_FILES = {
    HOME_VIEW, KEY_USAGE_VIEW, APP_HEADER, VERSION_BADGE, UPDATE_SERVICE,
}

# 必须保持原样的功能性文档跳转与外部数据源。
# 这些链接指向上游仓库的 docs/ 路径或另一个仓库，改指目标仓库只会 404 / 断掉价格同步。
PRESERVE = [
    ("frontend/src/components/admin/AdminComplianceDialog.vue",
     f"{UPSTREAM_URL}/blob/main/docs/legal/admin-compliance", True),
    ("frontend/src/stores/adminCompliance.ts",
     f"{UPSTREAM_URL}/blob/main/docs/legal/admin-compliance", True),
    ("frontend/src/views/admin/SettingsView.vue",
     f"{UPSTREAM_URL}/blob/main/docs/PAYMENT", True),
    ("backend/internal/service/admin_compliance.go",
     f"{UPSTREAM_URL}/blob/main/docs/legal/admin-compliance", False),
    ("backend/internal/config/config.go",
     "https://raw.githubusercontent.com/Wei-Shaw/model-price-repo", False),
]

# 下载来源的域名白名单必须原封不动 —— 改写只换仓库，不放宽安全边界。
ALLOWLIST_ANCHORS = [
    re.compile(r'allowedDownloadHost\s*=\s*"github\.com"'),
    re.compile(r'allowedAssetHost\s*=\s*"objects\.githubusercontent\.com"'),
]

# Go module 路径就是上游仓库名，属于代码标识而非跳转地址，必须保留。
GO_MODULE_IMPORT = re.compile(r'"github\.com/Wei-Shaw/sub2api/[^"]+"')

# dist 中的裸仓库地址（后面不跟路径），即被删除的入口所用的地址。
# 负向断言把 .../sub2api/blob/main/docs/... 这类保留链接排除在外。
BARE_URL_IN_DIST = re.compile(r"https://github\.com/Wei-Shaw/sub2api(?![A-Za-z0-9_./-])")

# 游离的上游标识：不带 https://github.com/ 前缀的 owner/repo 片段。
# VersionBadge 那类「运行时拼 URL」的写法在 dist 里只留下这种片段，
# 完整地址的探针抓不到 —— 这条专门补那个盲区。
LOOSE_UPSTREAM_IN_DIST = [
    re.compile(r"(?<!github\.com/)Wei-Shaw/sub2api"),
    re.compile(re.escape(UPSTREAM_DOCKER_IMAGE)),
]

REPO_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


class PatchError(Exception):
    """改写断言失败。"""


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines(keepends=True)


def write_lines(path: Path, lines: list[str]) -> None:
    path.write_text("".join(lines), encoding="utf-8")


def require_file(root: Path, rel: str) -> Path:
    path = root / rel
    if not path.is_file():
        raise PatchError(f"{rel}: 文件不存在，上游结构可能已变化")
    return path


def find_single(lines: list[str], marker: str, rel: str, what: str) -> int:
    """定位唯一命中行；0 处或多处都视为上游结构变化。"""
    hits = [i for i, line in enumerate(lines) if marker in line]
    if len(hits) != 1:
        raise PatchError(
            f"{rel}: 预期 {what} 命中 1 处，实际 {len(hits)} 处"
            f"（锚点 {marker!r}）—— 上游结构已变化，补丁需要更新"
        )
    return hits[0]


def drop_anchor_element(lines: list[str], marker: str, rel: str) -> None:
    """删除包含 marker 的整个 <a> 元素（含其内部图标与文案）。"""
    hit = find_single(lines, marker, rel, "GitHub 入口")

    start = hit
    while start >= 0 and not re.match(r"\s*<a[\s>]", lines[start]):
        start -= 1
    if start < 0:
        raise PatchError(f"{rel}: 命中 {marker!r} 但向上找不到 <a> 起始行")

    end = hit
    while end < len(lines) and "</a>" not in lines[end]:
        end += 1
    if end >= len(lines):
        raise PatchError(f"{rel}: 命中 {marker!r} 但向下找不到 </a> 结束行")

    del lines[start:end + 1]
    collapse_blank_run(lines, start)


def drop_declaration(lines: list[str], marker: str, rel: str) -> None:
    """删除失去引用的声明，连带其上紧邻的单行注释。"""
    hit = find_single(lines, marker, rel, "失效常量声明")
    del lines[hit]
    if hit - 1 >= 0 and lines[hit - 1].lstrip().startswith("//") \
            and "github" in lines[hit - 1].lower():
        del lines[hit - 1]
        hit -= 1
    collapse_blank_run(lines, hit)


def drop_line(lines: list[str], marker: str, rel: str, what: str) -> None:
    """删除唯一命中的整行。"""
    del lines[find_single(lines, marker, rel, what)]


def drop_block(lines: list[str], start_marker: str, rel: str, what: str) -> None:
    """删除从命中行起、到其后第一行独立 `})` 为止的整块声明。"""
    start = find_single(lines, start_marker, rel, what)
    end = start
    while end < len(lines) and lines[end].strip() != "})":
        end += 1
    if end >= len(lines):
        raise PatchError(f"{rel}: 命中 {start_marker!r} 但找不到该块的结束行")
    del lines[start:end + 1]
    collapse_blank_run(lines, start)


def replace_single(lines: list[str], marker: str, new_body: str, rel: str, what: str) -> None:
    """把唯一命中行替换成新内容，保留原缩进。"""
    hit = find_single(lines, marker, rel, what)
    indent = re.match(r"\s*", lines[hit]).group(0)
    lines[hit] = f"{indent}{new_body}\n"


def collapse_blank_run(lines: list[str], index: int) -> None:
    """删除后若前后各留一个空行，合并为一个。"""
    if 0 < index < len(lines) and lines[index].strip() == "" \
            and lines[index - 1].strip() == "":
        del lines[index]


def patch_footer_view(root: Path, rel: str, log: list[str]) -> None:
    """首页 / 用量页页脚：删除 GitHub 链接与 githubUrl 常量。"""
    path = require_file(root, rel)
    lines = read_lines(path)

    drop_anchor_element(lines, ':href="githubUrl"', rel)
    drop_declaration(lines, "const githubUrl =", rel)

    write_lines(path, lines)

    after = path.read_text(encoding="utf-8")
    if "githubUrl" in after:
        raise PatchError(f"{rel}: 改写后仍残留 githubUrl 引用")
    if UPSTREAM_URL in after:
        raise PatchError(f"{rel}: 改写后仍残留上游仓库地址")
    log.append(f"{rel}: 已删除页脚 GitHub 入口与 githubUrl 常量")


def patch_app_header(root: Path, log: list[str]) -> None:
    """右上角用户下拉导航：删除整块 GitHub 项（含 svg 图标与文案）。"""
    path = require_file(root, APP_HEADER)
    lines = read_lines(path)

    drop_anchor_element(lines, f'href="{UPSTREAM_URL}"', APP_HEADER)

    write_lines(path, lines)

    after = path.read_text(encoding="utf-8")
    if UPSTREAM_URL in after:
        raise PatchError(f"{APP_HEADER}: 改写后仍残留上游仓库地址")
    log.append(f"{APP_HEADER}: 已删除下拉导航中的 GitHub 项")


def patch_version_badge(root: Path, target_image: str, log: list[str]) -> None:
    """版本徽章的手动回滚指引：删掉脚本回滚（依赖上游仓库的 install.sh），
    只保留 Docker 回滚，并把镜像改为本仓库的 GHCR 镜像。

    脚本回滚拼的是 raw.githubusercontent.com/<上游仓库>/<tag>/deploy/install.sh，
    照抄执行会把实例换成上游构建；本仓库是瘦仓库、不携带 deploy/，改指自身只会 404。
    内置的一键在线回滚（后端 applyReleaseAssets）不受影响，仍是主路径。
    """
    path = require_file(root, VERSION_BADGE)
    lines = read_lines(path)

    # 1) 删除上游仓库常量与依赖它的脚本回滚命令
    drop_line(lines, "const GITHUB_REPO =", VERSION_BADGE, "上游仓库常量")
    drop_block(lines, "const scriptRollbackCommand = computed(", VERSION_BADGE, "脚本回滚命令")

    # 2) 手动回滚只剩 Docker 一种方式
    replace_single(
        lines, "const manualTab = ref<", "const manualTab = ref<'docker'>('docker')",
        VERSION_BADGE, "手动回滚方式的当前选择",
    )
    replace_single(
        lines, "manualTab.value = 'script'", "manualTab.value = 'docker'",
        VERSION_BADGE, "手动回滚方式的重置值",
    )
    drop_line(lines, "label: t('version.deployScript')", VERSION_BADGE, "脚本回滚选项卡")
    replace_single(
        lines, "? dockerRollbackCommand.value : scriptRollbackCommand.value",
        "dockerRollbackCommand.value", VERSION_BADGE, "当前展示的手动回滚命令",
    )

    # 3) 镜像指向本仓库的 GHCR（上游用的是它自己的 Docker Hub 镜像，我们没有推）
    text = "".join(lines)
    if UPSTREAM_DOCKER_IMAGE not in text:
        raise PatchError(f"{VERSION_BADGE}: 找不到上游镜像标识 {UPSTREAM_DOCKER_IMAGE!r}")
    text = text.replace(UPSTREAM_DOCKER_IMAGE, target_image)
    text = text.replace("// Docker Hub image published by CI", "// Container image published by CI")
    path.write_text(text, encoding="utf-8")

    after = path.read_text(encoding="utf-8")
    for residue, what in [
        ("GITHUB_REPO", "上游仓库常量"),
        ("scriptRollbackCommand", "脚本回滚命令"),
        ("deployScript", "脚本回滚选项卡"),
        ("Wei-Shaw", "上游仓库标识"),
        ("weishaw", "上游镜像标识"),
    ]:
        if residue in after:
            raise PatchError(f"{VERSION_BADGE}: 改写后仍残留{what}（{residue}）")
    if target_image not in after:
        raise PatchError(f"{VERSION_BADGE}: 改写后未找到目标镜像 {target_image}")
    if "manualTab" not in after or "dockerRollbackCommand" not in after:
        raise PatchError(f"{VERSION_BADGE}: 改写误伤了 Docker 回滚逻辑")
    log.append(f"{VERSION_BADGE}: 删除脚本回滚，Docker 回滚镜像 -> {target_image}")


def patch_update_service(root: Path, target_repo: str, log: list[str]) -> None:
    """在线更新来源：githubRepo 常量改指目标仓库。"""
    path = require_file(root, UPDATE_SERVICE)
    before = path.read_text(encoding="utf-8")

    for anchor in ALLOWLIST_ANCHORS:
        if not anchor.search(before):
            raise PatchError(
                f"{UPDATE_SERVICE}: 找不到下载域名白名单锚点 {anchor.pattern}，"
                "上游安全边界可能已变化，补丁需要复核"
            )
    if not GO_MODULE_IMPORT.search(before):
        raise PatchError(f"{UPDATE_SERVICE}: 找不到上游 Go module 导入路径，结构可能已变化")

    const_pattern = re.compile(r'(githubRepo\s*=\s*")' + re.escape(UPSTREAM_REPO) + r'(")')
    if len(const_pattern.findall(before)) != 1:
        raise PatchError(
            f"{UPDATE_SERVICE}: 预期 githubRepo 常量命中 1 处，"
            f"实际 {len(const_pattern.findall(before))} 处"
        )

    after = const_pattern.sub(rf"\g<1>{target_repo}\g<2>", before)
    path.write_text(after, encoding="utf-8")

    if const_pattern.search(after):
        raise PatchError(f"{UPDATE_SERVICE}: 改写后 githubRepo 仍指向上游")
    if not re.search(r'githubRepo\s*=\s*"' + re.escape(target_repo) + r'"', after):
        raise PatchError(f"{UPDATE_SERVICE}: 改写后未找到目标仓库常量")
    for anchor in ALLOWLIST_ANCHORS:
        if not anchor.search(after):
            raise PatchError(f"{UPDATE_SERVICE}: 改写误伤了下载域名白名单 {anchor.pattern}")
    if not GO_MODULE_IMPORT.search(after):
        raise PatchError(f"{UPDATE_SERVICE}: 改写误伤了 Go module 导入路径")
    log.append(f"{UPDATE_SERVICE}: githubRepo -> {target_repo}（域名白名单与 module 路径未动）")


def check_preserved(root: Path, log: list[str]) -> list[str]:
    """确认功能性文档跳转与外部数据源未被误伤，返回需要在 dist 中复查的标记。"""
    dist_markers: list[str] = []
    for rel, marker, in_frontend in PRESERVE:
        path = root / rel
        if not path.is_file():
            raise PatchError(f"{rel}: 文件不存在，无法确认保留项")
        if marker not in path.read_text(encoding="utf-8"):
            raise PatchError(f"{rel}: 应当保留的链接 {marker!r} 已不存在")
        if in_frontend and marker not in dist_markers:
            dist_markers.append(marker)
    log.append(f"保留项校验通过：{len(PRESERVE)} 处文档链接 / 外部数据源原样保留")
    return dist_markers


def check_changed_files(root: Path, log: list[str]) -> None:
    """确认改动面就是预期的四个文件，多一个都算越界。"""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-only"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise PatchError(f"无法读取上游工作副本的改动列表：{exc}") from exc

    changed = {line.strip() for line in out.splitlines() if line.strip()}
    if changed != EXPECTED_CHANGED_FILES:
        unexpected = sorted(changed - EXPECTED_CHANGED_FILES)
        missing = sorted(EXPECTED_CHANGED_FILES - changed)
        raise PatchError(
            "改动面与预期不符 —— "
            f"多出：{unexpected or '无'}；缺少：{missing or '无'}"
        )
    log.append(f"改动面校验通过：仅 {len(EXPECTED_CHANGED_FILES)} 个预期文件被修改")


def target_ghcr_image(target_repo: str) -> str:
    """本仓库推送的 GHCR 镜像名。镜像名固定为 sub2api（由 GoReleaser 的 project_name 决定），
    与仓库名无关；命名空间是小写的 owner。"""
    owner = target_repo.split("/", 1)[0].lower()
    return f"ghcr.io/{owner}/sub2api"


def do_patch(root: Path, target_repo: str) -> int:
    log: list[str] = []
    target_image = target_ghcr_image(target_repo)

    patch_footer_view(root, HOME_VIEW, log)
    patch_footer_view(root, KEY_USAGE_VIEW, log)
    patch_app_header(root, log)
    patch_version_badge(root, target_image, log)
    patch_update_service(root, target_repo, log)
    dist_markers = check_preserved(root, log)
    check_changed_files(root, log)

    (root / MANIFEST_NAME).write_text(
        json.dumps(
            {
                "upstream_repo": UPSTREAM_REPO,
                "target_repo": target_repo,
                "target_image": target_image,
                "removed_entry_url": UPSTREAM_URL,
                "preserve_dist_markers": dist_markers,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    print("去品牌改写完成：")
    for line in log:
        print(f"  ✓ {line}")
    return 0


def do_verify_dist(root: Path) -> int:
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise PatchError(f"找不到 {MANIFEST_NAME}，请先执行改写再校验产物")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    dist = root / "backend/internal/web/dist"
    if not dist.is_dir():
        raise PatchError("前端产物目录 backend/internal/web/dist 不存在")

    files = [p for p in dist.rglob("*") if p.is_file()]
    if not files:
        raise PatchError("前端产物目录为空")

    bare_url_offenders: list[str] = []
    loose_offenders: list[str] = []
    seen_markers: set[str] = set()
    markers = manifest.get("preserve_dist_markers", [])
    target_image = manifest.get("target_image", "")
    target_image_seen = False

    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = str(path.relative_to(dist))
        if BARE_URL_IN_DIST.search(text):
            bare_url_offenders.append(rel)
        if any(p.search(text) for p in LOOSE_UPSTREAM_IN_DIST):
            loose_offenders.append(rel)
        for marker in markers:
            if marker in text:
                seen_markers.add(marker)
        if target_image and target_image in text:
            target_image_seen = True

    if bare_url_offenders:
        raise PatchError(
            "产物中仍出现被删除入口的上游地址：" + ", ".join(sorted(bare_url_offenders)[:5])
        )
    if loose_offenders:
        raise PatchError(
            "产物中仍出现游离的上游标识（运行时拼 URL 的写法）："
            + ", ".join(sorted(loose_offenders)[:5])
        )
    missing = [m for m in markers if m not in seen_markers]
    if missing:
        raise PatchError("产物中丢失了应当保留的文档链接：" + ", ".join(missing))
    if target_image and not target_image_seen:
        raise PatchError(f"产物中找不到目标镜像标识 {target_image}")

    print(
        f"产物校验通过：{len(files)} 个文件中无上游标识（含游离片段），"
        f"{len(markers)} 处保留链接仍在，目标镜像标识存在"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="对上游 sub2api 工作副本执行去品牌改写")
    parser.add_argument("--upstream", required=True, help="上游工作副本路径")
    parser.add_argument("--repo", help="目标仓库 owner/repo（改写模式必填）")
    parser.add_argument(
        "--verify-dist", action="store_true",
        help="仅校验前端构建产物，不做改写",
    )
    args = parser.parse_args()

    root = Path(args.upstream).resolve()
    if not root.is_dir():
        print(f"error: 上游工作副本不存在：{root}", file=sys.stderr)
        return 2

    try:
        if args.verify_dist:
            return do_verify_dist(root)
        if not args.repo:
            print("error: 改写模式需要 --repo owner/repo", file=sys.stderr)
            return 2
        if not REPO_PATTERN.match(args.repo):
            print(f"error: 目标仓库格式非法：{args.repo}", file=sys.stderr)
            return 2
        if args.repo == UPSTREAM_REPO:
            print("error: 目标仓库不能是上游仓库本身", file=sys.stderr)
            return 2
        return do_patch(root, args.repo)
    except PatchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
