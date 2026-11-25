"""The proposed portfolio rule does not starve a campaign in a quiet week, and matches the current maths once there is history."""
from clipping import pipeline


def test_quiet_week_is_not_squeezed_to_one_clip():
    assert pipeline.portfolio_allowance(0.4, week=0, made=0, wanted=26, room=10) == 10      # today: 1
    assert pipeline.portfolio_allowance(0.4, week=3, made=2, wanted=26, room=8) == 8


def test_with_history_the_share_still_limits_one_campaign():
    assert pipeline.portfolio_allowance(0.4, week=20, made=5, wanted=26, room=5) == 5
    assert pipeline.portfolio_allowance(0.4, week=20, made=8, wanted=26, room=2) == 0       # already at 8/20 = 40 %
    assert pipeline.portfolio_allowance(0.4, week=30, made=0, wanted=26, room=10) == 10     # 20 allowed, the weekly cap of 10 binds
