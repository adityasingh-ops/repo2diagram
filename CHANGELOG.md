# Changelog

## 0.2.1

- Fix: read and write files as UTF-8, so the CLI works on Windows.
- Releases are only cut from version tags, not the moving `v0` Action tag.

## 0.2.0

- **More languages:** import graphs for Rust (`mod`/`use`, workspace crates), Java/Kotlin/Scala (packages) and C# (namespaces).
- **GitHub Action:** `uses: adityasingh-ops/repo2diagram@v0` comments a PR's architecture impact and keeps it updated.
- **`-f pr`:** compact Markdown report focused on changed modules and their blast radius.
- **`--watch`:** live diagram on localhost that redraws as you code.
- **Gallery:** diagrams of well-known projects in `docs/gallery`.
- Fix: folders like `samples/` deep inside JVM package paths were hidden as "examples".

## 0.1.0

- First release: CLI, interactive HTML / Markdown / Mermaid / JSON output, `--diff` blast radius,
  Ollama / OpenAI-compatible / Claude labels, MCP server, agent skill.
