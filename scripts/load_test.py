from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from dataclasses import dataclass, field

import httpx


@dataclass
class Stats:
    total_requests: int = 0
    stream_requests: int = 0
    non_stream_requests: int = 0
    errors: int = 0
    latencies_ms: list[float] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Async load test for Gemini Internal Service.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--duration-seconds", type=int, default=30)
    parser.add_argument("--stream-ratio", type=float, default=0.3)
    parser.add_argument("--burst-size", type=int, default=3)
    parser.add_argument("--account-id", default=None)
    parser.add_argument("--message", default="Give a short answer: what is an internal AI service?")
    return parser.parse_args()


async def create_session(client: httpx.AsyncClient, account_id: str | None) -> str:
    payload = {"routing_policy": "sticky"}
    if account_id:
        payload["account_id"] = account_id
    response = await client.post("/v1/sessions", json=payload)
    response.raise_for_status()
    return response.json()["session_id"]


async def send_non_stream(client: httpx.AsyncClient, session_id: str, message: str) -> None:
    response = await client.post(
        "/v1/messages",
        json={"session_id": session_id, "message": message, "stream": False},
    )
    response.raise_for_status()


async def send_stream(client: httpx.AsyncClient, session_id: str, message: str) -> None:
    async with client.stream(
        "POST",
        "/v1/messages:stream",
        json={"session_id": session_id, "message": message, "stream": True},
    ) as response:
        response.raise_for_status()
        async for _ in response.aiter_lines():
            pass


async def worker(
    worker_id: int,
    client: httpx.AsyncClient,
    args: argparse.Namespace,
    deadline: float,
    stats: Stats,
) -> None:
    session_id = await create_session(client, args.account_id)
    while time.perf_counter() < deadline:
        for _ in range(args.burst_size):
            if time.perf_counter() >= deadline:
                break
            started = time.perf_counter()
            try:
                if random.random() < args.stream_ratio:
                    stats.stream_requests += 1
                    await send_stream(client, session_id, f"[worker {worker_id}] {args.message}")
                else:
                    stats.non_stream_requests += 1
                    await send_non_stream(client, session_id, f"[worker {worker_id}] {args.message}")
                stats.total_requests += 1
                stats.latencies_ms.append((time.perf_counter() - started) * 1000)
            except Exception:
                stats.errors += 1
        await asyncio.sleep(0.2)


async def main_async(args: argparse.Namespace) -> int:
    headers = {"Authorization": f"Bearer {args.token}"}
    stats = Stats()
    deadline = time.perf_counter() + args.duration_seconds

    async with httpx.AsyncClient(base_url=args.base_url, headers=headers, timeout=120.0) as client:
        tasks = [
            asyncio.create_task(worker(worker_id, client, args, deadline, stats))
            for worker_id in range(args.workers)
        ]
        await asyncio.gather(*tasks)

    summary = {
        "workers": args.workers,
        "duration_seconds": args.duration_seconds,
        "total_requests": stats.total_requests,
        "stream_requests": stats.stream_requests,
        "non_stream_requests": stats.non_stream_requests,
        "errors": stats.errors,
        "p50_ms": round(statistics.median(stats.latencies_ms), 2) if stats.latencies_ms else None,
        "p95_ms": round(statistics.quantiles(stats.latencies_ms, n=20)[18], 2)
        if len(stats.latencies_ms) >= 20
        else None,
        "avg_ms": round(statistics.mean(stats.latencies_ms), 2) if stats.latencies_ms else None,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    args = parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
