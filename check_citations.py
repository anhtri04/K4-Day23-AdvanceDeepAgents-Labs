"""check_citations.py - STUDENT IMPLEMENTS `check`.   Runs INSIDE the sandbox (standard library only).

research.py uploads this file to the sandbox and the lead agent runs it with the `execute` tool:
    python3 /tmp/work/research/check_citations.py [report.md] [sources.json]
It must exit 0 and print "OK: ..." when the report is consistent, else print each problem and exit 1.
"""
import json
import re
import sys

REPORT = "/tmp/work/report/report.md"
SOURCES = "/tmp/work/research/sources.json"

# A citation group: [3]  [1, 2]  [1-3]  [2-3]; but NOT a Markdown link [3](url).
_GROUP = re.compile(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\](?!\()")
# Code spans / fenced blocks: citations inside them must be ignored.
_CODE = re.compile(r"(```.*?```|`[^`\n]*`)", re.DOTALL)
# The References heading, on its own line.
_REF_HEADING = re.compile(r"(?m)^##[ \t]+References[ \t]*$")
# A reference line starts with [n].
_REF_LINE = re.compile(r"^\[(\d+)\]")
# One URL; trailing sentence punctuation is not part of the URL.
_URL = re.compile(r"https?://[^\s)\]]+")


def _expand_group(group):
    """[1] -> [1]; [1, 2] -> [1, 2]; [1-3] -> [1, 2, 3]."""
    numbers = []
    for part in re.split(r"\s*,\s*", group):
        span = re.fullmatch(r"(\d+)\s*[–-]\s*(\d+)", part)
        if span:
            a, b = int(span.group(1)), int(span.group(2))
            numbers.extend(range(a, b + 1) if 0 <= b - a <= 200 else [a, b])
        else:
            numbers.append(int(part))
    return numbers


def _body_of(report_text):
    """Everything before the LAST `## References` heading."""
    matches = list(_REF_HEADING.finditer(report_text))
    return (report_text[: matches[-1].start()] if matches else report_text).rstrip()


def _references_of(report_text):
    """The text AFTER the LAST `## References` heading."""
    matches = list(_REF_HEADING.finditer(report_text))
    return report_text[matches[-1].end():] if matches else ""


def _cited_numbers(body):
    """Numbers cited as [n] in the body only, ignoring code spans and links."""
    cited = set()
    for index, segment in enumerate(_CODE.split(body)):
        if index % 2:  # odd segments are code
            continue
        for match in _GROUP.finditer(segment):
            cited.update(_expand_group(match.group(1)))
    return cited


def check(report_text, sources):
    """Return a list of problem strings (empty list = OK)."""
    problems = []
    if not isinstance(sources, list) or not sources:
        return ["no sources in sources.json"]

    by_n = {}
    seen_urls = {}
    for entry in sources:
        if not isinstance(entry, dict):
            problems.append("a source entry is not a JSON object")
            continue
        n = entry.get("n")
        url = entry.get("url")
        if not isinstance(n, int) or isinstance(n, bool):
            problems.append(f"source {url!r} has a non-integer n: {n!r}")
            continue
        if n in by_n:
            problems.append(f"duplicate source number [{n}] in sources.json")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            problems.append(f"source [{n}] has an invalid url: {url!r}")
        elif url in seen_urls:
            problems.append(f"duplicate source url in sources.json: {url}")
        else:
            seen_urls[url] = n
        by_n[n] = url

    if not _REF_HEADING.search(report_text):
        problems.append("report is missing the '## References' heading")

    body = _body_of(report_text)
    cited = _cited_numbers(body)
    for n in sorted(cited):
        if n not in by_n:
            problems.append(f"[{n}] is cited in the body but missing from sources.json")
    for n in sorted(by_n):
        if n not in cited:
            problems.append(f"source [{n}] is never cited in the body")

    referenced = {}
    for raw in _references_of(report_text).splitlines():
        line = raw.strip()
        match = _REF_LINE.match(line)
        if not match:
            continue
        n = int(match.group(1))
        if n in referenced:
            problems.append(f"more than one reference line for [{n}]")
            continue
        referenced[n] = line
        urls = _URL.findall(line)
        if len(urls) != 1:
            problems.append(f"reference [{n}] must contain exactly one URL (found {len(urls)})")
        elif n in by_n and urls[0] != by_n[n]:
            problems.append(f"reference [{n}] url {urls[0]} != sources.json url {by_n[n]}")

    for n in sorted(referenced):
        if n not in by_n:
            problems.append(f"reference [{n}] does not match any source in sources.json")
    for n in sorted(by_n):
        if n not in referenced:
            problems.append(f"source [{n}] has no line in '## References'")

    return problems


def main(argv):
    report_path = argv[1] if len(argv) > 1 else REPORT
    sources_path = argv[2] if len(argv) > 2 else SOURCES
    try:
        with open(report_path, encoding="utf-8") as f:
            report = f.read()
        with open(sources_path, encoding="utf-8") as f:
            sources = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"cannot read inputs: {exc}")
        return 1
    problems = check(report, sources)
    if problems:
        print("\n".join(problems))
        return 1
    print(f"OK: {len(sources)} sources, all citations resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
