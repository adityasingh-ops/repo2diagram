import asyncio

from repo2diagram import mcp_server


def test_tools_are_registered():
    tools = asyncio.run(mcp_server.mcp.list_tools())
    assert {t.name for t in tools} == {"diagram_repo", "repo_structure", "module_details", "diff_impact"}


def test_diagram_repo_tool(repo):
    out = mcp_server.diagram_repo(str(repo), save_html=True)
    assert "```mermaid" in out
    assert (repo / "demo-architecture.html").exists()


def test_module_details_and_unknown_module(repo):
    assert "Used by" in mcp_server.module_details(str(repo), "src/demo/db")
    assert "No module" in mcp_server.module_details(str(repo), "nope")


def test_diff_impact_tool(git_repo):
    (git_repo / "src/demo/db/models.py").write_text("class User: pass\n")
    out = mcp_server.diff_impact(str(git_repo), "main")
    assert "CHANGED src/demo/db" in out and "src/demo/api" in out
