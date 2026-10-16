#!/usr/bin/env python3
"""
claude_review.py

Sends an unpacked Power Platform app/solution's source (canvas app .pa.yaml,
flow definitions, etc.) to Claude for a structured review that Solution
Checker doesn't do: formula complexity, naming conventions, dead controls,
cross-app consistency, and general maintainability.

Writes a markdown report to --output. The calling workflow uploads that
report as a build artifact and/or posts it as a PR comment.

Usage:
    python claude_review.py --source solutions/process-hub/src \
                             --app-name process-hub \
                             --output out/claude-review.md

Requires: pip install anthropic
Env var:  ANTHROPIC_API_KEY
"""

import argparse
import os
import sys
from pathlib import Path

import anthropic

# File types worth sending for review. Keep this list tight — large binary
# or generated files (icons, .msapp internals) add cost without adding signal.
REVIEW_EXTENSIONS = {".pa.yaml", ".yaml", ".yml", ".json", ".xml", ".fx.yaml"}

# Hard cap so one runaway app doesn't blow past context/cost limits.
MAX_TOTAL_CHARS = 180_000

REVIEW_PROMPT = """You are reviewing the source of a Microsoft Power Platform app \
called "{app_name}" as part of a CI/CD quality gate, ahead of promotion to \
Staging/Production. Solution Checker has already run separately for security/ \
performance anti-patterns and dependency checks have already run separately — \
do NOT repeat those categories. Focus on what only a code-literate reviewer \
would catch:

1. Formula quality: overly nested or duplicated Power Fx formulas, formulas \
that should be extracted into a named formula/component, obvious \
inefficiency.
2. Naming conventions: inconsistent control/screen/variable naming, unclear \
names that would confuse the next developer.
3. Dead weight: controls, variables, or collections that are defined but \
never referenced anywhere else in the source.
4. Structural concerns: screens or components doing too much, logic that \
belongs in a flow/plugin instead of the canvas app, hard-coded values that \
should be environment variables or configuration.
5. Anything else a careful senior Power Platform developer would flag in \
code review.

Output a concise markdown report with a short summary line (e.g. "3 findings: \
1 high, 2 low"), then one bullet per finding: file, what you found, why it \
matters, and a one-line suggested fix. If you find nothing worth flagging, \
say so plainly — do not invent findings to fill space.

Source files follow, each preceded by its relative path:
"""


def collect_source(source_dir: Path) -> str:
    chunks = []
    total = 0
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in REVIEW_EXTENSIONS and not path.name.endswith(".pa.yaml"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        rel = path.relative_to(source_dir)
        chunk = f"\n--- FILE: {rel} ---\n{text}\n"
        if total + len(chunk) > MAX_TOTAL_CHARS:
            chunks.append(f"\n[... truncated, remaining files skipped to stay under size limit ...]\n")
            break
        chunks.append(chunk)
        total += len(chunk)
    return "".join(chunks)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="Path to unpacked solution/app source")
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--output", required=True, help="Path to write the markdown report")
    parser.add_argument("--model", default="claude-sonnet-5")
    args = parser.parse_args()

    source_dir = Path(args.source)
    if not source_dir.exists():
        print(f"Source folder not found: {source_dir}", file=sys.stderr)
        sys.exit(1)

    source_text = collect_source(source_dir)
    if not source_text.strip():
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            f"# Claude review — {args.app_name}\n\nNo reviewable source files found under `{args.source}`.\n"
        )
        print("No reviewable files found; wrote placeholder report.")
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    prompt = REVIEW_PROMPT.format(app_name=args.app_name) + source_text

    response = client.messages.create(
        model=args.model,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )

    report_text = "".join(
        block.text for block in response.content if block.type == "text"
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"# Claude review — {args.app_name}\n\n{report_text}\n")
    print(f"Wrote review to {output_path}")

    # Fail the build on anything the review calls "high" severity, so this
    # is a real gate rather than an FYI. Adjust the keyword/severity scheme
    # to match however your team wants to triage findings.
    if "high" in report_text.lower() and "0 high" not in report_text.lower():
        print("::warning::Claude review flagged high-severity findings — see out/claude-review.md")
        # Uncomment to make this a hard gate instead of a warning:
        # sys.exit(1)


if __name__ == "__main__":
    main()
