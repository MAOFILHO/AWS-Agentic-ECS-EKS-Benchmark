"""FastAPI service wrapping the triage agent. Run: uvicorn workload.app:app"""

from fastapi import FastAPI
from pydantic import BaseModel

from workload.agent import build_graph, make_llm


class Ticket(BaseModel):
    text: str


class Triage(BaseModel):
    category: str
    reply: str


def create_app(llm=None) -> FastAPI:
    graph = build_graph(llm or make_llm())
    app = FastAPI(title="triage-workload")

    @app.post("/triage", response_model=Triage)
    async def triage(ticket: Ticket) -> Triage:
        result = await graph.ainvoke({"text": ticket.text})
        return Triage(category=result["category"], reply=result["reply"])

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz():
        return {"status": "ready"}

    return app


app = create_app()
