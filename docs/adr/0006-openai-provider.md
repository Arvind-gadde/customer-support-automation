# ADR 0006: OpenAI API as the model provider

**Status:** accepted (supersedes the original Azure OpenAI choice)

## Context
The service was first built on Azure OpenAI with managed-identity auth. We moved to the OpenAI API directly: new models typically arrive there first, there are no per-region model/quota constraints or deployment resources to manage, and the moderation endpoint is available without extra setup.

## Decision
- `ChatOpenAI` / `OpenAIEmbeddings` / `openai.AsyncOpenAI` (moderation), constructed only in `llm/factory.py` and `bootstrap.py`.
- The key is a **project-scoped** key with a **budget limit** in the OpenAI dashboard, stored in Key Vault and injected as a Container Apps secret reference. Evals use a separate project key.
- `OPENAI_BASE_URL` lets the same code talk to an internal LLM gateway or any OpenAI-compatible endpoint.
- Hosting stays on Azure (Container Apps, Postgres, Key Vault, App Insights).

## Consequences
- **+** One file changes to swap providers; the graph, tests and API are untouched (this migration proved it: no node changed).
- **+** Fewer Azure resources: no OpenAI account, deployments or RBAC roles.
- **−** We now hold a long-lived secret (the API key) instead of using managed identity. Mitigations: Key Vault, project scoping, budget caps, rotation runbook.
- **−** Data residency: prompts are processed by OpenAI, not inside our Azure region. That needs sign-off for regulated data; the Azure path remains a one-file change back.
- **−** Rate limits are per organisation/project rather than per deployment; `OPENAI_FALLBACK_MODEL` and the 503 path cover throttling.
