# ADR 0005: Human-in-the-loop via interrupts

**Status:** accepted

## Context
Some actions (large refunds) need human approval, and some conversations need a human to take over. Either can take minutes to hours, across deploys.

## Decision
Use LangGraph `interrupt()` with the Postgres checkpointer. Staff resume with `POST /v1/threads/{id}/resume`, sending a typed `ApprovalDecision` or `HumanReply`. The service checks that the decision type matches the pending interrupt.

Rules we follow because of interrupt semantics (the node **re-executes from the top** on resume):
- No side effects before `interrupt()` in the same node. Ticket creation is its own node, before `await_human`.
- Side-effecting calls carry idempotency keys anyway (`thread_id:message_count` for tickets, `tool_call_id` for refunds).
- On rejection, every pending tool call gets a `ToolMessage`, keeping the message history valid for the OpenAI API.

## Consequences
- **+** No queues or extra state machine. A paused conversation is just a checkpoint, and any replica can resume it.
- **+** Approvals are recorded in the append-only `audit_log`.
- **−** Pending approvals don't expire on their own. A real deployment would add an SLA sweep job.
