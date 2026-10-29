---
name: repo2diagram
description: Draw an accurate architecture diagram of a codebase from its real import graph, or show the blast radius of a branch/PR. Use when the user asks for an architecture overview, a map of a repo, how modules depend on each other, onboarding docs, an ARCHITECTURE.md, or what a set of changes might break.
---

# repo2diagram

`repo2diagram` builds the module graph from real imports (Python, JS/TS, Go) and renders it as Mermaid or an interactive HTML page.

## Commands

Run with `uvx repo2diagram` if it is not installed.

- Overview of the current repo as Markdown (renders on GitHub):
  `uvx repo2diagram . --no-llm -f md -o -`
- Interactive page the user can open:
  `uvx repo2diagram . --no-llm -o architecture.html`
- Blast radius of the current branch:
  `uvx repo2diagram . --no-llm --diff main -f md -o -`
- Full graph as JSON for your own reasoning:
  `uvx repo2diagram . --no-llm -f json -o -`
- Another repo: replace `.` with a path, a git URL or `owner/repo`.

## How to use the output

1. Prefer `--no-llm`: you write better module descriptions yourself after reading the module table and, if needed, a few key files.
2. Edges point from the importing module to the imported one. Modules many others point to are the core; modules that point to many others are entry points or orchestration.
3. In `--diff` output, 🔴 marks changed modules and 🟠 marks modules that depend on them. Suggest reviewing or testing the 🟠 ones.
4. When writing an ARCHITECTURE.md, keep the generated Mermaid block and replace the table's empty descriptions with your own one-line summaries.
