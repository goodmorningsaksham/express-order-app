"""Deterministic AST and regex risk assessor for PR diffs with policy store integration."""
import re
from typing import Dict, Any, List
from changeproof.policy_store import load_policies

class RiskAssessor:
    def __init__(self, policy_path: str = "policy_store.json"):
        self.policy_path = policy_path

    def assess_diff(self, diff_text: str) -> Dict[str, Any]:
        """Calculates risk score based on deterministic signals in the diff and stored human policies.

        All patterns are anchored to the unified-diff addition prefix (^\+) with
        a negative lookahead (?!\+) to exclude the '+++ b/...' file-header lines,
        so that only genuine added lines trigger signals.

        Context lines (space-prefixed) and removed lines (^-) never match.
        """
        score = 0
        signals_detected: List[str] = []

        # Signal 1: Retry count increase on added lines only.
        # Excludes '+++ b/...' file header lines via negative lookahead (?!\+).
        if re.search(r'^\+(?!\+).*(?:RETRIES_MAX|max_retries)\s*=.*(["\']?([4-9]|\d{2,})["\']?)', diff_text, re.MULTILINE):
            score += 30
            signals_detected.append("Aggressive retry count increase (max_retries >= 4)")

        # Signal 2: Backoff removal on added lines only.
        if re.search(r'^\+(?!\+).*(?:RETRY_BACKOFF_FACTOR|backoff)\s*=.*(["\']?0(\.0)?["\']?)', diff_text, re.MULTILINE) or \
           re.search(r'^\+(?!\+).*wait_fixed\(0\)', diff_text, re.MULTILINE):
            score += 20
            signals_detected.append("Removal of backoff / immediate retry execution")

        # Signal 3: Aggressive timeout reduction on added lines only.
        if re.search(r'^\+(?!\+).*(?:RETRY_TIMEOUT_SECONDS|timeout)\s*=.*(["\']?0\.[1-9]["\']?)', diff_text, re.MULTILINE):
            score += 20
            signals_detected.append("Aggressive timeout reduction (timeout < 1.0s)")

        # Signal 4: Touches networking/client call without circuit breaker
        if "+with httpx.Client" in diff_text or "+client.post" in diff_text:
            score += 15
            signals_detected.append("Downstream HTTP dependency modification")

        # Signal 5: Violation of stored human governance policies
        policies = load_policies(self.policy_path)
        for p in policies:
            rule_text = p.get("rule", "").lower()
            # Match e.g. "retries must not exceed 4" or "payment-service retries must not exceed 4"
            match = re.search(r"retries must not exceed (\d+)", rule_text)
            if match:
                limit = int(match.group(1))
                diff_m = re.search(r'^\+(?!\+).*(?:RETRIES_MAX|max_retries)\s*=.*(["\']?(\d+)["\']?)', diff_text, re.MULTILINE)
                if diff_m:
                    val = int(diff_m.group(2))
                    if val > limit:
                        score += 35
                        pol_id = p.get("policy_id", "POL")
                        signals_detected.append(f"Stored Human Policy Violation ({pol_id}): retries ({val}) exceed human limit ({limit})")

        # Signal 6: Test-only discount.
        # Only fires when the diff contains at least one +++ / --- file header
        # AND every such header points into the tests/ directory.
        file_headers = [
            line for line in diff_text.splitlines()
            if line.startswith("+++ ") or line.startswith("--- ")
        ]
        if file_headers and all(
            line.startswith("+++ b/tests/") or line.startswith("--- a/tests/")
            for line in file_headers
        ):
            score = max(0, score - 40)
            signals_detected.append("Test-only modifications detected (discounted)")

        if score >= 50:
            level = "HIGH"
        elif score >= 20:
            level = "MEDIUM"
        else:
            level = "LOW"

        return {
            "score": score,
            "level": level,
            "signals": signals_detected,
            "requires_experiment": level == "HIGH",
        }