def generate_id(url: str) -> str:
    """Return a human‑readable identifier derived from *url*.

    The identifier is the URL path with leading/trailing slashes stripped
    and interior slashes replaced by underscores. If the path is empty,
    ``"unknown"`` is returned.
    """
    from urllib.parse import urlparse
    path = urlparse(url).path.strip('/')
    return path.replace('/', '_') if path else 'unknown'
