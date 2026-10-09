# Deep Research Agent - Work Report

Status of the K4-Day23 Advance Deep Agents lab: setup, implementation, and observed
results. Generated as a progress log; the graded deliverables are the files under
`reports/`.

Date: 2026-10-09

## 1. Objective

Build a multi-agent deep-research system (Deep Agents + sandbox) that takes a topic,
plans sub-questions, delegates them to parallel researcher subagents, retrieves sources
from arXiv / Hugging Face / the web, and writes a citation-backed survey report.

## 2. Environment and setup

| Item | Value |
|---|---|
| Python | 3.14.7 (venv at `.venv/`) |
| Dependencies | `pip install -r requirements.txt` (all pinned packages installed) |
| LLM | OpenAI-compatible endpoint: DeepSeek (`https://api.deepseek.com/v1`, `deepseek-chat`) |
| Sandbox | `SANDBOX=docker` (local Docker, `python:3.12-slim`), no Daytona account needed |
| Web search/fetch | Linkup API (`LINKUP_API_KEY`) |
| Secrets | stored in `.env` (gitignored); no secrets in tracked files |

Commands used:
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # then fill LAB_API_KEY, LINKUP_API_KEY
```

Verified: `make_model()` builds `ChatOpenAI(deepseek-chat)`; Docker sandbox
`execute`/`upload`/`download` and cleanup work; `.env` is ignored by git.

## 3. Implementation summary

All four "STUDENT IMPLEMENTS" files were completed and smoke-tested.

| File | Implemented |
|---|---|
| `check_citations.py` | `check(report_text, sources)`: source schema (int `n`, http(s) URL, unique URL), body/`## References` split, grouped `[1, 2]`/`[1-3]` expansion, code-span and Markdown-link exclusion, cited-vs-sources cross-check, exactly one reference line per source with one matching URL. |
| `tools.py` | `with_retry` (exponential backoff + jitter, capped, honours `Retry-After`, re-raises on last attempt); `arxiv_search` (sanitized query, >=3s spacing, longer retry cap, Atom parsing, version-stripped IDs); `hf_daily_papers` / `hf_search_papers` (compact records, `ai_summary` preferred, string `upvotes` coerced); `web_search` / `web_fetch` via Linkup. |
| `agents.py` | `LEAD_PROMPT` (plan -> parallel delegation -> merge/>=3 families -> write body -> finalize -> validate -> spot-check), `RESEARCHER_PROMPT`, `CHECKER_PROMPT`, `build_subagents()` (researcher = 5 source tools, citation-checker = `web_fetch`), `build_lead_agent()` with `TodoListMiddleware` + call/tool limits. |
| `research.py` | `slugify` (traversal-safe, <=60 chars, fallback `topic`), `build_prompt`, `summarize` (task/tool counts + lead tokens), `save_outputs` (writes exact sandbox bytes; writes nothing on failure), `main` (exit 2/1/0; sandbox always cleaned up). |

Loop/cost guards (GUIDE 2.5): lead `ModelCallLimitMiddleware(150)` /
`ToolCallLimitMiddleware(300)`; subagents `40` / `60`; lead `recursion_limit=1000`.

## 4. Provider change: Exa -> Linkup

The web tool provider was switched from the Exa MCP endpoint to the Linkup HTTP API
(`POST https://api.linkup.so/v1/search` and `/v1/fetch`, bearer auth).

- `web_search` -> `/v1/search` with `q`, `depth=standard`, `outputType=searchResults`, `maxResults`; formatted as text with titles and URLs.
- `web_fetch` -> `/v1/fetch` with `url`, `mode=standard`, `renderJs=true`; markdown truncated to 12000 chars.
- Retries: 429 / 5xx / transport errors go through `with_retry`; 400/401/402 fail fast. `LINKUP_API_KEY` is redacted from every error string.
- `.env` / `.env.example` now use `LINKUP_API_KEY` instead of `EXA_API_KEY`.

Note: `GUIDE.md` / `README.md` / `RUBRIC.md` still describe Exa (item 1.3 is
Exa-specific). The Linkup implementation provides the equivalent robustness
(standard 429 + `Retry-After` retry, key redaction).

## 5. Observed results

### 5.1 Completed reports (all 5 topics)

| Topic | subagent_calls | sources | source_families | elapsed_s | tokens (in/out) | check |
|---|---|---|---|---|---|---|
| survey about world model | 6 | 61 | arxiv, hf-search, web | 682.1 | 699,765 / 17,293 | OK |
| survey about reinforcement learning for LLM reasoning | 5 | 33 | arxiv, hf-daily, hf-search, web | 521.7 | 543,223 / 14,373 | OK |
| survey about LLM agents and tool use | 6 | 60 | arxiv, hf-search, web | 477.9 | 832,904 / 19,531 | OK |
| survey about video and multimodal generation | 7 | 82 | arxiv, hf-daily, hf-search, web | 730.0 | 1,123,087 / 23,453 | OK |
| survey about efficient inference and small language models | 6 | 87 | arxiv, hf-search, web | 1142.6 | 833,708 / 20,982 | OK |

Tokens are lead-only (subagent tokens are not counted, so real cost is higher).

All five reports satisfy:
- `subagent_calls >= 3` and at least 3 of 4 source families (RUBRIC 2.1, 2.2);
- `check_citations.py` prints `OK: N sources, all citations resolve`;
- no duplicate URLs and no source-family/URL mismatches.

`self_check.py` result: **5 topics `OK`, git/secrets `OK` → READY to submit** (manual
rubric items still graded by a person).

### 5.2 Reliability note

Topic 5 failed once with a transient `OpenAIConnectionError` (network). That run
correctly wrote **no** report and exited non-zero, exercising the failure path of
`save_outputs` / `main`; the re-run on a stable connection succeeded (see 5.1).

## 6. Known issues

### 6.1 Source-family/URL mismatch (topic 3) - FIXED and confirmed
The first topic-3 run had source `n=74` labeled `source="arxiv"` with an
`https://arxiv.org/html/2504.15546v2` URL. Per GUIDE 2.2 a paper found through web should
be `source="web"` (or normalized to the `/abs/` URL).

Fix (prompt hardening, `agents.py`, no report hand-editing):
- `LEAD_PROMPT` merge step: `source` is the **tool**, not the domain (web-found papers are
  `web`); arXiv URLs must be the canonical `https://arxiv.org/abs/<id>` (no `/html/`,
  `/pdf/`, or `vN`); a mandatory self-check of every manifest entry before writing.
- `RESEARCHER_PROMPT`: same labeling rule plus canonical-URL rule in the note format.

The topic-3 re-run is clean (0 mismatches, 0 duplicate URLs), confirming the fix.

### 6.2 Documentation drift
`GUIDE.md` / `README.md` / `RUBRIC.md` reference Exa; the implementation now uses Linkup.

### 6.3 Cost
Lead input tokens are ~4.0M across the 5 topics (high because of many file reads and
subagent results in context). Consider tightening `recursion_limit` / limits or the
number of delegated sub-questions if cost matters.

## 7. Next steps

Done: all 5 topics generated, `check_citations.py` OK for each, `self_check.py` READY.

Remaining:
1. Inspect 5 random citations per report and open the sources (RUBRIC 4.2).
2. Write the submission README (install + run + how to read `reports/`).
3. Commit source + `reports/`, push to a **public** GitHub repo.
4. Re-run `.venv/bin/python self_check.py` on the clean checkout.

## 8. How to run

```bash
source .venv/bin/activate
python research.py "survey about world model"
python3 check_citations.py reports/<slug>.md reports/<slug>.sources.json
python self_check.py
```

Each run writes `reports/<slug>.md`, `<slug>.sources.json`, `<slug>.meta.json`.
