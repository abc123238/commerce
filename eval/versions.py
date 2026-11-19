"""评测版本信息：runner 版本、提示词指纹、judge 版本、git commit。

把每次运行固定到具体的代码/提示词组合，保证 run 之间的对比可信。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from app.config import settings

RUNNER_VERSION = "2.0"
JUDGE_PROMPT_VERSION = "v2-context"

EVAL_DIR = Path(__file__).resolve().parent
_TRACE_ROOT = EVAL_DIR.parent


def prompt_hash() -> str:
    """Agent 系统提示词（客服 + 分析）的 sha256 短指纹。"""
    from app.agent.prompts import ANALYTICS_PROMPT, SYSTEM_PROMPT

    payload = f"{SYSTEM_PROMPT}\n---\n{ANALYTICS_PROMPT}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def git_commit() -> str | None:
    """当前提交；容器内没有 .git 时回退到 GIT_COMMIT 环境变量。"""
    env_commit = os.getenv("GIT_COMMIT")
    if env_commit:
        return env_commit.strip()[:40]
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_TRACE_ROOT,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:  # noqa: BLE001
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def agent_model_name() -> str:
    return settings.anthropic_model


def run_meta() -> dict:
    return {
        "runner_version": RUNNER_VERSION,
        "git_commit": git_commit(),
        "agent_model": agent_model_name(),
        "judge_model": agent_model_name(),
        "agent_prompt_hash": prompt_hash(),
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
    }
