"""Proof certificate rendering module."""
from jinja2 import Template
from typing import Dict, Any

DEFAULT_MD_TEMPLATE = """# CHANGE PROOF CERTIFICATE
Generated: {{ timestamp }} | Experiment: {{ experiment_id }} | Commit: {{ git_commit }}

{% if verification_status == "PASS" %}
> **STATUS**: [PASS] PROVEN & VERIFIED SAFE — Patch passed deterministic criteria.
{% elif verification_status == "INCONCLUSIVE" %}
> **STATUS**: [INCONCLUSIVE] Insufficient runtime evidence to prove safety. NOT CERTIFIED FOR PRODUCTION.
{% else %}
> **STATUS**: [FAIL] VERIFICATION FAILED — Remediation patch failed deterministic assertions.
{% endif %}

## Evaluation Summary
- **Risk Level**: {{ risk_level }} (Score: {{ risk_score }}/100)
- **Failure Class**: Retry Amplification / Retry Storm
- **Primary Hypothesis**: {{ hypothesis_title }} (Confidence: {{ hypothesis_confidence }})
- **Deterministic Verification Verdict**: **{{ verification_status }}**

{% if candidate_hypotheses %}
## Candidate Hypotheses Evaluated (Multi-Signal Analysis)
{% if candidate_hypotheses | length > 1 %}
> **Note on Joint Attribution**: {{ candidate_hypotheses | length }} signals were changed together in this diff and evaluated via a single combined experiment. This confirms the **COMBINATION** produced the observed failure; it does not isolate which individual signal(s) would be sufficient on their own. Independent attribution would require separate ablation experiments per signal.
{% endif %}

| Hypothesis ID | Signal / Trigger | Mechanism & Grounding | Experiment Verdict | Telemetry Evidence |
|---|---|---|---|---|
{% for h in candidate_hypotheses %}
| **{{ h.id }}** | {{ h.signal }} | {{ h.description }} | `{{ h.status_label }}` | {{ h.evidence }} |
{% endfor %}
{% endif %}

## Key Metric Observations & Throughput Context
| Metric | Pre-Patch (Broken) | Post-Patch (Remediated) | Target / Safe Bound | Status |
|---|---|---|---|---|
| **Retries / Request** | **{{ pre_retries_per_request | default(pre_summary.retries_per_request if pre_summary else 'N/A') }}** | **{{ post_retries_per_request | default(post_summary.retries_per_request if post_summary else 'N/A') }}** | `> 2.0` (Pre) / `<= 1.1` (Post) | {{ "CONTROLLED" if verification_status == "PASS" else "UNBOUNDED" }} |
| **Throughput (req/s)** | {{ pre_throughput | default(pre_summary.throughput_req_per_sec if pre_summary else 'N/A') }} req/s | {{ post_throughput | default(post_summary.throughput_req_per_sec if post_summary else 'N/A') }} req/s | Context (Normalized capacity) | Reported |
| **Total Requests** | {{ pre_total_requests | default(pre_summary.total_requests if pre_summary else 'N/A') }} | {{ post_total_requests | default(post_summary.total_requests if post_summary else 'N/A') }} | `>= 100` Sample Size | Validated |
| **Rate (retries/min)** | {{ pre_rate_per_min | default(pre_summary.rate_per_min if pre_summary else 'N/A') }} /min | {{ post_rate_per_min | default(post_summary.rate_per_min if post_summary else 'N/A') }} /min | Context (Un-normalized rate) | Reported |

## Deterministic Assertion Verification
| Metric | Phase | Observed Value | Condition | Condition Met |
|---|---|---|---|---|
{% for row in diff_table %}
| {{ row.metric }} | {{ row.phase }} | {{ row.observed_value }} | `{{ row.condition }}` | {{ "YES" if row.condition_met else "NO" }} |
{% endfor %}

{% if patch_diff %}
## Recommended Remediation Patch
{% if patch_reasoning %}
> **Remediation Reasoning** [{{ patch_source | default('LLM') | upper }}]: {{ patch_reasoning }}

{% endif %}
```diff
{{ patch_diff }}
```
{% endif %}

## Reproducibility & Artifacts
- **Reproduction Capsule**: `{{ capsule_path }}`
- **Replay Command**: `python -m changeproof.replay {{ capsule_path }}`

## Human Engineering Decision
[ ] APPROVED FOR DEPLOYMENT   [ ] REJECTED   [ ] ESCALATE FOR REVIEW
Reviewer Signature: _______________________ Date: _______________
"""

class CertificateGenerator:
    def __init__(self, template_str: str = DEFAULT_MD_TEMPLATE):
        self.template = Template(template_str)

    def render(self, context: Dict[str, Any]) -> str:
        return self.template.render(context)

    def generate_and_save(self, context: Dict[str, Any], output_path: str) -> str:
        content = self.render(context)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        return output_path
