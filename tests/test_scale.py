from concurrent.futures import ThreadPoolExecutor
from time import monotonic
from portal.demo import seed
from portal.domain import evaluate, summaries


def test_300_account_synthetic_scan():
    """Exercises real profile evaluation and aggregation at the specified 50-account concurrency.

    AWS collection/STS is replaced by deterministic evidence, so this is a CPU/contract load gate,
    not evidence that live regional AWS APIs meet the scan-duration target.
    """
    data = seed(299)
    started = monotonic()

    def scan(account):
        findings, score = evaluate(account, data["observations"][account["id"]])
        return {**score, **summaries(findings)}

    with ThreadPoolExecutor(max_workers=50) as executor:
        results = list(executor.map(scan, data["accounts"].values()))
    assert len(results) == 300
    assert all(0 <= r["score_pct"] <= 100 and r["rule_summary"] for r in results)
    assert monotonic() - started < 600
