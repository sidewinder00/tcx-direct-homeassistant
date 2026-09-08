"""Passive document inspection only; never used by schedule write guards."""

from typing import Any


def document_containers(data: Any):
    """Inspect document boundaries, not arbitrary nested equipment properties."""
    if not isinstance(data, dict):
        return
    yield "root", data
    payload = data.get("payload")
    if isinstance(payload, dict):
        yield "payload", payload
    for prefix, parent in (("root", data), ("payload", payload)):
        if isinstance(parent, dict):
            for name, document in parent.items():
                if isinstance(document, dict) and ("state" in document or "metadata" in document):
                    yield f"{prefix}.{name}", document


def _document_has_schedule_fields(document: dict) -> bool:
    for group, kinds in (
        ("state", ("desired", "reported", "delta")),
        ("metadata", ("desired", "reported")),
    ):
        container = document.get(group)
        if isinstance(container, dict) and any(
            isinstance(container.get(kind), dict) and "sh" in container[kind] for kind in kinds
        ):
            return True
    return False


def has_schedule_fields(data: Any) -> bool:
    """Trace relevance: explicit schedule fields, including null/malformed values.

    Unscoped malformed containers alone do not consume the bounded event history.
    The trace separately retains every REST response and Authorization snapshot.
    This narrower predicate must never decide whether read evidence is current.
    """
    return any(_document_has_schedule_fields(doc) for _, doc in document_containers(data))


def has_malformed_schedule_containers(data: Any) -> bool:
    """Diagnostic relevance: schedule fields or potentially relevant malformed state."""
    for _, document in document_containers(data):
        # Malformed state containers cannot establish irrelevance.
        state = document.get("state")
        if "state" in document and not isinstance(state, dict):
            return True
        if isinstance(state, dict) and any(
            kind in state and not isinstance(state[kind], dict)
            for kind in ("desired", "reported", "delta")
        ):
            return True
        metadata = document.get("metadata")
        if "metadata" in document and not isinstance(metadata, dict):
            return True
        if (
            isinstance(metadata, dict)
            and "desired" in metadata
            and not isinstance(metadata["desired"], dict)
        ):
            return True
    return False
