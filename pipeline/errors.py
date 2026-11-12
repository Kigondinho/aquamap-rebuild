"""Exceptions for data acquisition. Every failure is reported, never silently replaced by fake data."""

from __future__ import annotations


class AcquisitionError(Exception):
    """A dataset could not be obtained. The message says what failed and what to try next."""


class SourceUnavailable(AcquisitionError):
    """The remote source could not be reached (network policy, outage, timeout, HTTP error)."""


class ValidationFailed(AcquisitionError):
    """The data was obtained but failed one or more blocking validation checks."""
