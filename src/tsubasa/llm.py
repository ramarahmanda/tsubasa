"""Headless Claude invocation for batch knowledge passes.

The CLI stays deterministic by default; these passes are explicitly invoked
(`tsubasa study|resolve|profile`) and go through `claude -p`. The command is
configurable so tests can stub it and CI can pin a model.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess


class LLMError(RuntimeError):
    pass


def claude_available(claude_cmd: str = "claude") -> bool:
    return shutil.which(claude_cmd.split()[0]) is not None


def run_claude(prompt: str, model: str = "", claude_cmd: str = "claude", timeout: int = 600,
               cwd=None) -> str:
    cmd = claude_cmd.split() + ["-p", prompt]
    if model:
        cmd += ["--model", model]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except (subprocess.SubprocessError, OSError) as e:
        raise LLMError(f"claude invocation failed: {e}") from e
    if out.returncode != 0:
        raise LLMError(f"claude exited {out.returncode}: {out.stderr.strip()[:300]}")
    return out.stdout


def extract_json(text: str):
    """Pull the first JSON array/object out of model output (tolerates prose
    and code fences around it)."""
    fenced = re.search(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", text, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))
    start = min((i for i in (text.find("["), text.find("{")) if i != -1), default=-1)
    if start == -1:
        raise LLMError(f"no JSON found in model output: {text[:200]!r}")
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(text[start:])
    return obj
