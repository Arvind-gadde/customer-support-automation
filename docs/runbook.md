# Runbook

Every log line is JSON with `request_id` and (inside a conversation) `thread_id`. Customers or support staff can quote the `X-Request-ID` from any response. Traces are in App Insights with one span per graph node (`agent.node.<name>`).

## Useful queries (Log Analytics / App Insights)

```kusto
// One conversation end-to-end (structlog events are JSON in the message body)
traces | where message has "<thread-id>" | order by timestamp asc

// Slowest graph nodes (p95), last hour - node spans land in `dependencies` (InProc)
dependencies
| where timestamp > ago(1h) and name startswith "agent.node."
| summarize p95=percentile(duration, 95), calls=count() by name | order by p95 desc

// Why are we handing off? Parse the reason out of the JSON log body
traces | where message has "handoff.ticket_created"
| extend reason = extract(@"reason['\"]?:\s*['\"]([a-z_]+)", 1, message)
| summarize count() by reason, bin(timestamp, 1h)

// LLM availability
traces | where message has "llm.unavailable" | summarize count() by bin(timestamp, 5m)

// Cache hit rate
traces | where message has "cache.hit" or message has "cache.miss"
| summarize hits=countif(message has "cache.hit"), total=count() by bin(timestamp, 1h)
| extend hit_rate = todouble(hits) / total
```

```sql
-- Postgres: cost per day and model
SELECT date_trunc('day', created_at) AS day, model,
       sum(cost_usd) AS usd, sum(input_tokens + output_tokens) AS tokens
FROM llm_usage GROUP BY 1, 2 ORDER BY 1 DESC;

-- Postgres: satisfaction by prompt version (online quality signal)
SELECT prompt_version, count(*) AS votes,
       round(avg((rating = 1)::int) * 100, 1) AS pct_positive
FROM feedback GROUP BY 1 ORDER BY 1 DESC;
```

> If you want these as first-class `customDimensions` columns, add a small
> structlog processor that copies the event dict into the stdlib record's
> `extra` before the OTel handler sees it. That's a worthwhile follow-up; the
> text queries above work without it.

## Suggested alerts

| Signal | Threshold | Likely cause |
|---|---|---|
| `llm.unavailable` | > 5 in 5 min | OpenAI throttling, outage or budget cap (check the dashboard; set a fallback model) |
| HTTP 5xx rate | > 2% over 5 min | Check `request.unhandled_error` logs |
| `handoff.ticket_created` with reason `no_relevant_knowledge` | spike vs baseline | KB gap or broken ingestion |
| `rag.ungrounded_answer` | spike | Prompt or model regression; run `make evals` |
| `guardrail.blocked` | spike | Abuse campaign; consider WAF/APIM rate limits |
| `output_guard.prompt_leak` or `output_guard.moderation_flagged` | any | Jailbreak attempt that got past input guards; review the thread |
| Daily `llm_usage` cost | > 2x 7-day average | Cache wiped, tool loops, or abuse |
| Feedback positive rate | drops > 10 points after a deploy | Prompt/model regression |
| Readiness failures | any replica | Postgres connectivity |

## Common incidents

**Customers get 503 "assistant is temporarily busy".** OpenAI is throttling (429) or degraded. Check status.openai.com and the project's rate limits and budget in the OpenAI dashboard (a hit budget cap looks like an outage). Configure `OPENAI_FALLBACK_MODEL`. Conversations aren't lost; customers can resend.

**Cost spike.** Query `llm_usage` (below) by hour and model. Usual causes: the semantic cache was wiped (KB re-ingest or a `PROMPT_VERSION` bump), a tool loop hitting the 5-iteration cap, or traffic growth.

**Thumbs-down spike.** Group `feedback` by `prompt_version`. A spike right after a deploy points at the prompt change; roll back and compare evals.

**Readiness probe failing, replicas out of rotation.** Postgres is unreachable. Check server status, connection count (`db_pool_max_size × replicas` must stay under the server's `max_connections`), and firewall rules.

**Approvals stuck.** List threads awaiting approval from the staff console, or query checkpoints. A pending approval never expires on its own. Resume it via `POST /v1/threads/{id}/resume`.

**Answers got worse after a deploy.** Compare `PROMPT_VERSION` and the model deployment against the last good eval in the `evals` workflow artifacts. Roll back with:
`az containerapp revision list -n <app> -g <rg> -o table`, then
`az containerapp update -n <app> -g <rg> --image <previous-image>`.

## Rotating secrets

- **API keys:** keys are comma-separated, so rotation has no downtime. Add the new key alongside the old (`az keyvault secret set`), restart the revision (`az containerapp revision restart`), migrate clients, remove the old key, restart again.
- **Postgres password:** update the server password and the `database-url` secret together, then restart.
- **OpenAI key:** create a new project key, `az keyvault secret set --name openai-api-key`, restart the revision, verify `/health/ready` and a test chat, then revoke the old key in the OpenAI dashboard.
