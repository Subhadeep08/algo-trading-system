# NSE Portfolio Cockpit

Automated daily portfolio monitoring and PMS-grade candidate screening system for a Groww NSE account.

## What this repo does

**Daily automation (GitHub Actions)** runs three cockpit phases each market day:

| Workflow | Schedule (IST) | Script |
|----------|---------------|--------|
| `cockpit-phase1.yml` | 09:10 Pre-market | `scripts/cockpit_runner.py --phase 1` |
| `cockpit-phase2.yml` | 13:35 Mid-market | `scripts/cockpit_runner.py --phase 2` |
| `cockpit-phase3.yml` | 15:35 Post-market | `scripts/cockpit_runner.py --phase 3` |
| `screen-candidates.yml` | 16:10 Post-close | `scripts/screen_candidates.py` |
| `update-portfolio.yml` | On-demand | `scripts/update_portfolio.py` |

All phases send results to Telegram via `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` secrets.

## Scripts

- **`scripts/cockpit_runner.py`** — Main cockpit: stop-loss audit, relative strength, GTT checks, Phase 3D holding re-qualification (Gates 1+2 on active holdings)
- **`scripts/screen_candidates.py`** — PMS candidate screener: 4 hard gates + 24-parameter secondary overlay, position sizing
- **`scripts/fetch_nse_prices.py`** — Fetches live NSE prices via yfinance, writes `references/live-prices.md`
- **`scripts/update_portfolio.py`** — AST-based updater for portfolio holdings and GTT orders in `cockpit_runner.py`

## References

- `.claude/skills/portfolio-cockpit/references/portfolio-data.md` — holdings, SL levels, targets, GTT orders (skill copy)
- `.claude/skills/portfolio-cockpit/references/pms-screening-spec.md` — full 24-parameter PMS spec, gate thresholds, formulas
- `.claude/skills/portfolio-cockpit/references/watchlist.md` — PMS candidates under screening
- `references/portfolio-data.md` — runtime copy read by GitHub Actions scripts
- `references/live-prices.md` — written by `fetch_nse_prices.py` at runtime (gitignored)

## Secrets required

| Secret | Purpose |
|--------|---------|
| `TELEGRAM_BOT_TOKEN` | Telegram bot authentication |
| `TELEGRAM_CHAT_ID` | Telegram chat to send reports to |
| `PORTFOLIO_VALUE_INR` | Current portfolio value for position sizing |
| `ANTHROPIC_API_KEY` | Claude API (screen_candidates.py Claude calls) |

## Interactive cockpit (Claude Code)

Invoke the `portfolio-cockpit` skill in any Claude Code session:
- "run my portfolio check" → auto-detects IST time → runs the right phase
- "phase 1" / "phase 2" / "phase 3" → explicit phase selection
- "watchlist screen" / "phase 5" → runs PMS candidate screening interactively
- "holding re-qualification" → runs Phase 3D (Gates 1+2 on active holdings)

## Dependencies

`pip install yfinance requests anthropic pytz`

yfinance works in GitHub Actions (open internet). In the Claude Code remote container,
yfinance returns 403 due to proxy restrictions — the skill uses WebSearch as fallback.
