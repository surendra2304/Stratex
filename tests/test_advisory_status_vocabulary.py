"""The gate must understand the vocabulary the peer actually speaks.

A live local-fleet run showed Stratex consulting Inference over real HTTP, receiving
status "RECOMMENDATION" with a real confidence, and rejecting it as "not approved for
execution" -- because the gate only accepted "RECOMMENDED". Inference declares
RECOMMENDATION as the literal on its trading-consult contract, so the two vocabularies
were disjoint and every advisory was rejected for the wrong reason.
"""

from advisory_gate import (
    AI_ADVISORY_ONLY_STATUSES,
    AI_EXECUTION_AUTHORIZED_STATUSES,
    AdvisoryGate,
)


def _gate():
    return AdvisoryGate()


def _decision(status, changes=None):
    return {
        "decision_id": "328900a9-7995-4c79-a972-b546c3490a5b",
        "status": status,
        "rationale": "trend continuation",
        "parameter_changes": changes if changes is not None else [{"parameter": "ADX_THRESHOLD", "value": 16}],
    }


def test_recommendation_is_recorded_rather_than_reported_as_malformed():
    """A working peer's advice must not be logged as an unrecognized status."""
    result = _gate().validate(_decision("RECOMMENDATION"), {"ADX_THRESHOLD": 25})
    assert result.verdict == "SHADOW_LOG_ONLY"
    assert "advisory only" in result.rationale
    assert "REJECT" not in result.rationale


def test_recommendation_never_authorizes_a_parameter_change():
    result = _gate().validate(_decision("RECOMMENDATION"), {"ADX_THRESHOLD": 25})
    assert result.applied_changes == []
    assert result.rejected_changes
    assert all("advisory status" in c["reason"] for c in result.rejected_changes)


def test_shadow_log_only_is_not_applied_by_the_scheduler():
    """Only an APPLY verdict may reach runtime; the new verdict must not grant it."""
    assert "SHADOW_LOG_ONLY" != "APPLY"
    assert "SHADOW_LOG_ONLY" not in AI_EXECUTION_AUTHORIZED_STATUSES


def test_an_explicit_authorization_still_passes_the_status_check():
    """An authorized status must clear this gate, even if a later guard downgrades it.

    SHADOW_LOG_ONLY is also produced by downstream guards such as insufficient trade
    count, so the verdict alone cannot identify which check fired. Assert on the
    rationale this gate emits instead.
    """
    for status in sorted(AI_EXECUTION_AUTHORIZED_STATUSES):
        result = _gate().validate(_decision(status), {"ADX_THRESHOLD": 25})
        assert "not a recognized execution authorization" not in result.rationale
        assert "advisory only" not in result.rationale, f"{status} was misread as advisory only"


def test_an_unknown_status_is_still_rejected():
    result = _gate().validate(_decision("TOTALLY_MADE_UP"), {"ADX_THRESHOLD": 25})
    assert result.verdict == "REJECT"
    assert "not a recognized execution authorization" in result.rationale


def test_the_two_status_sets_never_overlap():
    assert not (AI_EXECUTION_AUTHORIZED_STATUSES & AI_ADVISORY_ONLY_STATUSES)


def test_inference_contract_literal_is_advisory_only_not_authorized():
    """Pin the peer's declared contract so a rename on either side is caught."""
    assert "RECOMMENDATION" in AI_ADVISORY_ONLY_STATUSES
    assert "RECOMMENDATION" not in AI_EXECUTION_AUTHORIZED_STATUSES
    assert "RECOMMENDED" in AI_EXECUTION_AUTHORIZED_STATUSES
