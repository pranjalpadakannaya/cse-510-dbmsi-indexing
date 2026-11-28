import math
from collections import Counter

from dbmsi.storage import RID
from dbmsi.inverted_index.index import tokenize


class BM25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b

        # RID -> term frequencies in that filename
        self.term_freqs: dict[RID, Counter[str]] = {}

        # RID -> number of terms in that filename
        self.doc_lengths: dict[RID, int] = {}
        self.total_doc_length = 0

        # term -> number of documents containing that term
        self.doc_freqs: Counter[str] = Counter()

    def add(self, rid: RID, filename: str) -> None:
        terms = tokenize(filename)
        frequencies = Counter(terms)

        self.term_freqs[rid] = frequencies
        self.doc_lengths[rid] = len(terms)
        self.total_doc_length += len(terms)

        for term in frequencies:
            self.doc_freqs[term] += 1

    @property
    def num_docs(self) -> int:
        return len(self.doc_lengths)

    @property
    def avg_doc_length(self) -> float:
        if not self.doc_lengths:
            return 0.0
        return self.total_doc_length / self.num_docs

    def score(self, rid: RID, query: str) -> float:
        if self.num_docs == 0:
            return 0.0

        score = 0.0
        doc_length = self.doc_lengths[rid]
        avg_length = self.avg_doc_length

        for term in tokenize(query):
            tf = self.term_freqs[rid].get(term, 0)

            if tf == 0:
                continue

            df = self.doc_freqs[term]

            idf = math.log(
                1 + (self.num_docs - df + 0.5) / (df + 0.5)
            )

            numerator = tf * (self.k1 + 1)

            denominator = tf + self.k1 * (
                1 - self.b + self.b * doc_length / avg_length
            )

            score += idf * numerator / denominator

        return score

    def search(self, query: str, k: int = 10) -> list[tuple[RID, float]]:
        if k <= 0:
            return []

        query_terms = tokenize(query)

        candidates = [
            rid
            for rid, frequencies in self.term_freqs.items()
            if any(term in frequencies for term in query_terms)
        ]

        ranked = [
            (rid, self.score(rid, query))
            for rid in candidates
        ]

        ranked.sort(key=lambda item: (-item[1], item[0]))

        return ranked[:k]

    def rank(
        self,
        candidates: set[RID],
        query_terms: list[str],
        k: int = 10,
    ) -> list[tuple[RID, float]]:
        if k <= 0:
            return []

        query = " ".join(query_terms)

        ranked = [
            (rid, self.score(rid, query))
            for rid in candidates
        ]

        ranked.sort(key=lambda item: (-item[1], item[0]))

        return ranked[:k]