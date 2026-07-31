"""Transport: build the session invocation, stream it to disk, parse it back.

Both arms go through `claude -p --output-format stream-json`. The raw stream
is written line by line as it arrives, so a crashed or killed run still leaves
usable evidence. Everything downstream (contamination postflight, citation
resolution, judging, summarising) reads the parsed stream, never the process,
which is what lets the stub model exercise the whole pipeline.

Two shapes of invocation:

  one-shot     prompt in argv, process exits after answering. Used by `probe`.
  streaming    `--input-format stream-json`: the process stays alive and takes
               further user messages on stdin. Used by `run`, because the
               follow-up loop has to continue the SAME session and
               `--no-session-persistence` (a contamination control we are not
               giving up) makes `--resume` unavailable by construction.

Measured on claude 2.1.220, and the numbers the harness records depend on it:
`result.total_cost_usd` is CUMULATIVE for the session, `result.usage` and
`result.num_turns` are per-turn. Per-turn cost is therefore a delta.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from pathlib import Path

# Same built-in tool set for both arms. Web tools are OFF on purpose: the
# benchmark asks about KEP statuses and revert rationales that are trivially
# lookupable online, and letting either arm out to the network would measure
# search, not the workspace.
# `Skill` matters: the captain's retrieval, capture and delegation are skills,
# and `--timeline` is documented only inside recall/SKILL.md. Omitting it did not
# measure a captain that chose not to use its skills, it measured one that could
# not: 0 Skill invocations in 144 runs. Arm A has no skills to gain, so the same
# tool list stays fair.
BUILTIN_TOOLS = "Bash,Read,Grep,Glob,TodoWrite,Skill"
DENIED_TOOLS = ("WebSearch", "WebFetch")

PROMPT_TEMPLATE = """\
You are working in a workspace containing four git repositories: {repos}.
Answer the question below from what is in this workspace.
Cite specific evidence for each claim: file paths, file:line, commit SHAs, or record ids.

{question}
"""


def build_prompt(question_text: str, repos: tuple[str, ...]) -> str:
    return PROMPT_TEMPLATE.format(repos=", ".join(repos), question=question_text)


def assert_no_bash_allowlist(argv: list[str]) -> None:
    """Bash must be granted wholesale.

    `Bash(git log:*)` matches only commands literally beginning `git log`, so
    every `git -C <repo> log ...` an agent actually emits is denied. That is
    silent at grading time and would zero out g-git-history. If a future
    change reintroduces a Bash pattern, fail here rather than in the numbers.
    """
    for i, token in enumerate(argv):
        if token in ("--allowedTools", "--allowed-tools"):
            bad = [t for t in argv[i + 1:] if t.startswith("Bash(")]
            if bad:
                raise SystemExit(f"refusing to run: Bash allowlist pattern in argv: {bad}")
    if "--permission-mode" not in argv:
        raise SystemExit("refusing to run: no --permission-mode; tool calls would prompt")


def claude_executable() -> str:
    """Absolute path to the CLI, resolved against the HARNESS's PATH.

    Arm A's child environment has every PATH entry holding a `tsubasa` entry
    point stripped out, and on this machine `tsubasa` and `claude` live in the
    same `~/.local/bin`. Since Popen resolves argv[0] against the *child's*
    PATH, leaving this as the bare word `claude` makes every vanilla run die
    with FileNotFoundError. Resolving it here keeps the contamination control
    (the session still cannot reach `tsubasa`) without disarming the launch.
    """
    return shutil.which("claude") or "claude"


def build_argv(arm: str, model: str, prompt: str, extra: tuple[str, ...] = (),
               stream_input: bool = False) -> list[str]:
    """The session invocation. `stream_input` swaps the positional prompt for
    stdin-delivered user messages so the follow-up loop can continue the same
    session; everything else about the two arms stays identical."""
    argv = [claude_executable(), "-p"]
    if stream_input:
        argv += ["--input-format", "stream-json"]
    else:
        argv.append(prompt)
    argv += [
        "--model", model,
        "--output-format", "stream-json",
        "--verbose",
        "--include-hook-events",      # hook output lands in the stream: evidence
        "--no-session-persistence",   # nothing survives to the next run
        "--strict-mcp-config",        # ignore every MCP config on the machine
        "--mcp-config", '{"mcpServers":{}}',
        "--tools", BUILTIN_TOOLS,
        "--disallowed-tools", *DENIED_TOOLS,
        "--permission-mode", "bypassPermissions",
    ]
    if arm == "A":
        # The single flag that turns every customisation off: CLAUDE.md,
        # skills, plugins (so the tsubasa SessionStart hook never binds),
        # MCP servers, custom agents. Asserted, not trusted: see contamination.
        argv.append("--safe-mode")
    argv += list(extra)
    return argv


def invoke(argv: list[str], cwd: Path, env: dict[str, str], stream_path: Path,
           timeout: int = 1800) -> dict:
    """Run the session, tee stdout to stream_path. Returns process metadata."""
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with stream_path.open("w") as sink:
        proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, bufsize=1)
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                sink.write(line)
                sink.flush()
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            return {"returncode": -1, "stderr": "timeout", "wall_clock_s": time.time() - started}
    stderr = (proc.stderr.read() if proc.stderr else "")[:4000]
    return {"returncode": proc.returncode, "stderr": stderr,
            "wall_clock_s": round(time.time() - started, 3)}


# ------------------------------------------------------- multi-turn session

def user_message(text: str) -> str:
    return json.dumps({"type": "user",
                       "message": {"role": "user",
                                   "content": [{"type": "text", "text": text}]}})


class Session:
    """A live streaming session: several user turns, one process, one session id.

    The whole stream (every turn) is teed to one file as it arrives, so an
    interrupted run still leaves the turns it completed on disk. `ask` returns
    the messages belonging to that turn only, so per-turn metrics are read off
    the turn rather than reconstructed later.
    """

    def __init__(self, argv: list[str], cwd: Path, env: dict[str, str],
                 stream_path: Path, timeout: int = 1800):
        self.argv, self.timeout = argv, timeout
        stream_path.parent.mkdir(parents=True, exist_ok=True)
        self.stream_path = stream_path
        self._sink = stream_path.open("w")
        self.started = time.time()
        self.timed_out = False
        self.eof = False
        self._stderr: list[str] = []
        self.proc = subprocess.Popen(
            argv, cwd=str(cwd), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1)
        # Drain stderr continuously: a long multi-turn session can otherwise
        # fill the pipe buffer and deadlock mid-run.
        self._drain = threading.Thread(target=self._read_stderr, daemon=True)
        self._drain.start()

    def _read_stderr(self) -> None:
        if self.proc.stderr is None:
            return
        for line in self.proc.stderr:
            self._stderr.append(line)

    @property
    def stderr(self) -> str:
        return "".join(self._stderr)

    def ask(self, text: str) -> dict:
        """Send one user message; read until that turn's `result`.

        Returns {"messages", "result", "seconds", "timed_out", "eof"}. A turn
        that hit the timeout or lost the process is reported, never raised: the
        partial stream is evidence and the caller decides what it means.
        """
        started = time.time()
        messages: list[dict] = []
        result: dict = {}
        if self.proc.poll() is not None:
            self.eof = True
            return {"messages": messages, "result": result, "seconds": 0.0,
                    "timed_out": False, "eof": True}
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(user_message(text) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self.eof = True
            return {"messages": messages, "result": result, "seconds": 0.0,
                    "timed_out": False, "eof": True}

        watchdog = threading.Timer(self.timeout, self._kill)
        watchdog.start()
        try:
            assert self.proc.stdout is not None
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    self.eof = True
                    break
                self._sink.write(line)
                self._sink.flush()
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                messages.append(msg)
                if msg.get("type") == "result":
                    result = msg
                    break
        finally:
            watchdog.cancel()
        return {"messages": messages, "result": result,
                "seconds": round(time.time() - started, 3),
                "timed_out": self.timed_out, "eof": self.eof and not result}

    def _kill(self) -> None:
        self.timed_out = True
        try:
            self.proc.kill()
        except OSError:
            pass

    def close(self) -> dict:
        try:
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=30)
        self._drain.join(timeout=10)
        self._sink.close()
        return {"returncode": self.proc.returncode,
                "stderr": ("timeout\n" if self.timed_out else "") + self.stderr[-4000:],
                "wall_clock_s": round(time.time() - self.started, 3)}


PATH_KEYS = ("file_path", "notebook_path", "path")

# A refused tool call reads, in the answer, exactly like a genuine evidence
# gap. Every one is counted so a harness artifact can never be graded as an
# arm's limitation.
DENIAL_SIGNATURES = (
    "requested permissions",
    "haven't granted it yet",
    "permission to use",
    "permission denied by",
    "was denied",
    "blocked by",
    "operation not permitted by",
    "query-only guard",
)


def _is_denial(text: str) -> bool:
    low = text.lower()
    return any(sig in low for sig in DENIAL_SIGNATURES)


def load_messages(stream_path: Path) -> tuple[str, list[dict]]:
    raw = stream_path.read_text() if stream_path.is_file() else ""
    messages: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            messages.append(json.loads(line))
        except ValueError:
            continue
    return raw, messages


def parse_stream(stream_path: Path) -> dict:
    """Whole-file parse. With a multi-turn stream the answer is every turn's
    text and the result fields are the last turn's; use `parse_turns` for
    per-turn numbers."""
    raw, messages = load_messages(stream_path)
    return parse_messages(messages, raw)


def parse_turns(stream_path: Path) -> list[dict]:
    """One parse per turn, split on `result` messages. The session init
    message is carried into every segment so the contamination postflight can
    be evaluated per turn as well as per run."""
    raw, messages = load_messages(stream_path)
    init = next((m for m in messages
                 if m.get("type") == "system" and m.get("subtype") == "init"), None)
    out: list[dict] = []
    segment: list[dict] = []
    for msg in messages:
        segment.append(msg)
        if msg.get("type") == "result":
            if init is not None and init not in segment:
                segment.insert(0, init)
            out.append(parse_messages(segment,
                                      "".join(json.dumps(m) + "\n" for m in segment)))
            segment = []
    if segment:
        if init is not None and init not in segment:
            segment.insert(0, init)
        out.append(parse_messages(segment, "".join(json.dumps(m) + "\n" for m in segment)))
    return out


def parse_messages(messages: list[dict], raw: str = "") -> dict:
    """Pull answer, tool calls, files, usage, cost and denials out of
    stream-json messages.

    Deliberately tolerant: an unrecognised message type is kept in the raw
    text (which the contamination scan reads whole) and otherwise ignored.
    """
    init: dict = {}
    hook_events: list[dict] = []
    rate_limits: list[dict] = []
    answer_parts: list[str] = []
    tool_calls: list[dict] = []
    files: list[str] = []
    result: dict = {}
    by_id: dict[str, dict] = {}
    denied: list[dict] = []

    for msg in messages:
        mtype = msg.get("type")
        if mtype == "system" and msg.get("subtype") == "init" and not init:
            init = msg
        elif str(msg.get("subtype") or "").startswith("hook_") or msg.get("hook_event"):
            # real shape (claude 2.1.220): type=system, subtype=hook_started |
            # hook_response, with hook_name/hook_event/output
            hook_events.append(msg)
        elif mtype == "rate_limit_event":
            rate_limits.append(msg.get("rate_limit_info") or {})
        elif mtype == "assistant":
            for block in (msg.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    answer_parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    payload = block.get("input") or {}
                    call = {"name": block.get("name", ""), "input": payload}
                    tool_calls.append(call)
                    by_id[str(block.get("id"))] = call
                    for key in PATH_KEYS:
                        value = payload.get(key)
                        if isinstance(value, str) and value and value not in files:
                            files.append(value)
        elif mtype == "user":
            for block in (msg.get("message") or {}).get("content") or []:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                body = block.get("content")
                text = body if isinstance(body, str) else json.dumps(body, sort_keys=True)
                if block.get("is_error") or _is_denial(text):
                    if not _is_denial(text):
                        continue  # a tool that ran and failed is evidence, not a denial
                    call = by_id.get(str(block.get("tool_use_id")), {})
                    denied.append({"tool": call.get("name", "?"),
                                   "input": call.get("input", {}),
                                   "reason": text[:400]})
        elif mtype == "result":
            result = msg

    answer = result.get("result") or "\n".join(p for p in answer_parts if p).strip()
    usage = result.get("usage") or {}
    return {
        "raw": raw,
        "init": init,
        "hook_events": hook_events,
        "rate_limits": rate_limits,
        "result_subtype": result.get("subtype", ""),
        "answer": answer.strip(),
        "tool_calls": tool_calls,
        "denied_tool_calls": denied,
        "files_opened": files,
        "num_turns": result.get("num_turns"),
        "cost_usd": result.get("total_cost_usd"),
        "duration_ms": result.get("duration_ms"),
        "session_model": init.get("model") or result.get("model") or "",
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        },
        "is_error": bool(result.get("is_error")) or not answer,
    }


# ------------------------------------------------------------ quota outcomes

# A run that died because the account ran out of session/usage allowance says
# nothing about the arm. Folding it into `wrong` would import last night's
# rate limit into the product comparison, so it gets its own retryable status.
QUOTA_SIGNATURES = (
    "usage limit",
    "rate limit",
    "rate_limit",
    "ratelimit",
    "quota",
    "credit balance",
    "insufficient credit",
    "429",
    "too many requests",
    "overloaded_error",
    "limit reached",
    "limit will reset",
    "resets at",
    "upgrade to increase",
    "out of usage",
)


def looks_like_quota(text: str) -> bool:
    low = (text or "").lower()
    return any(sig in low for sig in QUOTA_SIGNATURES)


def rate_limit_warnings(parsed: dict) -> list[str]:
    """`allowed_warning` and friends: the request went THROUGH, the account is
    just near its window. Worth recording (the campaign may not fit before the
    window resets) and emphatically not a failure."""
    out = []
    for info in parsed.get("rate_limits") or []:
        status = str(info.get("status") or "").lower()
        if status.startswith("allowed") and status != "allowed":
            out.append(f"{status} type={info.get('rateLimitType')} "
                       f"resetsAt={info.get('resetsAt')}")
    return out


def quota_evidence(parsed: dict, stderr: str = "", returncode: int | None = 0) -> str:
    """Non-empty when this turn failed for allowance reasons.

    Three independent signals, in order of strength:
      1. a `rate_limit_event` whose status does NOT begin with `allowed` (the
         CLI's own structured statement that the request was refused),
      2. an error result whose subtype or text carries a quota signature,
      3. a failed process whose stderr does.
    Signal 2 and 3 require an error context: the WORD "quota" appearing in a
    successful answer about etcd is not a quota failure.

    `allowed_warning` is NOT a failure and must never be treated as one. It
    means "you are near the five-hour window, request allowed", and the turn
    that carries it has a real answer in it. Reading it as a failure quarantines
    perfectly good runs and, worse, reports them as an account problem. Only a
    status that does not start with `allowed` means the request was refused.
    """
    for info in parsed.get("rate_limits") or []:
        status = str(info.get("status") or "").lower()
        if status and not status.startswith("allowed"):
            return (f"rate_limit_event status={status} "
                    f"type={info.get('rateLimitType')} resetsAt={info.get('resetsAt')}")
    errored = bool(parsed.get("is_error")) or (returncode not in (0, None)) \
        or str(parsed.get("result_subtype") or "").startswith("error")
    if not errored:
        return ""
    for label, text in (("result_subtype", parsed.get("result_subtype") or ""),
                        ("answer", parsed.get("answer") or ""),
                        ("stderr", stderr or "")):
        if looks_like_quota(text):
            snippet = next(s for s in QUOTA_SIGNATURES if s in text.lower())
            return f"{label} carries {snippet!r}: {text.strip()[:200]}"
    return ""
