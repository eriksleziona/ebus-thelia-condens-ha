"""Helpers for optional eBUS adapter hardware reset hooks."""

import logging
import subprocess
import time
from dataclasses import dataclass


@dataclass
class AdapterResetConfig:
    """Configuration for optional adapter hardware reset."""

    command: str = ""
    cooldown_seconds: float = 900.0
    settle_seconds: float = 20.0
    timeout_seconds: float = 30.0


class AdapterResetController:
    """Run an external command to power-cycle or reset the eBUS adapter."""

    def __init__(self, config: AdapterResetConfig):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
        self._last_reset_monotonic = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.config.command.strip())

    def can_reset(self, now: float) -> bool:
        if not self.enabled:
            return False
        if self._last_reset_monotonic <= 0.0:
            return True
        return (now - self._last_reset_monotonic) >= max(0.0, self.config.cooldown_seconds)

    def seconds_until_reset_allowed(self, now: float) -> float:
        if not self.enabled or self._last_reset_monotonic <= 0.0:
            return 0.0

        return max(0.0, self.config.cooldown_seconds - (now - self._last_reset_monotonic))

    def reset(self, reason: str) -> bool:
        """Execute the configured adapter reset command."""
        if not self.enabled:
            return False

        self.logger.warning("Running adapter reset command: %s", reason)

        try:
            completed = subprocess.run(
                ["/bin/sh", "-lc", self.config.command],
                capture_output=True,
                text=True,
                timeout=max(1.0, self.config.timeout_seconds),
                check=False,
            )
        except Exception as e:
            self.logger.error("Adapter reset command crashed: %s", e)
            return False

        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        if stdout:
            self.logger.info("Adapter reset stdout: %s", stdout)
        if stderr:
            self.logger.warning("Adapter reset stderr: %s", stderr)

        if completed.returncode != 0:
            self.logger.error("Adapter reset command failed with exit code %s", completed.returncode)
            return False

        self._last_reset_monotonic = time.monotonic()
        self.logger.warning("Adapter reset command completed successfully")
        return True
