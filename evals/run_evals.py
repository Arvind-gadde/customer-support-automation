"""Offline evals against the REAL OpenAI API (not run in the PR pipeline: costs tokens).

Unit tests prove the graph's *wiring*; evals measure the *model's behaviour*
inside it: routing accuracy, answer content, escalation and safety. Run on every
prompt or model change and compare against the last baseline in evals/results/.

    uv run python -m evals.run_evals [--min-pass-rate 0.9]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from support_agent.agent.graph import build_graph
from support_agent.agent.prompts import PROMPT_VERSION
from support_agent.agent.tools import build_tools
from support_agent.core.config import get_settings
from support_agent.core.logging import configure_logging
from support_agent.integrations.orders import InMemoryOrderService
from support_agent.integrations.ticketing import InMemoryTicketingClient
from support_agent.llm.factory import build_dependencies
from support_agent.llm.moderation import NoopModerator
from support_agent.rag.ingest import load_chunks
from support_agent.rag.reranker import PassthroughReranker
from support_agent.rag.retriever import InMemoryRetriever
from support_agent.rag.semantic_cache import NullSemanticCache
from support_agent.services.agent_service import AgentService
from support_agent.services.schemas import ChatRequest

HERE = Path(__file__).parent
KB = HERE.parent / "data" / "knowledge_base"


@dataclass
class CaseResult:
    id: str
    passed: bool
    latency_s: float
    intent: str | None
    status: str
    failures: list[str] = field(default_factory=list)


def _check(case: dict, intent: str | None, status: str, reply: str) -> list[str]:
    failures = []
    if case["expect_intent"] != "any" and intent != case["expect_intent"]:
        failures.append(f"intent={intent} expected={case['expect_intent']}")
    if case["expect_status"] != "any" and status != case["expect_status"]:
        failures.append(f"status={status} expected={case['expect_status']}")
    wanted = case.get("must_include_any") or []
    if wanted and not any(w.lower() in reply.lower() for w in wanted):
        failures.append(f"reply missing any of {wanted}")
    for banned in case.get("must_not_include", []):
        if banned.lower() in reply.lower():
            failures.append(f"reply leaked '{banned}'")
    return failures


async def run(min_pass_rate: float) -> int:
    settings = get_settings()
    configure_logging("WARNING", json_logs=False)
    deps = build_dependencies(
        settings,
        tools=build_tools(InMemoryOrderService()),
        retriever=InMemoryRetriever(load_chunks(KB)),
        reranker=PassthroughReranker(),
        cache=NullSemanticCache(),  # evals must measure the model, never the cache
        moderator=NoopModerator(),
        ticketing=InMemoryTicketingClient(),
    )
    service = AgentService(build_graph(deps, InMemorySaver()), settings)
    cases = [json.loads(line) for line in (HERE / "dataset.jsonl").read_text().splitlines() if line]

    results: list[CaseResult] = []
    for case in cases:
        start = time.perf_counter()
        turn = await service.chat(ChatRequest(customer_id="cust_123", message=case["message"]))
        elapsed = round(time.perf_counter() - start, 2)
        failures = _check(case, turn.intent, turn.status.value, turn.reply or "")
        results.append(
            CaseResult(case["id"], not failures, elapsed, turn.intent, turn.status.value, failures)
        )
        mark = "PASS" if not failures else "FAIL"
        print(f"[{mark}] {case['id']:<22} {elapsed:>5}s  {'; '.join(failures)}")

    pass_rate = sum(r.passed for r in results) / len(results)
    latencies = sorted(r.latency_s for r in results)
    p95 = latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))]
    print(f"\nprompt_version={PROMPT_VERSION} model={settings.openai_chat_model}")
    print(f"pass_rate={pass_rate:.0%}  p95_latency={p95}s  cases={len(results)}")

    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / f"{PROMPT_VERSION}.json").write_text(
        json.dumps(
            {
                "prompt_version": PROMPT_VERSION,
                "pass_rate": pass_rate,
                "p95_latency_s": p95,
                "results": [asdict(r) for r in results],
            },
            indent=2,
        )
    )
    return 0 if pass_rate >= min_pass_rate else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-pass-rate", type=float, default=0.9)
    sys.exit(asyncio.run(run(parser.parse_args().min_pass_rate)))


if __name__ == "__main__":
    main()
