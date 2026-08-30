"""Context builder extracting dependency topology and Prometheus runtime snapshots."""
import os
import yaml
from typing import Dict, Any, Optional
from changeproof.telemetry import PrometheusCollector
from changeproof.policy_store import load_policies

class ContextBuilder:
    def __init__(self, compose_path: str = "docker-compose.yml", policy_path: str = "policy_store.json"):
        self.compose_path = compose_path
        self.policy_path = policy_path

    def build_topology(self) -> Dict[str, Any]:
        if not os.path.exists(self.compose_path):
            return {"services": {}, "networks": []}

        with open(self.compose_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        services = {}
        for s_name, s_cfg in data.get("services", {}).items():
            services[s_name] = {
                "ports": s_cfg.get("ports", []),
                "depends_on": s_cfg.get("depends_on", []),
                "environment": s_cfg.get("environment", []),
            }

        return {
            "services": services,
            "networks": list(data.get("networks", {}).keys()),
        }

    def build_context(self, pr_diff: str, prometheus_url: Optional[str] = "http://localhost:9090") -> Dict[str, Any]:
        topology = self.build_topology()
        policies = load_policies(self.policy_path)
        
        runtime_snapshot = {}
        if prometheus_url:
            collector = PrometheusCollector(prometheus_url)
            if collector.is_healthy():
                runtime_snapshot = {
                    "requests": collector.query_instant("http_requests_total"),
                    "retries": collector.query_instant("retry_count_total"),
                }

        return {
            "topology": topology,
            "policies": policies,
            "runtime_snapshot": runtime_snapshot,
            "diff": pr_diff,
        }
