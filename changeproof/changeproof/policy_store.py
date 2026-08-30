"""Policy store reader, writer, and validator."""
import json
import os
from typing import List, Dict, Any

REQUIRED_FIELDS = {"policy_id", "created_at", "author", "trigger", "rule", "decision", "rationale", "experiment_id"}

def validate_policy(policy: Dict[str, Any]) -> bool:
    if not isinstance(policy, dict):
        return False
    return REQUIRED_FIELDS.issubset(set(policy.keys()))

def load_policies(path: str = "policy_store.json") -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
            if isinstance(data, list):
                return [p for p in data if validate_policy(p)]
            return []
        except json.JSONDecodeError:
            return []

def record_policy(policy: Dict[str, Any], path: str = "policy_store.json") -> bool:
    if not validate_policy(policy):
        raise ValueError(f"Invalid policy schema. Must contain: {REQUIRED_FIELDS}")
    policies = load_policies(path)
    policies.append(policy)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(policies, f, indent=2)
    return True
