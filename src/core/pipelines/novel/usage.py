"""Per-answer API accounting, shared safely by nested worker threads."""
from contextvars import ContextVar
from threading import Lock

CURRENT = ContextVar("reader_usage", default=None)
OUTPUT_CAP = ContextVar("reader_output_cap", default=None)


class UsageLimitError(RuntimeError):
    pass


def output_limit(requested):
    cap = OUTPUT_CAP.get()
    return min(requested, cap) if cap is not None else requested


class UsageMeter:
    def __init__(self, max_requests=None):
        self.lock = Lock()
        self.calls = 0
        self.records = []
        self.carried = None
        self.max_requests = max_requests
        self.request_guard = None
        self.record_callback = None
        self.on_change = None

    def carry(self, summary):
        with self.lock:
            if self.calls == 0 and not self.records and self.carried is None:
                self.carried = dict(summary)

    def request(self):
        with self.lock:
            previous = (self.carried or {}).get("api_requests", 0)
            if self.max_requests is not None and self.calls + previous >= self.max_requests:
                raise UsageLimitError("本轮模型请求次数已达到上限")
            if self.request_guard:
                self.request_guard()
            self.calls += 1
        if self.on_change:
            self.on_change()

    def record(self, model, usage, service_tier="default"):
        with self.lock:
            self.records.append((model, usage or {}, service_tier))
            if self.record_callback:
                self.record_callback()
        if self.on_change:
            self.on_change()

    def summary(self, include_carried=True):
        result = dict(api_requests=self.calls, input_tokens=0, output_tokens=0,
                      total_tokens=0, cached_input_tokens=0, cache_write_input_tokens=0,
                      reasoning_tokens=0, estimated_cost_usd=0.0, usage_complete=True,
                      known_cost_usd=0.0, unpriced_requests=0, reported_requests=0)
        known_prices = True
        for model, usage, tier in self.records:
            inputs = usage.get("input_tokens", usage.get("prompt_tokens"))
            outputs = usage.get("output_tokens", usage.get("completion_tokens"))
            if inputs is None or outputs is None:
                result["usage_complete"] = False
                known_prices = False
                continue
            result["reported_requests"] += 1
            details = usage.get("input_tokens_details", usage.get("prompt_tokens_details")) or {}
            output_details = usage.get("output_tokens_details", usage.get("completion_tokens_details")) or {}
            cached = details.get("cached_tokens", 0)
            writes = details.get("cache_write_tokens", 0)
            result["input_tokens"] += inputs
            result["output_tokens"] += outputs
            result["total_tokens"] += usage.get("total_tokens", inputs + outputs)
            result["cached_input_tokens"] += cached
            result["cache_write_input_tokens"] += writes
            result["reasoning_tokens"] += output_details.get("reasoning_tokens", 0)
            if model.startswith("gpt-6.1-sol") and tier in {"default", "standard", "auto", "flex", "fast", "ultrafast"}:
                multiplier = {"flex": 0.5, "fast": 2, "ultrafast": 6}.get(tier, 1)
                long_input, long_output = (2, 1.5) if inputs > 272000 else (1, 1)
                cost = ((max(0, inputs - cached - writes) * 2 + cached * 0.10 + writes * 2.50) *
                        long_input + outputs * 10 * long_output) / 1_000_000
                result["estimated_cost_usd"] += cost * multiplier
            else:
                known_prices = False
                result["unpriced_requests"] += 1
        if len(self.records) < self.calls:
            result["usage_complete"] = False
            if not self.records:
                known_prices = False
        result["known_cost_usd"] = round(result["estimated_cost_usd"], 6)
        result["estimated_cost_usd"] = result["known_cost_usd"] if known_prices else None
        if include_carried and self.carried:
            for field in ("api_requests", "input_tokens", "output_tokens", "total_tokens",
                          "cached_input_tokens", "cache_write_input_tokens", "reasoning_tokens"):
                result[field] += self.carried.get(field, 0)
            result["reported_requests"] += self.carried.get("reported_requests", self.carried.get("api_requests", 0) if self.carried.get("usage_complete") else 0)
            result["unpriced_requests"] += self.carried.get("unpriced_requests", 0)
            result["known_cost_usd"] = round(result["known_cost_usd"] + (
                self.carried.get("known_cost_usd", self.carried.get("estimated_cost_usd")) or 0), 6)
            previous_cost = self.carried.get("estimated_cost_usd")
            current_cost = result["estimated_cost_usd"]
            result["estimated_cost_usd"] = (
                round(previous_cost + current_cost, 6)
                if previous_cost is not None and current_cost is not None else None
            )
            result["usage_complete"] = result["usage_complete"] and self.carried.get("usage_complete", False)
            result["resumed"] = True
        return result


def request_started():
    meter = CURRENT.get()
    if meter:
        meter.request()


def record_usage(model, usage, tier="default"):
    meter = CURRENT.get()
    if meter:
        meter.record(model, usage, tier)


def cached_result(result):
    return {**result, "cache_hit": True, "usage": UsageMeter().summary(),
            "generation_usage": result.get("generation_usage", result.get("usage"))}
