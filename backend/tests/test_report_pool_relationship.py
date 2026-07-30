from scripts import daily_report


def test_pool_relationship_guide_distinguishes_strategy_report_and_watchlist_layers():
    guide = "\n".join(daily_report.build_pool_relationship_guide())

    assert "短线池" in guide
    assert "中长线池" in guide
    assert "易淘金自选股" in guide
    assert "两个正式池" in guide
    assert "短线池 + 中长线池 + 当前持仓" in guide
    assert "不反向晋级" in guide
    assert "每日重新评分" in guide


def test_short_pool_section_places_relationship_guide_before_the_table():
    section = daily_report._short_pool_section({"target_scores": []})

    heading_index = section.index("## 三、短线关注标的池")
    guide_index = next(
        index for index, line in enumerate(section) if "重点关注 ≠ 可买" in line
    )
    table_index = next(
        index for index, line in enumerate(section) if line.startswith("| 标的 |")
    )

    assert heading_index < guide_index < table_index
    assert "| 查看顺序 |" in section[table_index]


def test_report_sections_only_render_retained_members_of_the_matching_pool():
    decision = {
        "target_scores": [
            {
                "code": "000001",
                "name": "短线保留",
                "pool_kind": "short_term",
                "pool_retained": True,
                "action": "watch",
                "score": 52,
            },
            {
                "code": "000002",
                "name": "短线淘汰",
                "pool_kind": "short_term",
                "pool_retained": False,
                "action": "watch",
                "score": 49,
            },
            {
                "code": "600000",
                "name": "长线保留",
                "pool_kind": "mid_long_term",
                "pool_retained": True,
                "action": "research_only",
                "long_quality_score": 62,
                "thesis_status": "healthy",
            },
            {
                "code": "600001",
                "name": "长线淘汰",
                "pool_kind": "mid_long_term",
                "pool_retained": False,
                "action": "research_only",
                "long_quality_score": 40,
                "thesis_status": "stale",
            },
        ],
        "outside_pool_scan": [
            {
                "code": "300001",
                "name": "未评分线索",
                "action": "watching",
                "affordable": True,
            }
        ],
    }

    short_section = "\n".join(daily_report._short_pool_section(decision))
    long_section = "\n".join(daily_report._long_pool_section(decision))

    assert "短线保留" in short_section
    assert "短线淘汰" not in short_section
    assert "长线保留" not in short_section
    assert "未评分线索" not in short_section
    assert "长线保留" in long_section
    assert "长线淘汰" not in long_section
    assert "短线保留" not in long_section
