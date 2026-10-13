"""Support-ticket triage agent: classify, retrieve, draft. One LLM call per ticket."""

import asyncio
import os
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

KNOWLEDGE_BASE = {
    "billing": "Refunds for duplicate charges post within 5 business days.",
    "technical": "Most errors clear after signing out, clearing the cache and signing in again.",
    "account": "Passwords reset from the sign-in page via the 'Forgot password' link.",
    "general": "Our support team answers every ticket within one business day.",
}

KEYWORDS = {
    "billing": ("charge", "invoice", "refund", "payment", "bill"),
    "technical": ("error", "crash", "bug", "slow", "broken"),
    "account": ("password", "login", "sign in", "account", "locked"),
}


DEFAULT_BEDROCK_MODEL_ID = "amazon.nova-micro-v1:0"


class MockLLM:
    """Fake LLM with a fixed latency, so a run measures platform overhead only."""

    def __init__(self, latency_ms: int = 0):
        self.latency_ms = latency_ms

    async def complete(self, prompt: str) -> str:
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000)
        return "Thanks for reaching out. " + prompt.rsplit("Context: ", 1)[-1]


class BedrockLLM:
    """Real Bedrock call through the Converse API."""

    def __init__(self, model_id: str, client):
        self.model_id = model_id
        self.client = client

    async def complete(self, prompt: str) -> str:
        resp = await asyncio.to_thread(
            self.client.converse,
            modelId=self.model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
        )
        return resp["output"]["message"]["content"][0]["text"]


def make_llm(env=os.environ, client=None):
    if env.get("LLM_MODE", "mock") == "bedrock":
        if client is None:
            import boto3

            client = boto3.client("bedrock-runtime")
        return BedrockLLM(env.get("BEDROCK_MODEL_ID") or DEFAULT_BEDROCK_MODEL_ID, client)
    return MockLLM(int(env.get("MOCK_LATENCY_MS", "0")))


class TriageState(TypedDict, total=False):
    text: str
    category: str
    context: str
    reply: str


def classify(state: TriageState) -> TriageState:
    text = state["text"].lower()
    for category, words in KEYWORDS.items():
        if any(w in text for w in words):
            return {"category": category}
    return {"category": "general"}


def retrieve(state: TriageState) -> TriageState:
    return {"context": KNOWLEDGE_BASE[state["category"]]}


def build_graph(llm):
    async def draft(state: TriageState) -> TriageState:
        prompt = (
            f"Write a short support reply to this {state['category']} ticket.\n"
            f"Ticket: {state['text']}\nContext: {state['context']}"
        )
        return {"reply": await llm.complete(prompt)}

    graph = StateGraph(TriageState)
    graph.add_node("classify", classify)
    graph.add_node("retrieve", retrieve)
    graph.add_node("draft", draft)
    graph.add_edge(START, "classify")
    graph.add_edge("classify", "retrieve")
    graph.add_edge("retrieve", "draft")
    graph.add_edge("draft", END)
    return graph.compile()
