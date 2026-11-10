"""Text matching for concept search: words in any order, without case or accents.

A query is split into words. A concept matches when every word starts a word of its labels or definitions
(so "session" finds "Sessions" but "lease" does not find "release"); a word of five letters or more also
matches through its stem, so "sessions" finds "Session" and "сессия" finds "сессии". Tiers rank the
matches, best first:

- 0: a label is exactly the query (same words, same order);
- 1: one label holds every word;
- 2: one label holds every word, some only through their stems;
- 3: the words are spread over the labels and definitions.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

_WORD = re.compile(r"\w+")


def normalize(text: str) -> str:
    """Casefolded, without accents (NFKD minus combining marks): "Ёлка" and "елка" are the same."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def words(text: str) -> tuple[str, ...]:
    return tuple(_WORD.findall(normalize(text)))


def stem(word: str) -> str:
    """A crude stem for plurals and inflections: two letters off a word of six or more, one off five."""
    n = len(word)
    return word[:-2] if n >= 6 else word[:-1] if n == 5 else word


@dataclass(frozen=True)
class Query:
    text: str
    words: tuple[str, ...]
    stems: tuple[str, ...]

    @classmethod
    def of(cls, text: str) -> Query:
        w = words(text)
        return cls(text, w, tuple(stem(x) for x in dict.fromkeys(w)))


@dataclass(frozen=True)
class Texts:
    """An entity's text, split once: the words of each label, and all words of labels and definitions."""
    labels: tuple[tuple[str, ...], ...]
    all: tuple[str, ...]

    @classmethod
    def of(cls, labels: Iterable[str], definitions: Iterable[str] = ()) -> Texts:
        lab = tuple(words(t) for t in labels)
        rest = tuple(w for t in definitions for w in words(t))
        return cls(lab, tuple(dict.fromkeys((*(w for l in lab for w in l), *rest))))


def _has(prefix: str, tokens: Sequence[str]) -> bool:
    return any(t.startswith(prefix) for t in tokens)


def tier(q: Query, t: Texts) -> int | None:
    """The match tier of `t` for `q` (module docstring), or None when it does not match."""
    if not q.words:
        return None
    uniq = tuple(dict.fromkeys(q.words))
    if any(l == q.words for l in t.labels):
        return 0
    if any(all(_has(w, l) for w in uniq) for l in t.labels):
        return 1
    if any(all(_has(s, l) for s in q.stems) for l in t.labels):
        return 2
    if all(_has(s, t.all) for s in q.stems):
        return 3
    return None
