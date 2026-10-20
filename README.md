# Juice Meter 🧃

A small, dependency-free Python CLI for provider usage and quota reports. Codex and OpenRouter are included; new providers can be added as adapters without changing the shared report interface.

Built for compact Telegram output and Hermes no-agent cron, but usable from any shell.

## Features

- Reads Codex OAuth credentials from a Hermes-compatible `auth.json`.
- Reports remaining Codex quota and reset time from the ChatGPT usage endpoint.
- Optionally fetches reset-credit inventory and expiry dates; inventory failures do not hide valid quota data.
- Supports OpenRouter through a separate adapter.
- Keeps credentials out of output and never includes raw HTTP error bodies.
- Uses only the Python standard library.

## Requirements

- Python 3.10+
- Network access to configured provider endpoints
- For Codex: a Hermes/OpenAI Codex-compatible `auth.json`
- For OpenRouter: `OPENROUTER_API_KEY` in the environment or configured env file

## Run

```bash
python3 juice_meter.py --provider codex
python3 juice_meter.py --provider openrouter
python3 juice_meter.py --provider all
```

Normalized JSON output (credentials and headers excluded):

```bash
python3 juice_meter.py --provider codex --raw
```

## Configuration

| Variable | Default |
|---|---|
| `CODEX_AUTH_PATH` | `~/.hermes/auth.json` |
| `CODEX_ENV_PATH` | `~/.hermes/.env` |
| `CODEX_USAGE_URL` | `https://chatgpt.com/backend-api/wham/usage` |
| `CODEX_RESET_CREDITS_URL` | `https://chatgpt.com/backend-api/wham/rate-limit-reset-credits` |
| `OPENROUTER_KEY_URL` | `https://openrouter.ai/api/v1/key` |

Example with profile-specific Hermes credentials:

```bash
CODEX_AUTH_PATH=/path/to/profile/auth.json \
CODEX_ENV_PATH=/path/to/profile/.env \
  python3 juice_meter.py --provider codex
```

Keep each profile's auth file and environment file separate. Do not put credentials in command-line arguments, source files, or repository history.

## Telegram output

Codex output uses this structure as one provider-specific report; other adapters have their own sections:

```text
**🤖 Codex Usage**

🟢🟢🟢🟢🟢 98% left
🔄 Tue, 27.10 @8am

🍄 **3x resets available** (exp. 22.10, 29.10 and 6.11)
```

Percentage and primary reset date/time stay unbolded. Expiries within 48 hours are individually bold and prefixed with `⚠️`. Missing or failed supplementary reset-credit data never replaces a successful quota report.

## Hermes cron

Install the script in each profile's `scripts/` directory, then create a `no_agent: true` job that runs `juice_meter.py --provider codex`. Set `CODEX_AUTH_PATH` and `CODEX_ENV_PATH` for that profile; never share credential files between profiles. For an existing Codex cron, a small profile-local wrapper can set those two variables and call the shared script.

## Add a provider

1. Implement an adapter returning `ProviderUsage` with one or more `UsageWindow` values.
2. Register the adapter in `ADAPTERS`.
3. Add focused tests for API mapping, errors, and output.
4. Verify single-provider and `--provider all` output.

The renderer stays separate from provider-specific fetching, so provider additions do not need to change the Codex adapter or report model.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## Security

- Never commit `auth.json`, `.env`, OAuth tokens, API keys, or generated reports.
- OAuth tokens are sent only to the configured Codex endpoint.
- The ChatGPT usage and reset-credit endpoints are unofficial and may change.

## License

MIT. See [LICENSE](LICENSE).
