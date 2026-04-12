#!/usr/bin/env python3
"""Tests for optional adapter hardware reset hooks."""

from unittest.mock import patch

from thelia.adapter_reset import AdapterResetConfig, AdapterResetController


class _CompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_reset_controller_disabled_without_command():
    controller = AdapterResetController(AdapterResetConfig(command=""))

    assert controller.enabled is False
    assert controller.can_reset(100.0) is False


def test_reset_controller_runs_shell_command_successfully():
    controller = AdapterResetController(AdapterResetConfig(command="echo reset", cooldown_seconds=60.0))

    with patch("thelia.adapter_reset.subprocess.run", return_value=_CompletedProcess(returncode=0, stdout="ok")) as mocked_run:
        ok = controller.reset("test reason")

    assert ok is True
    mocked_run.assert_called_once()
    assert controller.can_reset(controller._last_reset_monotonic + 61.0) is True  # pylint: disable=protected-access


def test_reset_controller_blocks_during_cooldown():
    controller = AdapterResetController(AdapterResetConfig(command="echo reset", cooldown_seconds=60.0))

    with patch("thelia.adapter_reset.subprocess.run", return_value=_CompletedProcess(returncode=0)):
        assert controller.reset("test reason") is True

    assert controller.can_reset(controller._last_reset_monotonic + 10.0) is False  # pylint: disable=protected-access
    assert controller.seconds_until_reset_allowed(controller._last_reset_monotonic + 10.0) == 50.0  # pylint: disable=protected-access


def test_reset_controller_returns_false_on_command_failure():
    controller = AdapterResetController(AdapterResetConfig(command="false"))

    with patch("thelia.adapter_reset.subprocess.run", return_value=_CompletedProcess(returncode=1, stderr="boom")):
        ok = controller.reset("test reason")

    assert ok is False
