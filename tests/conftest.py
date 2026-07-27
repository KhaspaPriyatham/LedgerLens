import re


def metric_value(metrics_text: str, name: str, **labels) -> float:
    """Parse a single Prometheus metric's current value out of exposition
    text, e.g. metric_value(text, "documents_reviewed_total", outcome="rejected").

    Returns 0.0 if the metric/label combination hasn't been emitted yet.

    Tests use this instead of asserting an exact absolute counter value,
    because the underlying CollectorRegistry in app/metrics.py is created
    once at module import time and is *not* reloaded between test files in
    this suite (only app.config/app.db/app.main are reloaded per test) --
    so counters accumulate across every test in the same pytest process.
    Asserting a hardcoded "...} 1.0" is only correct by coincidence of test
    ordering; asserting a delta of +1 across an action is correct
    regardless of how many other tests touched the same counter first.
    """
    if labels:
        label_str = ",".join(f'{k}="{v}"' for k, v in labels.items())
        pattern = re.compile(
            rf'^{re.escape(name)}\{{{re.escape(label_str)}\}} ([0-9.eE+-]+)$', re.MULTILINE
        )
    else:
        pattern = re.compile(rf'^{re.escape(name)} ([0-9.eE+-]+)$', re.MULTILINE)
    match = pattern.search(metrics_text)
    return float(match.group(1)) if match else 0.0
