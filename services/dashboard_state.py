# -*- coding: utf-8 -*-
"""
Dashboard 状态查询层
把原先散落在 man.py 中的聚合读取能力收口，便于 REST / SSE 共用。
"""


def build_dashboard_snapshot(man_module):
    return {
        "server_time": man_module.now_str(),
        "run_id": man_module.RUN_ID,
        "strategy_state": man_module.get_strategy_state_snapshot(),
        "summary": man_module.get_dual_summary_snapshot(),
        "strategy_params": man_module.get_strategy_param_snapshot(),
        "ai_config": man_module.get_ai_runtime_snapshot(),
        "position": man_module.get_position_snapshot(),
        "pending_open": man_module.get_pending_open_snapshot(),
        "pending_ai_suggestion": man_module.get_pending_ai_suggestion_snapshot(),
        "runtime_update_status": man_module.get_runtime_status(),
        "trading_mode": {
            "mode": man_module.TRADING_MODE,
            "api_configured": bool(man_module.BINANCE_API_KEY and man_module.BINANCE_API_SECRET),
            "base_url": man_module.BINANCE_BASE_URL
        },
        "live_account": man_module.get_live_account_snapshot() if man_module.TRADING_MODE == "LIVE" else None,
        "api_rate_limit": {
            "market_data": {
                "server_time_cache_seconds": man_module.get_market_cache_ttl(f"{man_module.BASE_URL}/fapi/v1/time"),
                "ticker_price_cache_seconds": man_module.get_market_cache_ttl(f"{man_module.BASE_URL}/fapi/v1/ticker/price"),
                "klines_cache_seconds": man_module.get_market_cache_ttl(f"{man_module.BASE_URL}/fapi/v1/klines"),
                "exchange_info_cache_seconds": man_module.get_market_cache_ttl(f"{man_module.BASE_URL}/fapi/v1/exchangeInfo")
            },
            "private_data": man_module.binance_client.get_rate_limit_snapshot() if man_module.binance_client else None
        },
        "recent_trades": man_module.read_recent_jsonl(man_module.TRADE_DETAIL_JSONL_FILE, limit=20),
        "recent_ai_records": man_module.read_recent_jsonl(man_module.AI_SUGGESTION_JSONL_FILE, limit=10),
        "logs": {
            "main": man_module.tail_main_log_for_run(limit=60),
            "error": man_module.tail_text_file(man_module.ERROR_LOG_FILE, limit=20),
            "trade": man_module.tail_text_file(man_module.TRADE_LOG_FILE, limit=30),
            "position": man_module.tail_text_file(man_module.POSITION_LOG_FILE, limit=20),
            "summary": man_module.tail_text_file(man_module.SUMMARY_LOG_FILE, limit=20)
        }
    }
