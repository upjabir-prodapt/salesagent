"""Apply test env via MonkeyPatch before any src module imports Settings."""

from __future__ import annotations

import pytest

from tests.settings_env import apply_test_settings_env

SESSION_MP = pytest.MonkeyPatch()
apply_test_settings_env(SESSION_MP)
