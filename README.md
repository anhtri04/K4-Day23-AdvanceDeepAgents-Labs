# Deep Research Agent (Deep Agents + Sandbox)

A multi-agent deep-research system: give it a topic (e.g. `survey about world model`)
and it plans sub-questions, delegates them to parallel researcher subagents, gathers
sources from **arXiv**, **Hugging Face** and the **web**, and writes a **citation-backed
survey report** whose references are generated deterministically and validated.

Implementation language: Python 3.11+. The reports produced by this submission are in
[`reports/`](reports/).

## Pipeline

```
research.py "<topic>"
  -> open_sandbox()                 # Docker (default here) or Daytona, always cleaned up
  -> lead Deep Agent (plan with write_todos, split into N >= 3 sub-questions)
  -> task x N (parallel) -> researcher subagents
        arxiv_search / hf_daily_papers / hf_search_papers / web_search / web_fetch
        write notes to /tmp/work/research/notes/
  -> lead merges notes -> /tmp/work/research/sources.json
  -> lead writes report body -> /tmp/work/report/report.md
  -> execute finalize_citations.py   # drops uncited sources, renumbers [n], writes ## References
  -> execute check_citations.py      # must print OK
  -> citation-checker subagent spot-checks claims
  -> download -> reports/<slug>.md, <slug>.sources.json, <slug>.meta.json
```

Network tools and API keys run on the **host**; the sandbox only stores files and runs
the citation scripts. Secrets never enter the sandbox.

## 1. Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then fill in your own keys
```

## 2. Configure `.env`

| Variable | Meaning |
|---|---|
| `LAB_BASE_URL`, `LAB_MODEL`, `LAB_API_KEY` | OpenAI-compatible endpoint (this submission uses DeepSeek: `https://api.deepseek.com/v1`, `deepseek-chat`). `model.py` also accepts a LangChain provider via `LAB_MODEL=<provider>:<model>`. The model **must support tool calling**. |
| `SANDBOX` | `docker` (default here; needs Docker, no account) or `daytona` (needs `DAYTONA_API_KEY`). |
| `SANDBOX_IMAGE` | Container image for Docker mode (default `python:3.12-slim`). |
| `LINKUP_API_KEY` | Web search/fetch via Linkup (https://app.linkup.so). Required for the `web` family. |

`.env` is gitignored. Never commit keys.

## 3. Run

One topic:

```bash
python research.py "survey about world model"
```

The five preset topics ([`topics.md`](topics.md)):

```bash
python research.py "survey about world model"
python research.py "survey about reinforcement learning for LLM reasoning"
python research.py "survey about LLM agents and tool use"
python research.py "survey about video and multimodal generation"
python research.py "survey about efficient inference and small language models"
```

Each run writes `reports/<slug>.md`, `reports/<slug>.sources.json` and
`reports/<slug>.meta.json`. A failed run writes nothing and exits non-zero.

## 4. How to read `reports/`

For a topic `T`, `slug` is `T` lower-cased with runs of non-word characters collapsed to
`-` (e.g. `survey-about-world-model`). Each topic has three files:

- **`<slug>.md`** — the survey. Structure: `# Title`, `## TL;DR`, `## Background`,
  3-6 theme sections, `## Trends and open problems`, `## References`. Every non-obvious
  claim carries an inline `[n]` that resolves to one line in `## References` with exactly
  one URL.
- **`<slug>.sources.json`** — the numbered source list used by the report:
  `[{"n", "id", "url", "title", "date", "source"}, ...]`, where `source` is one of
  `arxiv`, `hf-daily`, `hf-search`, `web`.
- **`<slug>.meta.json`** — grading evidence: `topic`, `model`, `elapsed_s`,
  `subagent_calls` (number of `task` delegations), `tool_calls`, `tokens` (lead only),
  `n_sources`, and `source_families`.

Check a report's citations with the same validator the agent runs in the sandbox:

```bash
python3 check_citations.py reports/<slug>.md reports/<slug>.sources.json
# -> OK: N sources, all citations resolve
```

## 5. Verify before submitting

`self_check.py` checks the automatic rubric items on your `reports/` folder (all 5 topics
present, `subagent_calls >= 3`, >= 3 source families, citations via `check_citations.check`,
and no leaked secrets). It is offline and free:

```bash
python self_check.py            # or: python self_check.py --no-git
```

## 6. Files

| File | Role |
|---|---|
| `research.py` | Main entry point; sandbox lifecycle, prompt, output saving. |
| `agents.py` | Lead prompt, researcher/citation-checker prompts, subagents, lead agent, loop/cost limits. |
| `tools.py` | `with_retry` + the 5 source tools (arXiv, HF daily/search, Linkup search/fetch). |
| `check_citations.py` | Citation validator (runs in the sandbox). |
| `model.py`, `sandbox.py`, `finalize_citations.py`, `self_check.py` | Provided, unmodified. |

## Notes

- **Web provider:** the lab describes the Exa MCP endpoint; this submission uses the
  **Linkup** HTTP API (`/v1/search`, `/v1/fetch`). The retry/robustness behaviour is
  equivalent (standard `429`/`5xx` retry, key redaction).
- Run with a cheap tool-calling model and keep the loop/cost limits (`agents.py`,
  `recursion_limit`) in place: a broken prompt can otherwise loop for a long time.
- Web content is **untrusted data**; the prompts forbid following instructions found in it.
