#!/usr/bin/env python3
"""Modular provider usage report for Hermes no-agent cron.

Supports deterministic one-shot usage checks for multiple providers without
printing secrets. Designed for compact Telegram output.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import textwrap
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

AUTH_PATH = Path(
    os.environ.get("CODEX_AUTH_PATH", "~/.hermes/auth.json")
).expanduser()
ENV_PATH = Path(
    os.environ.get("CODEX_ENV_PATH", "~/.hermes/.env")
).expanduser()
CODEX_USAGE_URL = os.environ.get(
    "CODEX_USAGE_URL", "https://chatgpt.com/backend-api/wham/usage"
)
CODEX_RESET_CREDITS_URL = os.environ.get(
    "CODEX_RESET_CREDITS_URL",
    "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits",
)
OPENROUTER_KEY_URL = os.environ.get(
    "OPENROUTER_KEY_URL", "https://openrouter.ai/api/v1/key"
)


@dataclass
class UsageWindow:
    label: str
    used_percent: float | None = None
    reset_after_seconds: int | None = None
    detail: str | None = None


@dataclass
class ProviderUsage:
    provider_key: str
    display_name: str
    plan_label: str | None = None
    windows: list[UsageWindow] = field(default_factory=list)
    error: str | None = None
    expected_labels: list[str] = field(default_factory=list)
    reset_credits_available: int | None = None
    reset_credit_expiries: list[datetime] = field(default_factory=list)


def load_env_file(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload.encode()).decode())
    except Exception:
        return {}


def request_json(url: str, headers: dict[str, str], timeout: float = 30) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc


def parse_reset_credit_inventory(
    payload: dict[str, Any], now: datetime | None = None
) -> tuple[int, list[datetime]]:
    """Count available, unexpired reset credits and return their expiries."""
    credits = payload.get("credits")
    if not isinstance(credits, list):
        raise ValueError("missing reset credits")
    current = now or datetime.now().astimezone()
    available: list[datetime | None] = []
    for credit in credits:
        if not isinstance(credit, dict) or credit.get("status") != "available":
            continue
        raw_expiry = credit.get("expires_at")
        expiry = None
        if isinstance(raw_expiry, str) and raw_expiry:
            expiry = datetime.fromisoformat(raw_expiry.replace("Z", "+00:00")).astimezone()
            if expiry <= current:
                continue
        available.append(expiry)
    available.sort(key=lambda value: (value is None, value.timestamp() if value else 0))
    return len(available), [value for value in available if value is not None]


def fmt_bar(percent: float | None, width: int = 10) -> str:
    if percent is None:
        return "[----------]"
    pct = max(0, min(100, int(round(percent))))
    filled = round(pct * width / 100)
    if pct > 0:
        filled = max(2, filled)
    filled = max(0, min(width, filled))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def fmt_percent(percent: float | None) -> str:
    if percent is None:
        return "n/a"
    return f"{int(round(percent))}%"


def fmt_reset(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    seconds = max(0, int(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours:
        return f"reset {hours}h"
    return f"reset {minutes}m"


def fmt_codex_reset(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"↻ {seconds // 60}m"
    if seconds < 86400:
        return f"↻ {seconds // 3600}h"
    reset_at = datetime.now() + timedelta(seconds=seconds)
    return f"↻ {reset_at.strftime('%a')}"


def fmt_codex_reset_long(seconds: int | None) -> str:
    if seconds is None:
        return "unavailable"
    reset_at = datetime.now() + timedelta(seconds=max(0, int(seconds)))
    return reset_at.strftime("%a, %-d.%m @%-I%p").replace("AM", "am").replace("PM", "pm")


def codex_circles(percent: float | None, total: int = 5) -> str:
    if percent is None:
        filled = 0
    else:
        filled = max(0, min(total, round(max(0, min(100, percent)) * total / 100)))
    return "🟢" * filled + "⚪️" * (total - filled)


def render_codex_compact(provider: ProviderUsage) -> str:
    """Render Codex in the compact Telegram format used by this installation."""
    lines = ["**🤖 Codex Usage**", ""]
    if provider.error or not provider.windows:
        message = compact_error_message(provider) if provider.error else "Usage data unavailable."
        lines.extend(["⚪️⚪️⚪️⚪️⚪️ unavailable ⚠️", f"⚠️ {message}"])
        return "\n".join(lines)

    multiple_windows = len(provider.windows) > 1
    for window in provider.windows:
        label = f"{window.label}: " if multiple_windows else ""
        if window.used_percent is None:
            usage = f"{label}⚪️⚪️⚪️⚪️⚪️ unavailable ⚠️"
        else:
            remaining = max(0.0, min(100.0, window.used_percent))
            alert = " ⚠️" if remaining <= 20 else ""
            usage = f"{label}{codex_circles(remaining)} {fmt_percent(remaining)} left{alert}"
        lines.append(usage)
        reset = fmt_codex_reset_long(window.reset_after_seconds)
        reset_text = "Reset time unavailable" if reset == "unavailable" else reset
        lines.append(f"🔄 {window.label} reset: {reset_text}" if multiple_windows else f"🔄 {reset_text}")

    if provider.reset_credits_available is not None:
        lines.append("")
        count = max(0, provider.reset_credits_available)
        noun = "reset" if count == 1 else "resets"
        text = f"🍄 **{count}x {noun} available**"
        if provider.reset_credit_expiries:
            now = datetime.now().astimezone()
            dates = []
            for expiry in provider.reset_credit_expiries:
                local_expiry = expiry.astimezone()
                date = local_expiry.strftime("%-d.%-m")
                if timedelta(0) <= local_expiry - now <= timedelta(hours=48):
                    dates.append(f"⚠️ **{date}**")
                else:
                    dates.append(date)
            if len(dates) == 1:
                text += f" (exp. {dates[0]})"
            elif len(dates) == 2:
                text += f" (exp. {dates[0]} and {dates[1]})"
            else:
                text += f" (exp. {', '.join(dates[:-1])} and {dates[-1]})"
        lines.append(text)
    return "\n".join(lines)


def compact_error_message(provider: ProviderUsage) -> str:
    """Return one safe, concise message without exposing raw API payloads."""
    error = (provider.error or "").lower()

    if "token_expired" in error or "token is expired" in error:
        return "Token expired. Sign in again."
    if "http 401" in error or "no codex auth" in error or "unauthorized" in error:
        return "Authentication failed."
    if "http 403" in error or "forbidden" in error:
        return "Access denied."
    if "http 429" in error or "rate limit" in error:
        return "Rate limited. Try again later."
    if "timed out" in error or "timeout" in error:
        return "Request timed out."
    if any(term in error for term in ("urlopen", "network", "dns", "connection")):
        return "Network unavailable."
    if "missing usage windows" in error:
        return "Usage data unavailable."
    return "Usage check failed."


def render_provider(provider: ProviderUsage) -> list[str]:
    title = "🧪 " + provider.display_name
    if provider.plan_label:
        title += f" {provider.plan_label}"
    lines = [title, "━━━━━━━━━━━━━━━━"]
    if provider.error:
        for label in provider.expected_labels:
            lines.append(f"{label:<4} [----------]  n/a")
        lines.extend(["", "ERROR"])
        lines.extend(textwrap.wrap(compact_error_message(provider), width=32))
        return lines
    for window in provider.windows:
        if provider.provider_key == "codex":
            reset = fmt_codex_reset(window.reset_after_seconds)
            if window.used_percent is None:
                suffix = "unlimited"
                if reset:
                    suffix += f" ({reset})"
            else:
                suffix = f"{fmt_percent(window.used_percent)} left"
                if reset:
                    suffix += f" ({reset})"
            marker = "⚠" if window.used_percent is not None and window.used_percent < 20 else " "
            lines.append(f"{window.label:<4} {fmt_bar(window.used_percent)} {marker}{suffix}".rstrip())
            continue
        suffix = window.detail or fmt_reset(window.reset_after_seconds) or ""
        lines.append(f"{window.label:<4} {fmt_bar(window.used_percent)} {fmt_percent(window.used_percent):>4}  {suffix}".rstrip())
    return lines


def usage_opening(provider: ProviderUsage) -> str:
    """Return the complete one-line Telegram opening for a provider report."""
    label = provider.display_name.title()
    plan = f" ({provider.plan_label})" if provider.plan_label else ""
    if provider.error:
        return f"## 🧪 {label} usage{plan} — ❌ Check failed; {compact_error_message(provider).lower()}"
    if not provider.windows:
        return f"## 🧪 {label} usage{plan} — ⚠️ No usage windows were returned."
    details = []
    for window in provider.windows:
        remaining = fmt_percent(window.used_percent)
        reset = fmt_codex_reset(window.reset_after_seconds) if provider.provider_key == "codex" else fmt_reset(window.reset_after_seconds)
        detail = f"{window.label} {remaining} left"
        if window.detail:
            detail = f"{window.label} {window.detail}"
        if reset:
            detail += f" ({reset})"
        details.append(detail)
    low = any(window.used_percent is not None and window.used_percent < 20 for window in provider.windows)
    status = "⚠️ Low remaining" if low else "✅ Available"
    return f"## 🧪 {label} usage{plan} — {status}; " + "; ".join(details) + "."


def render_report(providers: list[ProviderUsage], title: str = "## 🧪 Provider Usage") -> str:
    if len(providers) == 1:
        if providers[0].provider_key == "codex":
            return render_codex_compact(providers[0])
        elif providers[0].provider_key == "openrouter":
            title = usage_opening(providers[0])
    body: list[str] = []
    for idx, provider in enumerate(providers):
        if idx:
            body.append("")
        body.extend(render_provider(provider))
    return title + "\n\n```\n" + "\n".join(body) + "\n```"


class CodexAdapter:
    key = "codex"
    display_name = "CODEX"

    @staticmethod
    def expected_labels(plan_label: str | None) -> list[str]:
        # Plus currently exposes only the weekly window. Other known Codex
        # plans may expose both the 5-hour and weekly windows.
        return ["wk"] if (plan_label or "").lower() == "plus" else ["5h", "wk"]

    def load_auth(self) -> tuple[str, str | None, str | None]:
        data = json.loads(AUTH_PATH.read_text())
        pool = (data.get("credential_pool") or {}).get("openai-codex")
        if isinstance(pool, list) and pool:
            # Credential pools may contain an older expired token before a
            # freshly refreshed one.  Do not blindly use pool[0]: select the
            # newest currently unexpired access token, falling back to the
            # newest entry only when every token is expired so the API error is
            # still reported accurately.
            candidates = []
            for index, candidate in enumerate(pool):
                token = candidate.get("access_token") or (candidate.get("tokens") or {}).get("access_token")
                if not token:
                    continue
                payload = decode_jwt_payload(token)
                auth_claim = payload.get("https://api.openai.com/auth", {})
                exp = payload.get("exp")
                refresh = candidate.get("last_refresh") or ""
                candidates.append((bool(exp and float(exp) > datetime.now().timestamp()), refresh, index, token, auth_claim))
            if candidates:
                usable = [item for item in candidates if item[0]]
                _, _, _, token, auth_claim = max(usable or candidates, key=lambda item: (item[1], item[2]))
                account_id = auth_claim.get("chatgpt_account_id")
                return token, account_id, auth_claim.get("chatgpt_plan_type")
        provider = (data.get("providers") or {}).get("openai-codex") or {}
        tokens = provider.get("tokens") or {}
        token = tokens.get("access_token")
        if token:
            payload = decode_jwt_payload(token)
            auth_claim = payload.get("https://api.openai.com/auth", {})
            account_id = tokens.get("account_id") or auth_claim.get("chatgpt_account_id")
            return token, account_id, auth_claim.get("chatgpt_plan_type")
        raise RuntimeError("no codex auth")

    def fetch(self) -> ProviderUsage:
        plan_label: str | None = None
        try:
            token, account_id, plan_label = self.load_auth()
            data = request_json(CODEX_USAGE_URL, {
                "Authorization": f"Bearer {token}",
                "ChatGPT-Account-Id": account_id or "",
                "User-Agent": "codex-cli",
                "Accept": "application/json",
            })
            rate = data.get("rate_limit") or {}
            primary = rate.get("primary_window")
            secondary = rate.get("secondary_window")
            if not isinstance(primary, dict):
                raise RuntimeError("missing usage windows")
            
            # Label windows only from the duration returned by the API.
            def window_label(seconds: int) -> str:
                if seconds <= 0:
                    return "window"
                if seconds == 18000:
                    return "5h"
                if seconds == 604800:
                    return "wk"
                return f"{seconds // 3600}h" if seconds >= 3600 else f"{seconds}s"
            
            windows = []
            # Primary window comes first in response, label based on actual duration
            primary_seconds_value = primary.get("limit_window_seconds")
            primary_seconds = int(primary_seconds_value) if isinstance(primary_seconds_value, (int, float)) else 0
            primary_used = primary.get("used_percent")
            primary_reset = primary.get("reset_after_seconds")
            windows.append(UsageWindow(
                window_label(primary_seconds),
                100.0 - float(primary_used) if isinstance(primary_used, (int, float)) else None,
                int(primary_reset) if isinstance(primary_reset, (int, float)) else None,
            ))
            if isinstance(secondary, dict):
                secondary_seconds_value = secondary.get("limit_window_seconds")
                secondary_seconds = int(secondary_seconds_value) if isinstance(secondary_seconds_value, (int, float)) else 0
                secondary_used = secondary.get("used_percent")
                secondary_reset = secondary.get("reset_after_seconds")
                windows.append(UsageWindow(
                    window_label(secondary_seconds),
                    100.0 - float(secondary_used) if isinstance(secondary_used, (int, float)) else None,
                    int(secondary_reset) if isinstance(secondary_reset, (int, float)) else None,
                ))
            reset_credit_count = None
            reset_credit_expiries: list[datetime] = []
            aggregate = data.get("rate_limit_reset_credits")
            if isinstance(aggregate, dict):
                count_value = aggregate.get("available_count")
                if isinstance(count_value, int) and not isinstance(count_value, bool):
                    reset_credit_count = max(0, count_value)
            try:
                inventory = request_json(CODEX_RESET_CREDITS_URL, {
                    "Authorization": f"Bearer {token}",
                    "ChatGPT-Account-ID": account_id or "",
                    "Accept": "application/json",
                    "OpenAI-Beta": "codex-1",
                    "originator": "Codex Desktop",
                }, timeout=4)
                reset_credit_count, reset_credit_expiries = parse_reset_credit_inventory(inventory)
            except Exception:
                pass
            return ProviderUsage(
                provider_key=self.key,
                display_name=self.display_name,
                plan_label=str(data.get("plan_type") or plan_label or "").lower() or None,
                windows=windows,
                reset_credits_available=reset_credit_count,
                reset_credit_expiries=reset_credit_expiries,
            )
        except Exception as exc:
            return ProviderUsage(
                self.key,
                self.display_name,
                plan_label=plan_label,
                error=f"codex {exc}",
                expected_labels=self.expected_labels(plan_label),
            )


class OpenRouterAdapter:
    key = "openrouter"
    display_name = "OPENROUTER"

    @staticmethod
    def safe_label(label: str | None) -> str | None:
        if not label:
            return None
        label = label.strip()
        if label.lower().startswith(("sk-", "sk_or_", "sk-or-")):
            return None
        return label[:24]

    def fetch(self) -> ProviderUsage:
        try:
            api_key = os.getenv("OPENROUTER_API_KEY")
            if not api_key:
                raise RuntimeError("missing OPENROUTER_API_KEY")
            data = request_json(OPENROUTER_KEY_URL, {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            }).get("data")
            if not isinstance(data, dict):
                raise RuntimeError("missing key data")
            label = self.safe_label(str(data.get("label") or "").strip() or None)
            limit = data.get("limit")
            remaining = data.get("limit_remaining")
            usage_daily = float(data.get("usage_daily") or 0)
            usage_weekly = float(data.get("usage_weekly") or 0)
            usage_monthly = float(data.get("usage_monthly") or 0)
            windows: list[UsageWindow] = []
            if isinstance(limit, (int, float)) and limit > 0 and isinstance(remaining, (int, float)):
                used = max(0.0, float(limit) - float(remaining))
                pct = min(100.0, max(0.0, used / float(limit) * 100))
                windows.append(UsageWindow("key", pct, detail=f"${float(remaining):.2f} left"))
            else:
                windows.append(UsageWindow("key", None, detail=f"${float(data.get('usage') or 0):.2f} used"))
            windows.extend([
                UsageWindow("day", None, detail=f"${usage_daily:.2f}"),
                UsageWindow("wk", None, detail=f"${usage_weekly:.2f}"),
                UsageWindow("mo", None, detail=f"${usage_monthly:.2f}"),
            ])
            return ProviderUsage(self.key, self.display_name, plan_label=label, windows=windows)
        except Exception as exc:
            return ProviderUsage(
                self.key,
                self.display_name,
                error=f"openrouter {exc}",
                expected_labels=["key", "day", "wk", "mo"],
            )


ADAPTERS = {
    "codex": CodexAdapter,
    "openrouter": OpenRouterAdapter,
}


def select_providers(selection: str) -> list[str]:
    if selection == "all":
        return ["codex", "openrouter"]
    return [part.strip() for part in selection.split(",") if part.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Render deterministic provider usage report.")
    parser.add_argument("--provider", default="all", help="Provider key, comma-list, or all. Default: all")
    parser.add_argument("--raw", action="store_true", help="Print normalized JSON without secrets")
    args = parser.parse_args()

    load_env_file()
    providers: list[ProviderUsage] = []
    for key in select_providers(args.provider):
        adapter_cls = ADAPTERS.get(key)
        if not adapter_cls:
            providers.append(ProviderUsage(key, key.upper(), error="unknown provider"))
            continue
        providers.append(adapter_cls().fetch())

    if args.raw:
        payload = []
        for provider in providers:
            item = provider.__dict__.copy()
            item["windows"] = [window.__dict__ for window in provider.windows]
            item["reset_credit_expiries"] = [expiry.isoformat() for expiry in provider.reset_credit_expiries]
            payload.append(item)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_report(providers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
