"""Minimal Tavily client: web search and page extraction for the cross-border agent."""
import os
import threading

import httpx

API = "https://api.tavily.com"


class Tavily:
    def __init__(self, api_key: str | None = None):
        self.key = api_key or os.environ.get("TAVILY_API_KEY")
        if not self.key:
            raise SystemExit("TAVILY_API_KEY is not set. Add it to .env.")
        self.http = httpx.Client(timeout=60, headers={"Authorization": f"Bearer {self.key}"})
        self.calls = {"search": 0, "extract": 0}
        self._lock = threading.Lock()

    def _count(self, kind: str) -> None:
        with self._lock:
            self.calls[kind] += 1

    def search(self, query: str, max_results: int = 6, include_domains: list | None = None,
               time_range: str | None = None) -> list[dict]:
        body = {"query": query, "max_results": max_results, "search_depth": "basic"}
        if include_domains:
            body["include_domains"] = include_domains
        if time_range:
            body["time_range"] = time_range
        r = self.http.post(f"{API}/search", json=body)
        r.raise_for_status()
        self._count("search")
        return [{"url": x["url"], "title": x.get("title", ""), "snippet": x.get("content", "")}
                for x in r.json().get("results", [])]

    def extract(self, urls: list[str]) -> list[dict]:
        r = self.http.post(f"{API}/extract", json={"urls": urls[:5], "extract_depth": "basic"})
        r.raise_for_status()
        self._count("extract")
        data = r.json()
        pages = [{"url": x["url"], "content": x.get("raw_content") or ""} for x in data.get("results", [])]
        pages += [{"url": f.get("url", ""), "content": "", "error": f.get("error", "could not read")}
                  for f in data.get("failed_results", [])]
        return pages
