"""The E2E runner: step selection, reporting and the live AI opt-in."""
import sys

import pytest

from e2e import demo_scenario


def test_unexpected_error_is_reported_and_later_steps_are_skipped(monkeypatch):
    scenario = demo_scenario.Scenario(
        "http://127.0.0.1:9", "http://localhost:5173", "secret-pass", fake_kakao=True, mailbox=None,
    )
    first, *rest = [method for _label, method in scenario.steps()]

    def boom():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(scenario, first, boom)
    assert scenario.execute() is False
    assert scenario.results[0][0] == "FAIL" and "RuntimeError: database unavailable" in scenario.results[0][2]
    assert [status for status, *_ in scenario.results[1:]] == ["SKIP"] * len(rest)
    assert all("secret-pass" not in detail for *_, detail in scenario.results)


def test_realtime_expiry_steps_can_be_left_out():
    def scenario(realtime_expiry: bool) -> demo_scenario.Scenario:
        return demo_scenario.Scenario("http://127.0.0.1:9", "http://localhost:5173", "pw", fake_kakao=True,
                                      mailbox=None, realtime_expiry=realtime_expiry)

    every = [method for _label, method in demo_scenario.Scenario.STEPS + demo_scenario.Scenario.MANUAL_STEPS]
    assert [method for _label, method in scenario(True).steps()] == every
    fast = [method for _label, method in scenario(False).steps()]
    assert fast == [method for method in every if method not in demo_scenario.Scenario.REALTIME_STEPS]
    assert len(every) - len(fast) == 2  # the setup and the wait for its deadline
    assert demo_scenario.Scenario.REALTIME_STEPS <= set(every)


@pytest.mark.parametrize("env", [{}, {"JIDAN_E2E_OPENAI": "1"}, {"OPENAI_API_KEY": "sk-test"}])
def test_live_runs_need_both_the_opt_in_and_a_key(monkeypatch, capsys, env):
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DB_NAME", "jidan_e2e_test")
    for name in ("JIDAN_E2E_OPENAI", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", ["e2e.demo_scenario", "--start-server", "--ai", "live"])
    assert demo_scenario.main() == 2
    assert "JIDAN_E2E_OPENAI=1" in capsys.readouterr().err


def test_live_mode_leaves_out_fake_only_steps():
    def steps(ai):
        scenario = demo_scenario.Scenario("http://127.0.0.1:9", "http://localhost:5173", "pw", fake_kakao=True,
                                          mailbox=None, ai=ai)
        return [method for _label, method in scenario.steps()]

    assert "step_m14_new_version" in steps("fake")
    assert "step_m14_new_version" not in steps("live")
    assert steps("live") == [m for m in steps("fake") if m not in demo_scenario.Scenario.FAKE_ONLY_STEPS]
