"""Deterministic assertion verifier for ChangeProof Ã¢â‚¬â€ Zero LLM calls."""
import os
import re
import json
import pandas as pd  # type: ignore[import-untyped]
from typing import Dict, Any, List, Optional, Tuple


class VerificationResult:
    def __init__(
        self,
        status: str,
        reason: str = "",
        diff_table: Optional[List[Dict[str, Any]]] = None,
        pre_summary: Optional[Dict[str, Any]] = None,
        post_summary: Optional[Dict[str, Any]] = None,
    ):
        self.status = status  # "PASS" | "FAIL" | "INCONCLUSIVE"
        self.reason = reason
        self.diff_table = diff_table or []
        self.pre_summary = pre_summary or {}
        self.post_summary = post_summary or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "diff_table": self.diff_table,
            "pre_summary": self.pre_summary,
            "post_summary": self.post_summary,
        }


def _load_run_context(path_str: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Loads metrics DataFrame and optional manifest.json context from a CSV path or run dir."""
    manifest = {}
    csv_path = path_str

    if os.path.isdir(path_str):
        # Path is a directory
        manifest_p = os.path.join(path_str, "manifest.json")
        if os.path.exists(manifest_p):
            try:
                with open(manifest_p, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
            except Exception:
                pass
        for fname in os.listdir(path_str):
            if fname.endswith(".csv"):
                csv_path = os.path.join(path_str, fname)
                break
    else:
        # Path is a CSV file
        parent_dir = os.path.dirname(path_str)
        manifest_p = os.path.join(parent_dir, "manifest.json")
        if os.path.exists(manifest_p):
            try:
                with open(manifest_p, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
            except Exception:
                pass

    if os.path.exists(csv_path):
        try:
            df = pd.read_csv(csv_path)
        except Exception:
            df = pd.DataFrame()
    else:
        df = pd.DataFrame()

    # Extract state-specific sub-dictionary if manifest is multi-phase
    filename = os.path.basename(csv_path).lower()
    if ("base" in filename or "pre" in filename) and ("base" in manifest or "pre" in manifest or "pre_run" in manifest):
        manifest = manifest.get("base") or manifest.get("pre") or manifest.get("pre_run") or manifest
    elif ("patch" in filename or "post" in filename) and ("patched" in manifest or "post" in manifest or "post_run" in manifest):
        manifest = manifest.get("patched") or manifest.get("post") or manifest.get("post_run") or manifest

    return df, manifest


def extract_metric_value(metric_name: str, condition_str: str, df: pd.DataFrame, manifest: Dict[str, Any]) -> float:
    """Extracts or computes the scalar value for a given metric condition prioritizing authoritative manifest values."""
    # 1. retries_per_request (normalized amplification ratio)
    if metric_name in ("retries_per_request", "retry_to_request_ratio", "retry_ratio", "amplification_factor", "amplification_ratio"):
        if "retries_per_request" in manifest:
            return float(manifest["retries_per_request"])
        if "retry_to_request_ratio" in manifest:
            return float(manifest["retry_to_request_ratio"])
        if "amplification_ratio" in manifest:
            return float(manifest["amplification_ratio"])
        delta_retries = manifest.get("retries_counted") or manifest.get("delta_retries_direct") or manifest.get("delta_retries")
        delta_requests = manifest.get("total_requests") or manifest.get("delta_requests_direct") or manifest.get("delta_requests")
        if delta_retries is not None and delta_requests is not None and float(delta_requests) > 0:
            return float(float(delta_retries) / float(delta_requests))
        if not df.empty and "metric_name" in df.columns:
            r_df = df[df["metric_name"] == "retry_count_total"]
            q_df = df[df["metric_name"] == "checkout_requests_total"]
            if not r_df.empty and not q_df.empty:
                r_delta = max(float(r_df["value"].max() - r_df["value"].min()), 0.0)
                q_delta = max(float(q_df["value"].max() - q_df["value"].min()), 0.0)
                if q_delta > 0:
                    return float(r_delta / q_delta)
        return 0.0

    # 2. total_requests (sample size)
    if metric_name in ("total_requests", "requests_total", "checkout_requests_total"):
        if "total_requests" in manifest:
            return float(manifest["total_requests"])
        req_val = manifest.get("delta_requests_direct") or manifest.get("delta_requests")
        if req_val is not None:
            return float(req_val)
        if not df.empty and "metric_name" in df.columns:
            q_df = df[df["metric_name"] == "checkout_requests_total"]
            if not q_df.empty:
                return max(float(q_df["value"].max() - q_df["value"].min()), 0.0)
        return 0.0

    # 3. throughput_req_per_sec
    if metric_name in ("throughput_req_per_sec", "throughput"):
        if "throughput_req_per_sec" in manifest:
            return float(manifest["throughput_req_per_sec"])
        if "throughput" in manifest:
            return float(manifest["throughput"])
        req_val = manifest.get("total_requests") or manifest.get("delta_requests_direct") or manifest.get("delta_requests")
        duration = manifest.get("duration_s") or manifest.get("experiment_duration_s") or manifest.get("duration_seconds")
        if req_val is not None and duration is not None and float(duration) > 0:
            return float(float(req_val) / float(duration))
        if not df.empty and "timestamp" in df.columns and len(df) > 1:
            dur = max(float(df["timestamp"].max() - df["timestamp"].min()), 1.0)
            if "metric_name" in df.columns:
                q_df = df[df["metric_name"] == "checkout_requests_total"]
                if not q_df.empty and len(q_df) > 1:
                    delta_req = max(float(q_df["value"].max() - q_df["value"].min()), 0.0)
                    return float(delta_req / dur)
        return 0.0

    # 4. Rate per min (prioritize authoritative direct/full-duration rate from manifest if present)
    if "rate_per_min" in condition_str or metric_name == "rate_per_min":
        if "rate_per_min" in manifest:
            return float(manifest["rate_per_min"])
        if "rate_per_min_direct" in manifest:
            return float(manifest["rate_per_min_direct"])
        if "rate_per_min_absolute" in manifest:
            return float(manifest["rate_per_min_absolute"])
        delta_retries = manifest.get("retries_counted") or manifest.get("delta_retries_direct") or manifest.get("delta_retries")
        duration = manifest.get("duration_s") or manifest.get("experiment_duration_s") or manifest.get("duration_seconds")
        if delta_retries is not None and duration is not None and float(duration) > 0:
            return float(float(delta_retries) / float(duration) * 60.0)

    # 5. Standard DataFrame / Counter metric
    if not df.empty:
        sub_df = df[df["metric_name"] == metric_name] if "metric_name" in df.columns else df
        if not sub_df.empty:
            return compute_metric_aggregate(sub_df, condition_str)

    # Manifest rate fallback if available
    if "rate_per_min" in condition_str and ("rate_per_min_direct" in manifest or "rate_per_min_csv" in manifest):
        return float(manifest.get("rate_per_min_direct") or manifest.get("rate_per_min_csv") or 0.0)

    return 0.0

    # 2. total_requests (sample size)
    if metric_name in ("total_requests", "requests_total", "checkout_requests_total"):
        req_val = manifest.get("delta_requests_direct") or manifest.get("delta_requests")
        if req_val is not None:
            return float(req_val)
        if not df.empty:
            if "metric_name" in df.columns:
                q_df = df[df["metric_name"] == "checkout_requests_total"]
                if not q_df.empty:
                    return max(float(q_df["value"].max() - q_df["value"].min()), 0.0)
            return float(len(df))
        return 0.0

    # 3. throughput_req_per_sec
    if metric_name in ("throughput_req_per_sec", "throughput"):
        req_val = manifest.get("delta_requests_direct") or manifest.get("delta_requests")
        duration = manifest.get("experiment_duration_s") or manifest.get("duration_seconds")
        if req_val is not None and duration is not None and float(duration) > 0:
            return float(req_val) / float(duration)
        if not df.empty and "timestamp" in df.columns and len(df) > 1:
            dur = max(float(df["timestamp"].max() - df["timestamp"].min()), 1.0)
            return float(len(df) / dur)
        return 0.0

    # 4. Rate per min (prioritize authoritative direct/full-duration rate from manifest if present)
    if "rate_per_min" in condition_str or metric_name == "rate_per_min":
        if "rate_per_min_direct" in manifest:
            return float(manifest["rate_per_min_direct"])
        if "rate_per_min_absolute" in manifest:
            return float(manifest["rate_per_min_absolute"])
        delta_retries = manifest.get("delta_retries_direct") or manifest.get("delta_retries")
        duration = manifest.get("experiment_duration_s") or manifest.get("duration_seconds")
        if delta_retries is not None and duration is not None and float(duration) > 0:
            return float(float(delta_retries) / float(duration) * 60.0)

    # 5. Standard DataFrame / Counter metric
    if not df.empty:
        sub_df = df[df["metric_name"] == metric_name] if "metric_name" in df.columns else df
        if not sub_df.empty:
            return compute_metric_aggregate(sub_df, condition_str)

    # Manifest rate fallback if available
    if "rate_per_min" in condition_str and ("rate_per_min_direct" in manifest or "rate_per_min_csv" in manifest):
        return float(manifest.get("rate_per_min_direct") or manifest.get("rate_per_min_csv") or 0.0)

    return 0.0


def compute_metric_aggregate(sub_df: pd.DataFrame, condition_str: str) -> float:
    """Computes the appropriate metric aggregate (rate per min or mean)."""
    if sub_df.empty:
        return 0.0
    val_s = sub_df["value"]
    if "rate_per_min" in condition_str or "rate" in condition_str:
        if "timestamp" in sub_df.columns and len(sub_df) > 1:
            t_min = float(sub_df["timestamp"].min())
            t_max = float(sub_df["timestamp"].max())
            duration_s = max(t_max - t_min, 1.0)
            delta_val = max(float(val_s.max() - val_s.min()), 0.0)
            return float((delta_val / duration_s) * 60.0)
        else:
            return float(val_s.iloc[-1])
    return float(val_s.mean())


def evaluate_condition_val(agg_val: float, condition_str: str) -> bool:
    """Evaluates comparison operator on aggregate value."""
    match = re.search(r'([><=]+)\s*([\d.]+)', condition_str)
    if not match:
        return False
    op, threshold_str = match.groups()
    threshold = float(threshold_str)

    if op == ">":
        return agg_val > threshold
    elif op == ">=":
        return agg_val >= threshold
    elif op == "<":
        return agg_val < threshold
    elif op == "<=":
        return agg_val <= threshold
    elif op == "==":
        return abs(agg_val - threshold) < 1e-3
    return False


def evaluate_condition(metric_series: pd.Series, condition_str: str) -> bool:
    """Backward-compatible series evaluation."""
    if metric_series.empty:
        return False
    df = pd.DataFrame({"value": metric_series})
    agg_val = compute_metric_aggregate(df, condition_str)
    return evaluate_condition_val(agg_val, condition_str)


def build_phase_summary(df: pd.DataFrame, manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Extracts summary and context metrics for reporting on certificates."""
    retries_per_req = extract_metric_value("retries_per_request", "", df, manifest)
    total_reqs = extract_metric_value("total_requests", "", df, manifest)
    throughput = extract_metric_value("throughput_req_per_sec", "", df, manifest)
    rate_per_min = extract_metric_value("retry_count_total", "rate_per_min", df, manifest)

    return {
        "retries_per_request": round(retries_per_req, 3),
        "total_requests": int(total_reqs),
        "throughput_req_per_sec": round(throughput, 2),
        "rate_per_min": round(rate_per_min, 2),
    }


def verify(pre_metrics_csv: str, post_metrics_csv: str, assertions: Dict[str, Any]) -> VerificationResult:
    """Sole deterministic authority evaluating pre and post experiment runs."""
    if not os.path.exists(pre_metrics_csv):
        return VerificationResult(status="INCONCLUSIVE", reason=f"Pre-patch metrics file missing: {pre_metrics_csv}")
    if not os.path.exists(post_metrics_csv):
        return VerificationResult(status="INCONCLUSIVE", reason=f"Post-patch metrics file missing: {post_metrics_csv}")

    pre_df, pre_manifest = _load_run_context(pre_metrics_csv)
    post_df, post_manifest = _load_run_context(post_metrics_csv)

    pre_summary = build_phase_summary(pre_df, pre_manifest)
    post_summary = build_phase_summary(post_df, post_manifest)

    diff_table = []

    # 1. Evaluate pre_patch assertions (Failure Reproduction check)
    pre_assertions = assertions.get("pre_patch", [])
    pre_reproduced = True
    for a in pre_assertions:
        m_name = a["metric"]
        cond = a["condition"]
        agg_val = extract_metric_value(m_name, cond, pre_df, pre_manifest)
        passed_cond = evaluate_condition_val(agg_val, cond)
        diff_table.append({
            "metric": m_name,
            "phase": "pre_patch",
            "condition": cond,
            "observed_value": round(agg_val, 3),
            "condition_met": passed_cond,
        })
        if not passed_cond:
            pre_reproduced = False

    if not pre_reproduced and pre_assertions:
        return VerificationResult(
            status="INCONCLUSIVE",
            reason="Pre-patch experiment did not reproduce the expected failure condition",
            diff_table=diff_table,
            pre_summary=pre_summary,
            post_summary=post_summary,
        )

    # 2. Evaluate post_patch assertions (Remediation Verification check)
    post_assertions = assertions.get("post_patch", [])
    post_passed = True
    for a in post_assertions:
        m_name = a["metric"]
        cond = a["condition"]
        agg_val = extract_metric_value(m_name, cond, post_df, post_manifest)
        passed_cond = evaluate_condition_val(agg_val, cond)
        diff_table.append({
            "metric": m_name,
            "phase": "post_patch",
            "condition": cond,
            "observed_value": round(agg_val, 3),
            "condition_met": passed_cond,
        })
        if not passed_cond:
            post_passed = False

    if post_passed:
        return VerificationResult(
            status="PASS",
            reason="Fix verified successfully",
            diff_table=diff_table,
            pre_summary=pre_summary,
            post_summary=post_summary,
        )
    else:
        return VerificationResult(
            status="FAIL",
            reason="Post-patch experiment violated safety thresholds",
            diff_table=diff_table,
            pre_summary=pre_summary,
            post_summary=post_summary,
        )
