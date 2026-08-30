"""Prometheus telemetry collector and CSV exporter."""
import requests
import pandas as pd
from typing import List, Dict, Any

class PrometheusCollector:
    def __init__(self, prometheus_url: str = "http://localhost:9090"):
        self.prometheus_url = prometheus_url.rstrip("/")

    def is_healthy(self) -> bool:
        try:
            resp = requests.get(f"{self.prometheus_url}/-/healthy", timeout=2.0)
            return resp.status_code == 200
        except Exception:
            return False

    def query_instant(self, query: str) -> List[Dict[str, Any]]:
        resp = requests.get(
            f"{self.prometheus_url}/api/v1/query",
            params={"query": query},
            timeout=5.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "success":
            return data.get("data", {}).get("result", [])
        return []

    def query_range(self, query: str, start_time: float, end_time: float, step_s: int = 1) -> List[Dict[str, Any]]:
        params = {
            "query": query,
            "start": str(start_time),
            "end": str(end_time),
            "step": f"{step_s}s",
        }
        resp = requests.get(
            f"{self.prometheus_url}/api/v1/query_range",
            params=params,
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "success":
            return data.get("data", {}).get("result", [])
        return []

    def export_metrics_to_df(self, metric_queries: List[Dict[str, Any]], start_time: float, end_time: float) -> pd.DataFrame:
        """Query multiple metrics over an experiment window and merge into a tabular DataFrame."""
        records = []
        for item in metric_queries:
            query = item["name"]
            labels = item.get("labels", {})
            if labels:
                label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
                full_query = f"{query}{{{label_str}}}"
            else:
                full_query = query

            results = self.query_range(full_query, start_time, end_time, step_s=1)
            for res in results:
                metric_labels = res.get("metric", {})
                values = res.get("values", [])
                for ts, val in values:
                    records.append({
                        "timestamp": float(ts),
                        "metric_name": query,
                        "service": metric_labels.get("service", "unknown"),
                        "target": metric_labels.get("target", "none"),
                        "value": float(val),
                    })

        if not records:
            return pd.DataFrame(columns=["timestamp", "metric_name", "service", "target", "value"])

        df = pd.DataFrame(records)
        df.sort_values(by=["timestamp", "metric_name"], inplace=True)
        return df
