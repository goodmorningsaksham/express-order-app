"""Toxiproxy REST API client for programmatic network fault injection."""
import requests
from typing import Dict, Any

class ToxiproxyClient:
    def __init__(self, admin_url: str = "http://localhost:8474"):
        self.admin_url = admin_url.rstrip("/")

    def get_proxies(self) -> Dict[str, Any]:
        resp = requests.get(f"{self.admin_url}/proxies", timeout=5.0)
        resp.raise_for_status()
        return resp.json()

    def get_proxy(self, proxy_name: str) -> Dict[str, Any]:
        resp = requests.get(f"{self.admin_url}/proxies/{proxy_name}", timeout=5.0)
        resp.raise_for_status()
        return resp.json()

    def add_latency(
        self,
        proxy_name: str,
        toxic_name: str = "latency_downstream",
        latency_ms: int = 2000,
        jitter_ms: int = 100,
        stream: str = "downstream",
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "name": toxic_name,
            "type": "latency",
            "stream": stream,
            "attributes": {
                "latency": latency_ms,
                "jitter": jitter_ms,
            },
        }
        resp = requests.post(f"{self.admin_url}/proxies/{proxy_name}/toxics", json=payload, timeout=5.0)
        resp.raise_for_status()
        return resp.json()

    def remove_toxic(self, proxy_name: str, toxic_name: str) -> bool:
        resp = requests.delete(f"{self.admin_url}/proxies/{proxy_name}/toxics/{toxic_name}", timeout=5.0)
        return resp.status_code == 204 or resp.status_code == 200

    def reset(self) -> bool:
        resp = requests.post(f"{self.admin_url}/reset", timeout=5.0)
        return resp.status_code == 204 or resp.status_code == 200
