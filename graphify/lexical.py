"""
Offline lexical retrieval: identifier-aware tokenisation + BM25.

Why this exists: the original retrieval compared question words against whole
symbol names, so "authentication" never matched `auth_login` and "find user by
email" never matched `findByEmail`. Splitting identifiers into subwords
(`findByEmail` -> find, by, email; `auth_login` -> auth, login) fixes a large
class of questions without needing any network call, and BM25 ranks by term
rarity so a match on "email" counts for less than a match on "revoke".
"""
import math
import re
from collections import Counter

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL_RE = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")

# Only question scaffolding — nothing domain-specific, so we never drop a term
# that might be the actual symbol the user means.
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "did", "do",
    "does", "for", "from", "get", "give", "how", "i", "if", "in", "into", "is",
    "it", "me", "my", "of", "on", "or", "please", "show", "so", "tell", "than",
    "that", "the", "their", "them", "then", "there", "these", "this", "to",
    "up", "use", "used", "was", "what", "when", "where", "which", "who", "why",
    "will", "with", "would", "you", "your",
}


def split_identifier(word: str) -> list:
    """`findByEmail` -> [findbyemail, find, by, email]; `auth_login` -> [auth_login, auth, login]."""
    out = [word.lower()]
    for part in word.split("_"):
        if not part:
            continue
        for piece in _CAMEL_RE.findall(part):
            out.append(piece.lower())
    return out


def tokenize(text: str, *, keep_stopwords: bool = False) -> list:
    tokens = []
    for word in _WORD_RE.findall(text or ""):
        for tok in split_identifier(word):
            if len(tok) < 2:
                continue
            if not keep_stopwords and tok in STOPWORDS:
                continue
            tokens.append(tok)
    return tokens


class Bm25Index:
    """Standard BM25 over pre-tokenised documents. No dependencies."""

    def __init__(self, documents: list, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs = [Counter(d) for d in documents]
        self.lengths = [sum(c.values()) for c in self.docs]
        self.avg_len = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        df = Counter()
        for counter in self.docs:
            df.update(counter.keys())
        n = max(len(self.docs), 1)
        self.idf = {
            term: math.log(1 + (n - freq + 0.5) / (freq + 0.5))
            for term, freq in df.items()
        }

    def expand(self, query_tokens: list, min_prefix: int = 4, per_token: int = 4) -> list:
        """
        Bridge morphology gaps without a stemmer: a query term absent from the
        vocabulary is replaced by vocabulary terms sharing a prefix with it.
        This is what lets "authentication" reach `auth_login` and
        "tokenizer" reach `token_generate` — pure BM25 would miss both because
        it only matches identical tokens.
        """
        vocab = self.idf.keys()
        expanded = list(query_tokens)
        for token in query_tokens:
            if token in self.idf or len(token) < min_prefix:
                continue
            prefix = token[:min_prefix]
            hits = [t for t in vocab if t.startswith(prefix) or token.startswith(t[:min_prefix])]
            hits.sort(key=lambda t: (abs(len(t) - len(token)), t))
            expanded.extend(hits[:per_token])
        return expanded

    def score(self, query_tokens: list, index: int) -> float:
        counter = self.docs[index]
        length = self.lengths[index] or 1
        total = 0.0
        for term in query_tokens:
            tf = counter.get(term)
            if not tf:
                continue
            idf = self.idf.get(term, 0.0)
            denom = tf + self.k1 * (1 - self.b + self.b * length / (self.avg_len or 1))
            total += idf * tf * (self.k1 + 1) / denom
        return total

    def search(self, query_tokens: list, limit: int = 10) -> list:
        scored = [(i, self.score(query_tokens, i)) for i in range(len(self.docs))]
        scored = [(i, s) for i, s in scored if s > 0]
        scored.sort(key=lambda pair: -pair[1])
        return scored[:limit]
