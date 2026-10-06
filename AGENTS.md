# AGENTS.md

Instructions for AI coding agents (and humans) working in this repo. Read this
before changing code. If a rule here conflicts with a request, say so instead
of silently breaking the rule.

## What this is

A customer-support agent: LangGraph graph behind FastAPI, OpenAI for models,
PostgreSQL (pgvector) for state + knowledge base + cache, deployed on Azure
Container Apps via Bicep. Start reading at `src/support_agent/agent/graph.py`.

## Commands

| Task | Command |
|---|---|
| Install | `make install` |
| Everything CI runs | `make check` (ruff, mypy --strict, pytest) |
| Tests incl. real Postgres | `docker compose up -d db && make test-db` |
| One test | `uv run pytest tests/integration/test_graph_flows.py -k refund` |
| Run locally | `make run` (needs `OPENAI_API_KEY` in `.env`) |
| Re-render graph diagram | `make graph` |
| Evals (real API, costs money) | `make evals` |

A change is done when `make check` passes, `make test-db` passes if you touched
SQL or persistence, and new behaviour has a test.

## Architecture rules (do not break)

1. **Dependencies point inward:** `api -> services -> agent -> (llm, rag, integrations, persistence) -> core`.
   `agent/` never imports FastAPI. Only `llm/factory.py` knows the provider is OpenAI.
2. **Every LLM call goes through `AgentDependencies`.** Never construct `ChatOpenAI`
   inside a node. That's what keeps the graph testable with fakes.
3. **Choose implementations only in `bootstrap.py`.** No `if settings.environment == ...` elsewhere.
4. **Config only via `core/config.py`.** Never read `os.environ` anywhere else.

## LangGraph rules (each one prevents a real bug)

- **State holds plain values only:** str, int, float, bool, None, list, dict, and
  LangChain messages. Store enums as `.value`. `test_checkpoint_safety.py`
  enforces this; custom types break paused conversations on library upgrades.
- **No side effects before `interrupt()` in the same node.** The node re-runs from
  the top on resume. Put the side effect in an earlier node (see `create_ticket`
  vs `await_human`).
- **Side-effecting calls carry idempotency keys** (`tool_call_id` for tools,
  `thread_id:message_count` for tickets).
- **Per-turn fields are reset in `state.new_turn()`.** Adding a per-turn field means adding its reset there.
- **Every customer-facing LLM answer must route through `guard_output`.**
- **Every rejected tool call still gets a `ToolMessage`**, or the next OpenAI call fails.

## Domain rules

- Money is `Decimal`, never `float` (convert at the tool boundary).
- Tools get `customer_id` via `InjectedState`, never as an LLM-visible argument.
  Missing and not-yours orders return the same response.
- Never log raw customer text; pass it through `redact_pii()` first. Never log secrets.
- Analytics writes (usage, feedback, cache) must never fail a customer's turn: catch, log, continue.

## How to add things

- **A prompt change:** edit `agent/prompts.py`, bump `PROMPT_VERSION` (this also
  invalidates the semantic cache), run `make evals` and compare with the last baseline.
- **A tool:** add it in `agent/tools.py` with a Google-style docstring (it becomes
  the schema). If it changes data, add a policy in `agent/policies.py` and decide
  whether it needs approval. Add a graph-flow test.
- **A node:** a `make_<name>(deps)` factory in `agent/nodes/`, wired in `graph.py`,
  routed by a pure function in `edges.py` with a unit test. Then `make graph`.
- **A SQL change:** a new numbered file in `persistence/sql/`. Never edit an applied
  migration. Changing the embedding model or dimensions needs a migration plus a re-ingest.
- **A new external service:** a Protocol + an HTTP implementation + an in-memory
  fake in `integrations/`, wired in `bootstrap.py`.

## Testing conventions

- Fakes, not mocks: pass `RunnableLambda`s or fake classes through `tests/fakes.py::make_deps`.
  Don't `patch()` internals.
- A test must be able to fail. Check that it goes red before trusting it green.
- Never call real APIs from `tests/`. Real-model behaviour belongs in `evals/`.

## Don'ts

- Don't add dependencies without a reason in the PR description.
- Don't put secrets in code, tests, Bicep defaults or logs.
- Don't widen `Settings(extra="ignore")` reliance: removing a setting must also
  update `.env.example`, Bicep env vars and docs (stale keys are silently ignored).
