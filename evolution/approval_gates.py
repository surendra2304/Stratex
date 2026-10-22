"""
evolution/approval_gates.py — Human-in-the-Loop Strategy Promotion & Governance Gate.

CRITICAL INVARIANTS:
1. NO strategy reaches live/production deployment without explicit human approval.
2. Emergency retirement can be automated on risk violations, but PROMOTION IS NEVER AUTOMATIC.
3. Every human approval logs a cryptographic audit hash and evidence package.
"""

import datetime
import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from logger import get_logger
from security_hardening import sign_audit_record

logger = get_logger("approval_gates")


@dataclass
class PromotionProposal:
    proposal_id: str
    genome_id: str
    archetype: str
    gauntlet_evidence_hash: str
    incubation_days: int
    live_profit_factor: float
    fidelity_score: float
    status: str = "PENDING_HUMAN_APPROVAL"  # "PENDING_HUMAN_APPROVAL", "APPROVED", "REJECTED"
    created_at: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat() + "Z")
    decided_at: str | None = None
    approver: str | None = None
    rationale: str | None = None
    signature: str | None = None


class HumanApprovalGate:
    """
    Manages human governance, promotion review queues, and cryptographic approval logging.
    """

    def __init__(self, state_file: str = "approval_queue.json"):
        self.state_file = state_file
        self.proposals: dict[str, PromotionProposal] = {}
        self.load_state()

    def load_state(self) -> None:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for pid, p in data.items():
                        self.proposals[pid] = PromotionProposal(**p)
            except Exception as e:
                logger.error(f"[APPROVAL_GATE] Failed to load {self.state_file}: {e}")

    def save_state(self) -> None:
        data = {pid: asdict(p) for pid, p in self.proposals.items()}
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def submit_promotion_proposal(
        self,
        genome_id: str,
        archetype: str,
        evidence_summary: dict[str, Any],
        incubation_days: int,
        live_pf: float,
        fidelity: float
    ) -> PromotionProposal:
        """Creates a pending promotion proposal for human operator review."""
        evidence_hash = sign_audit_record(evidence_summary)
        proposal_id = f"PROP_{genome_id}_{int(time.time())}"

        proposal = PromotionProposal(
            proposal_id=proposal_id,
            genome_id=genome_id,
            archetype=archetype,
            gauntlet_evidence_hash=evidence_hash,
            incubation_days=incubation_days,
            live_profit_factor=live_pf,
            fidelity_score=fidelity,
            status="PENDING_HUMAN_APPROVAL"
        )
        self.proposals[proposal_id] = proposal
        self.save_state()
        logger.info(f"[APPROVAL_GATE] 📋 Submitted promotion proposal {proposal_id} for genome {genome_id}")
        return proposal

    def approve_proposal(self, proposal_id: str, approver: str, rationale: str = "") -> PromotionProposal | None:
        """Approves a strategy for deployment with cryptographic signature."""
        if proposal_id not in self.proposals:
            return None

        prop = self.proposals[proposal_id]
        if prop.status != "PENDING_HUMAN_APPROVAL":
            return prop

        prop.status = "APPROVED"
        prop.decided_at = datetime.datetime.utcnow().isoformat() + "Z"
        prop.approver = approver
        prop.rationale = rationale

        # Sign the human decision
        sig = sign_audit_record(asdict(prop))
        prop.signature = sig

        self.save_state()
        logger.info(f"[APPROVAL_GATE] ✅ Strategy {prop.genome_id} APPROVED for production by {approver}")
        return prop

    def reject_proposal(self, proposal_id: str, approver: str, rationale: str = "") -> PromotionProposal | None:
        """Rejects a strategy promotion."""
        if proposal_id not in self.proposals:
            return None

        prop = self.proposals[proposal_id]
        prop.status = "REJECTED"
        prop.decided_at = datetime.datetime.utcnow().isoformat() + "Z"
        prop.approver = approver
        prop.rationale = rationale
        prop.signature = sign_audit_record(asdict(prop))

        self.save_state()
        logger.info(f"[APPROVAL_GATE] ❌ Strategy {prop.genome_id} REJECTED by {approver}")
        return prop

    def get_pending_proposals(self) -> list[dict[str, Any]]:
        return [asdict(p) for p in self.proposals.values() if p.status == "PENDING_HUMAN_APPROVAL"]

    def get_all_proposals(self) -> list[dict[str, Any]]:
        return [asdict(p) for p in self.proposals.values()]
