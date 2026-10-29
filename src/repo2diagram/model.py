"""Plain data model shared by the scanner, summarizer, renderers and MCP server."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Module:
    id: str  # stable node id used in diagrams, e.g. "m3"
    path: str  # directory relative to repo root ("" for root files)
    name: str  # display name
    files: list[str] = field(default_factory=list)
    languages: dict[str, int] = field(default_factory=dict)
    loc: int = 0
    role: str = ""  # short label, e.g. "API layer"
    summary: str = ""  # one-sentence description
    entrypoints: list[str] = field(default_factory=list)
    is_test: bool = False
    changed: bool = False  # touched by --diff
    impacted: bool = False  # depends (transitively) on a changed module
    changed_files: list[str] = field(default_factory=list)

    @property
    def main_language(self) -> str:
        if not self.languages:
            return ""
        return max(self.languages.items(), key=lambda kv: kv[1])[0]


@dataclass
class Edge:
    source: str  # module id that imports
    target: str  # module id being imported
    weight: int = 1  # number of import statements


@dataclass
class RepoGraph:
    name: str
    root: str
    modules: list[Module] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    summary: str = ""
    dependencies: list[str] = field(default_factory=list)  # third-party packages
    languages: dict[str, int] = field(default_factory=dict)
    web_url: str = ""  # e.g. https://github.com/o/r/blob/main — for file links
    diff_base: str = ""
    provider: str = "static"
    llm_errors: int = 0
    llm_error: str = ""

    def module(self, module_id: str) -> Module | None:
        return next((m for m in self.modules if m.id == module_id), None)

    def find(self, name_or_path: str) -> Module | None:
        key = name_or_path.strip("/")
        for m in self.modules:
            if key in (m.id, m.path, m.name):
                return m
        return None

    def to_dict(self) -> dict:
        d = asdict(self)
        for m, md in zip(self.modules, d["modules"]):
            md["main_language"] = m.main_language
        return d
