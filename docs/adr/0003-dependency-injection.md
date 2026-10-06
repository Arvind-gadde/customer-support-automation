# ADR 0003: Dependency injection for every LLM call

**Status:** accepted

## Context
Agent code that instantiates `ChatOpenAI` inside nodes can only be tested by mocking HTTP or patching. Those tests are brittle, slow and usually skipped.

## Decision
Nodes receive an `AgentDependencies` object holding `Runnable`s (router, grader, rewriter, groundedness judge, answerer, tool-bound agent) and `Protocol`s (retriever, ticketing, order service). Only `llm/factory.py` knows about the provider (OpenAI). `bootstrap.py` is the single composition root.

## Consequences
- **+** The full graph runs in tests with `RunnableLambda` fakes: the full suite in about 2 seconds, no network, deterministic.
- **+** Swapping provider or model, or adding a fallback deployment, touches one file.
- **+** Each internal LLM call has a typed contract (`IntentDecision`, `RelevanceGrade`, …).
- **−** Fakes can't tell you whether the *model* behaves well. That is what `evals/` is for (see the testing strategy in the README).
