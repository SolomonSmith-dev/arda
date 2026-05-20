"""Finrod -- retriever-tier RAG agent built on LlamaIndex.

Wraps a `VectorStoreIndex` and exposes three actions through the
`BaseAgent.run` contract:

  - ``ingest`` -- index a document. Splits on LlamaIndex's chunker
    (sentence-level), attaches `doc_id` + caller-supplied metadata,
    and reports the number of nodes inserted.
  - ``query``  -- retrieves top-k semantically similar chunks and
    synthesizes a grounded answer with the configured LLM.
  - ``stats``  -- returns the node count currently held by the index's
    docstore.

`forget(predicate)` complements those actions for callers that need
metadata-filtered deletion (e.g. TomBombadil's per-viewer memory).

Components (LLM, embedding model, vector store) are injected via the
constructor so tests can wire deterministic mocks. The defaults come
from the factory modules (`llm.py`, `embeddings.py`, `store.py`) and
honor `settings.use_mock_llm` / `settings.mock_embedder_enabled`.
"""

from __future__ import annotations

from typing import Any, ClassVar

from agents.base import BaseAgent
from agents.finrod.embeddings import build_embed_model
from agents.finrod.llm import build_llm
from agents.finrod.store import build_vector_store
from core.config import Tier
from core.logging import get_logger
from core.models import AgentResult, AgentTask, TaskStatus

log = get_logger("agents.finrod.agent")

DEFAULT_TOP_K = 5


class Finrod(BaseAgent):
    """LlamaIndex-backed retriever agent.

    The constructor accepts injectable LLM / embedding / vector_store
    components; defaults are pulled from the factory modules so tests
    can override them without touching real APIs or downloading models.
    """

    tier: ClassVar[Tier] = "retriever"
    name: ClassVar[str] = "finrod"

    def __init__(
        self,
        *,
        llm: Any | None = None,
        embed_model: Any | None = None,
        vector_store: Any | None = None,
    ):
        from llama_index.core import StorageContext, VectorStoreIndex

        self._llm = llm if llm is not None else build_llm()
        self._embed_model = embed_model if embed_model is not None else build_embed_model()
        self._vector_store = (
            vector_store if vector_store is not None else build_vector_store()
        )

        storage_context = StorageContext.from_defaults(vector_store=self._vector_store)
        # Empty index; documents arrive via `_ingest`. Components are
        # passed explicitly so we don't mutate LlamaIndex's `Settings`
        # global -- multiple Finrod instances (e.g. concurrent tests)
        # stay isolated.
        self._index = VectorStoreIndex(
            nodes=[],
            storage_context=storage_context,
            embed_model=self._embed_model,
        )

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
                    result={"chunk_count": self.node_count()},
                )
            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=TaskStatus.FAILED,
                error=f"unknown action: {action}",
            )
        except Exception as e:  # noqa: BLE001
            log.error("finrod_run_failed", agent_task_id=task.task_id, exception=str(e))
            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=TaskStatus.FAILED,
                error=str(e),
            )

    async def _ingest(self, task: AgentTask) -> AgentResult:
        from llama_index.core import Document

        doc_id = task.payload.get("doc_id")
        text = task.payload.get("text")
        if not doc_id or not text:
            return AgentResult(
                task_id=task.task_id,
                agent=self.name,
                status=TaskStatus.FAILED,
                error="ingest requires payload.doc_id and payload.text",
            )

        # `doc_id` is preserved as the Document's stable id_ so chunks
        # inserted from the same document share a `ref_doc_id` --
        # making cleanup (delete_ref_doc / forget) deterministic.
        # `excluded_embed_metadata_keys` prevents LlamaIndex from
        # prepending metadata to the text used for embedding: we
        # already filter on metadata after retrieval, so embedding
        # over pure content keeps semantic similarity honest.
        metadata = {"doc_id": doc_id, **(task.payload.get("metadata") or {})}
        document = Document(
            text=text,
            id_=doc_id,
            metadata=metadata,
            excluded_embed_metadata_keys=list(metadata.keys()),
            excluded_llm_metadata_keys=list(metadata.keys()),
        )

        before = self.node_count()
        self._index.insert(document)
        after = self.node_count()
        chunks_ingested = after - before

        log.info("finrod_ingest", doc_id=doc_id, chunks=chunks_ingested)
        return AgentResult(
            task_id=task.task_id,
            agent=self.name,
            status=TaskStatus.COMPLETED,
            result={"doc_id": doc_id, "chunks_ingested": chunks_ingested},
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

        if self.node_count() == 0:
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

        query_engine = self._index.as_query_engine(
            llm=self._llm,
            similarity_top_k=top_k,
        )
        response = await query_engine.aquery(question)
        answer = str(response).strip() or "No relevant context found."
        sources = _sources_from_response(response)

        log.info(
            "finrod_query",
            question_length=len(question),
            retrieved=len(sources),
        )

        return AgentResult(
            task_id=task.task_id,
            agent=self.name,
            status=TaskStatus.COMPLETED,
            result={
                "question": question,
                "answer": answer,
                "sources": sources,
            },
        )

    def node_count(self) -> int:
        return len(self._index.docstore.docs)

    async def forget(self, predicate: dict[str, Any]) -> int:
        """Remove every node whose metadata is a superset of `predicate`.

        Used by TomBombadil's `memory.forget_facts` to scrub all facts
        for a given viewer. Returns the number of nodes deleted. A
        non-empty predicate that matches nothing returns 0; an empty
        predicate is a no-op (we never wipe the whole index).
        """
        if not predicate:
            return 0

        matching_ids = [
            node.node_id
            for node in self._index.docstore.docs.values()
            if all(node.metadata.get(k) == v for k, v in predicate.items())
        ]
        if not matching_ids:
            return 0

        self._index.delete_nodes(matching_ids, delete_from_docstore=True)
        log.info("finrod_forget", predicate=predicate, deleted=len(matching_ids))
        return len(matching_ids)


def _sources_from_response(response: Any) -> list[dict[str, Any]]:
    """Project LlamaIndex's `source_nodes` into Finrod's stable
    `{id, text, score, metadata}` envelope. Score is coerced to float
    (LlamaIndex returns `Optional[float]`); a missing score becomes 0.0
    so JSON serialization downstream stays predictable.
    """
    out: list[dict[str, Any]] = []
    for n in getattr(response, "source_nodes", []) or []:
        node = n.node
        out.append(
            {
                "id": node.node_id,
                "text": node.get_content(),
                "score": float(n.score) if n.score is not None else 0.0,
                "metadata": dict(node.metadata or {}),
            }
        )
    return out
