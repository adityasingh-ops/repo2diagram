"""LLM summaries for modules and the whole repo, with an on-disk cache."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from repo2diagram.llm import LLMError
from repo2diagram.model import Module, RepoGraph
from repo2diagram.scanner import language_of

SYSTEM = (
    "You are a senior engineer writing labels for an architecture diagram. "
    "Be concrete and specific to the code you are shown; never invent features. "
    "Reply with a single JSON object and nothing else."
)
MODULE_PROMPT = """Module `{path}` in repository `{repo}`.
Languages: {langs}. Lines of code: {loc}.
Files ({nfiles}):
{files}

Imported by: {imported_by}
Imports: {imports}

Excerpts:
{excerpts}

Return JSON: {{"role": "<2-4 word label, e.g. 'REST API', 'Vector store'>", "summary": "<one sentence, max 22 words, what this module does>"}}"""

REPO_PROMPT = """Repository `{repo}`. Main third-party dependencies: {deps}.
Modules:
{modules}

Return JSON: {{"summary": "<2 sentences: what the project is and how the main pieces fit together>"}}"""

EXCERPT_LINES = 40
MAX_EXCERPT_FILES = 4


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "repo2diagram"


def _cached(client, prompt: str) -> dict:
    key = hashlib.sha256(f"{client.name}|{client.model}|{SYSTEM}|{prompt}".encode()).hexdigest()
    path = cache_dir() / f"{key}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    data = parse_json(client.complete(SYSTEM, prompt))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def parse_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply (tolerates code fences and chatter)."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _excerpts(graph: RepoGraph, mod: Module, inbound: dict[str, int]) -> str:
    root = Path(graph.root)
    code = [f for f in mod.files if language_of(f)]
    readme = [f for f in mod.files if PurePosixPath(f).name.lower().startswith("readme")
              and PurePosixPath(f).parent.as_posix() == (mod.path or ".")]
    # Prefer entrypoints, package inits, then the largest files.
    def rank(f: str) -> tuple:
        name = PurePosixPath(f).name
        return (f not in mod.entrypoints, name not in ("__init__.py", "index.ts", "index.js", "mod.rs", "lib.rs"),
                -inbound.get(f, 0), -(root / f).stat().st_size)
    picks = readme[:1] + sorted(code, key=rank)[:MAX_EXCERPT_FILES]
    chunks = []
    for f in picks:
        try:
            lines = (root / f).read_text(encoding="utf-8", errors="replace").splitlines()[:EXCERPT_LINES]
        except OSError:
            continue
        chunks.append(f"--- {f}\n" + "\n".join(lines))
    return "\n".join(chunks) or "(no source files)"


def summarize(graph: RepoGraph, client, workers: int = 4, on_progress=None) -> None:
    """Fill ``role``/``summary`` on every module and ``graph.summary``. Mutates in place."""
    if client is None:
        return
    graph.provider = f"{client.name}:{client.model}"
    names = {m.id: m.name for m in graph.modules}
    imported_by: dict[str, list[str]] = {m.id: [] for m in graph.modules}
    imports: dict[str, list[str]] = {m.id: [] for m in graph.modules}
    for e in graph.edges:
        imported_by.setdefault(e.target, []).append(names.get(e.source, e.source))
        imports.setdefault(e.source, []).append(names.get(e.target, e.target))

    lock = threading.Lock()

    def _safe(prompt: str) -> dict:
        try:
            return _cached(client, prompt)
        except LLMError as e:
            with lock:
                graph.llm_errors += 1
                graph.llm_error = graph.llm_error or str(e)
            return {}

    def one(mod: Module) -> None:
        files = mod.files[:60]
        prompt = MODULE_PROMPT.format(
            path=mod.path or "(repository root)",
            repo=graph.name,
            langs=", ".join(f"{k} ({v})" for k, v in mod.languages.items()) or "none",
            loc=mod.loc,
            nfiles=len(mod.files),
            files="\n".join(f"- {f}" for f in files) + ("\n- …" if len(mod.files) > 60 else ""),
            imported_by=", ".join(imported_by.get(mod.id, [])) or "nothing",
            imports=", ".join(imports.get(mod.id, [])) or "nothing",
            excerpts=_excerpts(graph, mod, {}),
        )
        data = _safe(prompt)
        if isinstance(data.get("role"), str) and data["role"].strip():
            mod.role = data["role"].strip()[:40]
        if isinstance(data.get("summary"), str):
            mod.summary = data["summary"].strip()[:240]
        if on_progress:
            on_progress(mod)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(one, graph.modules))

    prompt = REPO_PROMPT.format(
        repo=graph.name,
        deps=", ".join(graph.dependencies[:25]) or "none",
        modules="\n".join(f"- {m.name} [{m.role}]: {m.summary}" for m in graph.modules),
    )
    data = _safe(prompt)
    if isinstance(data.get("summary"), str):
        graph.summary = data["summary"].strip()[:600]
