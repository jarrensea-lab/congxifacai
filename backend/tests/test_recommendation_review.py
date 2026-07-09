import pytest

from app.services.recommendation_review import (
    build_recommendation_review,
    render_recommendation_review_markdown,
)


@pytest.mark.asyncio
async def test_recommendation_review_handles_missing_portfolio(tmp_path):
    review = await build_recommendation_review(portfolio_path=str(tmp_path / "missing.json"))

    assert review["executed"]["count"] == 0
    assert review["items"] == []
    assert review["system_gap"] == "portfolio_missing"

    markdown = render_recommendation_review_markdown(review)
    assert "未找到本地持仓文件" in markdown
