"""Public-web prior-art lookup (pypi/npm), vendored from the now-archived
my-librarian repo — myidea was its only consumer outside that repo, and a
package dependency on an archived repo has no CI, no releases, and can vanish
from an environment without warning (exactly what took mytelegrambot down for
23 days via this same import). Keeping this logic in-repo, not importing it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass

from mythings.http import http_get

Fetcher = Callable[..., bytes]

NPM_SEARCH_ENDPOINT = "https://registry.npmjs.org/-/v1/search"
PYPI_JSON_ENDPOINT = "https://pypi.org/pypi/{name}/json"

# PyPI retired its free-text search endpoint; a small curated seed table maps
# common task keywords to well-known package names, enriched via the JSON API
# (per-package, keyless) rather than guessed at query time.
PYPI_SEED: dict[str, tuple[str, ...]] = {
    "markdown": ("markdown-it-py", "mistune", "Markdown"),
    "html": ("markdown-it-py", "beautifulsoup4", "lxml"),
    "pdf": ("pypandoc", "reportlab", "weasyprint"),
    "typeset": ("pypandoc",),
    "yaml": ("PyYAML", "ruamel.yaml"),
    "json": ("orjson",),
    "csv": ("pandas",),
    "excel": ("openpyxl", "xlsxwriter"),
    "http": ("httpx", "requests"),
    "cli": ("click", "typer"),
    "test": ("pytest",),
    "template": ("jinja2",),
    "config": ("pydantic",),
    "date": ("python-dateutil", "pendulum"),
}

_PERMISSIVE_LICENSES = frozenset(
    {"mit", "bsd", "bsd-2-clause", "bsd-3-clause", "apache-2.0", "isc"}
)
_COPYLEFT_LICENSES = frozenset({"gpl", "gpl-2.0", "gpl-3.0", "lgpl", "agpl-3.0", "agpl"})

# A short, deterministic stopword set — same discipline as MyResearcher's
# tokenizer (no NLP dependency, harness: dependency-free runtime).
_STOPWORDS = frozenset(
    "a an and are as at be by convert for from how in into is it of on or the "
    "to via with what why tool library".split()
)

# npm's search API rejects any `text` param over 64 chars (HTTP 400,
# ERR_TEXT_LENGTH); keep the joined query under that regardless of how many
# terms a verbose task title/body tokenizes into.
_MAX_QUERY_LEN = 64


@dataclass(frozen=True)
class Candidate:
    name: str
    registry: str  # "pypi" | "npm" | "github"
    description: str
    license: str  # normalized lowercase id, or "unknown"
    popularity: float  # 0.0-1.0, registry-specific normalization
    url: str
    last_release: str | None = None  # ISO date, if known


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    word: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            word.append(ch)
        elif word:
            out.append("".join(word))
            word = []
    if word:
        out.append("".join(word))
    return out


def build_query(title: str, body: str = "") -> str:
    seen: set[str] = set()
    terms: list[str] = []
    for tok in tokenize(title) + tokenize(body):
        if tok in _STOPWORDS or len(tok) < 2 or tok in seen:
            continue
        seen.add(tok)
        terms.append(tok)

    query = ""
    for term in terms[:12]:
        candidate = f"{query} {term}".strip()
        if len(candidate) > _MAX_QUERY_LEN:
            break
        query = candidate
    return query


def normalize_license(raw: str | None) -> str:
    if not raw:
        return "unknown"
    text = raw.strip().lower()
    for known in _PERMISSIVE_LICENSES | _COPYLEFT_LICENSES:
        if known in text:
            return known
    return text or "unknown"


def is_copyleft(license_id: str) -> bool:
    return license_id in _COPYLEFT_LICENSES


def search_npm(query: str, *, fetch: Fetcher = http_get, limit: int = 10) -> list[Candidate]:
    if not query:
        return []
    params = urllib.parse.urlencode({"text": query, "size": limit})
    try:
        raw = fetch(f"{NPM_SEARCH_ENDPOINT}?{params}")
    except urllib.error.HTTPError:
        # e.g. ERR_TEXT_LENGTH if a query still slips past build_query's cap
        # (a caller passing a raw query directly) -- degrade like an empty
        # result set rather than crashing the whole survey run.
        return []
    payload = json.loads(raw)
    candidates: list[Candidate] = []
    for obj in payload.get("objects", []):
        pkg = obj.get("package", {})
        name = pkg.get("name")
        if not name:
            continue
        score = obj.get("score", {}).get("detail", {}).get("popularity", 0.0)
        candidates.append(
            Candidate(
                name=name,
                registry="npm",
                description=(pkg.get("description") or "").strip(),
                license=normalize_license(pkg.get("license")),
                popularity=float(score or 0.0),
                url=pkg.get("links", {}).get("npm", f"https://www.npmjs.com/package/{name}"),
                last_release=pkg.get("date"),
            )
        )
    return candidates


def _seed_names(query: str) -> list[str]:
    tokens = set(tokenize(query))
    names: list[str] = []
    seen: set[str] = set()
    for keyword, packages in PYPI_SEED.items():
        if keyword not in tokens:
            continue
        for name in packages:
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def search_pypi(query: str, *, fetch: Fetcher = http_get, limit: int = 10) -> list[Candidate]:
    names = _seed_names(query)[:limit]
    candidates: list[Candidate] = []
    for name in names:
        raw = fetch(PYPI_JSON_ENDPOINT.format(name=name))
        payload = json.loads(raw)
        info = payload.get("info", {})
        releases = payload.get("releases", {})
        last_release = None
        for files in releases.values():
            for f in files:
                upload_time = f.get("upload_time_iso_8601") or f.get("upload_time")
                if upload_time and (last_release is None or upload_time > last_release):
                    last_release = upload_time
        candidates.append(
            Candidate(
                name=info.get("name", name),
                registry="pypi",
                description=(info.get("summary") or "").strip(),
                license=normalize_license(info.get("license")),
                # Curated seed matches are already known-good; fixed high
                # popularity so they compete fairly against npm's live score.
                popularity=0.9,
                url=info.get("project_url") or f"https://pypi.org/project/{name}/",
                last_release=last_release,
            )
        )
    return candidates


def _score(candidate: Candidate, query_tokens: set[str]) -> tuple[float, float, str]:
    haystack = set(tokenize(candidate.name)) | set(tokenize(candidate.description))
    overlap = len(query_tokens & haystack)
    downrank = -0.5 if is_copyleft(candidate.license) else 0.0
    return (overlap + candidate.popularity + downrank, candidate.popularity, candidate.name)


def retrieve(
    title: str,
    body: str = "",
    *,
    registries: tuple[str, ...] = ("pypi", "npm"),
    top: int = 10,
    fetch: Fetcher = http_get,
) -> list[Candidate]:
    query = build_query(title, body)
    found: list[Candidate] = []
    if "pypi" in registries:
        found += search_pypi(query, fetch=fetch, limit=top)
    if "npm" in registries:
        found += search_npm(query, fetch=fetch, limit=top)

    deduped: dict[str, Candidate] = {}
    for cand in found:
        key = f"{cand.registry}:{cand.name}".lower()
        deduped.setdefault(key, cand)

    query_tokens = set(tokenize(query))
    ranked = sorted(deduped.values(), key=lambda c: _score(c, query_tokens), reverse=True)
    return ranked[:top]
