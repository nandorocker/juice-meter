import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import juice_meter


class FormattingTests(unittest.TestCase):
    def test_bar_and_percent(self):
        self.assertEqual(juice_meter.fmt_bar(9), "[##--------]")
        self.assertEqual(juice_meter.fmt_percent(91.2), "91%")
        self.assertEqual(juice_meter.fmt_percent(None), "n/a")

    def test_codex_report_preserves_layout_and_plain_quota_lines(self):
        provider = juice_meter.ProviderUsage(
            "codex", "CODEX", windows=[juice_meter.UsageWindow("wk", 98, 604800)]
        )
        report = juice_meter.render_codex_compact(provider)
        lines = report.splitlines()
        self.assertEqual(lines[0], "**🤖 Codex Usage**")
        self.assertEqual(lines[1], "")
        self.assertIn("98% left", lines[2])
        self.assertFalse(lines[2].startswith("**"))
        self.assertRegex(lines[3], r"^🔄 \w{3}, \d{1,2}\.\d{1,2} @\d{1,2}(am|pm)$")
        self.assertNotIn("**", lines[3])

    def test_expiry_within_48_hours_is_marked(self):
        provider = juice_meter.ProviderUsage(
            "codex",
            "CODEX",
            windows=[juice_meter.UsageWindow("wk", 98, 604800)],
            reset_credits_available=1,
            reset_credit_expiries=[datetime.now().astimezone() + timedelta(hours=47)],
        )
        report = juice_meter.render_codex_compact(provider)
        self.assertIn("🍄 **1x reset available** (exp. ⚠️ **", report)

    def test_credit_inventory_filters_expired_and_redeemed(self):
        now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
        count, expiries = juice_meter.parse_reset_credit_inventory(
            {
                "credits": [
                    {"status": "available", "expires_at": "2026-10-21T12:00:00Z"},
                    {"status": "available", "expires_at": "2026-10-19T12:00:00Z"},
                    {"status": "redeemed", "expires_at": "2026-10-21T12:00:00Z"},
                    {"status": "available"},
                ]
            },
            now=now,
        )
        self.assertEqual(count, 2)
        self.assertEqual(expiries, [datetime(2026, 10, 21, 12, tzinfo=timezone.utc).astimezone()])

    def test_auth_loader_uses_newest_unexpired_token(self):
        auth = {
            "credential_pool": {
                "openai-codex": [
                    {"access_token": "old", "last_refresh": "2026-01-01"},
                    {"access_token": "new", "last_refresh": "2026-02-01"},
                ]
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "auth.json"
            path.write_text(json.dumps(auth))
            with patch.object(juice_meter, "AUTH_PATH", path), patch.object(
                juice_meter, "decode_jwt_payload", side_effect=[{"exp": 1}, {"exp": 4102444800}]
            ):
                token, _, _ = juice_meter.CodexAdapter().load_auth()
        self.assertEqual(token, "new")

    def test_optional_credit_endpoint_failure_keeps_usage(self):
        payload = {
            "plan_type": "plus",
            "rate_limit": {"primary_window": {
                "limit_window_seconds": 604800,
                "used_percent": 2,
                "reset_after_seconds": 3600,
            }},
            "rate_limit_reset_credits": {"available_count": 3},
        }
        adapter = juice_meter.CodexAdapter()
        with patch.object(adapter, "load_auth", return_value=("fake-token", "account", "plus")), patch.object(
            juice_meter, "request_json", side_effect=[payload, RuntimeError("inventory unavailable")]
        ):
            result = adapter.fetch()
        self.assertFalse(result.error)
        self.assertEqual(result.windows[0].used_percent, 98)
        self.assertEqual(result.reset_credits_available, 3)
        self.assertEqual(result.reset_credit_expiries, [])


if __name__ == "__main__":
    unittest.main()
