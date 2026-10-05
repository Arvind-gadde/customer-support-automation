from langchain_core.messages import AIMessage
from langgraph.graph import END

from support_agent.agent import edges
from support_agent.agent.policies import requires_approval
from support_agent.agent.state import HandoffReason, Intent


def test_refund_approval_threshold() -> None:
    call = {"name": "issue_refund", "args": {"amount": 50}}
    assert not requires_approval(call, 50)
    assert requires_approval({**call, "args": {"amount": 50.01}}, 50)
    assert requires_approval({**call, "args": {"amount": "lots"}}, 50)  # unparseable => human
    assert not requires_approval({"name": "lookup_order", "args": {}}, 50)


def test_after_classify_routes_every_intent() -> None:
    assert edges.after_classify({"intent": Intent.FAQ}) == "retrieve"
    assert edges.after_classify({"intent": Intent.ACCOUNT}) == "account_agent"
    assert edges.after_classify({"intent": Intent.SMALLTALK}) == "smalltalk_reply"
    assert edges.after_classify({"intent": Intent.HUMAN}) == "create_ticket"
    assert edges.after_classify({}) == "create_ticket"  # unknown => safe default


def test_after_grade() -> None:
    doc = {"id": "1", "source": "s", "title": "t", "content": "c", "score": 1.0}
    assert edges.after_grade({"documents": [doc]}) == "generate_answer"
    assert edges.after_grade({"documents": []}) == "rewrite_query"
    assert (
        edges.after_grade({"documents": [], "handoff_reason": HandoffReason.NO_RELEVANT_KNOWLEDGE})
        == "create_ticket"
    )


def test_after_account_agent() -> None:
    with_tools = AIMessage(content="", tool_calls=[{"name": "x", "args": {}, "id": "1"}])
    assert edges.after_account_agent({"messages": [with_tools]}) == "review_tool_calls"
    assert edges.after_account_agent({"messages": [AIMessage(content="done")]}) == END
