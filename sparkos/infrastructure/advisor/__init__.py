"""Isolated advisor service: requests a higher-capability model for bounded advice."""

from sparkos.infrastructure.advisor.models import (
    AdvisorRequest,
    AdvisorResult,
)
from sparkos.infrastructure.advisor.service import AdvisorService

__all__ = [
    "AdvisorRequest",
    "AdvisorResult",
    "AdvisorService",
]
