from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


def normalized_role_company(company: str, role: str) -> str:
    def normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    return f"{normalize(company)}::{normalize(role)}"


def attach_query(url: str, query_param: str, query: str) -> str:
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    q[query_param] = [query]
    updated = parsed._replace(query=urlencode(q, doseq=True))
    return urlunparse(updated)

