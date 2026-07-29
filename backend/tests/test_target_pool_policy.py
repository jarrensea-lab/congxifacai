from app.services.target_pool_policy import (
    MID_LONG_TERM_POOL,
    SHORT_TERM_POOL,
    decide_pool_membership,
    infer_pool_kind,
)


def test_infer_pool_kind_uses_only_short_and_mid_long_term_categories():
    assert infer_pool_kind({"status": "watching"}) == SHORT_TERM_POOL
    assert (
        infer_pool_kind(
            {"status": "research_reference"},
            long_thesis={"symbol": "600000", "thesis_status": "healthy"},
        )
        == MID_LONG_TERM_POOL
    )
    assert infer_pool_kind({"pool_kind": "long_term"}) == MID_LONG_TERM_POOL


def test_short_pool_uses_hysteresis_and_evicts_an_existing_low_score_item():
    retained = decide_pool_membership(
        pool_kind=SHORT_TERM_POOL,
        scorecard={"score": 52, "missing_data": []},
        previously_retained=True,
    )
    evicted = decide_pool_membership(
        pool_kind=SHORT_TERM_POOL,
        scorecard={"score": 49, "missing_data": []},
        previously_retained=True,
    )
    new_candidate = decide_pool_membership(
        pool_kind=SHORT_TERM_POOL,
        scorecard={"score": 54, "missing_data": []},
        previously_retained=False,
    )

    assert retained["retained"] is True
    assert retained["threshold"] == 50
    assert evicted["retained"] is False
    assert evicted["reason"] == "score_below_retention_threshold"
    assert new_candidate["retained"] is False
    assert new_candidate["threshold"] == 55


def test_mid_long_pool_requires_healthy_thesis_and_higher_score():
    admitted = decide_pool_membership(
        pool_kind=MID_LONG_TERM_POOL,
        scorecard={
            "long_quality_score": 62,
            "thesis_status": "healthy",
            "missing_data": [],
        },
        previously_retained=False,
    )
    broken = decide_pool_membership(
        pool_kind=MID_LONG_TERM_POOL,
        scorecard={
            "long_quality_score": 80,
            "thesis_status": "broken",
            "missing_data": [],
        },
        previously_retained=True,
    )

    assert admitted["retained"] is True
    assert admitted["threshold"] == 60
    assert broken["retained"] is False
    assert broken["reason"] == "long_thesis_broken"


def test_incomplete_daily_data_never_causes_automatic_eviction():
    existing = decide_pool_membership(
        pool_kind=SHORT_TERM_POOL,
        scorecard={"score": 0, "missing_data": ["kline"]},
        previously_retained=True,
    )
    new_candidate = decide_pool_membership(
        pool_kind=SHORT_TERM_POOL,
        scorecard={"score": 80, "missing_data": ["fund_flow"]},
        previously_retained=False,
    )

    assert existing["retained"] is True
    assert existing["reason"] == "incomplete_data_preserve_existing"
    assert new_candidate["retained"] is False
    assert new_candidate["reason"] == "incomplete_data_not_admitted"
