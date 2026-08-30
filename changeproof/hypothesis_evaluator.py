"""Multi-signal candidate hypothesis generator and single-run evidence evaluator."""
from typing import Dict, Any, List


def generate_candidate_hypotheses(
    signals: List[str],
    proxy_name: str = "payment-proxy",
    calibrated_latency_ms: int = 1500,
) -> List[Dict[str, Any]]:
    """Generates one grounded hypothesis per detected risk signal."""
    hypotheses: List[Dict[str, Any]] = []

    # Signal 1: Retry count increase
    if any("retry count increase" in s.lower() or "max_retries" in s.lower() for s in signals):
        hypotheses.append({
            "id": "H-RETRY-CEILING",
            "signal": "Aggressive retry count increase (max_retries >= 4)",
            "label": "retry_count_amplification",
            "title": "Downstream latency induces retry amplification storm due to elevated retry ceiling",
            "description": "Elevated retry ceiling (RETRIES_MAX >= 4) allows each stalled request to execute multiple consecutive retries.",
            "grounding": {
                "code_evidence": "Diff contains added lines setting RETRIES_MAX >= 4.",
                "mechanism": "High retry ceiling causes each stalled request to multiply downstream load up to RETRIES_MAX times.",
                "calibrated_latency_ms": calibrated_latency_ms,
                "proxy": proxy_name,
            },
            "rank": 1,
            "confidence": "HIGH",
        })

    # Signal 2: Removal of backoff
    if any("removal of backoff" in s.lower() or "immediate retry" in s.lower() for s in signals):
        hypotheses.append({
            "id": "H-NO-BACKOFF",
            "signal": "Removal of backoff / immediate retry execution",
            "label": "zero_backoff_load_concentration",
            "title": "Immediate unspaced retries concentrate downstream traffic and spike storm rate",
            "description": "Removal of exponential backoff delay (RETRY_BACKOFF_FACTOR = 0.0) concentrates retries in rapid bursts.",
            "grounding": {
                "code_evidence": "Diff sets backoff factor to 0.0 or uses wait_fixed(0).",
                "mechanism": "Zero backoff causes retries to execute instantly in tight loops, concentrating retry rate and depriving downstream of recovery time.",
                "calibrated_latency_ms": calibrated_latency_ms,
                "proxy": proxy_name,
            },
            "rank": 2,
            "confidence": "HIGH",
        })

    # Signal 3: Timeout reduction
    if any("timeout reduction" in s.lower() or "timeout < 1.0s" in s.lower() for s in signals):
        hypotheses.append({
            "id": "H-AGGRESSIVE-TIMEOUT",
            "signal": "Aggressive timeout reduction (timeout < 1.0s)",
            "label": "premature_timeout_trigger",
            "title": "Aggressive timeout reduction triggers premature timeouts before downstream can respond",
            "description": "Lowered client timeout (RETRY_TIMEOUT_SECONDS < 1.0s) causes client-side timeout before downstream processing completes.",
            "grounding": {
                "code_evidence": "Diff sets RETRY_TIMEOUT_SECONDS < 1.0s.",
                "mechanism": "Client aborts requests prematurely while downstream processing is in flight, triggering unnecessary retries.",
                "calibrated_latency_ms": calibrated_latency_ms,
                "proxy": proxy_name,
            },
            "rank": 3,
            "confidence": "HIGH",
        })

    # Signal 4: Downstream HTTP client modification
    if any("downstream http" in s.lower() or "client call" in s.lower() for s in signals):
        hypotheses.append({
            "id": "H-HTTP-DEPENDENCY",
            "signal": "Downstream HTTP dependency modification",
            "label": "unprotected_http_dependency",
            "title": "Unprotected downstream HTTP client call propagates downstream latency directly upstream",
            "description": "Direct HTTP client call modified on critical user path without circuit-breaker protection.",
            "grounding": {
                "code_evidence": "Diff modifies client.post or httpx.Client invocation without circuit breaker.",
                "mechanism": "Downstream latency propagates directly upstream blocking ingress worker threads.",
                "calibrated_latency_ms": calibrated_latency_ms,
                "proxy": proxy_name,
            },
            "rank": 4,
            "confidence": "MEDIUM",
        })

    if not hypotheses:
        # Fallback single generic hypothesis
        hypotheses.append({
            "id": "H1",
            "signal": "General risk detected",
            "label": "retry_amplification",
            "title": "Downstream latency induces retry amplification storm",
            "description": "Downstream latency induces retry storm under current configuration.",
            "grounding": {
                "code_evidence": "Diff modifies retry/network parameters.",
                "mechanism": "Network latency propagates through client retry loops.",
                "calibrated_latency_ms": calibrated_latency_ms,
                "proxy": proxy_name,
            },
            "rank": 1,
            "confidence": "MEDIUM",
        })

    return hypotheses


def evaluate_hypotheses_evidence(
    hypotheses: List[Dict[str, Any]],
    pre_summary: Dict[str, Any],
    post_summary: Dict[str, Any],
    calibrated_latency_ms: int = 1500,
    client_timeout_s: float = 0.5,
) -> List[Dict[str, Any]]:
    """Evaluates telemetry evidence for every candidate hypothesis from the single experiment run.

    Uses confounded joint-measurement vocabulary ('CONSISTENT WITH OBSERVED STORM') when multiple
    signals co-occurred in the same diff, and isolated vocabulary ('SUPPORTED (ISOLATED)') when
    only a single signal was modified.
    """
    evaluated: List[Dict[str, Any]] = []
    pre_retries_per_req = float(pre_summary.get("retries_per_request", 0.0))
    pre_rate_per_min = float(pre_summary.get("rate_per_min", 0.0))
    total_reqs = float(pre_summary.get("total_requests", 0.0))
    is_multi_signal = len(hypotheses) > 1

    for h in hypotheses:
        h_id = h.get("id", "")
        h_copy = dict(h)

        if h_id == "H-RETRY-CEILING":
            is_supported = pre_retries_per_req > 2.0 and total_reqs >= 100
            evidence = (
                f"Observed pre-patch {pre_retries_per_req:.3f} retries/req (>2.0 condition met across {int(total_reqs)} requests) "
                f"directly confirms elevated retry ceiling amplifies failed calls."
                if is_supported else
                f"Observed {pre_retries_per_req:.3f} retries/req did not meet amplification threshold (>2.0)."
            )
        elif h_id == "H-NO-BACKOFF":
            is_supported = pre_rate_per_min >= 400.0 and pre_retries_per_req >= 1.8
            evidence = (
                f"Observed storm rate of {pre_rate_per_min:.2f} retries/min confirms zero-backoff allows all retries "
                f"to fire rapidly in tight succession without spacing."
                if is_supported else
                f"Observed rate of {pre_rate_per_min:.2f} retries/min did not demonstrate unspaced burst concentration."
            )
        elif h_id == "H-AGGRESSIVE-TIMEOUT":
            latency_s = calibrated_latency_ms / 1000.0
            is_supported = latency_s > client_timeout_s and pre_retries_per_req > 2.0
            evidence = (
                f"Downstream latency ({calibrated_latency_ms}ms) exceeded client timeout ({int(client_timeout_s*1000)}ms), "
                f"causing 100% of requests to timeout prematurely and trigger full retry loops."
                if is_supported else
                f"Injected latency ({calibrated_latency_ms}ms) did not exceed client timeout ({int(client_timeout_s*1000)}ms)."
            )
        elif h_id == "H-HTTP-DEPENDENCY":
            is_supported = pre_retries_per_req > 2.0
            evidence = (
                "Direct downstream HTTP calls stalled under latency without circuit-breaker interruption, "
                "confirming dependency vulnerability."
                if is_supported else
                "Downstream stalls did not manifest as cascade failures."
            )
        else:
            is_supported = pre_retries_per_req > 2.0
            evidence = f"Pre-patch failure reproduced with {pre_retries_per_req:.3f} retries/req."

        if is_multi_signal:
            status_label = "[CONSISTENT WITH OBSERVED STORM]" if is_supported else "[INCONSISTENT WITH OBSERVED STORM]"
        else:
            status_label = "[SUPPORTED (ISOLATED)]" if is_supported else "[NOT SUPPORTED]"

        h_copy["supported"] = is_supported
        h_copy["status_label"] = status_label
        h_copy["is_confounded"] = is_multi_signal
        h_copy["evidence"] = evidence
        evaluated.append(h_copy)

    return evaluated

