"""Primary LLM reasoning and coding agent implementation."""
import os
import json
from typing import Dict, Any
from changeproof.tools import (
    propose_hypothesis,
)
from changeproof.risk_assessor import RiskAssessor
from changeproof.context_builder import ContextBuilder
from changeproof.experiment_synthesizer import ExperimentSynthesizer
from changeproof.hypothesis_evaluator import generate_candidate_hypotheses

SYSTEM_PROMPT = """You are the ChangeProof Reliability Investigator Agent.
Your role is to evaluate high-risk code changes by constructing counterfactual experiments,
reproducing failures under real faults, generating minimal remediation patches, and replaying experiments.

You have access to 8 engineering tools:
1. read_file(path)
2. read_topology()
3. read_runtime_snapshot()
4. propose_hypothesis(hypotheses)
5. run_experiment(spec)
6. read_metrics(run_id)
7. write_patch(diff)
8. run_tests()

Rules:
- Form hypotheses grounded in code, topology, and metrics.
- Execute experiments via run_experiment(spec) to test failure hypotheses.
- Propose minimal code patches for reproduced failures.
- You propose; the deterministic verifier decides.
"""


class ChangeProofAgent:
    def __init__(self, run_dir: str = "runs/agent_run"):
        self.run_dir = run_dir
        os.makedirs(self.run_dir, exist_ok=True)
        self.trajectory_log = os.path.join(self.run_dir, "agent_trajectory.jsonl")

    def log_action(self, action_type: str, data: Dict[str, Any]):
        entry = {"action_type": action_type, "data": data}
        with open(self.trajectory_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def run_investigation(self, pr_diff: str) -> Dict[str, Any]:
        """Runs grounded investigation on the PR diff with multi-hypothesis formulation."""
        self.log_action("start", {"diff_length": len(pr_diff)})

        # 1. Risk Assessment
        assessor = RiskAssessor()
        risk_scorecard = assessor.assess_diff(pr_diff)
        self.log_action("risk_assessed", risk_scorecard)

        # 2. Context Building
        builder = ContextBuilder()
        context = builder.build_context(pr_diff)
        self.log_action("context_built", {"topology_services": list(context["topology"]["services"].keys())})

        # 3. Dynamic Topology-Driven Experiment Synthesis
        synthesizer = ExperimentSynthesizer()
        proxy_name = "payment-proxy"
        calibrated_latency_ms = 1500
        synthesized_spec: Dict[str, Any] = {}

        try:
            synthesized_spec = synthesizer.synthesize(pr_diff=pr_diff, case_id="agent-investigation-spec")
            fault_info = synthesized_spec.get("fault", {})
            toxic_info = fault_info.get("toxic", {}).get("attributes", {})
            proxy_name = fault_info.get("proxy", "payment-proxy")
            calibrated_latency_ms = toxic_info.get("latency", 1500)
        except Exception:
            pass

        # 4. Resolve changed file content for LLM-grounded hypothesis generation.
        # Extract changed file path from diff and read its current content.
        code_context = ""
        changed_file_path = ""
        for line in pr_diff.splitlines():
            if line.startswith("+++ b/"):
                changed_file_path = line[6:].strip()
                break
        if changed_file_path and os.path.exists(changed_file_path):
            try:
                with open(changed_file_path, "r", encoding="utf-8") as f:
                    code_context = f.read()
            except Exception:
                pass

        # 5. Multi-Signal Hypothesis Formulation (one LLM-grounded hypothesis per detected signal)
        hypotheses = generate_candidate_hypotheses(
            signals=risk_scorecard.get("signals", []),
            proxy_name=proxy_name,
            calibrated_latency_ms=calibrated_latency_ms,
            diff_text=pr_diff,
            code_context=code_context,
        )

        # Attach the synthesized spec to the primary hypothesis
        if hypotheses and synthesized_spec:
            hypotheses[0]["synthesized_spec"] = synthesized_spec

        propose_hypothesis(hypotheses, output_path=os.path.join(self.run_dir, "hypothesis.json"))
        self.log_action("hypotheses_proposed", {
            "count": len(hypotheses),
            "hypotheses": hypotheses,
        })

        return {
            "status": "INVESTIGATION_COMPLETED",
            "risk_scorecard": risk_scorecard,
            "hypotheses": hypotheses,
            "run_dir": self.run_dir,
        }
