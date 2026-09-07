from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class VerificationResult:
    command: str
    ok: bool
    output: str


@dataclass(frozen=True, slots=True)
class AuditBundle:
    generated_at_ns: int
    commit: str
    verifications: tuple[VerificationResult, ...]
    findings: tuple[str, ...]


def run_command(command: str) -> VerificationResult:
    proc = subprocess.run(command, shell=True, text=True, capture_output=True, check=False)
    output = (proc.stdout + "\n" + proc.stderr).strip()
    return VerificationResult(command, proc.returncode == 0, output)



def write_audit(path: str, bundle: AuditBundle) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({
            "generated_at_ns": bundle.generated_at_ns,
            "commit": bundle.commit,
            "verifications": [asdict(x) for x in bundle.verifications],
            "findings": list(bundle.findings),
        }, fh, indent=2)
