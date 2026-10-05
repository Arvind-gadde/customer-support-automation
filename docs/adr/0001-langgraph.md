# ADR 0001: LangGraph over a hand-rolled agent loop

**Status:** accepted

## Context
A support agent needs branching (FAQ vs account vs handoff), loops (tool calls, query rewrites), pauses lasting hours (approvals, human replies) that must survive deploys and replica restarts, and per-step observability.

## Decision
Model the agent as an explicit LangGraph `StateGraph` with a Postgres checkpointer.

## Consequences
- **+** Control flow is explicit, reviewable code (`graph.py`), not emergent from a prompt. Routing functions are pure and unit-tested.
- **+** `interrupt()` plus the checkpointer give durable human-in-the-loop for free. A paused thread resumes on any replica.
- **+** Bounded loops (`max_query_rewrites`, `max_tool_iterations`, `recursion_limit`) are enforced in code.
- **−** LangGraph semantics have sharp edges. Nodes re-run on resume, so side effects must sit before interrupts in a *separate* node. Checkpointed state must be serialisable without custom types. Both are documented and covered by tests.
- **−** We're coupled to LangGraph's checkpoint schema. Upgrades go through the Postgres integration test first.

## Alternatives considered
- **Prebuilt ReAct agent** (`create_react_agent`): fine for demos, but it gives no place for CRAG, approval gates or confidence-gated routing.
- **Hand-rolled loop:** we'd end up re-implementing checkpointing, resumption and streaming, badly.
