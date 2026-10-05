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
```

> If you want these as first-class `customDimensions` columns, add a small
> structlog processor that copies the event dict into the stdlib record's
> `extra` before the OTel handler sees it. That's a worthwhile follow-up; the
> text queries above work without it.

## Suggested alerts

| Signal | Threshold | Likely cause |
|---|---|---|
| `llm.unavailable` | > 5 in 5 min | Azure OpenAI throttling (raise TPM quota or add a fallback deployment) |
| HTTP 5xx rate | > 2% over 5 min | Check `request.unhandled_error` logs |
| `handoff.ticket_created` with reason `no_relevant_knowledge` | spike vs baseline | KB gap or broken ingestion |
| `rag.ungrounded_answer` | spike | Prompt or model regression; run `make evals` |
| `guardrail.blocked` | spike | Abuse campaign; consider WAF/APIM rate limits |
| Readiness failures | any replica | Postgres connectivity |

## Common incidents

**Customers get 503 "assistant is temporarily busy".** Azure OpenAI is throttling (429) or degraded. Check the deployment's TPM utilisation in the portal and increase `chatCapacity`, or configure `AZURE_OPENAI_FALLBACK_DEPLOYMENT`. Conversations aren't lost; customers can resend.

**Readiness probe failing, replicas out of rotation.** Postgres is unreachable. Check server status, connection count (`db_pool_max_size × replicas` must stay under the server's `max_connections`), and firewall rules.

**Approvals stuck.** List threads awaiting approval from the staff console, or query checkpoints. A pending approval never expires on its own. Resume it via `POST /v1/threads/{id}/resume`.

**Answers got worse after a deploy.** Compare `PROMPT_VERSION` and the model deployment against the last good eval in the `evals` workflow artifacts. Roll back with:
`az containerapp revision list -n <app> -g <rg> -o table`, then
`az containerapp update -n <app> -g <rg> --image <previous-image>`.

## Rotating secrets

- **API keys:** keys are comma-separated, so rotation has no downtime. Add the new key alongside the old (`az keyvault secret set`), restart the revision (`az containerapp revision restart`), migrate clients, remove the old key, restart again.
- **Postgres password:** update the server password and the `database-url` secret together, then restart.
- **Azure OpenAI:** nothing to rotate. Access is via managed identity.
