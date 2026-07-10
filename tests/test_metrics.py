from shared.metrics import MetricsCollector

def test_metrics_collector_counts_and_aggregates_latency():
    collector = MetricsCollector()
    collector.increment("requests"); collector.increment("requests"); collector.increment("errors")
    collector.observe_latency("copilot", 10); collector.observe_latency("copilot", 30)
    snapshot = collector.snapshot()
    assert snapshot["counters"]["requests"] == 2
    assert snapshot["counters"]["errors"] == 1
    assert snapshot["latencies"]["copilot"]["count"] == 2
    assert snapshot["latencies"]["copilot"]["avg_ms"] == 20
