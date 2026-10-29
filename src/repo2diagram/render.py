"""Render a RepoGraph as Mermaid, Markdown, JSON or a self-contained interactive HTML page."""

from __future__ import annotations

import html
import json
from dataclasses import replace
from importlib import resources

from repo2diagram.model import RepoGraph

MAX_EDGES = 120

ROLE_CLASSES = [
    ("api", ("api", "route", "endpoint", "controller", "handler", "server", "gateway", "rest", "graphql")),
    ("ui", ("ui", "frontend", "component", "page", "screen", "view", "widget", "web")),
    ("data", ("data", "model", "schema", "db", "database", "storage", "store", "repository", "migration")),
    ("ai", ("ai", "ml", "llm", "agent", "prompt", "inference", "embedding", "vector", "pipeline", "model zoo")),
    ("infra", ("infra", "deploy", "docker", "k8s", "terraform", "ci", "config", "script")),
    ("util", ("util", "shared", "common", "helper", "lib", "tool")),
    ("test", ("test", "spec")),
]


def role_class(role: str) -> str:
    r = role.lower()
    for cls, words in ROLE_CLASSES:
        if any(w in r for w in words):
            return cls
    return "core"


def _label(text: str) -> str:
    return text.replace('"', "'").replace("<", "‹").replace(">", "›")


def top_edges(graph: RepoGraph, per_node: int = 3):
    """The links worth drawing: each module's strongest few dependencies, capped overall.

    Every link still shows up in the side panel and in JSON output; this only keeps the
    picture readable.
    """
    by_source: dict[str, list] = {}
    for e in sorted(graph.edges, key=lambda e: -e.weight):
        by_source.setdefault(e.source, []).append(e)
    keep = [e for edges in by_source.values() for e in edges[:per_node]]
    limit = min(MAX_EDGES, max(20, int(2.5 * len(graph.modules))))
    return sorted(keep, key=lambda e: -e.weight)[:limit]


def to_mermaid(graph: RepoGraph, direction: str = "LR") -> str:
    lines = [f"flowchart {direction}"]
    # Group nested modules under their parent directory, e.g. packages/*.
    groups: dict[str, list] = {}
    for m in graph.modules:
        parent = m.path.rsplit("/", 1)[0] if "/" in m.path else ""
        groups.setdefault(parent, []).append(m)

    def node(m) -> str:
        sub = m.role or m.main_language
        text = f"<b>{_label(m.name)}</b>" + (f"<br/><small>{_label(sub)}</small>" if sub else "")
        return f'  {m.id}["{text}"]'

    for parent, mods in groups.items():
        if parent and len(mods) > 1:
            gid = "g_" + "".join(c if c.isalnum() else "_" for c in parent)
            lines.append(f'  subgraph {gid}["{_label(parent)}/"]')
            lines.extend("  " + node(m) for m in mods)
            lines.append("  end")
        else:
            lines.extend(node(m) for m in mods)

    for e in top_edges(graph):
        lines.append(f"  {e.source} --> {e.target}")

    lines += [
        "  classDef api fill:#dbeafe,stroke:#2563eb,color:#0f172a",
        "  classDef ui fill:#fce7f3,stroke:#db2777,color:#0f172a",
        "  classDef data fill:#dcfce7,stroke:#16a34a,color:#0f172a",
        "  classDef ai fill:#ede9fe,stroke:#7c3aed,color:#0f172a",
        "  classDef infra fill:#f1f5f9,stroke:#64748b,color:#0f172a",
        "  classDef util fill:#fef9c3,stroke:#ca8a04,color:#0f172a",
        "  classDef test fill:#f5f5f4,stroke:#a8a29e,color:#0f172a",
        "  classDef core fill:#ffedd5,stroke:#ea580c,color:#0f172a",
        "  classDef changed stroke:#dc2626,stroke-width:4px",
        "  classDef impacted stroke:#f59e0b,stroke-width:3px,stroke-dasharray:6 3",
    ]
    by_class: dict[str, list[str]] = {}
    for m in graph.modules:
        by_class.setdefault(role_class(m.role), []).append(m.id)
    for cls, ids in by_class.items():
        lines.append(f"  class {','.join(ids)} {cls}")
    changed = [m.id for m in graph.modules if m.changed]
    impacted = [m.id for m in graph.modules if m.impacted]
    if changed:
        lines.append(f"  class {','.join(changed)} changed")
    if impacted:
        lines.append(f"  class {','.join(impacted)} impacted")
    return "\n".join(lines)


def to_markdown(graph: RepoGraph, report: dict | None = None, direction: str = "LR") -> str:
    out = [f"# {graph.name} — architecture", ""]
    if graph.summary:
        out += [graph.summary, ""]
    if report:
        out += [(f"**Diff vs `{report['base']}`:** {report['changed_files']} files changed in "
                 f"{len(report['changed_modules'])} modules "
                 f"({', '.join(report['changed_modules']) or 'none'}); "
                 f"blast radius: {', '.join(report['impacted_modules']) or 'none'}."), ""]
    out += ["```mermaid", to_mermaid(graph, direction), "```", "",
            "| Module | Role | What it does | Files | LOC |", "|---|---|---|---|---|"]
    for m in graph.modules:
        flag = " 🔴" if m.changed else (" 🟠" if m.impacted else "")
        out.append(f"| `{m.path or '.'}`{flag} | {m.role} | {m.summary} | {len(m.files)} | {m.loc} |")
    if graph.dependencies:
        out += ["", "**Key dependencies:** " + ", ".join(f"`{d}`" for d in graph.dependencies[:20])]
    out += ["", "_Generated by [repo2diagram](https://github.com/adityasingh-ops/repo2diagram)_", ""]
    return "\n".join(out)


def to_json(graph: RepoGraph, report: dict | None = None) -> str:
    data = graph.to_dict()
    if report:
        data["diff"] = report
    return json.dumps(data, indent=2)


def to_html(graph: RepoGraph, report: dict | None = None, direction: str = "LR") -> str:
    template = resources.files("repo2diagram").joinpath("templates/diagram.html").read_text()
    data = graph.to_dict()
    data["diff"] = report
    payload = json.dumps(data).replace("</", "<\\/")
    return (template
            .replace("__TITLE__", html.escape(f"{graph.name} · architecture"))
            .replace("__MERMAID__", html.escape(to_mermaid(graph, direction)))
            .replace("__DATA__", payload))


PR_MARKER = "<!-- repo2diagram:pr -->"


def focus(graph: RepoGraph) -> RepoGraph:
    """Only the changed modules, their blast radius and what they directly use."""
    keep = {m.id for m in graph.modules if m.changed or m.impacted}
    keep |= {e.target for e in graph.edges if e.source in keep and graph.module(e.source).changed}
    return replace(graph, modules=[m for m in graph.modules if m.id in keep],
                   edges=[e for e in graph.edges if e.source in keep and e.target in keep])


def to_pr_comment(graph: RepoGraph, report: dict | None, direction: str = "LR") -> str:
    """A compact Markdown comment for pull requests (used by the GitHub Action)."""
    out = [PR_MARKER, "### 🗺️ Architecture impact", ""]
    if not report:
        return "\n".join(out + ["Run with `--diff <base>` to see what this change touches.", ""])
    changed = [m for m in graph.modules if m.changed]
    impacted = [m for m in graph.modules if m.impacted]
    if not changed:
        return "\n".join(out + [(f"No source modules changed vs `{report['base']}` "
                                f"({report['changed_files']} non-source files)."), ""])
    out.append(f"**{len(changed)}** module{'s' if len(changed) != 1 else ''} changed · "
               f"**{len(impacted)}** more depend on them · {report['changed_files']} files vs `{report['base']}`")
    out += ["", "```mermaid", to_mermaid(focus(graph), direction), "```", "",
            "| | Module | Why |", "|---|---|---|"]
    for m in changed:
        out.append(f"| 🔴 | `{m.path or '.'}` | {len(m.changed_files)} file(s) changed |")
    names = {m.id: m.path or "." for m in graph.modules}
    for m in impacted:
        via = sorted({names[e.target] for e in graph.edges
                      if e.source == m.id and (graph.module(e.target).changed or graph.module(e.target).impacted)})
        out.append(f"| 🟠 | `{m.path or '.'}` | depends on {', '.join(f'`{v}`' for v in via[:3])} |")
    out += ["", ("<sub>🔴 changed · 🟠 may be affected — generated by "
            "[repo2diagram](https://github.com/adityasingh-ops/repo2diagram)</sub>"), ""]
    return "\n".join(out)


FORMATS = {"html": ".html", "mermaid": ".mmd", "md": ".md", "json": ".json", "pr": ".pr.md"}


def render(graph: RepoGraph, fmt: str, report: dict | None = None, direction: str = "LR") -> str:
    if fmt == "html":
        return to_html(graph, report, direction)
    if fmt == "mermaid":
        return to_mermaid(graph, direction) + "\n"
    if fmt == "md":
        return to_markdown(graph, report, direction)
    if fmt == "json":
        return to_json(graph, report)
    if fmt == "pr":
        return to_pr_comment(graph, report, direction)
    raise ValueError(f"unknown format {fmt!r}")
