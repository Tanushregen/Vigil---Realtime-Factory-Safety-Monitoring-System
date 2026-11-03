"""Shared helpers for all schema modules."""
from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_name(value: str) -> str:
    """'Conveyor Belt ' -> 'conveyor_belt'. Same noun always maps to the same key."""
    cleaned = _NON_ALNUM.sub("_", value.strip().lower()).strip("_")
    if not cleaned:
        raise ValueError("name must contain at least one letter or digit")
    return cleaned


# Use for object / zone / room / region names everywhere.
NormalizedName = Annotated[str, AfterValidator(normalize_name)]
