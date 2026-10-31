"""Immutable, auditable BM25/Jaccard indexes with optional lexical MMR.

BM25 uses the positive Robertson IDF variant and unique query terms. MMR combines
max-normalized relevance and token-set Jaccard redundancy. Scores are algorithmic
ranking signals, never probabilities, evidence strength, or semantic entailment.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from manuscriptforge.utils.text import word_tokens

TOKENIZER_VERSION = "unicode-alphanumeric-hyphen-apostrophe-v1"
TOKENIZER_VERSIONS = {"unicode": TOKENIZER_VERSION, "legacy": "legacy-ascii-words-v1"}
SCHEMA_VERSION = "1.0"
TOKEN_RE = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tokenize(text: str, *, mode: Literal["unicode", "legacy"] = "unicode") -> tuple[str, ...]:
    """Case-fold Unicode words/numbers; keep internal ASCII hyphens/apostrophes."""
    if mode == "legacy":
        return tuple(word_tokens(text))
    if mode != "unicode":
        raise ValueError("Unknown tokenizer")
    return tuple(TOKEN_RE.findall(text.casefold().replace("’", "'").replace("−", "-")))


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("Metadata keys must be strings")
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError("Metadata must contain finite JSON values")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


@dataclass(frozen=True, slots=True)
class RetrievalRecord:
    id: str
    text: str
    source_id: str
    content_hash: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("A nonempty record ID is required")
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise ValueError("A nonempty source ID is required")
        if not isinstance(self.text, str):
            raise ValueError("Record text must be a string")
        if self.content_hash != text_sha256(self.text):
            raise ValueError(f"Record text hash mismatch: {self.id}")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("Metadata must be an object")
        object.__setattr__(self, "metadata", _freeze(self.metadata))

    @classmethod
    def from_text(
        cls, id: str, text: str, source_id: str, metadata: Mapping[str, Any] | None = None
    ) -> RetrievalRecord:
        return cls(
            id=id,
            text=text,
            source_id=source_id,
            content_hash=text_sha256(text),
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "source_id": self.source_id,
            "content_hash": self.content_hash,
            "metadata": _thaw(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class RetrievalFilter:
    """None means unrestricted; an explicitly empty collection matches no records."""

    source_ids: frozenset[str] | None = None
    content_hashes: frozenset[str] | None = None
    sections: frozenset[str] | None = None
    style_modes: frozenset[str] | None = None

    def __post_init__(self) -> None:
        for name in ("source_ids", "content_hashes", "sections", "style_modes"):
            value = getattr(self, name)
            if value is not None:
                if isinstance(value, str):
                    raise ValueError("Filter values must be collections of strings")
                items = tuple(value)
                if not all(isinstance(item, str) for item in items):
                    raise ValueError("Filter values must be collections of strings")
                object.__setattr__(self, name, frozenset(items))

    def matches(self, record: RetrievalRecord) -> bool:
        section = record.metadata.get("section", record.metadata.get("section_type"))
        style_mode = record.metadata.get("style_mode")
        return (
            (self.source_ids is None or record.source_id in self.source_ids)
            and (self.content_hashes is None or record.content_hash in self.content_hashes)
            and (self.sections is None or isinstance(section, str) and section in self.sections)
            and (
                self.style_modes is None
                or isinstance(style_mode, str)
                and style_mode in self.style_modes
            )
        )


@dataclass(frozen=True, slots=True)
class SearchHit:
    record_id: str
    source_id: str
    content_hash: str
    text: str
    metadata: Mapping[str, Any]
    score: float
    rank: int
    selection_score: float | None = None
    diversity_penalty: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "source_id": self.source_id,
            "content_hash": self.content_hash,
            "text": self.text,
            "metadata": _thaw(self.metadata),
            "score": self.score,
            "rank": self.rank,
            "selection_score": self.selection_score,
            "diversity_penalty": self.diversity_penalty,
        }


class LexicalIndex:
    """Precomputed lexical index. Use strategy='bm25' explicitly for the new path.

    Input records and nested metadata are snapshotted immutably. Search never updates
    corpus statistics. Identical scores break ties by record ID, independent of input
    order. Filters constrain eligibility but do not silently re-fit corpus IDF.
    """

    def __init__(
        self,
        records: Iterable[RetrievalRecord],
        *,
        strategy: Literal["jaccard", "bm25"] = "jaccard",
        k1: float = 1.5,
        b: float = 0.75,
        stop_words: Iterable[str] = (),
        tokenizer: Literal["unicode", "legacy"] = "unicode",
    ) -> None:
        if strategy not in {"jaccard", "bm25"}:
            raise ValueError("Unknown lexical retrieval strategy")
        if tokenizer not in TOKENIZER_VERSIONS:
            raise ValueError("Unknown tokenizer")
        if isinstance(k1, bool) or not math.isfinite(k1) or k1 <= 0:
            raise ValueError("k1 must be finite and positive")
        if isinstance(b, bool) or not math.isfinite(b) or not 0 <= b <= 1:
            raise ValueError("b must be between zero and one")
        supplied = tuple(records)
        if not all(isinstance(record, RetrievalRecord) for record in supplied):
            raise ValueError("Index inputs must be RetrievalRecord instances")
        if len({record.id for record in supplied}) != len(supplied):
            raise ValueError("Record IDs must be unique")
        self._records = tuple(sorted(supplied, key=lambda record: record.id))
        self._strategy = strategy
        self._k1 = float(k1)
        self._b = float(b)
        self._tokenizer = tokenizer
        if isinstance(stop_words, str):
            raise ValueError("stop_words must be an iterable of individual words")
        supplied_stop_words = tuple(stop_words)
        if not all(isinstance(word, str) for word in supplied_stop_words):
            raise ValueError("stop_words must contain strings")
        self._stop_words = frozenset(word.casefold() for word in supplied_stop_words)
        counts = [
            Counter(
                term
                for term in tokenize(record.text, mode=tokenizer)
                if term not in self._stop_words
            )
            for record in self._records
        ]
        self._lengths = tuple(sum(count.values()) for count in counts)
        self._term_sets = tuple(frozenset(count) for count in counts)
        self._average_length = sum(self._lengths) / len(self._records) if self._records else 0.0
        postings: dict[str, list[tuple[int, int]]] = {}
        for position, count in enumerate(counts):
            for term, frequency in count.items():
                postings.setdefault(term, []).append((position, frequency))
        self._postings = MappingProxyType(
            {term: tuple(rows) for term, rows in sorted(postings.items())}
        )
        self._document_frequencies = MappingProxyType(
            {term: len(rows) for term, rows in self._postings.items()}
        )
        self._corpus_sha256 = text_sha256(
            _canonical([record.to_dict() for record in self._records])
        )

    @property
    def records(self) -> tuple[RetrievalRecord, ...]:
        return self._records

    @property
    def strategy(self) -> str:
        return self._strategy

    @property
    def document_lengths(self) -> tuple[int, ...]:
        return self._lengths

    @property
    def document_frequencies(self) -> Mapping[str, int]:
        return self._document_frequencies

    @property
    def average_document_length(self) -> float:
        return self._average_length

    @property
    def corpus_sha256(self) -> str:
        return self._corpus_sha256

    def search(
        self,
        query: str,
        limit: int = 5,
        *,
        filters: RetrievalFilter | None = None,
        mmr_lambda: float | None = None,
        candidate_pool_size: int | None = None,
    ) -> list[SearchHit]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit must be a nonnegative integer")
        if mmr_lambda is not None and (
            isinstance(mmr_lambda, bool)
            or not math.isfinite(mmr_lambda)
            or not 0 <= mmr_lambda <= 1
        ):
            raise ValueError("mmr_lambda must be between zero and one")
        if candidate_pool_size is not None and (
            isinstance(candidate_pool_size, bool)
            or not isinstance(candidate_pool_size, int)
            or candidate_pool_size < limit
        ):
            raise ValueError("candidate_pool_size must be an integer at least as large as limit")
        terms = frozenset(
            term for term in tokenize(query, mode=self._tokenizer) if term not in self._stop_words
        )
        if not terms or not limit:
            return []
        scores: dict[int, float] = {}
        intersections: dict[int, int] = {}
        size = len(self._records)
        for term in sorted(terms):
            rows = self._postings.get(term, ())
            if not rows:
                continue
            inverse_frequency = math.log1p((size - len(rows) + 0.5) / (len(rows) + 0.5))
            for position, frequency in rows:
                if filters is not None and not filters.matches(self._records[position]):
                    continue
                if self._strategy == "jaccard":
                    intersections[position] = intersections.get(position, 0) + 1
                else:
                    length_norm = (
                        1 - self._b + self._b * self._lengths[position] / self._average_length
                    )
                    contribution = (
                        inverse_frequency
                        * frequency
                        * (self._k1 + 1)
                        / (frequency + self._k1 * length_norm)
                    )
                    scores[position] = scores.get(position, 0.0) + contribution
        if self._strategy == "jaccard":
            scores = {
                position: intersection
                / (len(terms) + len(self._term_sets[position]) - intersection)
                for position, intersection in intersections.items()
            }
        candidates = sorted(
            scores, key=lambda position: (-scores[position], self._records[position].id)
        )
        if mmr_lambda is None:
            return [
                self._hit(position, scores[position], rank + 1)
                for rank, position in enumerate(candidates[:limit])
            ]
        pool = candidates[: candidate_pool_size or max(50, limit * 8)]
        if not pool:
            return []
        maximum = max(scores.values())
        selected: list[int] = []
        hits: list[SearchHit] = []
        while pool and len(selected) < limit:
            reranked = []
            for position in pool:
                redundancy = max(
                    (
                        len(self._term_sets[position] & self._term_sets[other])
                        / max(1, len(self._term_sets[position] | self._term_sets[other]))
                        for other in selected
                    ),
                    default=0.0,
                )
                selection = mmr_lambda * scores[position] / maximum - (1 - mmr_lambda) * redundancy
                reranked.append((selection, redundancy, position))
            selection, redundancy, position = min(
                reranked, key=lambda row: (-row[0], -scores[row[2]], self._records[row[2]].id)
            )
            hits.append(self._hit(position, scores[position], len(hits) + 1, selection, redundancy))
            selected.append(position)
            pool.remove(position)
        return hits

    def _hit(
        self,
        position: int,
        score: float,
        rank: int,
        selection: float | None = None,
        redundancy: float = 0.0,
    ) -> SearchHit:
        record = self._records[position]
        return SearchHit(
            record.id,
            record.source_id,
            record.content_hash,
            record.text,
            record.metadata,
            score,
            rank,
            selection,
            redundancy,
        )

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "tokenizer_version": TOKENIZER_VERSIONS[self._tokenizer],
            "configuration": {
                "strategy": self._strategy,
                "k1": self._k1,
                "b": self._b,
                "stop_words": sorted(self._stop_words),
                "tokenizer": self._tokenizer,
            },
            "records": [record.to_dict() for record in self._records],
            "document_lengths": list(self._lengths),
            "average_document_length": self._average_length,
            "document_frequencies": dict(self._document_frequencies),
            "postings": {
                term: [[self._records[position].id, frequency] for position, frequency in rows]
                for term, rows in self._postings.items()
            },
            "corpus_sha256": self._corpus_sha256,
        }

    @property
    def index_sha256(self) -> str:
        return text_sha256(_canonical(self._payload()))

    def to_dict(self) -> dict[str, Any]:
        payload = self._payload()
        return {**payload, "index_sha256": text_sha256(_canonical(payload))}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LexicalIndex:
        expected = {
            "schema_version",
            "tokenizer_version",
            "configuration",
            "records",
            "document_lengths",
            "average_document_length",
            "document_frequencies",
            "postings",
            "corpus_sha256",
            "index_sha256",
        }
        if not isinstance(data, Mapping) or set(data) != expected:
            raise ValueError("Serialized index has unexpected or missing fields")
        if data["schema_version"] != SCHEMA_VERSION:
            raise ValueError("Unsupported serialized index version")
        payload = {key: value for key, value in data.items() if key != "index_sha256"}
        if text_sha256(_canonical(payload)) != data["index_sha256"]:
            raise ValueError("Serialized index checksum mismatch")
        config = data["configuration"]
        if not isinstance(config, dict) or set(config) != {
            "strategy",
            "k1",
            "b",
            "stop_words",
            "tokenizer",
        }:
            raise ValueError("Serialized index configuration is invalid")
        if data["tokenizer_version"] != TOKENIZER_VERSIONS.get(config["tokenizer"]):
            raise ValueError("Serialized tokenizer version is unsupported")
        if not isinstance(data["records"], list):
            raise ValueError("Serialized records must be a list")
        try:
            records = [RetrievalRecord(**record) for record in data["records"]]
            rebuilt = cls(records, **config)
        except (TypeError, KeyError) as exc:
            raise ValueError("Serialized records or configuration are invalid") from exc
        if rebuilt._payload() != payload:
            raise ValueError("Serialized index statistics do not match its records")
        return rebuilt

    def save(self, path: Path, *, overwrite: bool = False) -> Path:
        """Write an explicit artifact; refuse to overwrite an existing index by default."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = (
            json.dumps(
                self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
            )
            + "\n"
        )
        with path.open("w" if overwrite else "x", encoding="utf-8", newline="\n") as stream:
            stream.write(serialized)
        return path

    @classmethod
    def load(cls, path: Path, *, max_bytes: int = 256 * 1024 * 1024) -> LexicalIndex:
        path = Path(path)
        if path.stat().st_size > max_bytes:
            raise ValueError("Serialized index exceeds the configured size limit")

        def constant(_: str) -> None:
            raise ValueError("Serialized index contains a non-finite number")

        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("Serialized index contains duplicate keys")
                result[key] = value
            return result

        data = json.loads(
            path.read_text(encoding="utf-8"), parse_constant=constant, object_pairs_hook=pairs
        )
        return cls.from_dict(data)
