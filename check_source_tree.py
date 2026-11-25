import re
import subprocess
from pathlib import Path, PurePosixPath


PRIVATE_DIRECTORIES = {"data", "demo-data", "review-runs", "work", "backups", ".codex", ".claude", "__pycache__"}
PRIVATE_FILES = {"analysis.json", "tracking.json", "timeline.json", "next_steps.json", "confirmations.json", "analysis_settings.json", "instance.json", "auth.json", "credentials.json", "全量台账.json", "盘点报告.md", ".analysis-worker.lock"}
PRIVATE_CONTENT = (
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"/Users/[A-Za-z0-9_.-]+/"),
)


def path_error(name, mode="100644"):
    path = PurePosixPath(name)
    parts = {part.casefold() for part in path.parts}
    filename = path.name.casefold()
    if mode not in ("100644", "100755"):
        return "符号链接、子模块或非普通文件不允许进入发布清单"
    if path.is_absolute() or ".." in path.parts:
        return "文件路径不合法"
    if parts & PRIVATE_DIRECTORIES or any(part.startswith(".local-") for part in parts):
        return "运行数据或备份目录不可提交"
    if filename in PRIVATE_FILES or filename == ".env" or filename.startswith((".env.", ".analysis-settings-", ".tracking-")):
        return "私人配置、分析或凭据文件不可提交"
    if re.search(r"\.(?:sqlite\w*|db)(?:-(?:wal|shm|journal))?$|\.(?:jsonl|log|pem|key|p12|pfx|zip|gz|pyc)$", filename):
        return "数据库、会话、日志、密钥或打包文件不可提交"
    return None


def inspect_index(root):
    records = subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=root).split(b"\0")
    errors = []
    total = 0
    for record in records:
        if not record:
            continue
        metadata, encoded_path = record.split(b"\t", 1)
        mode, blob, stage = metadata.decode().split()
        name = encoded_path.decode("utf-8", errors="replace")
        total += 1
        reason = path_error(name, mode)
        if stage != "0":
            reason = "存在未解决的合并冲突"
        if reason:
            errors.append((name, reason))
            continue
        content = subprocess.check_output(["git", "cat-file", "blob", blob], cwd=root)
        if any(pattern.search(content) for pattern in PRIVATE_CONTENT):
            errors.append((name, "发现疑似凭据或个人绝对路径，请人工核查"))
    return total, errors


def main():
    root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
    total, errors = inspect_index(root)
    if not total:
        raise SystemExit("Git 索引为空，不能确认发布内容")
    for name, reason in errors:
        print(f"拒绝提交：{name}：{reason}")
    if errors:
        raise SystemExit(1)
    print(f"Source boundary check passed: {total} indexed files. Manual privacy review is still required.")


if __name__ == "__main__":
    main()
