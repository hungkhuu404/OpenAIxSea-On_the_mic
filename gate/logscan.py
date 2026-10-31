"""Fail-closed validation of draft-rule access in Codex JSONL events."""

import json
import re
import shlex
from pathlib import Path

ENVELOPES = {"thread.started", "turn.started", "turn.completed", "turn.failed", "error"}
ITEM_EVENTS = {"item.started", "item.updated", "item.completed"}
IGNORED_ITEMS = {"agent_message", "reasoning", "todo_list", "error"}
SHELLS = {"powershell": "-command", "pwsh": "-command", "cmd": "/c",
          "bash": "-lc", "sh": "-c", "zsh": "-lc"}


def _tokens(command):
    lexer = shlex.shlex(command, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.escape = ""
    return list(lexer)


def _segments(command):
    quote = None
    start = index = 0
    while index < len(command):
        char = command[index]
        if char in "\"'":
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
        elif quote is None and char in ";&|":
            operator = char
            if command[index:index + 2] in ("&&", "||"):
                operator = command[index:index + 2]
            if operator == "&":
                raise ValueError("background command is forbidden")
            yield command[start:index]
            index += len(operator)
            start = index
            continue
        index += 1
    if quote:
        raise ValueError("unclosed quote")
    yield command[start:]


def _basename(value):
    return value.replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")


def _path(value, root, rule_id, files, *, write=False, visited=None):
    visited = set() if visited is None else visited
    if not isinstance(value, str):
        raise ValueError("missing literal path")
    value = value.replace("\\", "/")
    if not value or any(char in value for char in "~$*?[]{}\n\r") or ".." in value.split("/"):
        raise ValueError("non-literal or traversing path")
    base = str(root).replace("\\", "/").rstrip("/")
    absolute = value.startswith("/") or re.match(r"^[A-Za-z]:/", value)
    if absolute:
        if not value.casefold().startswith(base.casefold() + "/"):
            raise ValueError("path outside candidate")
        value = value[len(base) + 1:]
    value = value.removeprefix("./").rstrip("/")
    lowered = value.casefold()
    parts = lowered.split("/")
    if lowered in ("", ".", "rules", "tests"):
        raise ValueError("directory includes forbidden scope")
    files.add(value)
    if any(part.startswith(("heldout", "held-out")) or part.endswith("_private")
           or part in ("_raw", "review", "candidates.jsonl") for part in parts):
        raise ValueError("forbidden path")
    own = {f"rules/{rule_id}.py".casefold(), f"rules/{rule_id}.meta.json".casefold(),
           f"tests/test_{rule_id}.py".casefold()}
    allowed = lowered in own
    if not write:
        allowed = allowed or lowered in {"agents.md", "pytest.ini", "requirements.txt",
                                         "rules/collisions.txt", "rules/law_basis.json"}
        allowed = allowed or (len(parts) == 2 and parts[0] == "rules" and parts[1].endswith(".py"))
        allowed = allowed or any(lowered == prefix or lowered.startswith(prefix + "/")
                                 for prefix in ("loop_input", "core", "textnorm", "data/law",
                                                ".agents/skills/draft-rule"))
    if not allowed:
        raise ValueError(f"forbidden path: {value}")
    target = root / value
    resolved = target.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("symlink outside candidate")
    if resolved != target.absolute():
        _path(resolved.relative_to(root).as_posix(), root, rule_id, files, write=write, visited=visited)
        return
    if target.is_dir():
        if resolved in visited:
            return
        visited.add(resolved)
        for child in sorted(target.iterdir()):
            _path(child.relative_to(root).as_posix(), root, rule_id, files, write=write, visited=visited)


def _read_args(name, args, root, rule_id, files):
    paths = []
    pattern_seen = name not in ("rg", "grep", "sed")
    flags = {"-n", "-i", "-w", "-F", "-q", "-c", "-l", "-L", "-o", "-v", "-s",
             "-H", "-h", "-N", "-S", "-r", "-R", "-E", "-b", "-A", "-T", "-u",
             "--line-number", "--ignore-case", "--fixed-strings", "--no-heading", "--heading",
             "--hidden", "--count", "--files-with-matches", "--files-without-match"}
    value_flags = {"--color", "--max-count", "-m", "--context", "-C", "-B", "--glob", "-g",
                   "--include", "--exclude"}
    if name in ("head", "tail"):
        value_flags |= {"-n", "-c", "--lines", "--bytes"}
        flags |= {"-f", "-q", "-v"}
    if name == "ls":
        flags |= {"-a", "-la", "-al", "-lh", "-lah", "-d", "-1", "--all"}
    index = 0
    options = True
    while index < len(args):
        arg = args[index]
        if options and arg == "--":
            options = False
        elif options and arg in value_flags:
            index += 1
            if index == len(args):
                raise ValueError("missing flag value")
        elif options and arg in ("-f", "--file") and name in ("rg", "grep"):
            index += 1
            if index == len(args):
                raise ValueError("missing pattern file")
            _path(args[index], root, rule_id, files)
            pattern_seen = True
        elif options and arg == "--files" and name == "rg":
            pattern_seen = True
        elif options and arg == "-e" and name in ("rg", "grep", "sed"):
            index += 1
            if index == len(args):
                raise ValueError("missing search pattern")
            if name == "sed" and not re.fullmatch(r"(?:(?:\d+|\$)(?:,(?:\d+|\$))?)?p", args[index]):
                raise ValueError("sed must only print lines")
            pattern_seen = True
        elif options and arg.startswith("-"):
            if arg not in flags:
                raise ValueError(f"unsupported read flag: {arg}")
        elif not pattern_seen:
            if name == "sed" and not re.fullmatch(r"(?:(?:\d+|\$)(?:,(?:\d+|\$))?)?p", arg):
                raise ValueError("sed must only print lines")
            pattern_seen = True
        else:
            paths.append(arg)
        index += 1
    if name == "sed" and "-n" not in args:
        raise ValueError("sed requires -n")
    if not pattern_seen or not paths:
        raise ValueError("explicit path operand required")
    for path in paths:
        _path(path, root, rule_id, files)


def _get_content(args, root, rule_id, files):
    paths = []
    index = 0
    while index < len(args):
        arg = args[index]
        option = arg.lower()
        if option == "-raw":
            pass
        elif option in ("-path", "-literalpath", "-encoding", "-totalcount"):
            index += 1
            if index == len(args):
                raise ValueError("missing Get-Content parameter value")
            value = args[index]
            if option in ("-path", "-literalpath"):
                paths.append(value)
            elif option == "-encoding" and value.lower() != "utf8":
                raise ValueError("Get-Content requires UTF8")
            elif option == "-totalcount" and not value.isdecimal():
                raise ValueError("invalid Get-Content count")
        elif arg.startswith("-"):
            raise ValueError("unsupported Get-Content parameter")
        else:
            paths.append(arg)
        index += 1
    if len(paths) != 1 or "," in paths[0]:
        raise ValueError("Get-Content requires exactly one literal path")
    _path(paths[0], root, rule_id, files)


def _command(command, root, rule_id, files, *, wrapped=False):
    if not isinstance(command, str) or not command.strip():
        raise ValueError("missing command")
    if any(token in command for token in ("$(", "`", "${", "\n", "\r", "<", ">")):
        raise ValueError("shell expansion or redirection is forbidden")
    tokens = _tokens(command)
    executable = tokens[0].replace("\\", "/")
    if any(char in executable for char in "~$*?[]{}") or ".." in executable.split("/"):
        raise ValueError("non-literal executable path")
    name = _basename(tokens[0])
    if name in SHELLS:
        switches = {"-c", "-lc"} if name in ("bash", "sh", "zsh") else {SHELLS[name]}
        if wrapped or len(tokens) < 3 or tokens[1].lower() not in switches:
            raise ValueError("invalid or nested shell wrapper")
        if name != "cmd" and len(tokens) != 3:
            raise ValueError("shell wrapper requires one command argument")
        payload = tokens[2] if len(tokens) == 3 else re.split(r"\s+/c\s+", command, maxsplit=1, flags=re.I)[1]
        _command(payload, root, rule_id, files, wrapped=True)
        return
    segments = list(_segments(command))
    if len(segments) > 1:
        for segment in segments:
            _command(segment, root, rule_id, files, wrapped=wrapped)
        return
    args = tokens[1:]
    if name == "get-content" and tokens[0].lower() == name:
        _get_content(args, root, rule_id, files)
    elif name in ("python", "python3", "pytest"):
        if name != "pytest":
            if args[:2] != ["-m", "pytest"]:
                raise ValueError("only the own pytest command is permitted")
            args = args[2:]
        options = {"-q", "-x", "-s", "-v", "--tb=short"}
        paths = [arg for arg in args if arg not in options]
        if "-q" not in args or len(paths) != 1 or paths[0].replace("\\", "/").casefold() != f"tests/test_{rule_id}.py".casefold():
            raise ValueError("only the own pytest target and approved options are permitted")
        _path(paths[0], root, rule_id, files)
    elif tokens[0] == "git":
        if not args or args[0] not in ("status", "diff"):
            raise ValueError("only git status and diff are permitted")
        permitted = {"--short", "-sb", "--porcelain", "-uall"} if args[0] == "status" else {"--stat", "--name-only", "--cached", "--"}
        for arg in args[1:]:
            if arg in permitted:
                continue
            if args[0] == "status" or arg.startswith("-"):
                raise ValueError("unsupported git argument")
            _path(arg, root, rule_id, files)
    elif tokens[0] == "pwd" and all(arg in ("-L", "-P") for arg in args):
        return
    elif tokens[0] in ("cat", "type", "head", "tail", "sed", "rg", "grep", "ls"):
        _read_args(tokens[0], args, root, rule_id, files)
    else:
        raise ValueError(f"command is forbidden: {tokens[0]}")


def scan_log(log_path, candidate_dir, rule_id):
    root = Path(candidate_dir).resolve()
    files, errors, seen = set(), [], set()
    lines = Path(log_path).read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, 1):
        if line in seen:
            continue
        seen.add(line)
        try:
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError("invalid event")
            envelope = event.get("type")
            if envelope in ENVELOPES:
                continue
            if envelope not in ITEM_EVENTS or not isinstance(event.get("item"), dict):
                raise ValueError("unknown event envelope")
            item = event["item"]
            kind = item.get("type")
            if kind in IGNORED_ITEMS:
                continue
            if kind in ("mcp_tool_call", "web_search"):
                raise ValueError("external access")
            if kind == "command_execution":
                _command(item.get("command"), root, rule_id, files)
            elif kind in ("file_read", "read_file"):
                _path(item["path"], root, rule_id, files)
            elif kind == "file_change" and isinstance(item.get("changes"), list) and item["changes"]:
                for change in item["changes"]:
                    _path(change["path"], root, rule_id, files, write=True)
            else:
                raise ValueError("unknown or missing item type")
        except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
            errors.append(f"Log line {number}: {error}")
    if not lines:
        errors.append("Log line 1: empty log")
    return sorted(files, key=str.casefold), errors
