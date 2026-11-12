"""The policy decision function for agent tool calls.

One entry point, :meth:`Policy.decide`, judges every tool call an agent makes,
whatever path it arrives by: shell commands, file reads and writes, and MCP
tools. MCP tools must be registered with a :class:`McpToolSpec` that says
what they touch, and are then judged by the same file and command rules.
Unregistered MCP tools are denied.

The policy is a second line of defence. The first is structural: the agent
works in a worktree of a separate clone whose push destinations are disabled.
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from prat import paths
from prat.domain import Autonomy
from prat.policy.secrets import find_secrets
from prat.policy.shell import (
    ShellParseError,
    SimpleCommand,
    located_substitutions,
    split_commands,
    split_heredocs,
)
from prat.projects import (
    ALWAYS_PROTECTED,
    DEFAULT_SECRET_PATHS,
    PROJECT_DIR,
    SECRET_PATH_EXCEPTIONS,
)

PROJECT_FILES = f"{PROJECT_DIR}/"


class ToolKind(StrEnum):
    BASH = "bash"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    MCP = "mcp"
    WEB = "web"


WEB_SEARCH = "web_search"
WEB_FETCH = "web_fetch"


@dataclass(frozen=True)
class ToolCall:
    kind: ToolKind
    command: str | None = None
    path: str | None = None
    content: str | None = None
    tool_name: str | None = None
    arguments: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def bash(cls, command: str) -> ToolCall:
        return cls(ToolKind.BASH, command=command)

    @classmethod
    def read(cls, path: str) -> ToolCall:
        return cls(ToolKind.FILE_READ, path=path)

    @classmethod
    def write(cls, path: str, content: str = "") -> ToolCall:
        return cls(ToolKind.FILE_WRITE, path=path, content=content)

    @classmethod
    def mcp(cls, tool_name: str, arguments: Mapping[str, Any] | None = None) -> ToolCall:
        return cls(ToolKind.MCP, tool_name=tool_name, arguments=dict(arguments or {}))

    @classmethod
    def web(cls, tool_name: str, arguments: Mapping[str, Any] | None = None) -> ToolCall:
        """``tool_name`` is ``web_search`` (argument ``query``) or ``web_fetch`` (``url``)."""
        return cls(ToolKind.WEB, tool_name=tool_name, arguments=dict(arguments or {}))

    def describe(self) -> dict[str, Any]:
        data: dict[str, Any] = {"kind": str(self.kind)}
        if self.command is not None:
            data["command"] = self.command
        if self.path is not None:
            data["path"] = self.path
        if self.content is not None:
            data["content_length"] = len(self.content)
        if self.tool_name is not None:
            data["tool_name"] = self.tool_name
            data["arguments"] = dict(self.arguments)
        return data


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str

    @classmethod
    def allow(cls, reason: str = "allowed") -> Decision:
        return cls(True, reason)

    @classmethod
    def deny(cls, reason: str) -> Decision:
        return cls(False, reason)


class McpAccess(StrEnum):
    READ = "read"
    WRITE = "write"
    EXEC = "exec"


@dataclass(frozen=True)
class McpToolSpec:
    """Declares what an MCP tool touches so the policy can judge it.

    ``path_args`` name arguments holding paths the tool reads (or writes, for
    ``WRITE`` tools). ``command_arg`` names the argument holding a shell
    command for ``EXEC`` tools; it is judged exactly like the bash tool.
    """

    name: str
    access: McpAccess
    path_args: tuple[str, ...] = ()
    command_arg: str | None = None
    content_arg: str | None = None
    min_autonomy: Autonomy = Autonomy.READ_ONLY


# Command classes -------------------------------------------------------------


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


ALWAYS_DENIED_COMMANDS: Mapping[str, str] = {
    **dict.fromkeys(
        _words("sudo su doas runas pkexec chroot"), "privilege escalation is not allowed"
    ),
    **dict.fromkeys(
        _words(
            "gh glab hub aws gcloud gsutil az kubectl helm terraform pulumi doctl heroku vercel "
            "netlify fly flyctl firebase wrangler serverless sls twine eb sam"
        ),
        "remote service and deployment CLIs are not allowed",
    ),
    **dict.fromkeys(
        _words(
            "shutdown reboot halt poweroff mkfs fdisk diskpart format mount umount systemctl "
            "service launchctl sc reg setx crontab iptables ufw chown kill killall pkill taskkill"
        ),
        "system administration commands are not allowed",
    ),
    **dict.fromkeys(
        _words("apt apt-get yum dnf pacman apk snap brew choco winget port zypper"),
        "system package managers are not allowed",
    ),
    "eval": "eval cannot be analysed",
    "svn": "version control other than the card worktree's git is not allowed",
}

NETWORK_COMMANDS = _words(
    "curl wget ssh scp sftp rsync nc ncat netcat telnet ftp socat http https aria2c "
    "invoke-webrequest iwr invoke-restmethod irm docker podman npx pnpx bunx pipx uvx"
)
READ_ONLY_COMMANDS = _words(
    "ls dir cat type head tail less more wc grep egrep fgrep rg ag find fd tree pwd echo printf "
    "which where whereis file stat du df diff cmp sort uniq cut tr nl column basename dirname "
    "realpath readlink date true false test [ printenv whoami uname id jq yq sed sha256sum "
    "sha1sum md5sum shasum xxd od hexdump strings cd pushd popd sleep seq expr tac rev fold fmt "
    "paste join comm git :"
)
FILE_COMMANDS = _words(
    "mkdir touch cp mv rm rmdir ln chmod tee truncate install patch del erase copy move md rd"
)
SHELLS = _words("sh bash zsh dash ksh ash fish busybox")
WINDOWS_SHELLS = _words("cmd powershell pwsh")
INTERPRETERS = _words("python python3 py node ruby perl php deno bun lua rscript")
INLINE_FLAGS = _words("-c -e --eval -r --command -p --print")
WRAPPERS = _words("nohup time nice timeout stdbuf exec command builtin xargs env ionice caffeinate")
SOURCE_COMMANDS = _words("source .")

PACKAGE_INSTALL: Mapping[str, frozenset[str]] = {
    "pip": _words("install download wheel"),
    "pip3": _words("install download wheel"),
    "uv": _words("pip add sync run tool lock"),
    "npm": _words("install i ci add update upgrade exec x create init dlx"),
    "pnpm": _words("install i add update up dlx create exec"),
    "yarn": _words("install add upgrade up dlx create"),
    "bun": _words("install i add update x create"),
    "go": _words("get install"),
    "cargo": _words("install add fetch update"),
    "gem": _words("install update"),
    "bundle": _words("install update add"),
    "composer": _words("install require update create-project"),
    "poetry": _words("add install update lock"),
}

PUBLISH: Mapping[str, frozenset[str]] = {
    "npm": _words("publish unpublish deprecate owner login adduser token"),
    "pnpm": _words("publish"),
    "yarn": _words("publish npm"),
    "bun": _words("publish"),
    "cargo": _words("publish yank login owner"),
    "gem": _words("push owner yank"),
    "poetry": _words("publish"),
    "uv": _words("publish"),
    "hatch": _words("publish"),
    "flit": _words("publish"),
    "dotnet": _words("nuget"),
}

# git ------------------------------------------------------------------------

GIT_GLOBAL_WITH_VALUE = _words(
    "-C -c --git-dir --work-tree --namespace --config-env --super-prefix --attr-source --list-cmds"
)
_PUSH_DENIED = "git push is not allowed: approved work is merged locally by PRAT"
_CREDENTIALS_DENIED = "credential helpers are not available to agents"
GIT_ALWAYS_DENIED: Mapping[str, str] = {
    "push": _PUSH_DENIED,
    "send-pack": _PUSH_DENIED,
    "http-push": _PUSH_DENIED,
    "send-email": "sending mail is not allowed",
    "imap-send": "sending mail is not allowed",
    "credential": _CREDENTIALS_DENIED,
    "credential-cache": _CREDENTIALS_DENIED,
    "credential-store": _CREDENTIALS_DENIED,
    "daemon": "git daemon is not allowed",
    "filter-branch": "history rewriting tools are not allowed",
    "filter-repo": "history rewriting tools are not allowed",
    "replace": "git replace is not allowed",
    "update-ref": "direct ref edits are not allowed",
    "mergetool": "interactive tools are not available",
    "difftool": "interactive tools are not available",
    "svn": "git svn is not allowed",
    "p4": "git p4 is not allowed",
}
GIT_NETWORK = _words("fetch pull clone ls-remote submodule lfs")
GIT_READ_ONLY = _words(
    "status log diff show rev-parse rev-list ls-files ls-tree cat-file blame annotate grep "
    "describe shortlog merge-base name-rev for-each-ref show-ref help version var count-objects "
    "whatchanged range-diff cherry check-ignore check-attr diff-tree diff-files diff-index "
    "show-branch verify-commit verify-tag fsck"
)
GIT_LOCAL_WRITE = _words(
    "add commit restore checkout switch stash mv rm reset revert cherry-pick merge rebase apply "
    "am tag notes clean format-patch gc prune bisect sparse-checkout init maintenance repack "
    "reflog symbolic-ref"
)
GIT_SAFE_CONFIG_PREFIXES = (
    "color.", "core.quotepath", "core.autocrlf", "core.safecrlf", "user.name", "user.email",
    "advice.", "log.", "diff.renames", "merge.conflictstyle", "init.defaultbranch",
    "commit.gpgsign", "tag.gpgsign", "status.", "column.", "i18n.",
)  # fmt: skip
GIT_DANGEROUS_ENV = re.compile(
    r"^(GIT_DIR|GIT_WORK_TREE|GIT_COMMON_DIR|GIT_INDEX_FILE|GIT_CONFIG.*|GIT_SSH.*|GIT_EXEC_PATH|"
    r"GIT_ASKPASS|GIT_PROXY_COMMAND|GIT_EXTERNAL_DIFF|GIT_EDITOR|GIT_SEQUENCE_EDITOR|"
    r"GIT_TEMPLATE_DIR|GIT_ALTERNATE_OBJECT_DIRECTORIES|GIT_OBJECT_DIRECTORY|SSH_ASKPASS|"
    r"LD_PRELOAD|LD_LIBRARY_PATH|DYLD_.*)$",
    re.IGNORECASE,
)
INLINE_PUSH = re.compile(r"\bgit\b.*\bpush\b|\bsend-pack\b|\bpushurl\b|pushinsteadof", re.I | re.S)

# npx and friends run a tool from the project's node_modules without the network when it is
# installed there; anything else (a package to fetch, a registry, an inline command) is network.
LOCAL_RUNNERS = _words("npx pnpx bunx")
# curl to this machine (a server the agent started) is a local check, allowed at medium. Only
# these options keep it local: -L could follow a redirect away, --resolve and --proxy could send
# it elsewhere, and -K could read other URLs, so any option not listed here needs high.
LOOPBACK_URL = re.compile(
    r"^(?:https?://)?(?:localhost|127(?:\.\d{1,3}){3}|\[::1\])(?::\d{1,5})?(?:[/?#]\S*)?$",
    re.IGNORECASE,
)
CURL_SHORT_FLAGS = frozenset("sSfiIv")
CURL_FLAGS = _words(
    "--silent --show-error --fail --fail-with-body --include --head --verbose --compressed"
)
CURL_VALUE_OPTIONS = _words(
    "-o --output -X --request -H --header -d --data --data-raw --data-binary --data-urlencode "
    "--json -w --write-out -m --max-time --connect-timeout --retry --retry-delay -A --user-agent"
)

MAX_DEPTH = 6


def command_name(token: str) -> str:
    """Normalise ``/usr/bin/Git.EXE`` style names to ``git``."""
    base = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1", ".com"):
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def _python_name(name: str) -> bool:
    return name in ("python", "python3", "py") or re.fullmatch(r"python3(\.\d+)?", name) is not None


@dataclass
class _Ctx:
    cwd: Path
    depth: int = 0


class Policy:
    """Decides whether an agent tool call may run.

    ``workdir``
        The card worktree. Writes must land inside it.
    ``protected``
        Glob patterns (relative to ``workdir``) that may never be written.
    ``secret_paths``
        Glob patterns for files that may never be read or written.
    ``denied_roots``
        Absolute paths the agent may not read or touch at all (the person's
        checkout, the agent clone's main directory, the PRAT home).
    ``project_commands``
        Argument lists of the project's own commands; allowed from ``low`` up.
    ``web_access``
        Whether web search and fetch are allowed. Only the researcher's policy
        sets it; every other agent's web call is denied here as well as never
        being offered the tools.
    ``project_files``
        Lets the agent write ``.prat/``: only the setup agent, which proposes the project
        file, gets this.
    ``denied_commands``
        Extra (pattern, reason) pairs, for example from the installation's
        overlay. A command whose name and arguments match a glob pattern is
        denied wherever it appears, including behind wrappers. They only add
        denials; nothing here can allow a command.
    """

    def __init__(
        self,
        autonomy: Autonomy,
        workdir: Path,
        *,
        protected: Sequence[str] = (),
        secret_paths: Sequence[str] = (),
        denied_roots: Iterable[Path] = (),
        project_commands: Iterable[Sequence[str]] = (),
        mcp_tools: Iterable[McpToolSpec] = (),
        web_access: bool = False,
        denied_commands: Iterable[tuple[str, str]] = (),
        project_files: bool = False,
    ) -> None:
        self.autonomy = autonomy
        self.denied_commands = [(pattern, reason) for pattern, reason in denied_commands]
        self.web_access = web_access
        self.workdir = paths.resolve(workdir)
        # Only the setup agent may write the project file (``.prat/``); ``.git`` stays protected.
        always = [p for p in ALWAYS_PROTECTED if not (project_files and p == PROJECT_FILES)]
        self.protected = [*always, *protected]
        self.secret_paths = [*DEFAULT_SECRET_PATHS, *secret_paths]
        self.denied_roots = [paths.resolve(p) for p in denied_roots]
        self.project_commands = [list(c) for c in project_commands if c]
        self.mcp_tools = {spec.name: spec for spec in mcp_tools}
        self.case_insensitive = paths.is_case_insensitive(self.workdir)

    # Public API ------------------------------------------------------------

    def __call__(self, call: ToolCall) -> Decision:
        return self.decide(call)

    def decide(self, call: ToolCall) -> Decision:
        try:
            if call.kind is ToolKind.FILE_READ:
                return self._decide_read(call.path)
            if call.kind is ToolKind.FILE_WRITE:
                return self._decide_write(call.path, call.content)
            if call.kind is ToolKind.BASH:
                return self._decide_bash(call.command or "", _Ctx(self.workdir))
            if call.kind is ToolKind.WEB:
                return self._decide_web(call)
            return self._decide_mcp(call)
        except (OSError, ValueError) as exc:
            return Decision.deny(f"could not evaluate tool call safely: {exc}")

    # Files -------------------------------------------------------------------

    def _is_secret_path(self, path: Path | str) -> bool:
        name = Path(str(path).replace("\\", "/")).name
        if any(
            paths.glob_match(name, exc, self.case_insensitive) for exc in SECRET_PATH_EXCEPTIONS
        ):
            return False
        if paths.matches_any(name, self.secret_paths, self.case_insensitive):
            return True
        rel = paths.relative_posix(path, self.workdir) if Path(path).is_absolute() else None
        return bool(rel and paths.matches_any(rel, self.secret_paths, self.case_insensitive))

    def check_read(self, raw: str, cwd: Path | None = None) -> Decision:
        target = paths.resolve(raw, cwd or self.workdir)
        if self._is_secret_path(target):
            return Decision.deny(f"{raw} looks like a secret file")
        if paths.is_within(target, self.workdir):
            return Decision.allow("read inside the worktree")
        for root in self.denied_roots:
            if paths.is_within(target, root):
                return Decision.deny(f"{raw} is inside a protected location")
        return Decision.allow("read outside the worktree")

    def check_write(self, raw: str, cwd: Path | None = None) -> Decision:
        if raw in ("/dev/null", "NUL", "nul", "/dev/stdout", "/dev/stderr"):
            return Decision.allow("null device")
        target = paths.resolve(raw, cwd or self.workdir)
        if not paths.is_within(target, self.workdir):
            return Decision.deny(f"{raw} is outside the card worktree")
        rel = paths.relative_posix(target, self.workdir) or ""
        if rel == "":
            return Decision.deny("the worktree root itself may not be replaced or removed")
        pattern = paths.matches_any(rel, self.protected, self.case_insensitive)
        if pattern is not None:
            return Decision.deny(f"{rel} is protected by pattern {pattern!r}")
        if self._is_secret_path(target):
            return Decision.deny(f"{rel} looks like a secret file")
        return Decision.allow("write inside the worktree")

    def _decide_read(self, path: str | None) -> Decision:
        if not path:
            return Decision.deny("missing path")
        return self.check_read(path)

    def _decide_write(self, path: str | None, content: str | None) -> Decision:
        if not self.autonomy.at_least(Autonomy.LOW):
            return Decision.deny("file writes are not allowed at autonomy read_only")
        if not path:
            return Decision.deny("missing path")
        decision = self.check_write(path)
        if not decision.allowed:
            return decision
        if content:
            found = find_secrets(content)
            if found:
                kinds = sorted({m.kind for m in found})
                return Decision.deny(f"content contains secret-like literals: {', '.join(kinds)}")
        return decision

    # MCP -----------------------------------------------------------------------

    def _decide_web(self, call: ToolCall) -> Decision:
        if not self.web_access:
            return Decision.deny("web tools are only available to the researcher")
        if call.tool_name == WEB_SEARCH:
            query = call.arguments.get("query")
            if not isinstance(query, str) or not query.strip():
                return Decision.deny("web search needs a query")
            return Decision.allow("web search")
        if call.tool_name == WEB_FETCH:
            url = call.arguments.get("url")
            if not isinstance(url, str) or not re.match(r"^https?://[^\s/]+", url, re.I):
                return Decision.deny("web fetch needs an http or https URL")
            return Decision.allow("web fetch")
        return Decision.deny(f"unknown web tool {call.tool_name!r}")

    def _decide_mcp(self, call: ToolCall) -> Decision:
        spec = self.mcp_tools.get(call.tool_name or "")
        if spec is None:
            return Decision.deny(f"MCP tool {call.tool_name!r} is not registered with the policy")
        if not self.autonomy.at_least(spec.min_autonomy):
            return Decision.deny(
                f"MCP tool {spec.name} needs autonomy {spec.min_autonomy}, run has {self.autonomy}"
            )
        args = call.arguments
        for arg in spec.path_args:
            value = args.get(arg)
            if value is None:
                continue
            if not isinstance(value, str):
                return Decision.deny(f"argument {arg} must be a path string")
            if spec.access is McpAccess.WRITE:
                content = args.get(spec.content_arg) if spec.content_arg else None
                decision = self._decide_write(value, content if isinstance(content, str) else None)
            else:
                decision = self.check_read(value)
            if not decision.allowed:
                return Decision.deny(f"MCP tool {spec.name}: {decision.reason}")
        if spec.access is McpAccess.EXEC:
            if not spec.command_arg:
                return Decision.deny(f"MCP tool {spec.name} declares exec without command_arg")
            command = args.get(spec.command_arg)
            if not isinstance(command, str):
                return Decision.deny(f"MCP tool {spec.name}: missing command")
            decision = self._decide_bash(command, _Ctx(self.workdir))
            if not decision.allowed:
                return Decision.deny(f"MCP tool {spec.name}: {decision.reason}")
            return decision
        return Decision.allow(f"MCP tool {spec.name} allowed")

    # Shell -------------------------------------------------------------------

    def _decide_bash(self, command: str, ctx: _Ctx) -> Decision:
        if ctx.depth > MAX_DEPTH:
            return Decision.deny("command nests too deeply to analyse")
        if not command.strip():
            return Decision.deny("empty command")
        found = find_secrets(command)
        if found:
            return Decision.deny("command contains secret-like literals")
        # Here-document bodies are data on standard input; only an unquoted one's $(...) runs.
        command, scanned = split_heredocs(command)
        for start, inner in located_substitutions(scanned):
            # Judged from the directory the shell is in when it runs this substitution.
            cwd = self._cwd_before(scanned[:start], ctx.cwd)
            decision = self._decide_bash(inner, _Ctx(cwd, ctx.depth + 1))
            if not decision.allowed:
                return Decision.deny(f"command substitution: {decision.reason}")
        try:
            commands = split_commands(command)
        except ShellParseError as exc:
            return Decision.deny(str(exc))
        if not commands:
            return Decision.deny("empty command")
        for simple in commands:
            decision = self._decide_simple(simple, ctx)
            if not decision.allowed:
                return decision
        return Decision.allow("command allowed")

    @staticmethod
    def _cwd_before(prefix: str, cwd: Path) -> Path:
        """The directory after the ``cd`` and ``pushd`` commands in ``prefix``, the text of a
        command line before some point in it. When the prefix cannot be parsed (it may end
        inside quotes), the starting directory: the stricter answer for relative paths."""
        for _ in range(2):
            try:
                commands = split_commands(prefix)
            except ShellParseError:
                prefix = prefix.rsplit('"', 1)[0] if prefix.count('"') % 2 else prefix
                continue
            for cmd in commands:
                if cmd.argv and command_name(cmd.argv[0]) in ("cd", "pushd") and cmd.argv[1:]:
                    cwd = paths.resolve(cmd.argv[-1], cwd)
            return cwd
        return cwd

    def _decide_simple(self, cmd: SimpleCommand, ctx: _Ctx) -> Decision:
        for name in cmd.assignments:
            if GIT_DANGEROUS_ENV.match(name):
                return Decision.deny(f"setting {name} is not allowed")
        for redirect in cmd.redirects:
            if redirect.is_fd_dup or not redirect.target:
                continue
            if redirect.is_write:
                if not self.autonomy.at_least(Autonomy.LOW) and redirect.target not in (
                    "/dev/null",
                    "NUL",
                    "nul",
                ):
                    return Decision.deny("writing files is not allowed at autonomy read_only")
                decision = self.check_write(redirect.target, ctx.cwd)
            else:
                decision = self.check_read(redirect.target, ctx.cwd)
            if not decision.allowed:
                return decision
        if not cmd.argv:
            return Decision.allow("assignment only")
        return self._decide_argv(cmd.argv, ctx)

    def _check_args(self, argv: Sequence[str], ctx: _Ctx) -> Decision:
        """Generic checks on every argument: secret files and denied roots."""
        for arg in argv[1:]:
            if not arg or (arg.startswith("-") and "=" not in arg):
                continue
            value = arg.split("=", 1)[1] if arg.startswith("-") else arg
            if not value or "://" in value:
                continue
            name = value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            if name and self._is_secret_path(name):
                return Decision.deny(f"{value} looks like a secret file")
            looks_like_path = (
                "/" in value or "\\" in value or value.startswith(("~", ".")) or value[1:3] == ":/"
            )
            if looks_like_path:
                target = paths.resolve(value, ctx.cwd)
                if paths.is_within(target, self.workdir):
                    continue
                for root in self.denied_roots:
                    if paths.is_within(target, root):
                        return Decision.deny(f"{value} is inside a protected location")
        return Decision.allow()

    def _decide_argv(self, argv: list[str], ctx: _Ctx) -> Decision:
        name = command_name(argv[0])
        args = argv[1:]

        decision = self._check_args(argv, ctx)
        if not decision.allowed:
            return decision
        line = " ".join([name, *args])
        for pattern, reason in self.denied_commands:
            if fnmatch.fnmatchcase(line, pattern):
                return Decision.deny(f"{name}: {reason}")

        if name == "kill" and _own_jobs_only(args):
            # Stopping a server the agent started in this same command (`kill $!`, `kill %1`)
            # touches nothing else; a PID number or a name could be anyone's process.
            return self._require(Autonomy.MEDIUM, "kill of the command's own background job")
        if name in ALWAYS_DENIED_COMMANDS:
            return Decision.deny(f"{name}: {ALWAYS_DENIED_COMMANDS[name]}")
        if name.startswith("mkfs"):
            return Decision.deny("system administration commands are not allowed")

        if name in WRAPPERS:
            return self._decide_wrapper(name, args, ctx)
        if name == "git":
            return self._decide_git(args, ctx)
        if name in SHELLS or name in WINDOWS_SHELLS:
            return self._decide_shell(name, args, ctx)
        if name in SOURCE_COMMANDS:
            return self._require(Autonomy.MEDIUM, f"{name} runs a script")
        if name in INTERPRETERS or _python_name(name):
            return self._decide_interpreter(name, args, ctx)
        if name in PUBLISH and args and args[0] in PUBLISH[name]:
            return Decision.deny(f"{name} {args[0]}: publishing is not allowed")
        if (name in NETWORK_COMMANDS or name in PACKAGE_INSTALL) and self._is_project_command(argv):
            return self._require(Autonomy.LOW, "project command")
        if name in LOCAL_RUNNERS:
            local = self._local_bin(args, ctx)
            if local is not None:
                return self._decide_argv([str(local), *args[1:]], ctx)
        if name == "curl":
            local_check = self._decide_loopback_curl(args, ctx)
            if local_check is not None:
                return local_check
        if name in NETWORK_COMMANDS:
            return self._require(Autonomy.HIGH, f"{name} uses the network")
        if name in PACKAGE_INSTALL and args and args[0] in PACKAGE_INSTALL[name]:
            return self._require(Autonomy.HIGH, f"{name} {args[0]} installs packages")
        if name == "dd":
            for arg in args:
                if arg.startswith("of="):
                    return self._require_write(arg[3:], ctx, "dd")
            return self._require(Autonomy.LOW, "dd")
        if name in ("cd", "pushd"):
            if args:
                ctx.cwd = paths.resolve(args[-1], ctx.cwd)
            return Decision.allow("change directory")
        if name == "find":
            return self._decide_find(args, ctx)
        if name == "sed" and any(a == "-i" or a.startswith(("-i", "--in-place")) for a in args):
            return self._decide_file_command("sed", _sed_files(args), ctx)
        if name in READ_ONLY_COMMANDS:
            return Decision.allow(f"{name} is read only")
        if len(args) == 1 and args[0] in ("--version", "-V", "version", "--help", "-h"):
            return Decision.allow("version or help query")
        if name in FILE_COMMANDS:
            return self._decide_file_command(name, args, ctx)
        if name in ("awk", "gawk", "mawk"):
            return self._require(Autonomy.LOW, "awk can run commands")
        if self._is_project_command(argv):
            return self._require(Autonomy.LOW, "project command")
        return self._require(Autonomy.MEDIUM, f"{name} is not on the allow list for this level")

    def _require(self, level: Autonomy, what: str) -> Decision:
        if self.autonomy.at_least(level):
            return Decision.allow(what)
        return Decision.deny(f"{what}: needs autonomy {level}, run has {self.autonomy}")

    def _require_write(self, target: str, ctx: _Ctx, what: str) -> Decision:
        level = self._require(Autonomy.LOW, what)
        if not level.allowed:
            return level
        return self.check_write(target, ctx.cwd)

    def _is_project_command(self, argv: Sequence[str]) -> bool:
        normalised = [command_name(argv[0]), *argv[1:]]
        for project in self.project_commands:
            candidate = [command_name(project[0]), *project[1:]]
            if normalised[: len(candidate)] == candidate:
                return True
        return False

    def _local_bin(self, args: Sequence[str], ctx: _Ctx) -> Path | None:
        """The worktree's own ``node_modules/.bin`` program that ``npx <tool>`` would run, found
        the way npx looks (the current folder, then its parents up to the worktree), or None."""
        if not args or _is_option(args[0]):
            return None
        tool = args[0]
        if not re.fullmatch(r"[A-Za-z0-9._-]+", tool):
            return None  # a path, a version (pkg@1) or a scoped package to fetch
        folder = ctx.cwd
        while paths.is_within(folder, self.workdir):
            for candidate in (tool, f"{tool}.cmd"):
                found = folder / "node_modules" / ".bin" / candidate
                if found.is_file():
                    return found
            if folder == self.workdir or folder.parent == folder:
                break
            folder = folder.parent
        return None

    def _decide_loopback_curl(self, args: Sequence[str], ctx: _Ctx) -> Decision | None:
        """A curl that only talks to this machine, judged at medium; None when it is anything
        else, so the network rule applies."""
        urls: list[str] = []
        i = 0
        while i < len(args):
            arg = args[i]
            if arg in CURL_VALUE_OPTIONS:
                if i + 1 >= len(args):
                    return None
                value = args[i + 1]
                if arg in ("-o", "--output"):
                    decision = self._require_write(value, ctx, "curl -o")
                elif value.startswith("@"):
                    decision = self.check_read(value[1:], ctx.cwd)
                else:
                    decision = Decision.allow()
                if not decision.allowed:
                    return decision
                i += 2
                continue
            short = arg.startswith("-") and not arg.startswith("--") and len(arg) > 1
            if arg in CURL_FLAGS or (short and set(arg[1:]) <= CURL_SHORT_FLAGS):
                i += 1
                continue
            if arg.startswith("-"):
                return None
            urls.append(arg)
            i += 1
        if not urls or not all(LOOPBACK_URL.match(url) for url in urls):
            return None
        return self._require(Autonomy.MEDIUM, "curl to this machine")

    def _decide_file_command(self, name: str, args: list[str], ctx: _Ctx) -> Decision:
        level = self._require(Autonomy.LOW, f"{name} changes files")
        if not level.allowed:
            return level
        operands = [a for a in args if not _is_option(a)]
        if not operands:
            return Decision.allow(f"{name} without operands")
        if name in ("cp", "install", "copy", "ln"):
            dest_flag = _option_value(args, ("-t", "--target-directory"))
            if dest_flag is not None:
                writes, reads = [dest_flag], operands
            else:
                writes, reads = operands[-1:], operands[:-1]
            for src in reads:
                decision = self.check_read(src, ctx.cwd)
                if not decision.allowed:
                    return decision
        else:
            writes = operands
        if name == "chmod" and writes:
            writes = writes[1:]  # first operand is the mode
        for target in writes:
            decision = self.check_write(target, ctx.cwd)
            if not decision.allowed:
                return decision
        return Decision.allow(f"{name} inside the worktree")

    def _decide_find(self, args: list[str], ctx: _Ctx) -> Decision:
        roots: list[str] = []
        i = 0
        while i < len(args) and not args[i].startswith(("-", "(", "!")):
            roots.append(args[i])
            i += 1
        while i < len(args):
            token = args[i]
            if token in ("-exec", "-execdir", "-ok", "-okdir"):
                end = i + 1
                while end < len(args) and args[end] not in (";", "+"):
                    end += 1
                inner = [a for a in args[i + 1 : end] if a != "{}"] or ["true"]
                decision = self._decide_argv(inner, _Ctx(ctx.cwd, ctx.depth + 1))
                if not decision.allowed:
                    return Decision.deny(f"find {token}: {decision.reason}")
                if command_name(inner[0]) in FILE_COMMANDS:
                    for root in roots or ["."]:
                        target = Path(root) / "_"
                        decision = self._require_write(str(target), ctx, f"find {token}")
                        if not decision.allowed:
                            return decision
                i = end + 1
                continue
            if token in ("-delete", "-fprint", "-fprintf", "-fls", "-fprint0"):
                for root in roots or ["."]:
                    # Judge a child of the root: find never deletes the start point
                    # itself unless it matches, and the root may be the worktree.
                    decision = self._require_write(str(Path(root) / "_"), ctx, f"find {token}")
                    if not decision.allowed:
                        return decision
            i += 1
        return Decision.allow("find")

    def _decide_wrapper(self, name: str, args: list[str], ctx: _Ctx) -> Decision:
        rest = list(args)
        if name == "env":
            while rest and (rest[0].startswith("-") or "=" in rest[0]):
                token = rest.pop(0)
                if token in ("-u", "--unset", "-C", "--chdir") and rest:
                    rest.pop(0)
                elif token in ("-S", "--split-string") and rest:
                    return self._decide_bash(rest[0], _Ctx(ctx.cwd, ctx.depth + 1))
                elif "=" in token and not token.startswith("-"):
                    var = token.split("=", 1)[0]
                    if GIT_DANGEROUS_ENV.match(var):
                        return Decision.deny(f"setting {var} is not allowed")
            if not rest:
                return Decision.allow("env listing")
        elif name == "timeout":
            while rest and rest[0].startswith("-"):
                token = rest.pop(0)
                if token in ("-s", "--signal", "-k", "--kill-after") and rest:
                    rest.pop(0)
            if rest:
                rest.pop(0)  # duration
        elif name in ("nice", "ionice"):
            while rest and rest[0].startswith("-"):
                token = rest.pop(0)
                if token in ("-n", "--adjustment", "-c", "-p") and rest:
                    rest.pop(0)
        elif name == "xargs":
            while rest and rest[0].startswith("-"):
                token = rest.pop(0)
                if token in ("-I", "-L", "-n", "-P", "-d", "-s", "-E", "-a") and rest:
                    rest.pop(0)
            if not rest:
                rest = ["echo"]
        else:
            while rest and rest[0].startswith("-"):
                rest.pop(0)
        if not rest:
            return Decision.allow(f"{name} without a command")
        return self._decide_argv(rest, _Ctx(ctx.cwd, ctx.depth + 1))

    def _decide_shell(self, name: str, args: list[str], ctx: _Ctx) -> Decision:
        inner: str | None = None
        if name in SHELLS:
            for i, arg in enumerate(args):
                if arg == "-c" or (
                    arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]
                ):
                    inner = args[i + 1] if i + 1 < len(args) else ""
                    break
                if not arg.startswith("-"):
                    # sh script.sh: runs a script file.
                    if name == "busybox":
                        return self._decide_argv(args[i:], _Ctx(ctx.cwd, ctx.depth + 1))
                    return self._require(Autonomy.MEDIUM, f"{name} runs a script")
            else:
                return Decision.deny(f"{name} reading commands from input cannot be analysed")
        elif name == "cmd":
            for i, token in enumerate(args):
                if token.lower() in ("/c", "/k", "/r"):
                    inner = " ".join(args[i + 1 :])
                    break
            else:
                return Decision.deny("cmd without /c cannot be analysed")
        else:
            for i, token in enumerate(args):
                low = token.lower()
                if low in ("-encodedcommand", "-enc", "-e", "-ec"):
                    return Decision.deny(f"{name} encoded commands cannot be analysed")
                if low in ("-command", "-c"):
                    inner = " ".join(args[i + 1 :])
                    break
                if low == "-file":
                    return self._require(Autonomy.MEDIUM, f"{name} runs a script")
            else:
                return Decision.deny(f"{name} without -Command cannot be analysed")
        if inner is None or not inner.strip():
            return Decision.deny(f"{name} with an empty command")
        decision = self._decide_bash(inner, _Ctx(ctx.cwd, ctx.depth + 1))
        if not decision.allowed:
            return Decision.deny(f"{name} -c: {decision.reason}")
        return decision

    def _decide_interpreter(self, name: str, args: list[str], ctx: _Ctx) -> Decision:
        if args and args[0] == "-m" and len(args) > 1:
            module = args[1]
            rest = args[2:]
            if module in ("pip", "pip3") and rest and rest[0] in PACKAGE_INSTALL["pip"]:
                return self._require(Autonomy.HIGH, f"pip {rest[0]} installs packages")
            if module in ("twine",):
                return Decision.deny("publishing is not allowed")
            if module in ("http.server", "smtpd"):
                return self._require(Autonomy.HIGH, f"{module} opens a network service")
        for i, token in enumerate(args):
            if token in INLINE_FLAGS or (
                token.startswith("-")
                and not token.startswith("--")
                and token[-1:] in "ce"
                and len(token) <= 3
                and token not in ("-m",)
            ):
                code = " ".join(args[i + 1 :])
                if INLINE_PUSH.search(code):
                    return Decision.deny("inline code appears to push with git")
                if find_secrets(code):
                    return Decision.deny("inline code contains secret-like literals")
                return self._require(Autonomy.MEDIUM, f"{name} with inline code")
            if not token.startswith("-"):
                break
        if self._is_project_command([name, *args]):
            return self._require(Autonomy.LOW, "project command")
        if args and len(args) == 1 and args[0] in ("--version", "-V"):
            return Decision.allow("version query")
        return self._require(Autonomy.MEDIUM, f"{name} runs code")

    def _decide_git(self, args: list[str], ctx: _Ctx) -> Decision:
        i = 0
        repo_dir = ctx.cwd
        while i < len(args):
            token = args[i]
            if token in GIT_GLOBAL_WITH_VALUE or any(
                token.startswith(opt + "=") for opt in GIT_GLOBAL_WITH_VALUE if opt.startswith("--")
            ):
                if "=" in token and token.startswith("--"):
                    opt, value = token.split("=", 1)
                    i += 1
                else:
                    opt = token
                    value = args[i + 1] if i + 1 < len(args) else ""
                    i += 2
                if opt == "-c":
                    key = value.split("=", 1)[0].lower()
                    if not key.startswith(GIT_SAFE_CONFIG_PREFIXES):
                        return Decision.deny(f"git -c {key} is not allowed")
                elif opt in ("-C", "--git-dir", "--work-tree"):
                    target = paths.resolve(value, repo_dir)
                    if not paths.is_within(target, self.workdir):
                        return Decision.deny(f"git {opt} {value} points outside the card worktree")
                    if opt == "-C":
                        repo_dir = target
                else:
                    return Decision.deny(f"git {opt} is not allowed")
                continue
            if token.startswith("--exec-path") or token.startswith("--config-env"):
                return Decision.deny(f"git {token.split('=')[0]} is not allowed")
            if token.startswith("-"):
                i += 1
                continue
            break
        if i >= len(args):
            return Decision.allow("git without a subcommand")
        sub = args[i].lower()
        rest = args[i + 1 :]

        if sub in GIT_ALWAYS_DENIED:
            return Decision.deny(GIT_ALWAYS_DENIED[sub])
        if sub == "config":
            return self._decide_git_config(rest)
        if sub == "remote":
            if (
                not rest
                or all(a in ("-v", "--verbose") for a in rest)
                or rest[0]
                in (
                    "show",
                    "get-url",
                )
            ):
                return Decision.allow("git remote listing")
            return Decision.deny("changing git remotes is not allowed")
        if sub == "worktree":
            if rest and rest[0] == "list":
                return Decision.allow("git worktree list")
            return Decision.deny("worktrees are managed by PRAT")
        if sub == "branch":
            for arg in rest:
                if arg in (
                    "-d",
                    "-D",
                    "--delete",
                    "-m",
                    "-M",
                    "--move",
                    "-c",
                    "-C",
                    "--copy",
                    "-f",
                    "--force",
                ) or arg.startswith(("--delete", "--move", "--copy")):
                    return Decision.deny("deleting, renaming or forcing branches is not allowed")
            listing = not [a for a in rest if not a.startswith("-")]
            return (
                Decision.allow("git branch")
                if listing
                else self._require(Autonomy.LOW, "git branch")
            )
        if sub == "tag" and any(a in ("-d", "--delete", "-f", "--force") for a in rest):
            return Decision.deny("deleting or moving tags is not allowed")
        if sub == "reflog" and rest and rest[0] in ("expire", "delete"):
            return Decision.deny("editing the reflog is not allowed")
        if sub == "stash" and (not rest or rest[0] in ("list", "show")):
            return (
                Decision.allow("git stash listing")
                if rest
                else self._require(Autonomy.LOW, "git stash")
            )
        if sub == "rebase" and any(a in ("-i", "--interactive") for a in rest):
            return Decision.deny("interactive rebase needs an editor")
        if sub == "lfs" and rest and rest[0] == "push":
            return Decision.deny(GIT_ALWAYS_DENIED["push"])
        if sub in GIT_NETWORK:
            return self._require(Autonomy.HIGH, f"git {sub} uses the network")
        if sub in GIT_READ_ONLY:
            return Decision.allow(f"git {sub} is read only")
        if sub in GIT_LOCAL_WRITE:
            return self._require(Autonomy.LOW, f"git {sub}")
        return Decision.deny(f"git {sub} is not a known subcommand (aliases are not allowed)")

    @staticmethod
    def _decide_git_config(rest: list[str]) -> Decision:
        read_flags = {
            "--get",
            "--get-all",
            "--get-regexp",
            "--list",
            "-l",
            "--show-origin",
            "--show-scope",
            "--name-only",
            "--null",
            "-z",
            "--local",
            "--global",
            "--system",
            "--worktree",
            "--includes",
            "--no-includes",
            "--type",
            "--bool",
            "--int",
            "--path",
        }
        if rest and rest[0] in ("get", "list"):
            return Decision.allow("git config read")
        flags = [a for a in rest if a.startswith("-")]
        operands = [a for a in rest if not a.startswith("-")]
        if any(f in ("--get", "--get-all", "--get-regexp", "--list", "-l") for f in flags) and all(
            f.split("=")[0] in read_flags for f in flags
        ):
            return Decision.allow("git config read")
        if len(operands) == 1 and not flags:
            return Decision.allow("git config read")
        return Decision.deny("changing git configuration is not allowed")


def _is_option(arg: str) -> bool:
    return arg.startswith("-") and arg != "-"


KILL_SIGNALS = re.compile(r"^-(?:\d{1,2}|[A-Z]{2,6}|s)$")


def _own_jobs_only(args: Sequence[str]) -> bool:
    """Whether a kill targets only the shell's own background jobs: ``$!`` or ``%N``, with an
    optional signal. Anything else (a PID, ``-1``, a process group) is left to the deny rule."""
    targets: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            if not re.fullmatch(r"[A-Z]{2,6}|\d{1,2}", arg):
                return False
            continue
        if arg == "-s":
            skip_next = True
        elif KILL_SIGNALS.match(arg) and arg != "-1":
            continue
        else:
            targets.append(arg)
    return bool(targets) and all(
        t in ("$!", '"$!"') or re.fullmatch(r"%\d{1,2}", t) for t in targets
    )


def _sed_files(args: Sequence[str]) -> list[str]:
    """The files an in-place sed edits. The script is the first operand unless -e or -f gave it,
    and the empty backup suffix of BSD and macOS sed (``sed -i '' ...``) is not a file."""
    files: list[str] = []
    script_given = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-e", "-f", "--expression", "--file"):
            script_given = True
            i += 2
            continue
        if arg.startswith(("--expression=", "--file=")):
            script_given = True
        elif arg == "-i" and i + 1 < len(args) and args[i + 1] == "":
            i += 2
            continue
        elif not _is_option(arg):
            files.append(arg)
        i += 1
    return files if script_given else files[1:]


def _option_value(args: Sequence[str], names: Sequence[str]) -> str | None:
    for i, arg in enumerate(args):
        for name in names:
            if arg == name and i + 1 < len(args):
                return args[i + 1]
            if arg.startswith(name + "="):
                return arg.split("=", 1)[1]
    return None


def denied_roots_for(*roots: Path | str | None) -> list[Path]:
    return [Path(os.fspath(r)) for r in roots if r]
