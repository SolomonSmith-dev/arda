from __future__ import annotations

from typing import ClassVar

from agents.base import BaseAgent
from agents.finrod.embeddings import Embedder, get_embedder
from agents.finrod.ingest import ingest_text
from agents.finrod.store import VectorStore, get_store
from core.logging import get_logger
from core.models import AgentResult, AgentTask, TaskStatus

log = get_logger("agents.finrod.agent")

DEFAULT_TOP_K = 5


def _build_llm():
    from core.config import settings

    if settings.use_mock_llm:
        from agents._mock_llm import MockLLM
        return MockLLM(model=settings.retriever_model)
    from langchain_groq import ChatGroq
    return ChatGroq(
        model=settings.retriever_model,
        api_key=settings.groq_api_key,
        temperature=0.2,
    )


def _build_prompt(question: str, chunks: list) -> str:
    context = "\n\n".join(f"[{i+1}] {c.text}" for i, c in enumerate(chunks))
    return (
        f"Answer the question using ONLY the context below. "
        f"If the context is insufficient, say so.\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {question}\n\nAnswer:"
    )


class Finrod(BaseAgent):
    """Retriever (RAG) agent. Ingests text into a vector store and
    answers queries by embedding the question, retrieving top-k
    chunks, and synthesizing an answer with the configured LLM.

    Store and embedder are injected so tests can use InMemoryStore +
    MockEmbedder without touching Milvus or downloading models. The
    real production path uses get_store() (Milvus with in-memory
    fallback) and get_embedder() (sentence-transformers in non-mock).
    """

    tier: ClassVar[str] = "retriever"
    name: ClassVar[str] = "finrod"

    def __init__(
        self,
        store: VectorStore | None = None,
        embedder: Embedder | None = None,
    ):
        self.store: VectorStore = store if store is not None else get_store()
        self.embedder: Embedder = embedder if embedder is not None else get_embedder()
        self._llm = _build_llm()

    async def run(self, task: AgentTask) -> AgentResult:
        action = task.payload.get("action", "query")

        try:
            if action == "ingest":
                return await self._ingest(task)
            if action == "query":
                return await self._query(task)
            if action == "stats":
                return AgentResult(
                    task_id=task.task_id,
                    agent=self.name,
                    status=TaskStatus.COMPLETED,
                    result={"chunk_count": self.store.count()},
                )
            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=TaskStatus.FAILED,
                error=f"unknown action: {action}",
            )
        except Exception as e:
            log.error("finrod_run_failed", agent_task_id=task.task_id, exception=str(e))
            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=TaskStatus.FAILED,
                error=str(e),
            )

    async def _ingest(self, task: AgentTask) -> AgentResult:
        doc_id = task.payload.get("doc_id")
        text = task.payload.get("text")
        if not doc_id or not text:
            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=TaskStatus.FAILED,
                error="ingest requires payload.doc_id and payload.text",
            )

        count = ingest_text(self.store, self.embedder, doc_id, text, task.payload.get("metadata"))
        log.info("finrod_ingest", doc_id=doc_id, chunks=count)
        return AgentResult(
            task_id=task.task_id,
            agent=self.name,
            status=TaskStatus.COMPLETED,
            result={"doc_id": doc_id, "chunks_ingested": count},
        )

    async def _query(self, task: AgentTask) -> AgentResult:
        question = task.payload.get("message") or task.payload.get("question")
        if not question:
            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=TaskStatus.FAILED,
                error="query requires payload.message or payload.question",
            )

        top_k = int(task.payload.get("top_k", DEFAULT_TOP_K))

        query_vec = self.embedder.embed([question])[0]
        chunks = self.store.search(query_vec, top_k=top_k)

        if not chunks:
            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=TaskStatus.COMPLETED,
                result={
                    "question": question,
                    "answer": "No relevant context found.",
                    "sources": [],
                },
            )

        prompt = _build_prompt(question, chunks)
        response = self._llm.invoke(prompt)
        answer = response.content if hasattr(response, "content") else str(response)

        log.info("finrod_query", question_length=len(question), retrieved=len(chunks))

        return AgentResult(
            task_id=task.task_id,
            agent=self.name,
            status=TaskStatus.COMPLETED,
            result={
                "question": question,
                "answer": answer,
                "sources": [
                    {"id": c.id, "text": c.text, "score": c.score, "metadata": c.metadata}
                    for c in chunks
                ],
            },
        )
