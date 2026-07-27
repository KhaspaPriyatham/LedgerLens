from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry, generate_latest

registry = CollectorRegistry()

moderation_latency_seconds = Histogram(
    "moderation_latency_seconds",
    "Time spent in the moderation gate",
    registry=registry,
)

extraction_latency_seconds = Histogram(
    "extraction_latency_seconds",
    "Time spent on GPT-4o vision extraction",
    registry=registry,
)

token_cost_usd_total = Counter(
    "token_cost_usd_total",
    "Cumulative USD cost of vision extraction calls",
    registry=registry,
)

documents_ingested_total = Counter(
    "documents_ingested_total",
    "Total documents ingested",
    ["status"],
    registry=registry,
)

documents_reviewed_total = Counter(
    "documents_reviewed_total",
    "Total documents manually reviewed by a human, by outcome",
    ["outcome"],  # "approved" | "rejected"
    registry=registry,
)

auto_approval_rate = Gauge(
    "auto_approval_rate",
    "Rolling fraction of documents auto-approved (no human review needed)",
    registry=registry,
)

throughput_docs_per_minute = Gauge(
    "throughput_docs_per_minute",
    "Documents processed per minute (rolling)",
    registry=registry,
)


# registry is exported directly for use in main.py
# Use: generate_latest(registry) to render metrics for Prometheus scraping
