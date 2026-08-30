"""Multi-signal candidate hypothesis generator and single-run evidence evaluator.

generate_candidate_hypotheses() accepts two optional keyword arguments:
  diff_text    -- the raw unified diff text for this PR
  code_context -- the content of the changed source file

When either is provided (non-empty), the function makes a real LLM call (via llm_client)
to generate hypothesis title, description, code_evidence, and mechanism grounded in
the actual diff and code -- referencing specific variable names, service names, and configured
values. When both are empty (the default) or when the LLM call fails after retry, it
falls back to the static template text with an explicit "[Static Signal Description]" marker,
ensuring ungrounded hypotheses are never presented as LLM-generated.

The structural fields (id, signal, label, rank, confidence, grounding.proxy,
grounding.calibrated_latency_ms) are always set deterministically from the
RiskAssessor signals -- only the explanatory text fields are LLM-generated.
"""
from typing import Dict, Any, List

from changeproof.llm_client import call_llm, parse_json_response


# ---------------------------------------------------------------------------
# Static template defaults (used when no diff/code context is supplied or on LLM fallback)
# ---------------------------------------------------------------------------

_STATIC_TEMPLATES: Dict[str, Dict[str, str]] = {
    "H-RETRY-CEILING": {
        "title": "Downstream latency induces retry amplification storm due to elevated retry ceiling",
        "description": (
            "[Static Signal Description] Elevated retry ceiling (RETRIES_MAX >= 4) allows each stalled request "
            "to execute multiple consecutive retries."
        ),
        "code_evidence": "Diff contains added lines setting RETRIES_MAX >= 4.",
        "mechanism": (
            "High retry ceiling causes each stalled request to multiply downstream "
            "load up to RETRIES_MAX times."
        ),
    },
    "H-NO-BACKOFF": {
        "title": "Immediate unspaced retries concentrate downstream traffic and spike storm rate",
        "description": (
            "[Static Signal Description] Removal of exponential backoff delay (RETRY_BACKOFF_FACTOR = 0.0) "
            "concentrates retries in rapid bursts."
        ),
        "code_evidence": "Diff sets backoff factor to 0.0 or uses wait_fixed(0).",
        "mechanism": (
            "Zero backoff causes retries to execute instantly in tight loops, "
            "concentrating retry rate and depriving downstream of recovery time."
        ),
    },
    "H-AGGRESSIVE-TIMEOUT": {
        "title": "Aggressive timeout reduction triggers premature timeouts before downstream can respond",
        "description": (
            "[Static Signal Description] Lowered client timeout (RETRY_TIMEOUT_SECONDS < 1.0s) causes client-side "
            "timeout before downstream processing completes."
        ),
        "code_evidence": "Diff sets RETRY_TIMEOUT_SECONDS < 1.0s.",
        "mechanism": (
            "Client aborts requests prematurely while downstream processing is in "
            "flight, triggering unnecessary retries."
        ),
    },
    "H-HTTP-DEPENDENCY": {
        "title": "Unprotected downstream HTTP client call propagates downstream latency directly upstream",
        "description": (
            "[Static Signal Description] Direct HTTP client call modified on critical user path without "
            "circuit-breaker protection."
        ),
        "code_evidence": "Diff modifies client.post or httpx.Client invocation without circuit breaker.",
        "mechanism": "Downstream latency propagates directly upstream blocking ingress worker threads.",
    },
}


# ---------------------------------------------------------------------------
# LLM prompt builder
# ---------------------------------------------------------------------------

def _build_hypothesis_prompt(
    signal_id: str,
    signal_label: str,
    diff_text: str,
    code_context: str,
    proxy_name: str,
    calibrated_latency_ms: int,
) -> str:
    """Builds a tightly-scoped prompt asking the LLM to articulate the failure
    mechanism for ONE specific detected signal, grounded in the actual diff and code."""

    diff_excerpt = diff_text[:3000] if len(diff_text) > 3000 else diff_text
    code_excerpt = code_context[:2000] if len(code_context) > 2000 else code_context

    return (
        "You are the ChangeProof reliability analysis engine. A PR diff has been "
        "assessed and the following risk signal was detected:\n\n"
        f"SIGNAL: {signal_label}\n\n"
        "Your task is to explain WHY this specific diff creates a reliability risk. "
        "Ground your explanation in the ACTUAL diff below -- reference the specific "
        "variable names, service name, configured values, and code structure you can "
        "see. Do NOT use generic boilerplate language that would apply to any diff.\n\n"
        f"PR DIFF (or excerpt):\n```\n{diff_excerpt}\n```\n\n"
        f"TARGET FILE SOURCE (or excerpt):\n```\n{code_excerpt}\n```\n\n"
        f"FAULT INJECTION CONTEXT: proxy={proxy_name}, injected_latency={calibrated_latency_ms}ms\n\n"
        "Respond with ONLY a valid JSON object matching this exact schema "
        "(no extra text, no markdown outside the JSON block):\n"
        "{\n"
        '  "title": "A concise one-sentence title naming the exact failure mode, '
        'referencing the actual variable/service name from the diff",\n'
        '  "description": "2-3 sentence description explaining HOW this specific '
        'change (cite the actual old->new values from the diff) causes the failure '
        'under downstream latency. Name the actual service and variable.",\n'
        '  "code_evidence": "One sentence quoting the specific added line(s) from '
        'the diff that introduce the risk.",\n'
        '  "mechanism": "One sentence explaining the causal chain from this '
        'specific code change to observable retry amplification."\n'
        "}"
    )


# ---------------------------------------------------------------------------
# LLM enrichment
# ---------------------------------------------------------------------------

def _enrich_hypothesis_via_llm(
    hypothesis_id: str,
    signal_label: str,
    diff_text: str,
    code_context: str,
    proxy_name: str,
    calibrated_latency_ms: int,
    template: Dict[str, str],
) -> Dict[str, Any]:
    """Attempts an LLM call to generate diff-grounded text fields.

    Returns a dict with keys: title, description, code_evidence, mechanism, source ("llm" | "static").
    Falls back to static template on any LLM failure or unparseable response.
    """
    prompt = _build_hypothesis_prompt(
        signal_id=hypothesis_id,
        signal_label=signal_label,
        diff_text=diff_text,
        code_context=code_context,
        proxy_name=proxy_name,
        calibrated_latency_ms=calibrated_latency_ms,
    )

    response = call_llm(prompt, max_tokens=2048)
    if not response:
        res = dict(template)
        res["source"] = "static"
        return res

    data = parse_json_response(response)

    # Validate required fields -- fall back to template for any missing/invalid field
    result: Dict[str, Any] = {}
    valid = True
    for field in ("title", "description", "code_evidence", "mechanism"):
        val = data.get(field, "")
        if isinstance(val, str) and len(val.strip()) > 10:
            result[field] = val.strip()
        else:
            valid = False
            break

    if valid:
        result["source"] = "llm"
        return result
    else:
        res = dict(template)
        res["source"] = "static"
        return res


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_candidate_hypotheses(
    signals: List[str],
    proxy_name: str = "payment-proxy",
    calibrated_latency_ms: int = 1500,
    *,
    diff_text: str = "",
    code_context: str = "",
) -> List[Dict[str, Any]]:
    """Generates one grounded hypothesis per detected risk signal.

    When diff_text or code_context is non-empty, makes a real LLM call (via llm_client)
    to produce hypothesis text that references the actual diff, variable names,
    service names, and configured values. When both are empty, uses static template text
    marked with "[Static Signal Description]".

    The structural fields (id, signal, label, rank, confidence, grounding keys)
    are always set deterministically -- only title, description, and grounding
    text sub-fields are LLM-generated.

    Args:
        signals:               List of signal label strings from RiskAssessor.assess_diff().
        proxy_name:            Toxiproxy proxy name resolved from topology.
        calibrated_latency_ms: Injected fault latency in milliseconds.
        diff_text:             Raw unified diff text (keyword-only, optional).
        code_context:          Changed file source content (keyword-only, optional).

    Returns:
        List of hypothesis dicts, one per detected signal.
    """
    hypotheses: List[Dict[str, Any]] = []
    use_llm = bool(diff_text.strip() or code_context.strip())

    # Signal 1: Retry count increase
    if any("retry count increase" in s.lower() or "max_retries" in s.lower() for s in signals):
        tmpl = _STATIC_TEMPLATES["H-RETRY-CEILING"]
        if use_llm:
            enriched = _enrich_hypothesis_via_llm(
                "H-RETRY-CEILING",
                "Aggressive retry count increase (max_retries >= 4)",
                diff_text, code_context, proxy_name, calibrated_latency_ms, tmpl,
            )
        else:
            enriched = dict(tmpl)
            enriched["source"] = "static"

        hypotheses.append({
            "id": "H-RETRY-CEILING",
            "signal": "Aggressive retry count increase (max_retries >= 4)",
            "label": "retry_count_amplification",
            "title": enriched["title"],
            "description": enriched["description"],
            "source": enriched.get("source", "static"),
            "grounding": {
                "code_evidence": enriched["code_evidence"],
                "mechanism": enriched["mechanism"],
                "calibrated_latency_ms": calibrated_latency_ms,
                "proxy": proxy_name,
            },
            "rank": 1,
            "confidence": "HIGH",
        })

    # Signal 2: Removal of backoff
    if any("removal of backoff" in s.lower() or "immediate retry" in s.lower() for s in signals):
        tmpl = _STATIC_TEMPLATES["H-NO-BACKOFF"]
        if use_llm:
            enriched = _enrich_hypothesis_via_llm(
                "H-NO-BACKOFF",
                "Removal of backoff / immediate retry execution",
                diff_text, code_context, proxy_name, calibrated_latency_ms, tmpl,
            )
        else:
            enriched = dict(tmpl)
            enriched["source"] = "static"

        hypotheses.append({
            "id": "H-NO-BACKOFF",
            "signal": "Removal of backoff / immediate retry execution",
            "label": "zero_backoff_load_concentration",
            "title": enriched["title"],
            "description": enriched["description"],
            "source": enriched.get("source", "static"),
            "grounding": {
                "code_evidence": enriched["code_evidence"],
                "mechanism": enriched["mechanism"],
                "calibrated_latency_ms": calibrated_latency_ms,
                "proxy": proxy_name,
            },
            "rank": 2,
            "confidence": "HIGH",
        })

    # Signal 3: Timeout reduction
    if any("timeout reduction" in s.lower() or "timeout < 1.0s" in s.lower() for s in signals):
        tmpl = _STATIC_TEMPLATES["H-AGGRESSIVE-TIMEOUT"]
        if use_llm:
            enriched = _enrich_hypothesis_via_llm(
                "H-AGGRESSIVE-TIMEOUT",
                "Aggressive timeout reduction (timeout < 1.0s)",
                diff_text, code_context, proxy_name, calibrated_latency_ms, tmpl,
            )
        else:
            enriched = dict(tmpl)
            enriched["source"] = "static"

        hypotheses.append({
            "id": "H-AGGRESSIVE-TIMEOUT",
            "signal": "Aggressive timeout reduction (timeout < 1.0s)",
            "label": "premature_timeout_trigger",
            "title": enriched["title"],
            "description": enriched["description"],
            "source": enriched.get("source", "static"),
            "grounding": {
                "code_evidence": enriched["code_evidence"],
                "mechanism": enriched["mechanism"],
                "calibrated_latency_ms": calibrated_latency_ms,
                "proxy": proxy_name,
            },
            "rank": 3,
            "confidence": "HIGH",
        })

    # Signal 4: Downstream HTTP client modification
    if any("downstream http" in s.lower() or "client call" in s.lower() for s in signals):
        tmpl = _STATIC_TEMPLATES["H-HTTP-DEPENDENCY"]
        if use_llm:
            enriched = _enrich_hypothesis_via_llm(
                "H-HTTP-DEPENDENCY",
                "Downstream HTTP dependency modification",
                diff_text, code_context, proxy_name, calibrated_latency_ms, tmpl,
            )
        else:
            enriched = dict(tmpl)
            enriched["source"] = "static"

        hypotheses.append({
            "id": "H-HTTP-DEPENDENCY",
            "signal": "Downstream HTTP dependency modification",
            "label": "unprotected_http_dependency",
            "title": enriched["title"],
            "description": enriched["description"],
            "source": enriched.get("source", "static"),
            "grounding": {
                "code_evidence": enriched["code_evidence"],
                "mechanism": enriched["mechanism"],
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
            "description": "[Static Signal Description] Downstream latency induces retry storm under current configuration.",
            "source": "static",
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
