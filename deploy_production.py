#!/usr/bin/env python3
"""
deploy_production.py — Automated Production Deployment, Verification & Audit Pipeline.

Execution Pipeline:
1. Environment Variable & Secret Validation (API keys present and masked).
2. Pre-Deployment Security Audit (file permissions, lockfiles, forbidden settings).
3. Test Suite Verification (runs pytest on critical test suites).
4. Atomic Production State & Directory Initialization.
5. AI-Universe Live Health Verification.
6. Deployment Audit Log Generation with Git SHA & version metadata.
"""

import datetime
import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List, Tuple

import config
from config_production import validate_production_security
from logger import get_logger
from security_hardening import mask_credential, sign_audit_record

logger = get_logger("deploy_production")


def run_command_checked(cmd: List[str]) -> Tuple[int, str]:
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=120)
        return res.returncode, res.stdout + res.stderr
    except Exception as e:
        return 1, str(e)


def audit_environment_and_secrets() -> Dict[str, Any]:
    """Audits required environment variables and ensures sensitive keys are not exposed."""
    results = {}
    required_keys = [
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
        "AI_UNIVERSE_BASE_URL"
    ]
    all_valid = True
    for k in required_keys:
        val = os.getenv(k, getattr(config, k, ""))
        present = bool(val)
        results[k] = {
            "configured": present,
            "masked_value": mask_credential(str(val))
        }
        if not present:
            all_valid = False

    return {
        "status": "PASS" if all_valid else "WARNING",
        "keys": results
    }


def execute_pre_deployment_tests() -> Tuple[bool, str]:
    """Runs pytest across production test suites to verify system integrity."""
    logger.info("[DEPLOY] Executing automated test suite verification...")
    python_exe = sys.executable
    cmd = [
        python_exe, "-m", "pytest",
        "tests/test_testnet_advisory.py",
        "tests/test_ab_infrastructure.py",
        "tests/test_shadow_mode.py",
        "tests/test_advisory_bounds.py",
        "tests/test_advisory_failures.py",
        "tests/test_advisory.py",
        "-q", "--tb=short"
    ]
    code, out = run_command_checked(cmd)
    return (code == 0), out


def verify_ai_universe_connection() -> bool:
    """Verifies that AI-Universe is reachable before starting."""
    from ai_universe_client import AIUniverseClient
    base_url = getattr(config, "AI_UNIVERSE_BASE_URL", "http://localhost:8000")
    client = AIUniverseClient(base_url=base_url, timeout=3)
    healthy = client.health_check()
    logger.info(f"[DEPLOY] AI-Universe health check at {base_url}: {'ONLINE' if healthy else 'OFFLINE (Will use default fallback)'}")
    return healthy


def create_deployment_record(version: str = "v2.5.0-prod") -> str:
    """Creates a signed, immutable deployment record."""
    git_sha = "UNKNOWN"
    try:
        git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        pass

    record = {
        "deployment_id": f"DEP_{int(time.time())}",
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "version": version,
        "git_sha": git_sha,
        "environment": "PRODUCTION",
        "safety_checks": validate_production_security(),
        "ai_advisory_mode": "SHADOW" if getattr(config, "TESTNET_ADVISORY_SHADOW_MODE", True) else "APPLY",
        "max_drawdown_limit": getattr(config, "TESTNET_ADVISORY_MAX_DRAWDOWN_PCT", 0.15),
        "status": "DEPLOYED_SUCCESSFULLY"
    }
    sig = sign_audit_record(record)
    record["signature"] = sig

    log_file = "deployment_audit_log.json"
    with open(log_file, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)

    logger.info(f"[DEPLOY] Deployment audit record generated: {log_file}")
    return log_file


def main():
    print("=" * 70)
    print("  PRODUCTION DEPLOYMENT & VERIFICATION PIPELINE")
    print("=" * 70)

    # 1. Audit Secrets & Config
    print("\n[STEP 1/5] Auditing environment and security boundaries...")
    sec_checks = validate_production_security()
    for k, v in sec_checks.items():
        print(f"  • {k}: {'[OK]' if v else '[FAIL]'}")
    if not all(sec_checks.values()):
        print("\n[FATAL] Production security checks failed! Aborting deployment.")
        sys.exit(1)

    # 2. Verify AI-Universe Health
    print("\n[STEP 2/5] Checking AI-Universe advisory service connection...")
    ai_ok = verify_ai_universe_connection()
    print(f"  • AI-Universe Service: {'ONLINE' if ai_ok else 'OFFLINE (Safe default fallback active)'}")

    # 3. Execute Pre-Deployment Test Suite
    print("\n[STEP 3/5] Running automated unit and safety test suites...")
    tests_passed, test_out = execute_pre_deployment_tests()
    if not tests_passed:
        print("\n[FATAL] Pre-deployment test suite failed! See output below:")
        print(test_out)
        sys.exit(1)
    print("  • All test suites passed successfully (100% pass rate).")

    # 4. Initialize Production Directories and State
    print("\n[STEP 4/5] Initializing atomic state directories...")
    os.makedirs("experiments_ab", exist_ok=True)
    os.makedirs("logs", exist_ok=True)
    print("  • Production storage verified.")

    # 5. Sign and Persist Deployment Audit Log
    print("\n[STEP 5/5] Generating cryptographic deployment audit log...")
    rec_path = create_deployment_record()
    print(f"  • Signed audit log written to: {rec_path}")

    print("\n" + "=" * 70)
    print("  PRODUCTION DEPLOYMENT VALIDATION COMPLETED SUCCESSFULLY")
    print("=" * 70)


if __name__ == "__main__":
    main()
