"""KBIndex — semantic + deterministic knowledge-base search.

Search strategy (KB-01 / KB-02):
  1. Semantic search via fastembed (ONNX, CPU) + FAISS-cpu.
     Falls back to deterministic if fastembed / faiss not installed
     (macOS x86_64 dev machines) or if the top score < threshold.
  2. Deterministic fallback: exact lookup in ``LoadedPack.signature_index``.

fastembed and faiss-cpu are in the optional ``[ml]`` dependency group.
"""

from __future__ import annotations

import logging
from typing import TypedDict

from libs.common.pack_loader import LoadedPack

log = logging.getLogger(__name__)

_BGE_MODEL = "BAAI/bge-small-en-v1.5"
_DEFAULT_THRESHOLD = 0.60


class KBSearchResult(TypedDict):
    kb_id: str
    title: str
    body_md: str
    remediation_step_ids: list[str]
    score: float
    via: str  # "semantic" | "deterministic"


class KBIndex:
    """Builds an in-memory index over a pack's KB articles."""

    def __init__(
        self,
        pack: LoadedPack,
        confidence_threshold: float = _DEFAULT_THRESHOLD,
    ) -> None:
        self._pack = pack
        self._threshold = confidence_threshold
        self._has_semantic = False
        self._article_ids: list[str] = []
        self._try_build_semantic()

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def search(
        self,
        signature: str,
        fallback_kb_id: str | None = None,
    ) -> KBSearchResult | None:
        """Return the best-matching KB article for ``signature``.

        Args:
            signature:      The error signature or log extract extracted by the agent.
            fallback_kb_id: KB article id to use when both semantic search and
                            signature_index fail (e.g. scenario's own kb_article_ref).
        """
        # 1. Semantic
        if self._has_semantic:
            result = self._semantic_search(signature)
            if result is not None and result["score"] >= self._threshold:
                return result

        # 2. Deterministic — exact signature index lookup
        kb_id = self._pack.signature_index.get(signature)

        # 3. Caller-supplied fallback
        if kb_id is None:
            kb_id = fallback_kb_id

        if kb_id and kb_id in self._pack.kb_articles:
            article = self._pack.kb_articles[kb_id]
            return KBSearchResult(
                kb_id=kb_id,
                title=article.title,
                body_md=article.body_md,
                remediation_step_ids=article.remediation_step_ids,
                score=1.0,
                via="deterministic",
            )

        return None

    @property
    def has_semantic(self) -> bool:
        return self._has_semantic

    # ──────────────────────────────────────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────────────────────────────────────

    def _try_build_semantic(self) -> None:
        try:
            import faiss
            import numpy as np
            from fastembed import TextEmbedding  # type: ignore[import]

            self._embedder = TextEmbedding(_BGE_MODEL)
            self._article_ids = list(self._pack.kb_articles.keys())
            texts = [f"{a.title}\n{a.body_md[:500]}" for a in self._pack.kb_articles.values()]
            embeddings = np.array(list(self._embedder.embed(texts)), dtype="float32")
            faiss.normalize_L2(embeddings)
            dim = embeddings.shape[1]
            self._faiss_index = faiss.IndexFlatIP(dim)
            self._faiss_index.add(embeddings)
            self._np = np
            self._faiss = faiss
            self._has_semantic = True
            log.info("KBIndex: semantic search ready (%d articles)", len(self._article_ids))
        except (ImportError, Exception) as exc:
            log.info("KBIndex: semantic search unavailable (%s); using deterministic fallback", exc)

    def _semantic_search(self, query: str) -> KBSearchResult | None:
        np = self._np
        query_emb = np.array(list(self._embedder.embed([query])), dtype="float32")
        self._faiss.normalize_L2(query_emb)
        scores, indices = self._faiss_index.search(query_emb, k=1)
        score = float(scores[0][0])
        idx = int(indices[0][0])
        if idx < 0 or idx >= len(self._article_ids):
            return None
        kb_id = self._article_ids[idx]
        article = self._pack.kb_articles[kb_id]
        return KBSearchResult(
            kb_id=kb_id,
            title=article.title,
            body_md=article.body_md,
            remediation_step_ids=article.remediation_step_ids,
            score=score,
            via="semantic",
        )
