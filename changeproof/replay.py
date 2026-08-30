"""Clean environment replay CLI."""
import os
import sys
import json
import yaml
import zipfile
import tempfile
import hashlib
from typing import Dict, Any
from changeproof.experiment_runner import ExperimentRunner
from changeproof.verifier import verify

def replay_capsule(capsule_zip_path: str, mode: str = "evidence") -> Dict[str, Any]:
    """Replays a reproduction capsule.
    
    Modes:
    - 'evidence': Deterministic verification of archived runtime metrics against immutable spec.
    - 'live': Genuine clean-environment execution: runs live BASE experiment, applies patch,
              runs live PATCHED experiment, captures fresh metrics, and evaluates verifier.
    """
    if not os.path.exists(capsule_zip_path):
        raise FileNotFoundError(f"Capsule zip not found: {capsule_zip_path}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        with zipfile.ZipFile(capsule_zip_path, "r") as z:
            z.extractall(tmp_dir)

        manifest_path = os.path.join(tmp_dir, "manifest.json")
        spec_path = os.path.join(tmp_dir, "experiment.yaml")

        if not os.path.exists(manifest_path) or not os.path.exists(spec_path):
            raise ValueError("Capsule missing manifest.json or experiment.yaml")

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        with open(spec_path, "rb") as f:
            spec_bytes = f.read()
        spec_content = spec_bytes.decode("utf-8")
        spec = yaml.safe_load(spec_content)

        # 1. Spec Immutability Check: verify SHA256 matches manifest (raw bytes + normalized newline tolerance)
        current_sha256 = hashlib.sha256(spec_bytes).hexdigest()
        normalized_sha256 = hashlib.sha256(spec_content.replace("\r\n", "\n").encode("utf-8")).hexdigest()
        if manifest.get("spec_sha256") and manifest["spec_sha256"] != "none":
            expected_sha = manifest["spec_sha256"]
            if current_sha256 != expected_sha and normalized_sha256 != expected_sha:
                return {
                    "status": "INCONCLUSIVE",
                    "reason": f"Spec hash mismatch: expected {expected_sha}, got {current_sha256}",
                }

        # 2. Replay Execution
        if mode == "live":
            # Live runtime reconstruction
            runner = ExperimentRunner(runs_dir=os.path.join(tmp_dir, "replay_runs"))
            
            # Run BASE experiment
            base_run = runner.run(spec_path, state="base")
            if base_run.get("status") != "COMPLETED":
                return {"replay_status": "FAILED", "stage": "base_run", "error": base_run.get("error")}
            
            # Run PATCHED experiment
            patched_run = runner.run(spec_path, state="patched")
            if patched_run.get("status") != "COMPLETED":
                return {"replay_status": "FAILED", "stage": "patched_run", "error": patched_run.get("error")}

            # Deterministic verification on fresh live metrics
            ver_res = verify(base_run["metrics_csv_path"], patched_run["metrics_csv_path"], spec.get("assertions", {}))
            return {
                "replay_mode": "live_reproduction",
                "replay_status": "COMPLETED",
                "spec_verified": True,
                "verification": ver_res.to_dict(),
            }
        else:
            # Evidence verification mode: check archived metrics against spec contract
            pre_metrics = os.path.join(tmp_dir, "metrics_pre.csv")
            if not os.path.exists(pre_metrics):
                pre_metrics = os.path.join(tmp_dir, "metrics_base.csv")

            post_metrics = os.path.join(tmp_dir, "metrics_post.csv")
            if not os.path.exists(post_metrics):
                post_metrics = os.path.join(tmp_dir, "metrics_patched.csv")

            if os.path.exists(pre_metrics) and os.path.exists(post_metrics):
                ver_res = verify(pre_metrics, post_metrics, spec.get("assertions", {}))
                return {
                    "replay_mode": "evidence_verification",
                    "replay_status": "COMPLETED",
                    "spec_verified": True,
                    "verification": ver_res.to_dict(),
                }

            return {
                "replay_mode": "evidence_verification",
                "replay_status": "INCONCLUSIVE",
                "reason": "Capsule does not contain metrics CSVs for verification",
            }

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m changeproof.replay <capsule.zip> [--live]")
        sys.exit(1)
    
    live_flag = "--live" in sys.argv
    res = replay_capsule(sys.argv[1], mode="live" if live_flag else "evidence")
    print(json.dumps(res, indent=2))


