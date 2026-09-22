from __future__ import annotations

MAX_TAG_LENGTH = 40


def normalise_tag(raw: str) -> str:
    """Lower case with runs of whitespace collapsed; ValueError when unusable."""
    tag = " ".join(raw.casefold().split())
    if not tag:
        raise ValueError("a tag needs at least one visible character")
    if "," in tag:
        raise ValueError("a tag cannot contain a comma")
    if len(tag) > MAX_TAG_LENGTH:
        raise ValueError(f"a tag is at most {MAX_TAG_LENGTH} characters")
    return tag
