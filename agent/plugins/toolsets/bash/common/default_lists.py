"""
Bash 工具集的默认白/黑名单。

两级默认白名单:
- CORE_ALLOW_LIST: 在 CLI 和容器中安全的只读命令——文本/JSON 处理、
  系统信息以及只读的 git/kubectl 动词。大多作用于管道输入，少数在给定路径时
  也会读取文件；均不修改状态。
- EXTENDED_ALLOW_LIST: 增加文件系统命令（cat、find、ls 等）——在容器中安全，
  但在本地机器上可能暴露敏感文件（~/.ssh、~/.aws 等）。

会把上述命令变成任意代码执行、写文件或删除文件的参数级原语（例如
`find -exec`、`sort --compress-program`、输出重定向）由 validation.py 中的
argv 级检查单独拦截，与白名单成员资格无关。

由 `builtin_allowlist` 配置字段控制:
- "core"（默认）: 使用 CORE_ALLOW_LIST
- "extended": 使用 EXTENDED_ALLOW_LIST
- "none": 空白名单，由用户自行管理

以下所有命令在 Windows 的 Git Bash（MSYS）与 POSIX 上均存在。
"""

from typing import List

# ======================= 中文导览 =======================
# Bash 默认白/黑名单的【数据源】：给 bash 审批提供阶层化的内置前缀名单。
# 三级白名单（由 config 的 builtin_allowlist 选档，见 config.py / default_lists.py）：
#   CORE_ALLOW_LIST    → 只读、容器/本地都安全：文本/JSON 处理、系统信息、git/kubectl 只读动词。
#   EXTENDED_ALLOW_LIST → core + 文件系统读写命令(ls/cat/find…)——容器里安全，但本地可能暴露敏感文件。
#   DEFAULT_DENY_LIST  → 默认黑名单（当前为空）。
# SENSITIVE_PATH_PATTERNS：敏感路径正则（~/.ssh、~/.aws、/.env 等）——任何命令文本命中即【直接 DENY，
#   白名单、既往审批均不可豁免】。它是被删掉的 filesystem 工具集 root_dir 沙箱的替代品：
#   白名单只读命令(head/grep/jq)可读任意路径，至少要把已知密钥位置圈起来。
# 注意：把命令从「只读」变成「任意执行/写删」的参数级原语（find -exec、sort --compress-program、
#   输出重定向）不靠这里拦，而由 argv 级检查在 validation.py 单独拦截。
# 消费方：validation.py 的 get_effective_lists()。
# =========================================================

# Core allow list - read-only commands safe on the CLI and in containers.
# See the module docstring for the file-read caveat.
CORE_ALLOW_LIST: List[str] = [
    # JSON processing
    "jq",
    # Text filtering (operates on stdin/piped data)
    "grep",
    "head",
    "tail",
    "sort",
    "uniq",
    "wc",
    "cut",
    "tr",
    # Process/system info (benign)
    "id",
    "whoami",
    "hostname",
    "uname",
    "date",
    "which",
    "type",
    # Prints arguments to stdout - does not read files
    "echo",
    # Git read-only commands
    "git status",
    "git log",
    "git diff",
    "git show",
    "git branch",
    "git remote",
    "git rev-parse",
    # Kubernetes read-only commands (RBAC-limited regardless of environment)
    "kubectl get",
    "kubectl describe",
    "kubectl logs",
    "kubectl top",
    "kubectl explain",
    "kubectl api-resources",
    "kubectl config view",
    "kubectl config current-context",
    "kubectl cluster-info",
    "kubectl version",
    "kubectl auth can-i",
    "kubectl diff",
    "kubectl events",
]

# Extended allow list - adds filesystem access commands
# Safe in containerized environments with minimal filesystems, but can expose
# sensitive files on local machines (~/.ssh, ~/.aws, /etc/shadow, etc.)
EXTENDED_ALLOW_LIST: List[str] = CORE_ALLOW_LIST + [
    # File reading
    "cat",
    "base64",
    # Filesystem traversal
    "ls",
    "dir",
    "find",
    "stat",
    "du",
    "df",
]

# Default deny list - commands that should require explicit approval
DEFAULT_DENY_LIST: List[str] = []

# Regex patterns (matched case-insensitively against the full command text)
# for paths whose contents are secrets. Any command referencing one of these
# is DENIED outright - allow-list membership, prior prefix approval, or user
# approval of a *different* command cannot override it. This is the bash
# toolset's replacement for the deleted filesystem toolset's root_dir sandbox:
# whitelisted read commands (head/grep/jq, cat/ls/find in extended) can read
# arbitrary paths, so at minimum the known secret locations are fenced off.
SENSITIVE_PATH_PATTERNS: List[str] = [
    r"\.ssh[/\\]?",  # SSH private keys / known_hosts
    r"\.aws[/\\]?",  # AWS credentials
    r"\.gcloud[/\\]?",  # GCP credentials
    r"\.azure[/\\]?",  # Azure credentials
    r"\.netrc\b",  # stored FTP/HTTP credentials
    r"\.git-credentials\b",
    r"\.env\b",  # dotenv files with secrets
    r"id_rsa",
    r"id_ed25519",
    r"/etc/shadow",
    r"\.agent[/\\]config\.yaml",  # this agent's own config (contains api_key)
]
