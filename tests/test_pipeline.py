import json
import sys

import pytest

from repo2diagram import config as cfgmod
from repo2diagram.cli import main
from repo2diagram.pipeline import build_graph
from repo2diagram.render import render, to_mermaid
from repo2diagram.summarize import parse_json, summarize


class FakeLLM:
    name = "fake"
    model = "fake-1"

    def __init__(self):
        self.calls = 0

    def complete(self, system, prompt):
        self.calls += 1
        if "Return JSON: {\"summary\": \"<2 sentences" in prompt:
            return '{"summary": "A demo app."}'
        return '```json\n{"role": "Fake role", "summary": "Does fake things."}\n```'


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    for var in ("R2D_PROVIDER", "R2D_MODEL", "R2D_BASE_URL", "R2D_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_summarize_fills_labels_and_caches(repo):
    graph, _ = build_graph(repo, summarize_with_llm=False)
    llm = FakeLLM()
    summarize(graph, llm, workers=2)
    assert all(m.role == "Fake role" for m in graph.modules)
    assert graph.summary == "A demo app."
    first = llm.calls
    graph2, _ = build_graph(repo, summarize_with_llm=False)
    summarize(graph2, llm)
    assert llm.calls == first  # served from cache


def test_parse_json_tolerates_chatter():
    assert parse_json('Sure! {"role": "x"} hope that helps') == {"role": "x"}
    assert parse_json("no json") == {}


def test_diff_marks_changed_and_blast_radius(git_repo):
    (git_repo / "src/demo/db/models.py").write_text("class User: pass\n")
    graph, report = build_graph(git_repo, summarize_with_llm=False, diff_base="main")
    changed = {m.path for m in graph.modules if m.changed}
    impacted = {m.path for m in graph.modules if m.impacted}
    assert changed == {"src/demo/db"}
    assert {"src/demo/services", "src/demo/api"} <= impacted
    assert report["changed_files"] == 1
    assert "class m" in to_mermaid(graph) and "changed" in to_mermaid(graph)


@pytest.mark.parametrize("fmt", ["html", "md", "mermaid", "json"])
def test_render_formats(repo, fmt):
    graph, _ = build_graph(repo, summarize_with_llm=False)
    out = render(graph, fmt)
    if fmt == "json":
        assert json.loads(out)["name"] == "demo"
    elif fmt == "html":
        assert "<!doctype html>" in out and "__DATA__" not in out and "flowchart LR" in out
    else:
        assert "flowchart LR" in out


def test_cli_writes_html(repo, tmp_path):
    out = tmp_path / "diagram.html"
    assert main([str(repo), "--no-llm", "-q", "-o", str(out)]) == 0
    assert out.read_text().startswith("<!doctype html>")


def test_config_roundtrip_and_permissions(capsys):
    assert main(["config", "set", "provider", "anthropic"]) == 0
    assert main(["config", "set", "api_key", "sk-ant-secret-key-123456"]) == 0
    assert cfgmod.resolve()["provider"] == "anthropic"
    if sys.platform != "win32":
        assert oct(cfgmod.config_path().stat().st_mode)[-3:] == "600"
    main(["config", "show"])
    assert "secret" not in capsys.readouterr().out
    assert main(["config", "set", "provider", "bogus"]) == 2


def test_static_mode_when_provider_none(repo):
    cfgmod.set_value("provider", "none")
    graph, _ = build_graph(repo)
    assert graph.provider == "static"


def test_pr_comment_focuses_on_the_change(git_repo):
    (git_repo / "src/demo/db/models.py").write_text("class User: pass\n")
    graph, report = build_graph(git_repo, summarize_with_llm=False, diff_base="main")
    out = render(graph, "pr", report)
    assert out.startswith("<!-- repo2diagram:pr -->")
    assert "🔴 | `src/demo/db`" in out and "🟠 | `src/demo/api`" in out
    assert "utils" not in out.split("```mermaid")[1].split("```")[0]  # unrelated module left out


def test_pr_comment_without_source_changes(git_repo):
    (git_repo / "README.md").write_text("changed\n")
    graph, report = build_graph(git_repo, summarize_with_llm=False, diff_base="main")
    assert "No source modules changed" in render(graph, "pr", report)
