"""Spec B (2026-07-11) reason code constants smoke test.

Spec: docs/superpowers/specs/2026-07-11-document-manager-backend-design.md §5.2
"""

from __future__ import annotations


def test_spec_b_reason_codes_are_present():
    from tools.webapi._helpers import ReasonCode

    assert ReasonCode.FILE_TOO_LARGE == "file_too_large"
    assert ReasonCode.FILE_MISSING_AT_REF == "file_missing_at_ref"
    assert ReasonCode.FILE_EXISTS == "file_exists"
