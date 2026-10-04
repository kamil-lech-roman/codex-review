"""Record auth hints; an explicit flag enables one synthetic-key request."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--claude", type=Path, required=True)
parser.add_argument("--request-with-invalid-key", action="store_true")
args = parser.parse_args()
binary = args.claude.resolve(strict=True)
allowed = (
    "HOME", "PATH", "USER", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
)
env = {key: os.environ[key] for key in allowed if key in os.environ}
command = [
    str(binary), "--safe-mode", "--restricted", "--setting-sources", "",
    "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
    "--settings", "{}", "auth", "status", "--json",
]
observations = []
with tempfile.TemporaryDirectory(prefix="claude-auth-status-") as cwd:
    for with_key in (False, True):
        child_env = dict(env)
        if with_key:
            child_env["ANTHROPIC_API_KEY"] = "synthetic-invalid-key-codex-probe"
        result = subprocess.run(
            command, env=child_env, cwd=cwd, text=True, capture_output=True, timeout=30,
        )
        body = json.loads(result.stdout)
        fields = ("loggedIn", "authMethod", "apiProvider", "subscriptionType", "apiKeySource")
        observations.append({
            "synthetic_api_key_present": with_key,
            "exit_code": result.returncode,
            "status": {key: body[key] for key in fields if key in body},
            "stderr_present": bool(result.stderr),
        })
    request_observation = None
    if args.request_with_invalid_key:
        request_command = command[:-3] + [
            "--print", "--model", "haiku", "--effort", "low", "--tools", "",
            "--permission-mode", "dontAsk", "--permission-prompts", "none",
            "--no-session-persistence", "--output-format", "json",
            "--system-prompt", "Reply only OK.",
        ]
        result = subprocess.run(
            request_command, input="Reply only OK.\n", env=child_env, cwd=cwd,
            text=True, capture_output=True, timeout=60,
        )
        body = json.loads(result.stdout)
        fields = ("type", "subtype", "is_error", "result", "terminal_reason",
                  "total_cost_usd", "num_turns", "modelUsage", "canonicalModel")
        request_observation = {
            "command": request_command,
            "synthetic_api_key_present": True,
            "exit_code": result.returncode,
            "result": {key: body[key] for key in fields if key in body},
            "stderr_present": bool(result.stderr),
        }

print(json.dumps({
    "platform": platform.platform(),
    "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
    "parent_claude_variable_names": sorted(
        key for key in os.environ
        if key.startswith(("CLAUDE", "ANTHROPIC", "USE_LOCAL_OAUTH"))
    ),
    "child_environment_names": sorted(env),
    "command": command,
    "observations": observations,
    "request_observation": request_observation,
    "scope": (
        "one synthetic-key request; no full adapter or billing qualification"
        if args.request_with_invalid_key else
        "account/configuration hints only; no model request or route qualification"
    ),
}, indent=2))
