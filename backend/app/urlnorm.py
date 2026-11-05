"""URL normalization so the same article reached via different links is stored once."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "yclid", "mc_cid", "mc_eid", "igshid", "si",
    "ref", "ref_src", "ref_url", "referrer", "source", "src", "cmpid", "ocid", "sr_share",
    "_hsenc", "_hsmi", "guccounter", "guce_referrer", "guce_referrer_sig", "taid", "smid",
    "rss", "feed", "output", "share", "s_cid", "cid", "mkt_tok", "trk", "trkcampaign",
}
TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "hsa_", "__s")
STRIP_HOST_PREFIXES = ("www.", "m.", "amp.", "mobile.")
ARXIV_RE = re.compile(r"^/(?:abs|pdf|html)/([0-9]{4}\.[0-9]{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/[0-9]{7})(?:v\d+)?(?:\.pdf)?/?$")


def normalize_url(url: str) -> str:
    """Return a canonical form of ``url`` for exact-duplicate detection.

    - lowercases scheme/host, treats http and https as the same, drops ``www.``/``m.``
    - removes fragments, tracking query params (utm_*, fbclid, ...) and sorts the rest
    - removes trailing slashes, ``/amp`` and ``index.html`` suffixes
    - canonicalizes arXiv abs/pdf/html links (any version) to ``arxiv.org/abs/<id>``
    """
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    for prefix in STRIP_HOST_PREFIXES:
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    port = parts.port
    if port and port not in (80, 443):
        host = f"{host}:{port}"

    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if host in ("arxiv.org", "export.arxiv.org"):
        m = ARXIV_RE.match(path)
        if m:
            return f"https://arxiv.org/abs/{m.group(1)}"
        host = "arxiv.org"
    for suffix in ("/index.html", "/index.htm", "/index.php", "/amp"):
        if path.lower().endswith(suffix):
            path = path[: -len(suffix)] or "/"
    if len(path) > 1:
        path = path.rstrip("/")

    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith(TRACKING_PREFIXES)
    ]
    query.sort()
    path = path if path != "/" else ""
    return urlunsplit(("https", host, path, urlencode(query), ""))
