"""ChangeProof unified command line interface."""
import os
import sys
import json
import time
import argparse
from typing import Dict, Any
from changeproof.agent import ChangeProofAgent
from changeproof.verifier import verify
from changeproof.policy_store import record_policy

def apply_human_decision_to_cert(
    cert_path: str,
    decision: str,
    author: str,
    rationale: str,
) -> str:
    """Updates the Human Engineering Decision section of a proof certificate."""
    if not os.path.exists(cert_path):
        raise FileNotFoundError(f"Certificate not found: {cert_path}")

    with open(cert_path, "r", encoding="utf-8") as f:
        content = f.read()

    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    appr_box = "[X]" if decision.upper() == "APPROVED" else "[ ]"
    rej_box = "[X]" if decision.upper() == "REJECTED" else "[ ]"
    esc_box = "[X]" if decision.upper() in ("ESCALATE", "HOLD") else "[ ]"

    new_decision_block = f"""## Human Engineering Decision
{appr_box} APPROVED FOR DEPLOYMENT   {rej_box} REJECTED   {esc_box} ESCALATE FOR REVIEW
Reviewer Signature: {author} | Date: {ts}
Decision Rationale: {rationale}
"""

    if "## Human Engineering Decision" in content:
        parts = content.split("## Human Engineering Decision")
        updated_content = parts[0] + new_decision_block
    else:
        updated_content = content + "\n\n" + new_decision_block

    with open(cert_path, "w", encoding="utf-8") as f:
        f.write(updated_content)

    return updated_content

def prompt_human_decision(experiment_id: str) -> Dict[str, Any]:
    """Interactive human approval gate for deployment decisions."""
    print(f"\n--- ChangeProof Human Engineering Approval Gate [{experiment_id}] ---")
    print("1) Approve & Deploy Remediation")
    print("2) Reject Remediation")
    print("3) Escalate for Further Review")
    choice = input("Enter decision [1-3] (default 3): ").strip()
    
    if choice == "1":
        return {"status": "APPROVED", "action": "deploy", "experiment_id": experiment_id}
    elif choice == "2":
        return {"status": "REJECTED", "action": "block", "experiment_id": experiment_id}
    else:
        return {"status": "HOLD", "action": "escalate_for_review", "experiment_id": experiment_id}

def main():
    parser = argparse.ArgumentParser(description="ChangeProof Agentic Reliability System")
    subparsers = parser.add_subparsers(dest="command")

    # Command: run
    run_parser = subparsers.add_parser("run", help="Run ChangeProof evaluation on a PR diff")
    run_parser.add_argument("--pr", required=True, help="Path to unified diff patch file")

    # Command: verify
    verify_parser = subparsers.add_parser("verify", help="Run deterministic verifier")
    verify_parser.add_argument("--pre", required=True, help="Pre-patch metrics CSV")
    verify_parser.add_argument("--post", required=True, help="Post-patch metrics CSV")
    verify_parser.add_argument("--spec", required=True, help="Experiment YAML spec")

    # Command: decide
    decide_parser = subparsers.add_parser("decide", help="Record human engineering decision on a certificate")
    decide_parser.add_argument("--cert", required=True, help="Path to proof_certificate.md")
    decide_parser.add_argument("--decision", required=True, choices=["APPROVED", "REJECTED", "ESCALATE"], help="Decision verdict")
    decide_parser.add_argument("--author", required=True, help="Reviewer name / title")
    decide_parser.add_argument("--rationale", required=True, help="Engineering rationale")
    decide_parser.add_argument("--policy-rule", help="Optional organizational rule to store in policy store")
    decide_parser.add_argument("--experiment-id", default="case-01", help="Associated experiment ID")

    args = parser.parse_args()

    if args.command == "run":
        if not os.path.exists(args.pr):
            print(f"Error: PR diff file not found: {args.pr}")
            sys.exit(1)
        with open(args.pr, "r", encoding="utf-8") as f:
            diff_text = f.read()

        agent = ChangeProofAgent()
        result = agent.run_investigation(diff_text)
        print(json.dumps(result, indent=2))

    elif args.command == "verify":
        import yaml
        with open(args.spec, "r", encoding="utf-8") as f:
            spec = yaml.safe_load(f)
        ver_res = verify(args.pre, args.post, spec.get("assertions", {}))
        print(json.dumps(ver_res.to_dict(), indent=2))

    elif args.command == "decide":
        apply_human_decision_to_cert(
            cert_path=args.cert,
            decision=args.decision,
            author=args.author,
            rationale=args.rationale,
        )
        print(f"Recorded [{args.decision}] decision on {args.cert}")

        if args.policy_rule:
            policy_entry = {
                "policy_id": f"POL-{int(time.time())}",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "author": args.author,
                "trigger": "human_governance_decision",
                "rule": args.policy_rule,
                "decision": f"{args.decision}_POLICY",
                "rationale": args.rationale,
                "experiment_id": args.experiment_id,
            }
            record_policy(policy_entry)
            print(f"Recorded governance policy in policy_store.json: {policy_entry['policy_id']} ('{args.policy_rule}')")

    else:
        parser.print_help()

if __name__ == "__main__":
    main()