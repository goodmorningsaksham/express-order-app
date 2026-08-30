"""Deterministic Experiment Runner orchestrating Docker, Toxiproxy, k6, and Prometheus."""
import os
import time
import json
import hashlib
import subprocess
import yaml
from typing import Dict, Any
from changeproof.toxiproxy_client import ToxiproxyClient
from changeproof.telemetry import PrometheusCollector

class ExperimentRunner:
    def __init__(self, runs_dir: str = "runs"):
        self.runs_dir = runs_dir
        os.makedirs(self.runs_dir, exist_ok=True)

    @staticmethod
    def compute_spec_hash(spec_content: str) -> str:
        return hashlib.sha256(spec_content.encode("utf-8")).hexdigest()

    def validate_spec(self, spec: Dict[str, Any]) -> bool:
        required_keys = {"id", "version", "target", "fault", "workload", "measurements", "assertions"}
        return required_keys.issubset(set(spec.keys()))

    def run(self, spec_path: str, state: str = "base") -> Dict[str, Any]:
        """Execute a complete experiment run from an immutable specification file."""
        if not os.path.exists(spec_path):
            raise FileNotFoundError(f"Experiment specification not found: {spec_path}")

        with open(spec_path, "r", encoding="utf-8") as f:
            raw_spec = f.read()
            spec = yaml.safe_load(raw_spec)

        if not self.validate_spec(spec):
            raise ValueError(f"Invalid experiment specification structure in {spec_path}")

        # Compute and freeze immutability hash
        spec_sha256 = self.compute_spec_hash(raw_spec)
        exp_id = spec["id"]
        run_id = f"{exp_id}_{state}_{int(time.time())}"
        run_dir = os.path.join(self.runs_dir, run_id)
        os.makedirs(run_dir, exist_ok=True)

        manifest = {
            "run_id": run_id,
            "experiment_id": exp_id,
            "state": state,
            "spec_sha256": spec_sha256,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": "RUNNING",
        }
        manifest_path = os.path.join(run_dir, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        # Save immutable copy of spec
        saved_spec_path = os.path.join(run_dir, "experiment.yaml")
        with open(saved_spec_path, "w", encoding="utf-8") as f:
            f.write(raw_spec)

        # Phase 2: Fault Injection Setup via Toxiproxy
        toxiproxy_admin = spec["fault"].get("admin_url", "http://localhost:8474")
        proxy_client = ToxiproxyClient(admin_url=toxiproxy_admin)
        fault_cfg = spec["fault"]

        toxic_applied = False
        start_time = time.time()

        try:
            if fault_cfg.get("tool") == "toxiproxy" and "toxic" in fault_cfg:
                toxic_attrs = fault_cfg["toxic"]["attributes"]
                proxy_client.add_latency(
                    proxy_name=fault_cfg["proxy"],
                    toxic_name=f"toxic_{run_id}",
                    latency_ms=toxic_attrs.get("latency", 2000),
                    jitter_ms=toxic_attrs.get("jitter", 100),
                )
                toxic_applied = True

            # Phase 3: Workload Generation
            workload_cfg = spec["workload"]
            duration_s = int(workload_cfg.get("duration", "45s").replace("s", ""))
            rps_target = workload_cfg.get("rps_target", 30)
            vus = workload_cfg.get("vus", 10)
            script_name = os.path.basename(workload_cfg.get("script", "checkout_load.js"))

            workloads_dir = os.path.abspath("workloads")
            k6_cmd = [
                "docker", "run", "--rm",
                "--network", "proofchange_changeproof-net",
                "-v", f"{workloads_dir}:/workloads",
                "-e", f"RPS_TARGET={rps_target}",
                "-e", f"DURATION={duration_s}s",
                "-e", f"VUS={vus}",
                "-e", "TARGET_URL=http://frontend-service:8000",
                "grafana/k6:0.49.0", "run", f"/workloads/{script_name}"
            ]
            
            k6_proc = subprocess.run(k6_cmd, capture_output=True, text=True)
            k6_log_path = os.path.join(run_dir, "k6_output.log")
            with open(k6_log_path, "w", encoding="utf-8") as f:
                f.write(f"STDOUT:\n{k6_proc.stdout}\nSTDERR:\n{k6_proc.stderr}")

            end_time = time.time()

            # Phase 4: Telemetry Collection from Prometheus
            prom_url = spec["measurements"].get("prometheus_url", "http://localhost:9090")
            collector = PrometheusCollector(prometheus_url=prom_url)
            metrics_df = collector.export_metrics_to_df(
                metric_queries=spec["measurements"].get("metrics", []),
                start_time=start_time,
                end_time=end_time,
            )

            metrics_csv_filename = f"metrics_{state}.csv"
            metrics_csv_path = os.path.join(run_dir, metrics_csv_filename)
            metrics_df.to_csv(metrics_csv_path, index=False)

            manifest["status"] = "COMPLETED"
            manifest["metrics_file"] = metrics_csv_filename
            manifest["metrics_rows"] = len(metrics_df)
            manifest["duration_seconds"] = round(end_time - start_time, 2)

            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)

            return {
                "run_id": run_id,
                "status": "COMPLETED",
                "spec_sha256": spec_sha256,
                "metrics_csv_path": metrics_csv_path,
                "run_dir": run_dir,
            }

        except Exception as e:
            manifest["status"] = "INCONCLUSIVE"
            manifest["error"] = str(e)
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2)
            return {
                "run_id": run_id,
                "status": "INCONCLUSIVE",
                "error": str(e),
                "run_dir": run_dir,
            }
        finally:
            # Phase 5: Teardown Faults
            if toxic_applied:
                try:
                    proxy_client.remove_toxic(fault_cfg["proxy"], f"toxic_{run_id}")
                except Exception as teardown_err:
                    # Log teardown failure into manifest so it is visible; do not
                    # raise because experiment evidence is already collected.
                    manifest.setdefault("warnings", []).append(
                        f"Toxic teardown failed: {teardown_err}"
                    )
                    with open(manifest_path, "w", encoding="utf-8") as f:
                        json.dump(manifest, f, indent=2)
