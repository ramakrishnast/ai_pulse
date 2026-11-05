"""Text embeddings: a local sentence-transformers model, with a dependency-free fallback.

The fallback (hashed word uni/bi-grams + character trigrams, TF-weighted, L2-normalized)
keeps the app working where the model cannot be downloaded, and keeps tests fast.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading

import numpy as np

from .config import get_settings

log = logging.getLogger(__name__)


class Embedder:
    name = "base"
    dim = 0
    dedupe_threshold = 0.9
    cluster_threshold = 0.7

    def encode(self, texts: list[str]) -> np.ndarray:  # pragma: no cover - interface
        raise NotImplementedError


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9.\-+]*")
_STOP = set(
    "a an and are as at be by for from has have how in into is it its of on or that the this to "
    "was were will with what why new now you your we our their they after over about".split()
)


def _h(token: str, dim: int) -> tuple[int, float]:
    digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
    v = int.from_bytes(digest, "little")
    return v % dim, (1.0 if (v >> 63) & 1 else -1.0)


class HashingEmbedder(Embedder):
    name = "hashing"
    dedupe_threshold = 0.85
    cluster_threshold = 0.50

    def __init__(self, dim: int = 2048):
        self.dim = dim

    @staticmethod
    def _stem(w: str) -> str:
        for suf in ("ing", "ed", "es", "s"):
            if len(w) > len(suf) + 3 and w.endswith(suf):
                return w[: -len(suf)]
        return w

    def _features(self, text: str) -> dict[str, float]:
        # The title (text before the first ". ") counts double; snippets only add context.
        title, _, rest = text.lower().partition(". ")
        feats: dict[str, float] = {}
        for part, weight in ((title, 2.0), (rest, 0.6)):
            words = [w.strip(".-+") for w in _TOKEN_RE.findall(part)]
            words = [self._stem(w) for w in words if w and w not in _STOP]
            for w in words:
                feats["w:" + w] = feats.get("w:" + w, 0) + weight
            for a, b in zip(words, words[1:]):
                k = "b:" + "_".join(sorted((a, b)))
                feats[k] = feats.get(k, 0) + 0.5 * weight
        return feats

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for feat, weight in self._features(text).items():
                idx, sign = _h(feat, self.dim)
                out[row, idx] += sign * float(np.sqrt(weight))
            norm = np.linalg.norm(out[row])
            if norm > 0:
                out[row] /= norm
        return out


class SentenceTransformerEmbedder(Embedder):
    name = "sentence-transformers"
    dedupe_threshold = 0.90
    cluster_threshold = 0.70

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # heavy import, lazy

        self.model = SentenceTransformer(model_name, device="cpu")
        self.dim = int(self.model.get_sentence_embedding_dimension())
        self.model_name = model_name

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        vecs = self.model.encode(texts, batch_size=64, normalize_embeddings=True,
                                 show_progress_bar=False, convert_to_numpy=True)
        return vecs.astype(np.float32)


_lock = threading.Lock()
_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    with _lock:
        if _embedder is not None:
            return _embedder
        settings = get_settings()
        backend = settings.embedding_backend
        if backend in ("auto", "sentence-transformers"):
            try:
                _embedder = SentenceTransformerEmbedder(settings.embedding_model)
                log.info("embeddings: sentence-transformers %s", settings.embedding_model)
            except Exception as exc:
                if backend == "sentence-transformers":
                    raise
                log.warning("embeddings: falling back to hashing embedder (%s)", exc)
        if _embedder is None:
            _embedder = HashingEmbedder()
            log.info("embeddings: hashing fallback")
        return _embedder


def set_embedder(embedder: Embedder | None) -> None:
    """Override the global embedder (tests)."""
    global _embedder
    with _lock:
        _embedder = embedder


def thresholds(embedder: Embedder) -> tuple[float, float]:
    s = get_settings()
    return (
        s.dedupe_threshold if s.dedupe_threshold is not None else embedder.dedupe_threshold,
        s.cluster_threshold if s.cluster_threshold is not None else embedder.cluster_threshold,
    )


def to_bytes(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_bytes(blob: bytes | None, dim: int | None = None) -> np.ndarray | None:
    if not blob:
        return None
    vec = np.frombuffer(blob, dtype=np.float32)
    if dim is not None and vec.shape[0] != dim:
        return None  # embedded with a different backend; caller re-embeds
    return vec


def item_text(title: str, snippet: str) -> str:
    return f"{title}. {(snippet or '')[:300]}"
