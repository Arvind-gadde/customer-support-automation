# ADR 0004: Auth model (BFF + API keys, tools scoped by injected state)

**Status:** accepted

## Context
End users authenticate in many ways (app login, OTP, SSO). This service shouldn't own user identity. Separately, an LLM with tools must never be able to act on another customer's data, whatever the prompt says.

## Decision
1. **Service-to-service API keys with two roles.** A `client` key is used by the BFF, which has already authenticated the user and passes the verified `customer_id`. A `staff` key is used by the internal console and is the only role that can resume threads. Keys live in Key Vault, are compared in constant time, and multiple keys are supported for zero-downtime rotation.
2. **Thread ownership.** A thread is bound to the `customer_id` that created it. Other customers get 403 when sending and 404 when reading.
3. **Authorization lives in tools, not prompts.** `customer_id` reaches tools via LangGraph `InjectedState`, so it isn't part of the schema the model sees. The tool also verifies ownership, and "not found" and "not yours" give identical responses.

## Consequences
- **+** A successful prompt injection still can't exfiltrate another customer's orders. There's a test for exactly this.
- **−** The BFF is trusted to send the correct `customer_id`. If that trust boundary isn't acceptable, replace API keys with Entra ID / JWT validation in `core/security.py` and derive `customer_id` from token claims. Nothing else changes.
