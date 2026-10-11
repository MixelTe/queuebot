def parse_int(v: str, default: int | None = None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default
