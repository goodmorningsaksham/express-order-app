"""8 sandboxed agent tools for ChangeProof primary agent."""
import os
import json
import subprocess
from typing import Dict, Any, List
from changeproof.context_builder import ContextBuilder
from changeproof.telemetry import PrometheusCollector
from changeproof.experiment_runner import ExperimentRunner

def read_file(path: str) -> str:
    """Read source files safely within workspace bounds."""
    clean_path = os.path.normpath(path)
    if clean_path.startswith("..") or os.path.isabs(clean_path):
        raise PermissionError("Access outside workspace is restricted.")
    if not os.path.exists(clean_path):
        raise FileNotFoundError(f"File not found: {clean_path}")
    with open(clean_path, "r", encoding="utf-8") as f:
        return f.read()

def read_topology() -> Dict[str, Any]:
    """Parse docker-compose.yml to extract service dependency topology."""
    builder = ContextBuilder()
    return builder.build_topology()

def read_runtime_snapshot(prometheus_url: str = "http://localhost:9090") -> Dict[str, Any]:
    """Fetch current Prometheus runtime metric snapshot."""
    collector = PrometheusCollector(prometheus_url)
    if not collector.is_healthy():
        return {"status": "unhealthy", "metrics": {}}
    return {
        "status": "healthy",
        "requests": collector.query_instant("http_requests_total"),
        "retries": collector.query_instant("retry_count_total"),
    }

def propose_hypothesis(hypotheses: List[Dict[str, Any]], output_path: str = "hypothesis.json") -> Dict[str, Any]:
    """Log ranked candidate hypotheses grounded in code, topology, and runtime evidence."""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"hypotheses": hypotheses, "selected": hypotheses[0] if hypotheses else {}}, f, indent=2)
    return {"status": "recorded", "count": len(hypotheses)}

def run_experiment(spec_dict: Dict[str, Any], temp_spec_path: str = "temp_experiment.yaml") -> Dict[str, Any]:
    """Execute one experiment run via deterministic experiment runner."""
    import yaml
    with open(temp_spec_path, "w", encoding="utf-8") as f:
        yaml.dump(spec_dict, f)
    runner = ExperimentRunner()
    res = runner.run(temp_spec_path)
    if os.path.exists(temp_spec_path):
        os.remove(temp_spec_path)
    return res

def read_metrics(run_id: str, runs_dir: str = "runs") -> Dict[str, Any]:
    """Retrieve saved metrics CSV for a specific run ID as JSON records."""
    import pandas as pd
    run_path = os.path.join(runs_dir, run_id)
    manifest_path = os.path.join(run_path, "manifest.json")
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Run {run_id} manifest not found.")
    
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
        
    metrics_file = manifest.get("metrics_file", "metrics_base.csv")
    metrics_csv = os.path.join(run_path, metrics_file)
    if not os.path.exists(metrics_csv):
        return {"rows": 0, "records": []}
        
    df = pd.read_csv(metrics_csv)
    return {"rows": len(df), "records": df.to_dict(orient="records")}

def write_patch(diff_content: str, patch_file: str = "patch.diff") -> Dict[str, Any]:
    """Apply a unified diff safely to the workspace."""
    with open(patch_file, "w", encoding="utf-8") as f:
        f.write(diff_content)
    try:
        subprocess.run(["git", "apply", patch_file], capture_output=True, text=True, check=True)
        return {"status": "applied", "patch_file": patch_file}
    except subprocess.CalledProcessError as e:
        return {"status": "error", "error": e.stderr}

def run_tests(test_target: str = "tests/unit") -> Dict[str, Any]:
    """Run pytest suite safely."""
    try:
        res = subprocess.run(["python", "-m", "pytest", test_target, "-q"], capture_output=True, text=True)
        return {
            "status": "PASS" if res.returncode == 0 else "FAIL",
            "returncode": res.returncode,
            "stdout": res.stdout,
        }
    except Exception as e:
        return {"status": "ERROR", "error": str(e)}
