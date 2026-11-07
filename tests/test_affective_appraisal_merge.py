"""Regression tests for deterministic fast-screen cues surviving LLM appraisal."""
from __future__ import annotations

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from plugin.astrbot_plugin_affective_core.dynamics import (  # noqa: E402
    local_fast_screen,
    merge_appraisal_with_fast_screen,
)


class AppraisalFastScreenMergeTests(unittest.TestCase):
    def test_fast_care_open_survives_llm_null(self):
        fast = local_fast_screen("昨晚发烧39度现在头好晕")
        self.assertIsNotNone(fast.get("care_open"))
        merged = merge_appraisal_with_fast_screen(
            {
                "intent": "venting",
                "user_distress": 0.2,
                "care_open": None,
                "care_resolved_ids": [],
                "delta": {},
            },
            fast,
        )
        self.assertIsNotNone(merged.get("care_open"))

    def test_fast_serious_task_beats_llm_teasing(self):
        fast = local_fast_screen("帮我改一下这个 bug")
        self.assertEqual(fast["intent"], "serious_task")
        merged = merge_appraisal_with_fast_screen(
            {
                "intent": "teasing",
                "user_distress": 0.0,
                "care_open": None,
                "care_resolved_ids": [],
                "delta": {},
            },
            fast,
        )
        self.assertEqual(merged["intent"], "serious_task")


if __name__ == "__main__":
    unittest.main()
