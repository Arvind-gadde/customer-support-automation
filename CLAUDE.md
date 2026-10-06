# CLAUDE.md

@AGENTS.md

Claude Code specifics:
- Prefer `make check` over running tools one by one; fix every ruff/mypy error you introduce.
- When you change the graph, run `make graph` and update `docs/architecture.md` if the shape changed.
- Ask before running `make evals` or anything else that spends API credits.
