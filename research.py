"""research.py - STUDENT IMPLEMENTS.  The main script.   Guide: GUIDE.md, part 3.

Usage:  python research.py "survey about world model"
Result: reports/<slug>.md   reports/<slug>.sources.json   reports/<slug>.meta.json
"""
import json  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import sys
import time  # noqa: F401
from collections import Counter  # noqa: F401
from pathlib import Path

from agents import (  # noqa: F401
    FINALIZER_PATH,
    NOTES_DIR,
    REPORT_PATH,
    SOURCES_PATH,
    VALIDATOR_PATH,
    WORKDIR,
    build_lead_agent,
)
from model import make_model
from sandbox import download, open_sandbox, upload

ROOT = Path(__file__).parent
REPORTS = ROOT / "reports"
VALIDATOR_SOURCE = ROOT / "check_citations.py"
FINALIZER_SOURCE = ROOT / "finalize_citations.py"   # provided: uploaded next to your validator


def slugify(topic):
    """Turn a topic into a safe file name: lower case, runs of non-word characters become one "-", max 60 chars,
    never empty (fall back to "topic"). The topic is user input: "../../x" must not escape reports/."""
    slug = re.sub(r"[^\w]+", "-", (topic or "").strip().lower(), flags=re.UNICODE).strip("-")
    return slug[:60].strip("-") or "topic"


def build_prompt(topic):
    """The user message sent to the lead agent."""
    return (
        f'Produce a citation-backed survey report on the topic: "{topic}".\n'
        f"Follow your system instructions exactly: plan with write_todos, delegate the sub-questions to researchers "
        f"in parallel, merge the notes into {SOURCES_PATH}, write the report body to {REPORT_PATH}, run the finalizer "
        f"and the validator, and make sure the report draws on at least 3 source families. "
        f"Stop only when the validator prints OK and the report exists."
    )


def summarize(messages, elapsed, model_name):
    """Return {"model", "elapsed_s", "subagent_calls", "tool_calls": {name: count}, "tokens": {"input", "output"}}.

    Lead messages only: subagent tokens are not included, so this undercounts the real cost.
    """
    tool_calls = Counter()
    tokens_in = 0
    tokens_out = 0
    for message in messages:
        for call in getattr(message, "tool_calls", None) or []:
            name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
            if name:
                tool_calls[name] += 1
        usage = getattr(message, "usage_metadata", None)
        if isinstance(usage, dict):
            tokens_in += int(usage.get("input_tokens") or 0)
            tokens_out += int(usage.get("output_tokens") or 0)
    return {
        "model": model_name,
        "elapsed_s": round(elapsed, 1),
        "subagent_calls": tool_calls.get("task", 0),
        "tool_calls": dict(tool_calls),
        "tokens": {"input": tokens_in, "output": tokens_out},
    }


def save_outputs(backend, topic, messages, elapsed, model_name, reports_dir=REPORTS):
    """Download the report from the sandbox and write the three files into reports_dir. Return the report path.

    A failed run must never leave an empty or half-written report behind: validate everything first.
    """
    files = download(backend, [REPORT_PATH, SOURCES_PATH])
    report = files.get(REPORT_PATH)
    raw_sources = files.get(SOURCES_PATH)
    if not report or not report.strip():
        raise RuntimeError("the agent produced no report (or an empty one)")
    if not raw_sources:
        raise RuntimeError("the agent produced no sources.json")
    try:
        sources = json.loads(raw_sources.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"sources.json is not valid JSON: {exc}") from exc
    if not isinstance(sources, list) or not sources:
        raise RuntimeError("sources.json is empty or not a JSON list")

    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(topic)
    meta = {
        "topic": topic,
        **summarize(messages, elapsed, model_name),
        "n_sources": len(sources),
        "source_families": sorted(
            {s.get("source") for s in sources if isinstance(s, dict) and s.get("source")}
        ),
    }
    (reports_dir / f"{slug}.sources.json").write_bytes(raw_sources)
    (reports_dir / f"{slug}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report_path = reports_dir / f"{slug}.md"
    report_path.write_bytes(report)
    return report_path


def main(topic):
    """Return the process exit code (0 ok, 1 failed run, 2 no topic)."""
    topic = (topic or "").strip()
    if not topic:
        print('usage: python research.py "<topic>"', file=sys.stderr)
        return 2

    model = make_model()
    model_name = (
        getattr(model, "model_name", None)
        or getattr(model, "model", None)
        or os.getenv("LAB_MODEL", "unknown")
    )
    start = time.monotonic()

    with open_sandbox() as backend:  # the sandbox is always cleaned up, even on errors
        backend.execute(f"mkdir -p {NOTES_DIR} {WORKDIR}/report")
        upload(backend, {
            VALIDATOR_PATH: VALIDATOR_SOURCE.read_bytes(),
            FINALIZER_PATH: FINALIZER_SOURCE.read_bytes(),
        })
        agent = build_lead_agent(backend, model)
        messages = []
        try:
            result = agent.invoke(
                {"messages": [{"role": "user", "content": build_prompt(topic)}]},
                config={"recursion_limit": 1000},
            )
            if isinstance(result, dict):
                messages = result.get("messages", [])
        except Exception as exc:  # noqa: BLE001 - report the failure and let save_outputs decide
            print(f"agent run raised {type(exc).__name__}: {exc}", file=sys.stderr)

        elapsed = time.monotonic() - start
        try:
            report_path = save_outputs(backend, topic, messages, elapsed, model_name)
        except RuntimeError as exc:
            print(f"FAILED: {exc}", file=sys.stderr)
            return 1

    print(f"saved {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(" ".join(sys.argv[1:])))
