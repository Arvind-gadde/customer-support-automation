# ADR 0007: Semantic cache correctness rules

**Status:** accepted

## Context
FAQ questions repeat heavily ("return window?", "COD charges?"). Each FAQ turn costs about 4 LLM calls. A semantic cache can serve repeats from one embedding call, but a wrong hit is worse than no cache: it is a confident wrong answer.

## Decision
Cache in Postgres (pgvector, HNSW), with these rules:

1. **FAQ intent only.** Account answers contain one customer's data and are never cached.
2. **First turn only.** Follow-ups ("and for electronics?") depend on context; they are never looked up or stored.
3. **Only fully vetted answers:** stored after the groundedness check *and* the output guard, and never if the guard modified the answer.
4. **High threshold** (cosine similarity 0.95), configurable.
5. **Namespaced by `PROMPT_VERSION` + chat model**, so a prompt or model change starts a fresh cache.
6. **TTL** (24 h) plus a full wipe by ingestion whenever the knowledge base changes.
7. **Never on the critical path:** lookup and store failures are logged and the turn proceeds.

The cache runs *after* intent routing, so its embedding call is only spent on FAQ turns.

## Consequences
- **+** Repeat FAQs skip retrieval, reranking, grading, generation and verification.
- **+** Every rule is enforced in an edge function or node and covered by a test.
- **−** Paraphrases below 0.95 still miss. Lowering the threshold trades correctness for hit rate and should be decided with eval data, not intuition.
- **−** HNSW plus a `WHERE namespace` filter is post-filtered; fine at this scale. Very large multi-namespace caches would want partitioning.
