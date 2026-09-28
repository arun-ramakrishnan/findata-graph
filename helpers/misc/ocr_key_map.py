#!/usr/bin/env python3
"""OCR provider -> API-key env-var mapping, with a no-plaintext key bridge.

WHY THIS EXISTS (2026-09-29): OCR expects a PROVIDER-SPECIFIC env-var name as
its fallback (`z-ai-coding` wants `Z_AI_CODING_API_KEY`, not our house key
name `ZAI_API_KEY`) — hit live when a managed review failed with "no
environment variable fallback found" after the operator's key was exported
under the house name. Also: `ocr config set ...api_key <value>` stores the
key in PLAINTEXT, and a known key NAME is itself a (small) security exposure
— so this file carries the mapping and the bridge instead of either.

Sources:
  - docs table: https://open-codereview.ai/docs/configuration (fetched
    2026-09-29) — covers the providers in the docs.
  - LOCAL-VERIFIED: `z-ai-coding` -> `Z_AI_CODING_API_KEY`, proven by the
    failed/succeeded pair of launches on 2026-09-29 (v1.12.10). The other
    four docs-absent built-ins are marked UNVERIFIED — probe with
    `env "<guess>=x" ocr review --model __probe__ --commit <sha>` before
    trusting one.

Precedence inside OCR (docs): static `api_key` > `api_key_cmd` > the env
fallback below. The no-plaintext-at-rest routes are the last two.

Modes:
  ocr_key_map.py list
      Print the mapping table (no secrets).
  ocr_key_map.py run --provider z-ai-coding -- <ocr args...>
      Resolve the operator key (OCR_KEY_NAME + OCR_KEY_FILE env, or flags),
      then exec the command with the provider's expected env var set to it.
      The key crosses only via the child's environment — never argv, never
      config.json, never this script's output.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# provider -> env-var fallback name (None = no key env: AWS credential chain).
# [docs] = docs table 2026-09-29; [verified] = proven live on this box;
# [unverified] = docs-absent built-in, name inferred — probe before use.
PROVIDER_ENV: dict[str, str | None] = {
    "anthropic": "ANTHROPIC_API_KEY",  # [docs]
    "bedrock": None,  # [docs] AWS credential chain
    "openai": "OPENAI_API_KEY",  # [docs]
    "openai-responses": "OPENAI_RESPONSES_API_KEY",  # [docs]
    "openrouter": "OPENROUTER_API_KEY",  # [docs]
    "gemini": "GEMINI_API_KEY",  # [docs]
    "dashscope": "DASHSCOPE_API_KEY",  # [docs]
    "dashscope-tokenplan": "DASHSCOPE_TOKENPLAN_KEY",  # [docs]
    "volcengine": "ARK_API_KEY",  # [docs]
    "deepseek": "DEEPSEEK_API_KEY",  # [docs]
    "tencent-tokenhub": "TENCENT_TOKENHUB_API_KEY",  # [docs]
    "hy-tokenplan": "TENCENT_HUNYUAN_TOKENPLAN_KEY",  # [docs]
    "iflytek": "SPARK_API_KEY",  # [docs]
    "kimi": "MOONSHOT_API_KEY",  # [docs]
    "kimi-global": "MOONSHOT_GLOBAL_API_KEY",  # [docs]
    "z-ai": "Z_AI_API_KEY",  # [docs]
    "mimo": "MIMO_API_KEY",  # [docs]
    "minimax": "MINIMAX_GLOBAL_API_KEY",  # [docs]
    "minimax-cn": "MINIMAX_API_KEY",  # [docs]
    "baidu-qianfan": "QIANFAN_API_KEY",  # [docs]
    "siliconflow": "SILICONFLOW_GLOBAL_API_KEY",  # [docs]
    "siliconflow-cn": "SILICONFLOW_API_KEY",  # [docs]
    "novita": "NOVITA_API_KEY",  # [docs]
    "xai": "XAI_API_KEY",  # [docs]
    # docs-absent built-ins present in the local registry (v1.12.10):
    "z-ai-coding": "Z_AI_CODING_API_KEY",  # [verified 2026-09-29, live launch pair]
    # Probed 2026-09-29 (dummy-value env at 7 candidate names incl. docs-style
    # guesses, against the discriminating control): all three docs-absent
    # providers stay at the "not configured in providers section" stage —
    # they have NO env-var fallback. Keys go via api_key_cmd (or never via
    # static api_key).
    "edenai": None,  # [probed: no env fallback]
    "litellm": None,  # [probed: no env fallback]
    "ollama-cloud": None,  # [probed: no env fallback]
}

DOCS_URL = "https://open-codereview.ai/docs/configuration"


def cmd_list() -> int:
    print(f"OCR provider -> API-key env fallback (docs {DOCS_URL}, v1.12.10 local)\n")
    print(f"  {'provider':22s} {'env var':32s} source")
    print("  " + "-" * 66)
    for provider, env in PROVIDER_ENV.items():
        if env is not None:
            env_disp = env
        elif provider == "bedrock":
            env_disp = "— (AWS credential chain)"
        else:
            env_disp = "— (no env fallback; api_key_cmd only)"
        print(f"  {provider:22s} {env_disp:32s}")
    print(
        "\nNo-plaintext routes (config never holds the key):\n"
        "  1. per-invocation: ocr_key_map.py run --provider <p> -- <ocr args>\n"
        "     (operator key name/file -> provider env var, exec only)\n"
        '  2. persistent: ocr config set providers.<p>.api_key_cmd "<command printing the key>"\n'
        "     (config.json 0600 stores the COMMAND, never the value)"
    )
    return 0


def _resolve_operator_key(args: argparse.Namespace) -> tuple[str, str, str]:
    """(key value, source description, key NAME) per OCR_KEY_NAME/FILE or flags."""
    key_name = args.key_name or os.environ.get("OCR_KEY_NAME", "")
    key_file = args.key_file or os.environ.get("OCR_KEY_FILE", "")
    if not key_name:
        raise SystemExit(
            "no operator key name: pass --key-name or export OCR_KEY_NAME "
            "(the procedure deliberately never assumes one)"
        )
    if key_file:
        import re

        text = Path(key_file).read_text()
        m = re.search(rf"^\s*(?:export\s+)?{re.escape(key_name)}=(.*)$", text, re.M)
        if not m:
            raise SystemExit(f"{key_name} not found in {key_file}")
        value = m.group(1).strip().strip("\"'")
    else:
        value = os.environ.get(key_name, "")
    value = value.lstrip("=")  # the duplicated-`=` guard (procedure §5b)
    if not value:
        raise SystemExit(f"{key_name} resolved to an empty key")
    return value, key_file or "environment", key_name


def cmd_run(args: argparse.Namespace) -> int:
    provider = args.provider
    env_name = PROVIDER_ENV.get(provider)
    if provider not in PROVIDER_ENV:
        raise SystemExit(
            f"unknown provider {provider!r} — see `ocr llm providers`; a custom "
            "provider has NO env fallback (set api_key_cmd or pass api_key)"
        )
    if env_name is None:
        raise SystemExit(f"provider {provider} uses the AWS credential chain, not a key env var")
    key, source, key_name = _resolve_operator_key(args)
    env = dict(os.environ)
    # Pop by NAME, never by value (muse-review finding 1, 2026-09-29: popping
    # by the secret value is a no-op and the house-named var — secret
    # included — leaked into the child env whenever the key came from the
    # environment rather than a file).
    env.pop(key_name, None)
    env[env_name] = key
    print(
        f"mapping operator {args.key_name or os.environ.get('OCR_KEY_NAME', '?')} "
        f"({source}) -> {env_name} for provider {provider}; exec: {' '.join(args.command)}",
        file=sys.stderr,
    )
    os.execvpe(args.command[0], args.command, env)  # noqa: S606  # fixed operator-invoked argv


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    sub.add_parser("list", help="print the provider -> env-var table (no secrets)")
    run = sub.add_parser("run", help="map the operator key and exec a command")
    run.add_argument("--provider", required=True, help="OCR provider name (see list)")
    run.add_argument("--key-name", help="operator key variable name (else $OCR_KEY_NAME)")
    run.add_argument("--key-file", help="file the key lives in (else $OCR_KEY_FILE / env)")
    run.add_argument("command", nargs="+", help="command to exec (e.g. ocr review ...)")
    args = ap.parse_args(argv)
    if args.mode == "list":
        return cmd_list()
    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
