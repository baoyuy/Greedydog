# -*- coding: utf-8 -*-

# ============================================================
# 币安 U 本位合约 模拟交易程序（修复增强版）
# ------------------------------------------------------------
# 特点：
# 1. 使用 Binance Futures 公共接口，不需要 API Key
# 2. 支持代理
# 3. 支持 1m / 3m / 5m / 15m 等分钟周期
# 4. 每个周期必须下注一次：LONG 或 SHORT
# 5. 周期结束自动平仓，再开下一单
# 6. 修复“平仓后没重新开仓”的问题
# 7. 增强详细记录，方便后续优化策略
#
# 重要说明：
# 1. 这是模拟交易，不是真实下单
# 2. 手续费使用你在 .env 配置的费率
# 3. 如果开启止盈止损，可能在周期中途提前平仓；
#    这种情况下本周期不会再次重开，要等下个周期
# ============================================================

import os
import time
import csv
import json
import argparse
import threading
from decimal import Decimal, ROUND_DOWN
import requests
import pandas as pd

from datetime import datetime, timezone
from dotenv import load_dotenv
from binance_client import BinanceClient
from adapters.binance_adapter import BinanceAdapter
from services.dashboard_state import build_dashboard_snapshot
from services.state_bus import state_bus

# ============================================================
# 一、读取 .env 配置
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_FILE_PATH)


def resolve_runtime_dir():
    raw_dir = os.getenv("RUNTIME_DIR", "runtime").strip() or "runtime"
    if os.path.isabs(raw_dir):
        return raw_dir
    return os.path.join(BASE_DIR, raw_dir)


def runtime_file(file_name):
    return os.path.join(RUNTIME_DIR, file_name)

# -------------------------
# 基础参数
# -------------------------
SYMBOL = os.getenv("SYMBOL", "BTCUSDT").upper()
INTERVAL = os.getenv("INTERVAL", "5m").lower()

NOTIONAL_USDT = float(os.getenv("NOTIONAL_USDT", "100"))
TAKER_FEE_RATE = float(os.getenv("TAKER_FEE_RATE", "0.0004"))
LEVERAGE = int(os.getenv("LEVERAGE", "5"))

TRADE_START_TIME = os.getenv("TRADE_START_TIME", "00:00")
TRADE_END_TIME = os.getenv("TRADE_END_TIME", "23:59")

STATUS_INTERVAL_SECONDS = int(os.getenv("STATUS_INTERVAL_SECONDS", "60"))

EMA_FAST = int(os.getenv("EMA_FAST", "20"))
EMA_SLOW = int(os.getenv("EMA_SLOW", "50"))
ADX_PERIOD = int(os.getenv("ADX_PERIOD", "14"))
VOLUME_MA_PERIOD = int(os.getenv("VOLUME_MA_PERIOD", "20"))
STRUCTURE_LOOKBACK = int(os.getenv("STRUCTURE_LOOKBACK", "3"))

ENABLE_TAKE_PROFIT = os.getenv("ENABLE_TAKE_PROFIT", "false").lower() == "true"
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.0045"))

ENABLE_STOP_LOSS = os.getenv("ENABLE_STOP_LOSS", "false").lower() == "true"
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.0035"))

USE_PROXY = os.getenv("USE_PROXY", "false").lower() == "true"
PROXY_URL = os.getenv("PROXY_URL", "").strip()

# 这个参数控制在“周期边界后的多少秒内”允许触发交易逻辑
# 默认 5 秒，更稳，不容易因为边界时的网络抖动错过
CYCLE_TRIGGER_WINDOW_SECONDS = int(os.getenv("CYCLE_TRIGGER_WINDOW_SECONDS", "5"))

# 如果某一周期平仓后开仓失败，程序会每隔多少秒自动补开仓一次
OPEN_RETRY_INTERVAL_SECONDS = int(os.getenv("OPEN_RETRY_INTERVAL_SECONDS", "5"))

# Binance Futures 公共接口地址
BASE_URL = "https://fapi.binance.com"

# 当前运行唯一 ID
RUN_ID = datetime.now().strftime("%Y%m%d_%H%M%S")
RUNTIME_DIR = resolve_runtime_dir()

# 日志文件
TRADE_LOG_FILE = runtime_file("futures_trade_log.txt")
POSITION_LOG_FILE = runtime_file("futures_position_log.txt")
ERROR_LOG_FILE = runtime_file("futures_error_log.txt")
SUMMARY_LOG_FILE = runtime_file("futures_summary_log.txt")

# 明细文件
TRADE_CSV_FILE = runtime_file("futures_trades.csv")
TRADE_DETAIL_JSONL_FILE = runtime_file("futures_trades_detail.jsonl")
AI_SUGGESTION_JSONL_FILE = runtime_file("ai_param_suggestions.jsonl")
AI_SKILL_FILE = os.path.join(BASE_DIR, "ai_parameter_optimizer_skill.txt")
AI_MANUAL_TRIGGER_FLAG_FILE = runtime_file("manual_ai_optimize.flag")

# 参数快照 / 汇总快照
RUN_CONFIG_FILE = runtime_file(f"run_config_snapshot_{RUN_ID}.json")
RUN_CONFIG_LATEST_FILE = runtime_file("run_config_snapshot_latest.json")

SUMMARY_JSON_FILE = runtime_file(f"futures_summary_snapshot_{RUN_ID}.json")
SUMMARY_JSON_LATEST_FILE = runtime_file("futures_summary_snapshot_latest.json")

# 统一主日志与运行时状态
MAIN_LOG_FILE = runtime_file("app.log")
RUNTIME_STATUS_FILE = runtime_file("runtime_status_latest.json")
ARCHIVE_DIR = runtime_file("archive")

# 当前持仓
position = None
LIVE_POSITION_SNAPSHOT_FILE = runtime_file("live_position_snapshot.json")
MARKET_DATA_RATE_LIMITS = {
    "/fapi/v1/time": 0.5,
    "/fapi/v1/ticker/price": 0.5,
    "/fapi/v1/klines": 1.0,
    "/fapi/v1/exchangeInfo": 10.0,
}
MARKET_DATA_CACHE_TTLS = {
    "/fapi/v1/time": 0.5,
    "/fapi/v1/ticker/price": 0.5,
    "/fapi/v1/klines": 1.0,
    "/fapi/v1/exchangeInfo": 3600.0,
}
MARKET_DATA_CACHE_LABELS = {
    "/fapi/v1/time": "server_time",
    "/fapi/v1/ticker/price": "ticker_price",
    "/fapi/v1/klines": "klines",
    "/fapi/v1/exchangeInfo": "exchange_info",
}
market_data_rate_limit_lock = threading.Lock()
market_data_last_request_at = {}
market_data_cache_lock = threading.Lock()
market_data_cache = {}
ai_last_analysis_trade_count = -1
strategy_thread = None
strategy_stop_event = threading.Event()
strategy_state_lock = threading.Lock()
strategy_state = {
    "running": False,
    "started_at": None,
    "stopped_at": None,
    "last_error": "",
    "last_message": "idle"
}
pending_ai_suggestion = None
pending_ai_lock = threading.Lock()

# 最近一次已经处理过的“已收盘K线开始时间”
# 用来防止同一个周期重复执行完整流程
last_trade_kline_open_time = None

# 如果某周期应该开仓，但因为瞬时网络/价格接口异常没有开成功
# 就把决策暂存下来，后续自动补开
pending_open_decision = None
pending_signal_kline_open_time = None
last_open_retry_ts = 0
binance_adapter = BinanceAdapter()


# ============================================================
# 二、统计信息
# ============================================================

stats = {
    "total_trades": 0,            # 总交易次数
    "win_trades": 0,              # 盈利次数
    "loss_trades": 0,             # 亏损次数
    "flat_trades": 0,             # 持平次数
    "total_gross_pnl": 0.0,       # 总毛利润
    "total_net_pnl": 0.0,         # 总净利润
    "total_fee": 0.0,             # 总手续费
    "max_profit": None,           # 最大单笔净利润
    "max_loss": None,             # 最大单笔净亏损（负数）
    "current_win_streak": 0,      # 当前连续盈利
    "current_loss_streak": 0,     # 当前连续亏损
    "max_win_streak": 0,          # 最大连续盈利
    "max_loss_streak": 0          # 最大连续亏损
}


# ============================================================
# 三、工具函数
# ============================================================

def interval_to_seconds(interval_text):
    """
    把 1m / 3m / 5m / 15m 转成秒数
    """
    interval_text = interval_text.strip().lower()

    if interval_text.endswith("m"):
        minutes = int(interval_text[:-1])
        return minutes * 60

    raise ValueError(f"暂不支持的周期格式: {interval_text}")


INTERVAL_SECONDS = interval_to_seconds(INTERVAL)

# 最大持仓秒数默认自动等于当前周期秒数
MAX_HOLD_SECONDS = int(os.getenv("MAX_HOLD_SECONDS", str(INTERVAL_SECONDS)))

AI_ENABLED = os.getenv("AI_ENABLED", "false").lower() == "true"
AI_BASE_URL = os.getenv("AI_BASE_URL", "").strip()
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "").strip()
AI_TIMEOUT_SECONDS = int(os.getenv("AI_TIMEOUT_SECONDS", "60"))
AI_AUTO_OPTIMIZE_ENABLED = os.getenv("AI_AUTO_OPTIMIZE_ENABLED", "false").lower() == "true"
AI_AUTO_TRIGGER_MIN_WIN_RATE = float(os.getenv("AI_AUTO_TRIGGER_MIN_WIN_RATE", "35"))
AI_AUTO_TRIGGER_MIN_TRADES = int(os.getenv("AI_AUTO_TRIGGER_MIN_TRADES", "20"))
AI_REQUIRE_CONFIRM_ON_MANUAL = os.getenv("AI_REQUIRE_CONFIRM_ON_MANUAL", "true").lower() == "true"
AI_REQUIRE_CONFIRM_ON_AUTO = os.getenv("AI_REQUIRE_CONFIRM_ON_AUTO", "true").lower() == "true"
AI_MAX_SUGGESTION_COUNT = int(os.getenv("AI_MAX_SUGGESTION_COUNT", "5"))
AI_ALLOWED_PARAMS = [
    x.strip() for x in os.getenv(
        "AI_ALLOWED_PARAMS",
        "EMA_FAST,EMA_SLOW,ADX_PERIOD,VOLUME_MA_PERIOD,STRUCTURE_LOOKBACK,"
        "ENABLE_TAKE_PROFIT,TAKE_PROFIT_PCT,ENABLE_STOP_LOSS,STOP_LOSS_PCT,MAX_HOLD_SECONDS"
    ).split(",") if x.strip()
]
AI_BLOCKED_PARAMS = [
    x.strip() for x in os.getenv(
        "AI_BLOCKED_PARAMS",
        "SYMBOL,INTERVAL,NOTIONAL_USDT,TAKER_FEE_RATE,LEVERAGE,TRADE_START_TIME,"
        "TRADE_END_TIME,USE_PROXY,PROXY_URL,AI_BASE_URL,AI_API_KEY,AI_MODEL"
    ).split(",") if x.strip()
]

TRADING_MODE = os.getenv("TRADING_MODE", "SIMULATION").upper()
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()
BINANCE_BASE_URL = os.getenv("BINANCE_BASE_URL", "https://fapi.binance.com").strip()

PARAM_TYPE_MAP = {
    "EMA_FAST": int,
    "EMA_SLOW": int,
    "ADX_PERIOD": int,
    "VOLUME_MA_PERIOD": int,
    "STRUCTURE_LOOKBACK": int,
    "ENABLE_TAKE_PROFIT": bool,
    "TAKE_PROFIT_PCT": float,
    "ENABLE_STOP_LOSS": bool,
    "STOP_LOSS_PCT": float,
    "MAX_HOLD_SECONDS": int,
    "SYMBOL": str,
    "INTERVAL": str,
    "NOTIONAL_USDT": float,
    "TAKER_FEE_RATE": float,
    "LEVERAGE": int,
    "TRADE_START_TIME": str,
    "TRADE_END_TIME": str,
    "STATUS_INTERVAL_SECONDS": int,
    "CYCLE_TRIGGER_WINDOW_SECONDS": int,
    "OPEN_RETRY_INTERVAL_SECONDS": int,
    "USE_PROXY": bool,
    "PROXY_URL": str,
    "AI_ENABLED": bool,
    "AI_BASE_URL": str,
    "AI_API_KEY": str,
    "AI_MODEL": str,
    "AI_TIMEOUT_SECONDS": int,
    "AI_AUTO_OPTIMIZE_ENABLED": bool,
    "AI_AUTO_TRIGGER_MIN_WIN_RATE": float,
    "AI_AUTO_TRIGGER_MIN_TRADES": int,
    "AI_REQUIRE_CONFIRM_ON_MANUAL": bool,
    "AI_REQUIRE_CONFIRM_ON_AUTO": bool,
    "TRADING_MODE": str,
    "BINANCE_API_KEY": str,
    "BINANCE_API_SECRET": str,
    "BINANCE_BASE_URL": str
}


CONFIG_SCHEMA = {
    "trade_basic": {
        "label": "基础交易配置",
        "fields": [
            {"key": "SYMBOL", "label": "交易对", "type": "string", "runtime_mutable": False, "effective_timing": "restart_required"},
            {"key": "INTERVAL", "label": "交易周期", "type": "string", "runtime_mutable": False, "effective_timing": "restart_required"},
            {"key": "NOTIONAL_USDT", "label": "名义金额(USDT)", "type": "float", "runtime_mutable": False, "effective_timing": "restart_required"},
            {"key": "TAKER_FEE_RATE", "label": "手续费率", "type": "float", "runtime_mutable": False, "effective_timing": "restart_required"},
            {"key": "LEVERAGE", "label": "杠杆倍数", "type": "int", "runtime_mutable": False, "effective_timing": "restart_required"}
        ]
    },
    "time_window": {
        "label": "交易时间窗口",
        "fields": [
            {"key": "TRADE_START_TIME", "label": "开始时间", "type": "time", "runtime_mutable": False, "effective_timing": "restart_required"},
            {"key": "TRADE_END_TIME", "label": "结束时间", "type": "time", "runtime_mutable": False, "effective_timing": "restart_required"}
        ]
    },
    "strategy": {
        "label": "策略参数",
        "fields": [
            {"key": "EMA_FAST", "label": "EMA 快线周期", "type": "int", "runtime_mutable": True, "effective_timing": "next_cycle"},
            {"key": "EMA_SLOW", "label": "EMA 慢线周期", "type": "int", "runtime_mutable": True, "effective_timing": "next_cycle"},
            {"key": "ADX_PERIOD", "label": "ADX 周期", "type": "int", "runtime_mutable": True, "effective_timing": "next_cycle"},
            {"key": "VOLUME_MA_PERIOD", "label": "成交量均线周期", "type": "int", "runtime_mutable": True, "effective_timing": "next_cycle"},
            {"key": "STRUCTURE_LOOKBACK", "label": "K线结构观察根数", "type": "int", "runtime_mutable": True, "effective_timing": "next_cycle"}
        ]
    },
    "risk": {
        "label": "止盈止损",
        "fields": [
            {"key": "ENABLE_TAKE_PROFIT", "label": "启用止盈", "type": "bool", "runtime_mutable": True, "effective_timing": "immediate"},
            {"key": "TAKE_PROFIT_PCT", "label": "止盈比例", "type": "float", "runtime_mutable": True, "effective_timing": "immediate"},
            {"key": "ENABLE_STOP_LOSS", "label": "启用止损", "type": "bool", "runtime_mutable": True, "effective_timing": "immediate"},
            {"key": "STOP_LOSS_PCT", "label": "止损比例", "type": "float", "runtime_mutable": True, "effective_timing": "immediate"},
            {"key": "MAX_HOLD_SECONDS", "label": "最大持仓秒数", "type": "int", "runtime_mutable": True, "effective_timing": "immediate"}
        ]
    },
    "runtime": {
        "label": "运行时参数",
        "fields": [
            {"key": "STATUS_INTERVAL_SECONDS", "label": "状态输出间隔(秒)", "type": "int", "runtime_mutable": False, "effective_timing": "restart_required"},
            {"key": "CYCLE_TRIGGER_WINDOW_SECONDS", "label": "周期触发窗口(秒)", "type": "int", "runtime_mutable": False, "effective_timing": "restart_required"},
            {"key": "OPEN_RETRY_INTERVAL_SECONDS", "label": "开仓重试间隔(秒)", "type": "int", "runtime_mutable": False, "effective_timing": "restart_required"}
        ]
    },
    "proxy": {
        "label": "代理配置",
        "fields": [
            {"key": "USE_PROXY", "label": "启用代理", "type": "bool", "runtime_mutable": True, "effective_timing": "immediate"},
            {"key": "PROXY_URL", "label": "代理地址", "type": "string", "runtime_mutable": True, "effective_timing": "immediate"}
        ]
    },
    "trading_mode": {
        "label": "交易模式配置",
        "fields": [
            {"key": "TRADING_MODE", "label": "交易模式", "type": "string", "runtime_mutable": False, "effective_timing": "restart_required"},
            {"key": "BINANCE_API_KEY", "label": "币安 API Key", "type": "password", "runtime_mutable": False, "effective_timing": "restart_required"},
            {"key": "BINANCE_API_SECRET", "label": "币安 API Secret", "type": "password", "runtime_mutable": False, "effective_timing": "restart_required"},
            {"key": "BINANCE_BASE_URL", "label": "币安 API 地址", "type": "string", "runtime_mutable": False, "effective_timing": "restart_required"}
        ]
    },
    "ai": {
        "label": "AI 参数优化",
        "fields": [
            {"key": "AI_ENABLED", "label": "启用 AI 参数优化", "type": "bool", "runtime_mutable": True, "effective_timing": "immediate"},
            {"key": "AI_AUTO_OPTIMIZE_ENABLED", "label": "启用自动触发", "type": "bool", "runtime_mutable": True, "effective_timing": "immediate"},
            {"key": "AI_REQUIRE_CONFIRM_ON_MANUAL", "label": "手动分析需要确认", "type": "bool", "runtime_mutable": True, "effective_timing": "immediate"},
            {"key": "AI_REQUIRE_CONFIRM_ON_AUTO", "label": "自动分析需要确认", "type": "bool", "runtime_mutable": True, "effective_timing": "immediate"},
            {"key": "AI_BASE_URL", "label": "AI_BASE_URL", "type": "string", "runtime_mutable": True, "effective_timing": "immediate"},
            {"key": "AI_API_KEY", "label": "AI_API_KEY", "type": "password", "runtime_mutable": True, "effective_timing": "immediate"},
            {"key": "AI_MODEL", "label": "AI_MODEL", "type": "string", "runtime_mutable": True, "effective_timing": "immediate"},
            {"key": "AI_TIMEOUT_SECONDS", "label": "超时秒数", "type": "int", "runtime_mutable": True, "effective_timing": "immediate"},
            {"key": "AI_AUTO_TRIGGER_MIN_WIN_RATE", "label": "自动触发胜率阈值", "type": "float", "runtime_mutable": True, "effective_timing": "immediate"},
            {"key": "AI_AUTO_TRIGGER_MIN_TRADES", "label": "最少交易数", "type": "int", "runtime_mutable": True, "effective_timing": "immediate"}
        ]
    }
}


def get_proxies():
    """
    返回 requests 代理配置
    """
    if not USE_PROXY:
        return None

    if not PROXY_URL:
        return None

    return {
        "http": PROXY_URL,
        "https": PROXY_URL
    }


PROXIES = get_proxies()

binance_client = None
if BINANCE_API_KEY and BINANCE_API_SECRET and BINANCE_BASE_URL:
    binance_client = BinanceClient(
        api_key=BINANCE_API_KEY,
        api_secret=BINANCE_API_SECRET,
        base_url=BINANCE_BASE_URL,
        proxies=PROXIES
    )
    binance_adapter.bind(binance_client)


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_round(v, digits=8):
    """
    把数值安全转成 float 并保留小数
    """
    try:
        if pd.isna(v):
            return None
        return round(float(v), digits)
    except Exception:
        return None


def to_text_time(obj):
    """
    把 datetime / pandas Timestamp 转成字符串
    """
    if obj is None:
        return None

    try:
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        return str(obj)
    except Exception:
        return str(obj)


def ensure_parent_dir(file_path):
    parent_dir = os.path.dirname(file_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)


def log_to_file(file_name, text):
    line = f"[{now_str()}] {text}"
    print(line)
    ensure_parent_dir(file_name)
    with open(file_name, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_unified(level, category, text, legacy_file=None):
    """统一日志入口，写入主日志并附加 RUN_ID"""
    timestamp = now_str()
    line = f"[{timestamp}] [{level}] [{category}] [RUN={RUN_ID}] {text}"
    print(line)
    ensure_parent_dir(MAIN_LOG_FILE)
    with open(MAIN_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    if legacy_file:
        legacy_line = f"[{timestamp}] {text}"
        ensure_parent_dir(legacy_file)
        with open(legacy_file, "a", encoding="utf-8") as f:
            f.write(legacy_line + "\n")


def log_trade(text):
    log_unified("INFO", "trade", text, TRADE_LOG_FILE)


def log_position(text):
    log_unified("INFO", "position", text, POSITION_LOG_FILE)


def log_error(text):
    log_unified("ERROR", "error", text, ERROR_LOG_FILE)


def log_summary(text):
    log_unified("INFO", "summary", text, SUMMARY_LOG_FILE)


def write_json_file(file_path, data):
    """
    把字典写入 JSON 文件
    """
    ensure_parent_dir(file_path)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def append_jsonl(file_path, data):
    """
    追加写入 JSONL 文件（一行一个 JSON）
    """
    ensure_parent_dir(file_path)
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")


def set_strategy_state(**kwargs):
    with strategy_state_lock:
        strategy_state.update(kwargs)
    state_bus.publish("strategy_state_updated", get_strategy_state_snapshot())


def get_strategy_state_snapshot():
    with strategy_state_lock:
        return {
            "running": strategy_state["running"],
            "started_at": to_text_time(strategy_state["started_at"]),
            "stopped_at": to_text_time(strategy_state["stopped_at"]),
            "last_error": strategy_state["last_error"],
            "last_message": strategy_state["last_message"],
            "thread_alive": strategy_thread.is_alive() if strategy_thread is not None else False
        }


def set_pending_ai_suggestion(proposal):
    global pending_ai_suggestion
    with pending_ai_lock:
        pending_ai_suggestion = proposal


def get_pending_ai_suggestion_snapshot():
    with pending_ai_lock:
        if pending_ai_suggestion is None:
            return None

        return json.loads(json.dumps(pending_ai_suggestion, ensure_ascii=False, default=str))


def clear_pending_ai_suggestion():
    global pending_ai_suggestion
    with pending_ai_lock:
        pending_ai_suggestion = None


def wait_or_stop(seconds):
    if seconds <= 0:
        return strategy_stop_event.is_set()
    return strategy_stop_event.wait(seconds)


def tail_text_file(file_path, limit=80):
    if not os.path.exists(file_path):
        return []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = [line.rstrip("\n") for line in f.readlines()]
        return lines[-limit:]
    except Exception:
        return []


def tail_main_log_for_run(limit=60, scan_limit=400):
    lines = tail_text_file(MAIN_LOG_FILE, limit=scan_limit)
    run_marker = f"[RUN={RUN_ID}]"
    filtered = [line for line in lines if run_marker in line]
    return filtered[-limit:]


def normalize_request_params(params):
    if params is None:
        return {}
    return dict(params)


def build_market_cache_key(url, params=None):
    normalized = tuple(sorted((k, str(v)) for k, v in normalize_request_params(params).items()))
    return url, normalized


def get_market_cache_ttl(url):
    for suffix, ttl in MARKET_DATA_CACHE_TTLS.items():
        if url.endswith(suffix):
            return ttl
    return 0


def get_market_cache_label(url):
    for suffix, label in MARKET_DATA_CACHE_LABELS.items():
        if url.endswith(suffix):
            return label
    return "market"


def get_market_min_interval(url):
    for suffix, seconds in MARKET_DATA_RATE_LIMITS.items():
        if url.endswith(suffix):
            return seconds
    return 0


def get_cached_market_response(url, params=None):
    ttl = get_market_cache_ttl(url)
    if ttl <= 0:
        return None

    cache_key = build_market_cache_key(url, params)
    now_ts = time.time()
    with market_data_cache_lock:
        cached = market_data_cache.get(cache_key)
        if not cached:
            return None
        cached_at, data = cached
        if now_ts - cached_at > ttl:
            market_data_cache.pop(cache_key, None)
            return None
        return data


def set_cached_market_response(url, params, data):
    ttl = get_market_cache_ttl(url)
    if ttl <= 0:
        return

    cache_key = build_market_cache_key(url, params)
    with market_data_cache_lock:
        market_data_cache[cache_key] = (time.time(), data)


def wait_market_rate_limit(url):
    min_interval = get_market_min_interval(url)
    if min_interval <= 0:
        return

    with market_data_rate_limit_lock:
        now_ts = time.time()
        last_ts = market_data_last_request_at.get(url, 0)
        wait_seconds = min_interval - (now_ts - last_ts)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        market_data_last_request_at[url] = time.time()


def cleanup_runtime_snapshots(keep_latest=12):
    patterns = [
        ("run_config_snapshot_*.json", {os.path.basename(RUN_CONFIG_LATEST_FILE), os.path.basename(RUN_CONFIG_FILE)}),
        ("futures_summary_snapshot_*.json", {os.path.basename(SUMMARY_JSON_LATEST_FILE), os.path.basename(SUMMARY_JSON_FILE)})
    ]

    ensure_parent_dir(os.path.join(ARCHIVE_DIR, "placeholder.txt"))
    for pattern, protected in patterns:
        matched = sorted(glob.glob(os.path.join(RUNTIME_DIR, pattern)), key=os.path.getmtime, reverse=True)
        removable = [path for path in matched if os.path.basename(path) not in protected]
        for old_path in removable[keep_latest:]:
            target_path = os.path.join(ARCHIVE_DIR, os.path.basename(old_path))
            try:
                if os.path.exists(target_path):
                    os.remove(target_path)
                os.replace(old_path, target_path)
            except Exception:
                continue


def rotate_main_log_if_needed(max_bytes=5 * 1024 * 1024):
    if not os.path.exists(MAIN_LOG_FILE):
        return
    try:
        if os.path.getsize(MAIN_LOG_FILE) <= max_bytes:
            return
        rotated_name = f"app_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        os.replace(MAIN_LOG_FILE, os.path.join(ARCHIVE_DIR, rotated_name))
    except Exception:
        return


def parse_bool_value(raw_value):
    if isinstance(raw_value, bool):
        return raw_value

    text = str(raw_value).strip().lower()
    if text in ["true", "1", "yes", "y", "on"]:
        return True
    if text in ["false", "0", "no", "n", "off"]:
        return False

    raise ValueError(f"无法解析布尔值: {raw_value}")


def format_env_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        text = f"{value:.10f}".rstrip("0").rstrip(".")
        return text if text else "0"
    return str(value)


def coerce_param_value(param_name, raw_value):
    param_type = PARAM_TYPE_MAP.get(param_name)
    if param_type is None:
        return raw_value

    if param_type is bool:
        return parse_bool_value(raw_value)
    if param_type is int:
        return int(raw_value)
    if param_type is float:
        return float(raw_value)

    return raw_value


def read_text_file_if_exists(file_path, default_text=""):
    if not os.path.exists(file_path):
        return default_text

    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def read_json_file_if_exists(file_path, default_data=None):
    if default_data is None:
        default_data = {}

    if not os.path.exists(file_path):
        return default_data

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default_data


def read_recent_jsonl(file_path, limit=30):
    if not os.path.exists(file_path):
        return []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
    except Exception:
        return []

    rows = []
    for line in lines[-limit:]:
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def get_strategy_param_snapshot():
    return {
        "SYMBOL": SYMBOL,
        "INTERVAL": INTERVAL,
        "NOTIONAL_USDT": NOTIONAL_USDT,
        "TAKER_FEE_RATE": TAKER_FEE_RATE,
        "LEVERAGE": LEVERAGE,
        "TRADE_START_TIME": TRADE_START_TIME,
        "TRADE_END_TIME": TRADE_END_TIME,
        "STATUS_INTERVAL_SECONDS": STATUS_INTERVAL_SECONDS,
        "EMA_FAST": EMA_FAST,
        "EMA_SLOW": EMA_SLOW,
        "ADX_PERIOD": ADX_PERIOD,
        "VOLUME_MA_PERIOD": VOLUME_MA_PERIOD,
        "STRUCTURE_LOOKBACK": STRUCTURE_LOOKBACK,
        "ENABLE_TAKE_PROFIT": ENABLE_TAKE_PROFIT,
        "TAKE_PROFIT_PCT": TAKE_PROFIT_PCT,
        "ENABLE_STOP_LOSS": ENABLE_STOP_LOSS,
        "STOP_LOSS_PCT": STOP_LOSS_PCT,
        "MAX_HOLD_SECONDS": MAX_HOLD_SECONDS,
        "CYCLE_TRIGGER_WINDOW_SECONDS": CYCLE_TRIGGER_WINDOW_SECONDS,
        "OPEN_RETRY_INTERVAL_SECONDS": OPEN_RETRY_INTERVAL_SECONDS
    }


def get_ai_runtime_snapshot():
    return {
        "enabled": AI_ENABLED,
        "base_url": AI_BASE_URL,
        "model": AI_MODEL,
        "timeout_seconds": AI_TIMEOUT_SECONDS,
        "auto_optimize_enabled": AI_AUTO_OPTIMIZE_ENABLED,
        "auto_trigger_min_win_rate": AI_AUTO_TRIGGER_MIN_WIN_RATE,
        "auto_trigger_min_trades": AI_AUTO_TRIGGER_MIN_TRADES,
        "require_confirm_on_manual": AI_REQUIRE_CONFIRM_ON_MANUAL,
        "require_confirm_on_auto": AI_REQUIRE_CONFIRM_ON_AUTO,
        "allowed_params": AI_ALLOWED_PARAMS,
        "blocked_params": AI_BLOCKED_PARAMS,
        "skill_file": AI_SKILL_FILE
    }


def get_effective_summary_snapshot():
    current_snapshot = get_summary_snapshot()
    if current_snapshot["total_trades"] > 0:
        return current_snapshot

    latest_snapshot = read_json_file_if_exists(SUMMARY_JSON_LATEST_FILE, {})
    if isinstance(latest_snapshot, dict) and latest_snapshot.get("total_trades", 0) > 0:
        return latest_snapshot

    return current_snapshot


def get_ai_chat_completions_url():
    if not AI_BASE_URL:
        return ""

    base = AI_BASE_URL.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def load_ai_skill_text():
    skill_text = read_text_file_if_exists(AI_SKILL_FILE, "").strip()
    if skill_text:
        return skill_text

    return (
        "你是量化参数修补 AI。你的职责是根据历史交易结果、当前参数和策略逻辑，"
        "找出会降低胜率的参数组合，并仅在允许修改的参数内提出尽量少且明确的修补建议。"
    )


def normalize_ai_message_content(content):
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text_value = item.get("text") or item.get("content") or ""
                if text_value:
                    parts.append(str(text_value))
            else:
                parts.append(str(item))
        return "\n".join(parts)

    return str(content)


def extract_json_from_text(text):
    cleaned = text.strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()

    try:
        return json.loads(cleaned)
    except Exception:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        return json.loads(cleaned[start:end + 1])

    raise ValueError("AI 返回内容中没有可解析的 JSON")


def build_ai_analysis_payload(trigger_mode):
    recent_trades = read_recent_jsonl(TRADE_DETAIL_JSONL_FILE, limit=30)
    latest_summary = get_effective_summary_snapshot()

    return {
        "trigger_mode": trigger_mode,
        "run_id": RUN_ID,
        "analysis_time": now_str(),
        "summary": latest_summary,
        "current_params": get_strategy_param_snapshot(),
        "ai_config": get_ai_runtime_snapshot(),
        "recent_trades": recent_trades,
        "rules": {
            "max_suggestion_count": AI_MAX_SUGGESTION_COUNT,
            "only_return_json": True
        }
    }


def request_ai_parameter_suggestions(trigger_mode):
    if not AI_ENABLED:
        raise ValueError("AI_ENABLED 未开启")
    if not AI_BASE_URL:
        raise ValueError("AI_BASE_URL 未配置")
    if not AI_API_KEY:
        raise ValueError("AI_API_KEY 未配置")
    if not AI_MODEL:
        raise ValueError("AI_MODEL 未配置")

    skill_text = load_ai_skill_text()
    analysis_payload = build_ai_analysis_payload(trigger_mode)
    system_prompt = (
        "你是一个量化交易参数修补助手。"
        "你只能分析参数是否合理，不允许修改 blocked_params 里的参数。"
        "如果数据不足，请返回 should_modify=false。"
        "输出必须是 JSON 对象，不要输出 markdown。"
        "JSON 格式固定为："
        "{\"summary\":\"简短结论\",\"should_modify\":true/false,"
        "\"suggestions\":[{\"param\":\"参数名\",\"value\":新值,"
        "\"reason\":\"修改理由\",\"expected_effect\":\"预期效果\",\"confidence\":0到1之间数字\"}],"
        "\"risk_notes\":[\"风险1\",\"风险2\"]}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": "附加 skill:\n" + skill_text},
        {"role": "user", "content": json.dumps(analysis_payload, ensure_ascii=False, indent=2)}
    ]

    body = {
        "model": AI_MODEL,
        "messages": messages,
        "temperature": 0.2
    }

    response = requests.post(
        get_ai_chat_completions_url(),
        headers={
            "Authorization": f"Bearer {AI_API_KEY}",
            "Content-Type": "application/json"
        },
        json=body,
        timeout=AI_TIMEOUT_SECONDS,
        proxies=PROXIES
    )
    response.raise_for_status()

    response_text = response.text
    try:
        data = response.json()
    except Exception as e:
        log_error(f"AI API 返回非 JSON 响应 | status={response.status_code} | text={response_text[:500]}")
        raise ValueError(f"AI API 返回格式错误: {e}")

    content = normalize_ai_message_content(data["choices"][0]["message"]["content"])
    parsed = extract_json_from_text(content)

    return {
        "request_payload": analysis_payload,
        "response_text": content,
        "parsed": parsed
    }


def validate_param_ranges(candidate_params):
    if candidate_params["EMA_FAST"] <= 0:
        raise ValueError("EMA_FAST 必须大于 0")
    if candidate_params["EMA_SLOW"] <= 0:
        raise ValueError("EMA_SLOW 必须大于 0")
    if candidate_params["EMA_FAST"] >= candidate_params["EMA_SLOW"]:
        raise ValueError("EMA_FAST 必须小于 EMA_SLOW")
    if candidate_params["ADX_PERIOD"] <= 1:
        raise ValueError("ADX_PERIOD 必须大于 1")
    if candidate_params["VOLUME_MA_PERIOD"] <= 1:
        raise ValueError("VOLUME_MA_PERIOD 必须大于 1")
    if candidate_params["STRUCTURE_LOOKBACK"] <= 1:
        raise ValueError("STRUCTURE_LOOKBACK 必须大于 1")
    if candidate_params["MAX_HOLD_SECONDS"] <= 0:
        raise ValueError("MAX_HOLD_SECONDS 必须大于 0")
    if candidate_params.get("ENABLE_TAKE_PROFIT", False):
        if candidate_params["TAKE_PROFIT_PCT"] <= 0:
            raise ValueError("TAKE_PROFIT_PCT 必须大于 0")
    if candidate_params.get("ENABLE_STOP_LOSS", False):
        if candidate_params["STOP_LOSS_PCT"] <= 0:
            raise ValueError("STOP_LOSS_PCT 必须大于 0")


def validate_ai_suggestions(ai_result):
    parsed = ai_result["parsed"]
    if not isinstance(parsed, dict):
        raise ValueError("AI 返回格式不是对象")

    summary = str(parsed.get("summary", "")).strip()
    should_modify = bool(parsed.get("should_modify", False))
    suggestions = parsed.get("suggestions", [])
    risk_notes = parsed.get("risk_notes", [])

    if not should_modify:
        return {
            "summary": summary or "AI 判断当前无需修改参数",
            "should_modify": False,
            "updates": {},
            "suggestions": [],
            "risk_notes": risk_notes if isinstance(risk_notes, list) else []
        }

    if not isinstance(suggestions, list):
        raise ValueError("AI suggestions 字段必须是数组")

    updates = {}
    normalized_suggestions = []

    for item in suggestions[:AI_MAX_SUGGESTION_COUNT]:
        if not isinstance(item, dict):
            continue

        param_name = str(item.get("param", "")).strip().upper()
        if not param_name:
            continue
        if param_name in AI_BLOCKED_PARAMS:
            continue
        if param_name not in AI_ALLOWED_PARAMS:
            continue
        if param_name not in PARAM_TYPE_MAP:
            continue

        value = coerce_param_value(param_name, item.get("value"))
        updates[param_name] = value
        normalized_suggestions.append({
            "param": param_name,
            "value": value,
            "reason": str(item.get("reason", "")).strip(),
            "expected_effect": str(item.get("expected_effect", "")).strip(),
            "confidence": safe_round(item.get("confidence", 0), 4)
        })

    if not updates:
        return {
            "summary": summary or "AI 没有给出可应用的参数建议",
            "should_modify": False,
            "updates": {},
            "suggestions": [],
            "risk_notes": risk_notes if isinstance(risk_notes, list) else []
        }

    candidate_params = get_strategy_param_snapshot()
    candidate_params.update(updates)
    validate_param_ranges(candidate_params)

    return {
        "summary": summary or "AI 给出了参数修补建议",
        "should_modify": True,
        "updates": updates,
        "suggestions": normalized_suggestions,
        "risk_notes": risk_notes if isinstance(risk_notes, list) else []
    }


def classify_param_effects(updates):
    immediate = []
    next_cycle = []
    next_position = []

    for param in updates.keys():
        if param in ["ENABLE_TAKE_PROFIT", "TAKE_PROFIT_PCT", "ENABLE_STOP_LOSS", "STOP_LOSS_PCT", "MAX_HOLD_SECONDS",
                     "USE_PROXY", "PROXY_URL", "AI_ENABLED", "AI_AUTO_OPTIMIZE_ENABLED", "AI_REQUIRE_CONFIRM_ON_MANUAL",
                     "AI_REQUIRE_CONFIRM_ON_AUTO", "AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "AI_TIMEOUT_SECONDS",
                     "AI_AUTO_TRIGGER_MIN_WIN_RATE", "AI_AUTO_TRIGGER_MIN_TRADES"]:
            immediate.append(param)
        elif param in ["EMA_FAST", "EMA_SLOW", "ADX_PERIOD", "VOLUME_MA_PERIOD", "STRUCTURE_LOOKBACK"]:
            next_cycle.append(param)
        else:
            next_position.append(param)

    return {"immediate": immediate, "next_cycle": next_cycle, "next_position": next_position}


def build_runtime_update_status(source, updates, proposal_id=None):
    effects = classify_param_effects(updates)
    has_position = position is not None

    note_parts = []
    if effects["immediate"]:
        note_parts.append("止盈止损类参数已写入当前进程，后续判断立即使用新值")
    if effects["next_cycle"]:
        note_parts.append("指标类参数将在下一根K线收盘后的信号计算中体现")
    if has_position and (effects["next_cycle"] or effects["next_position"]):
        note_parts.append("当前持仓的开仓方向不会被回溯修改")

    return {
        "updated_at": now_str(),
        "source": source,
        "proposal_id": proposal_id,
        "updated_params": list(updates.keys()),
        "effective_immediate": effects["immediate"],
        "effective_next_cycle": effects["next_cycle"],
        "effective_next_position": effects["next_position"],
        "has_open_position": has_position,
        "note": "；".join(note_parts) if note_parts else "参数已更新到当前进程"
    }


def save_runtime_status(status):
    try:
        write_json_file(RUNTIME_STATUS_FILE, status)
        state_bus.publish("runtime_status_updated", status)
    except Exception:
        pass


def get_runtime_status():
    return read_json_file_if_exists(RUNTIME_STATUS_FILE, {})


def apply_runtime_updates(updates):
    global EMA_FAST, EMA_SLOW, ADX_PERIOD, VOLUME_MA_PERIOD, STRUCTURE_LOOKBACK
    global ENABLE_TAKE_PROFIT, TAKE_PROFIT_PCT, ENABLE_STOP_LOSS, STOP_LOSS_PCT, MAX_HOLD_SECONDS

    for key, value in updates.items():
        globals()[key] = value


def apply_updates_to_env_file(updates):
    if not updates:
        return

    if os.path.exists(ENV_FILE_PATH):
        with open(ENV_FILE_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
    else:
        lines = []

    found_keys = set()
    new_lines = []

    for line in lines:
        stripped = line.strip()
        replaced = False

        for key, value in updates.items():
            if stripped.startswith(f"{key}="):
                new_lines.append(f"{key}={format_env_value(value)}\n")
                found_keys.add(key)
                replaced = True
                break

        if not replaced:
            new_lines.append(line)

    for key, value in updates.items():
        if key not in found_keys:
            if new_lines and new_lines[-1].strip():
                new_lines.append("\n")
            new_lines.append(f"{key}={format_env_value(value)}\n")

    with open(ENV_FILE_PATH, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


def apply_ai_runtime_config_updates(updates):
    global AI_ENABLED, AI_BASE_URL, AI_API_KEY, AI_MODEL, AI_TIMEOUT_SECONDS
    global AI_AUTO_OPTIMIZE_ENABLED, AI_AUTO_TRIGGER_MIN_WIN_RATE, AI_AUTO_TRIGGER_MIN_TRADES
    global AI_REQUIRE_CONFIRM_ON_MANUAL, AI_REQUIRE_CONFIRM_ON_AUTO

    type_map = {
        "AI_ENABLED": bool,
        "AI_BASE_URL": str,
        "AI_API_KEY": str,
        "AI_MODEL": str,
        "AI_TIMEOUT_SECONDS": int,
        "AI_AUTO_OPTIMIZE_ENABLED": bool,
        "AI_AUTO_TRIGGER_MIN_WIN_RATE": float,
        "AI_AUTO_TRIGGER_MIN_TRADES": int,
        "AI_REQUIRE_CONFIRM_ON_MANUAL": bool,
        "AI_REQUIRE_CONFIRM_ON_AUTO": bool
    }

    for key, raw_value in updates.items():
        if key not in type_map:
            continue

        expected_type = type_map[key]
        if expected_type is bool:
            value = parse_bool_value(raw_value)
        elif expected_type is int:
            value = int(raw_value)
        elif expected_type is float:
            value = float(raw_value)
        else:
            value = str(raw_value).strip()

        globals()[key] = value

    apply_updates_to_env_file(updates)
    save_run_config_snapshot()
    runtime_status = build_runtime_update_status(source="ai_config_save", updates=updates)
    runtime_status["persisted_to_env"] = True
    runtime_status["applied_in_memory"] = True
    save_runtime_status(runtime_status)
    log_trade("AI 配置已写入当前进程并同步到 .env，后续 AI 请求会按新配置执行")


def get_config_schema_snapshot():
    return CONFIG_SCHEMA


def get_current_config_snapshot():
    current = get_strategy_param_snapshot()
    current.update({
        "USE_PROXY": USE_PROXY,
        "PROXY_URL": PROXY_URL,
        "AI_ENABLED": AI_ENABLED,
        "AI_BASE_URL": AI_BASE_URL,
        "AI_API_KEY": "",
        "AI_MODEL": AI_MODEL,
        "AI_TIMEOUT_SECONDS": AI_TIMEOUT_SECONDS,
        "AI_AUTO_OPTIMIZE_ENABLED": AI_AUTO_OPTIMIZE_ENABLED,
        "AI_AUTO_TRIGGER_MIN_WIN_RATE": AI_AUTO_TRIGGER_MIN_WIN_RATE,
        "AI_AUTO_TRIGGER_MIN_TRADES": AI_AUTO_TRIGGER_MIN_TRADES,
        "AI_REQUIRE_CONFIRM_ON_MANUAL": AI_REQUIRE_CONFIRM_ON_MANUAL,
        "AI_REQUIRE_CONFIRM_ON_AUTO": AI_REQUIRE_CONFIRM_ON_AUTO
    })
    return current


def apply_general_config_updates(updates):
    global USE_PROXY, PROXY_URL, PROXIES, AI_ENABLED, AI_BASE_URL, AI_API_KEY, AI_MODEL, AI_TIMEOUT_SECONDS
    global AI_AUTO_OPTIMIZE_ENABLED, AI_AUTO_TRIGGER_MIN_WIN_RATE, AI_AUTO_TRIGGER_MIN_TRADES
    global AI_REQUIRE_CONFIRM_ON_MANUAL, AI_REQUIRE_CONFIRM_ON_AUTO
    global SYMBOL, INTERVAL, NOTIONAL_USDT, TAKER_FEE_RATE, LEVERAGE
    global TRADE_START_TIME, TRADE_END_TIME, STATUS_INTERVAL_SECONDS
    global CYCLE_TRIGGER_WINDOW_SECONDS, OPEN_RETRY_INTERVAL_SECONDS, INTERVAL_SECONDS

    runtime_updates = {}
    env_updates = {}

    for key, raw_value in updates.items():
        if key not in PARAM_TYPE_MAP:
            continue
        value = coerce_param_value(key, raw_value)
        if key == "AI_API_KEY" and str(value).strip() == "":
            continue
        runtime_updates[key] = value
        env_updates[key] = value

    candidate_params = get_strategy_param_snapshot()
    candidate_params.update(runtime_updates)
    validate_param_ranges(candidate_params)

    for key, value in runtime_updates.items():
        if key in ["EMA_FAST", "EMA_SLOW", "ADX_PERIOD", "VOLUME_MA_PERIOD", "STRUCTURE_LOOKBACK",
                   "ENABLE_TAKE_PROFIT", "TAKE_PROFIT_PCT", "ENABLE_STOP_LOSS", "STOP_LOSS_PCT", "MAX_HOLD_SECONDS"]:
            globals()[key] = value
        elif key in ["USE_PROXY", "PROXY_URL"]:
            globals()[key] = value
        elif key in ["AI_ENABLED", "AI_BASE_URL", "AI_MODEL", "AI_TIMEOUT_SECONDS", "AI_AUTO_OPTIMIZE_ENABLED",
                     "AI_AUTO_TRIGGER_MIN_WIN_RATE", "AI_AUTO_TRIGGER_MIN_TRADES", "AI_REQUIRE_CONFIRM_ON_MANUAL",
                     "AI_REQUIRE_CONFIRM_ON_AUTO"]:
            globals()[key] = value
        elif key == "AI_API_KEY" and value:
            globals()[key] = value
        elif key in ["SYMBOL", "INTERVAL", "NOTIONAL_USDT", "TAKER_FEE_RATE", "LEVERAGE",
                     "TRADE_START_TIME", "TRADE_END_TIME", "STATUS_INTERVAL_SECONDS",
                     "CYCLE_TRIGGER_WINDOW_SECONDS", "OPEN_RETRY_INTERVAL_SECONDS"]:
            globals()[key] = value
            if key == "INTERVAL":
                INTERVAL_SECONDS = interval_to_seconds(value)

    if "USE_PROXY" in runtime_updates or "PROXY_URL" in runtime_updates:
        PROXIES = get_proxies()

    apply_updates_to_env_file(env_updates)
    save_run_config_snapshot()

    runtime_status = build_runtime_update_status(source="general_config_save", updates=runtime_updates)
    runtime_status["persisted_to_env"] = True
    runtime_status["applied_in_memory"] = True
    save_runtime_status(runtime_status)
    return runtime_status


def get_position_snapshot():
    if position is None:
        return None

    return {
        "trade_id": position.get("trade_id"),
        "symbol": position.get("symbol"),
        "side": position.get("side"),
        "entry_time": to_text_time(position.get("entry_time")),
        "entry_price": safe_round(position.get("entry_price")),
        "qty": safe_round(position.get("qty")),
        "notional_usdt": safe_round(position.get("notional_usdt")),
        "entry_notional": safe_round(position.get("entry_notional")),
        "entry_fee": safe_round(position.get("entry_fee")),
        "status": position.get("status"),
        "signal_kline_open_time": to_text_time(position.get("signal_kline_open_time"))
    }


def get_pending_open_snapshot():
    if pending_open_decision is None:
        return None

    return {
        "decision": pending_open_decision,
        "signal_kline_open_time": to_text_time(pending_signal_kline_open_time)
    }


def get_live_account_snapshot():
    """获取实盘账户快照"""
    if not binance_adapter.available():
        return None

    try:
        account = binance_adapter.get_account()
        positions = binance_adapter.get_position_risk()
        account_cache_ttl = getattr(binance_adapter, "get_cache_ttl", lambda *args, **kwargs: 0)("GET", "/fapi/v2/account")
        position_cache_ttl = getattr(binance_adapter, "get_cache_ttl", lambda *args, **kwargs: 0)("GET", "/fapi/v3/positionRisk")

        return {
            "total_wallet_balance": account.get('totalWalletBalance'),
            "total_unrealized_profit": account.get('totalUnrealizedProfit'),
            "available_balance": account.get('availableBalance'),
            "positions": [
                {
                    "symbol": p['symbol'],
                    "position_amt": p['positionAmt'],
                    "entry_price": p['entryPrice'],
                    "unrealized_profit": p['unRealizedProfit']
                }
                for p in positions if float(p['positionAmt']) != 0
            ],
            "cached_seconds": max(account_cache_ttl, position_cache_ttl)
        }
    except Exception as e:
        log_error(f"查询账户信息失败: {e}")
        return None


def get_symbol_exchange_info(symbol):
    """获取交易对规则信息"""
    try:
        if binance_adapter.available():
            data = binance_adapter.get_exchange_info()
        else:
            url = f"{BASE_URL}/fapi/v1/exchangeInfo"
            data = http_get(url)

        for item in data.get("symbols", []):
            if item.get("symbol") == symbol:
                return item
    except Exception as e:
        log_error(f"获取交易对规则失败: {e}")

    return None


def normalize_order_qty(symbol, qty):
    """按交易规则规范化下单数量"""
    info = get_symbol_exchange_info(symbol)
    if not info:
        return qty

    quantity_precision = int(info.get("quantityPrecision", 8))
    filters = info.get("filters", [])
    step_size = None
    min_qty = None

    for item in filters:
        if item.get("filterType") == "LOT_SIZE":
            step_size = float(item.get("stepSize", "0"))
            min_qty = float(item.get("minQty", "0"))
            break

    normalized = round(qty, quantity_precision)

    if step_size and step_size > 0:
        normalized = int(normalized / step_size) * step_size
        normalized = round(normalized, quantity_precision)

    if min_qty and normalized < min_qty:
        raise ValueError(f"下单数量 {normalized} 小于最小下单数量 {min_qty}")

    return normalized


def _decimal_places_from_step(step_size_text):
    """根据 stepSize 文本推导小数位数"""
    if not step_size_text or "." not in step_size_text:
        return 0
    stripped = step_size_text.rstrip("0")
    if stripped.endswith("."):
        return 0
    return len(stripped.split(".")[1])


def format_order_qty(symbol, qty):
    """按 Binance 交易规则格式化数量字符串，避免浮点误差"""
    info = get_symbol_exchange_info(symbol)
    if not info:
        return str(qty)

    quantity_precision = int(info.get("quantityPrecision", 8))
    filters = info.get("filters", [])
    step_size_text = None
    min_qty_decimal = None

    for item in filters:
        if item.get("filterType") == "LOT_SIZE":
            step_size_text = item.get("stepSize", "0")
            min_qty_text = item.get("minQty", "0")
            min_qty_decimal = Decimal(min_qty_text)
            break

    qty_decimal = Decimal(str(qty))

    if step_size_text and Decimal(step_size_text) > 0:
        step_decimal = Decimal(step_size_text)
        qty_decimal = (qty_decimal / step_decimal).to_integral_value(rounding=ROUND_DOWN) * step_decimal
        decimal_places = _decimal_places_from_step(step_size_text)
        quantizer = Decimal("1") if decimal_places <= 0 else Decimal(f"1e-{decimal_places}")
        qty_decimal = qty_decimal.quantize(quantizer, rounding=ROUND_DOWN)
    else:
        quantizer = Decimal("1") if quantity_precision <= 0 else Decimal(f"1e-{quantity_precision}")
        qty_decimal = qty_decimal.quantize(quantizer, rounding=ROUND_DOWN)

    if min_qty_decimal is not None and qty_decimal < min_qty_decimal:
        raise ValueError(f"下单数量 {qty_decimal} 小于最小下单数量 {min_qty_decimal}")

    qty_text = format(qty_decimal, "f")
    if "." in qty_text:
        qty_text = qty_text.rstrip("0").rstrip(".")
    return qty_text or "0"


def get_order_trades(symbol, order_id, max_attempts=5, retry_delay_seconds=0.2):
    """查询订单对应的真实成交明细，短轮询等待明细落账后再返回"""
    if not binance_adapter.available():
        return []

    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            trades = binance_adapter.get_user_trades(symbol=symbol, order_id=order_id)
            if trades:
                if attempt > 1:
                    log_trade(
                        f"成交明细延迟到账，重试后获取成功 | symbol={symbol} | order_id={order_id} | "
                        f"attempt={attempt}/{max_attempts} | trade_count={len(trades)}"
                    )
                return trades
        except Exception as e:
            last_error = e

        if attempt < max_attempts:
            time.sleep(retry_delay_seconds)

    if last_error is not None:
        log_error(
            f"查询订单成交明细失败 | symbol={symbol} | order_id={order_id} | "
            f"attempts={max_attempts} | error={last_error}"
        )
    else:
        log_error(
            f"订单已成交但未在轮询窗口内查到成交明细 | symbol={symbol} | order_id={order_id} | "
            f"attempts={max_attempts} | retry_delay_seconds={retry_delay_seconds}"
        )

    return []


def sanitize_position_for_storage(raw_position):
    """把持仓对象转换为可 JSON 持久化的结构"""
    if raw_position is None:
        return None

    return json.loads(json.dumps(raw_position, ensure_ascii=False, default=str))


def persist_live_position_snapshot():
    """把当前实盘持仓快照写入文件，供重启恢复使用"""
    if TRADING_MODE != "LIVE":
        return

    try:
        if position is None:
            if os.path.exists(LIVE_POSITION_SNAPSHOT_FILE):
                os.remove(LIVE_POSITION_SNAPSHOT_FILE)
            return

        snapshot = sanitize_position_for_storage(position)
        write_json_file(LIVE_POSITION_SNAPSHOT_FILE, snapshot)
    except Exception as e:
        log_error(f"写入实盘持仓快照失败: {e}")


def clear_live_position_snapshot():
    """删除本地实盘持仓快照"""
    try:
        if os.path.exists(LIVE_POSITION_SNAPSHOT_FILE):
            os.remove(LIVE_POSITION_SNAPSHOT_FILE)
    except Exception as e:
        log_error(f"删除实盘持仓快照失败: {e}")


def rebuild_live_position_from_exchange(exchange_position):
    """根据交易所真实持仓重建本地持仓对象"""
    global position

    if not exchange_position:
        return False

    try:
        position_amt = float(exchange_position.get("positionAmt", 0) or 0)
        if position_amt == 0:
            return False

        entry_price = float(exchange_position.get("entryPrice", 0) or 0)
        if entry_price <= 0:
            raise ValueError(f"真实持仓入场价异常: {exchange_position}")

        qty = abs(position_amt)
        side = "LONG" if position_amt > 0 else "SHORT"
        entry_notional = abs(position_amt * entry_price)
        unrealized_profit = float(exchange_position.get("unRealizedProfit", 0) or 0)
        leverage_value = exchange_position.get("leverage")

        local_snapshot = read_json_file_if_exists(LIVE_POSITION_SNAPSHOT_FILE, {})
        local_symbol = local_snapshot.get("symbol") if isinstance(local_snapshot, dict) else None
        local_side = local_snapshot.get("side") if isinstance(local_snapshot, dict) else None
        local_trade_id = local_snapshot.get("trade_id") if isinstance(local_snapshot, dict) else None
        local_decision_snapshot = local_snapshot.get("decision_snapshot") if isinstance(local_snapshot, dict) else None
        local_signal_time = local_snapshot.get("signal_kline_open_time") if isinstance(local_snapshot, dict) else None
        local_entry_order_id = local_snapshot.get("entry_order_id") if isinstance(local_snapshot, dict) else None
        local_entry_fee = local_snapshot.get("entry_fee") if isinstance(local_snapshot, dict) else None
        local_entry_fee_source = local_snapshot.get("entry_fee_source") if isinstance(local_snapshot, dict) else None
        local_entry_commission_asset = local_snapshot.get("entry_commission_asset") if isinstance(local_snapshot, dict) else None
        local_entry_trade_details = local_snapshot.get("entry_trade_details") if isinstance(local_snapshot, dict) else None
        local_entry_raw_order = local_snapshot.get("entry_raw_order") if isinstance(local_snapshot, dict) else None
        local_entry_order_status = local_snapshot.get("entry_order_status") if isinstance(local_snapshot, dict) else None
        local_entry_time_text = local_snapshot.get("entry_time") if isinstance(local_snapshot, dict) else None

        try:
            restored_entry_time = datetime.fromisoformat(local_entry_time_text) if local_entry_time_text else datetime.now()
        except Exception:
            restored_entry_time = datetime.now()

        if local_symbol != SYMBOL or local_side != side:
            local_trade_id = None
            local_decision_snapshot = None
            local_signal_time = None
            local_entry_order_id = None
            local_entry_fee = None
            local_entry_fee_source = None
            local_entry_commission_asset = None
            local_entry_trade_details = None
            local_entry_raw_order = None
            local_entry_order_status = None

        position = {
            "run_id": RUN_ID,
            "trade_id": local_trade_id or f"{SYMBOL}_{side}_restored_{int(time.time())}",
            "symbol": SYMBOL,
            "side": side,
            "entry_time": restored_entry_time,
            "entry_price": entry_price,
            "qty": qty,
            "notional_usdt": entry_notional,
            "entry_notional": entry_notional,
            "entry_fee": float(local_entry_fee or 0),
            "entry_fee_source": local_entry_fee_source or "restored_snapshot_or_unknown",
            "entry_commission_asset": local_entry_commission_asset or "USDT",
            "decision_snapshot": local_decision_snapshot,
            "signal_kline_open_time": local_signal_time,
            "status": "OPEN",
            "trading_mode": "LIVE",
            "entry_order_id": local_entry_order_id,
            "entry_order_status": local_entry_order_status or "RESTORED_FROM_EXCHANGE",
            "entry_raw_order": local_entry_raw_order,
            "entry_trade_details": local_entry_trade_details or [],
            "restored_from_exchange": True,
            "exchange_position_amt": position_amt,
            "exchange_break_even_price": float(exchange_position.get("breakEvenPrice", 0) or 0),
            "exchange_unrealized_profit": unrealized_profit,
            "leverage": int(leverage_value) if leverage_value not in [None, ""] else LEVERAGE
        }

        persist_live_position_snapshot()
        log_trade(
            f"[实盘恢复] 已从交易所重建持仓 | symbol={SYMBOL} | side={side} | "
            f"qty={qty:.8f} | entry_price={entry_price:.8f} | unrealized={unrealized_profit:.8f}"
        )
        return True
    except Exception as e:
        log_error(f"从交易所重建持仓失败: {e}")
        return False


def restore_live_position_if_needed():
    """程序启动时尝试从交易所真实持仓恢复本地状态"""
    global position

    if TRADING_MODE != "LIVE":
        clear_live_position_snapshot()
        return False

    if not binance_adapter.available():
        log_error("实盘恢复失败：币安客户端未初始化")
        return False

    if position is not None:
        return True

    try:
        positions = binance_adapter.get_position_risk(SYMBOL)
        active_positions = [p for p in positions if abs(float(p.get("positionAmt", 0) or 0)) > 0]

        if not active_positions:
            clear_live_position_snapshot()
            log_trade(f"[实盘恢复] 未检测到 {SYMBOL} 的真实持仓")
            return False

        restored = rebuild_live_position_from_exchange(active_positions[0])
        if len(active_positions) > 1:
            log_error(f"检测到多个真实持仓记录，当前仅恢复第一条 | count={len(active_positions)}")
        return restored
    except Exception as e:
        log_error(f"恢复真实持仓失败: {e}")
        return False


def aggregate_order_trade_details(trades):
    """聚合订单成交明细，得到真实手续费和已实现盈亏"""
    total_commission = 0.0
    total_realized_pnl = 0.0
    commission_asset = None

    for trade in trades or []:
        commission = float(trade.get("commission", 0) or 0)
        realized_pnl = float(trade.get("realizedPnl", 0) or 0)
        total_commission += abs(commission)
        total_realized_pnl += realized_pnl
        if not commission_asset:
            commission_asset = trade.get("commissionAsset")

    return {
        "commission": total_commission,
        "realized_pnl": total_realized_pnl,
        "commission_asset": commission_asset or "USDT"
    }


def get_dashboard_snapshot():
    return build_dashboard_snapshot(man_module=__import__(__name__))


def prompt_user_confirmation(prompt_text, default_no=True):
    try:
        answer = input(prompt_text).strip().lower()
    except EOFError:
        return not default_no

    if answer in ["y", "yes", "1"]:
        return True
    if answer in ["n", "no", "0"]:
        return False

    return not default_no


def save_ai_suggestion_record(trigger_mode, validated_result, ai_result, applied, approved, proposal_id=None):
    record = {
        "time": now_str(),
        "proposal_id": proposal_id,
        "trigger_mode": trigger_mode,
        "applied": applied,
        "approved": approved,
        "summary": validated_result.get("summary"),
        "updates": {
            key: format_env_value(value)
            for key, value in validated_result.get("updates", {}).items()
        },
        "suggestions": validated_result.get("suggestions", []),
        "risk_notes": validated_result.get("risk_notes", []),
        "raw_response_text": ai_result.get("response_text", "")
    }
    append_jsonl(AI_SUGGESTION_JSONL_FILE, record)


def build_ai_proposal(trigger_mode, validated_result, ai_result):
    suggestions = validated_result.get("suggestions", [])
    editable_suggestions = []

    for item in suggestions:
        editable_suggestions.append({
            "param": item["param"],
            "original_value": item["value"],
            "current_value": item["value"],
            "enabled": True,
            "reason": item.get("reason", ""),
            "expected_effect": item.get("expected_effect", ""),
            "confidence": item.get("confidence", 0),
            "edited_by_user": False
        })

    return {
        "proposal_id": f"{RUN_ID}_{trigger_mode}_{int(time.time())}",
        "trigger_mode": trigger_mode,
        "created_at": now_str(),
        "status": "pending",
        "validated_result": validated_result,
        "editable_suggestions": editable_suggestions,
        "ai_result": {
            "response_text": ai_result.get("response_text", ""),
            "request_payload": ai_result.get("request_payload", {})
        }
    }


def update_pending_ai_proposal_edits(payload):
    proposal = get_pending_ai_suggestion_snapshot()
    if proposal is None:
        raise ValueError("当前没有待编辑的 AI 建议")

    edits = payload.get("suggestions", [])
    if not isinstance(edits, list):
        raise ValueError("suggestions 必须是数组")

    editable = proposal.get("editable_suggestions", [])
    editable_map = {item.get("param"): item for item in editable}

    for edit_item in edits:
        if not isinstance(edit_item, dict):
            continue
        param_name = str(edit_item.get("param", "")).strip().upper()
        if not param_name or param_name not in editable_map:
            continue

        target = editable_map[param_name]
        if "enabled" in edit_item:
            target["enabled"] = bool(edit_item.get("enabled"))
        if "current_value" in edit_item:
            target["current_value"] = coerce_param_value(param_name, edit_item.get("current_value"))
            target["edited_by_user"] = target["current_value"] != target.get("original_value")

    validate_edited_ai_proposal(proposal)
    set_pending_ai_suggestion(proposal)
    return proposal


def build_updates_from_editable_suggestions(proposal):
    updates = {}
    for item in proposal.get("editable_suggestions", []):
        if not item.get("enabled", True):
            continue
        param_name = item.get("param")
        if not param_name:
            continue
        updates[param_name] = coerce_param_value(param_name, item.get("current_value"))
    return updates


def validate_edited_ai_proposal(proposal):
    updates = build_updates_from_editable_suggestions(proposal)
    if not updates:
        raise ValueError("至少需要保留一条启用中的 AI 建议")

    for param_name in updates.keys():
        if param_name in AI_BLOCKED_PARAMS:
            raise ValueError(f"AI 建议中包含禁止修改的参数: {param_name}")
        if param_name not in AI_ALLOWED_PARAMS:
            raise ValueError(f"AI 建议中包含不允许修改的参数: {param_name}")
        if param_name not in PARAM_TYPE_MAP:
            raise ValueError(f"未知参数: {param_name}")

    candidate_params = get_strategy_param_snapshot()
    candidate_params.update(updates)
    validate_param_ranges(candidate_params)
    return updates


def create_ai_parameter_proposal(trigger_mode="manual"):
    existing = get_pending_ai_suggestion_snapshot()
    if existing is not None:
        log_trade("当前已有待确认的 AI 修改建议，请先处理现有建议")
        return existing

    try:
        ai_result = request_ai_parameter_suggestions(trigger_mode)
        validated_result = validate_ai_suggestions(ai_result)
    except Exception as e:
        log_error(f"AI 参数分析失败: {e}")
        return None

    log_trade(f"AI 参数分析触发成功 | 模式={trigger_mode}")
    log_trade(f"AI 结论: {validated_result['summary']}")

    if not validated_result["should_modify"]:
        save_ai_suggestion_record(trigger_mode, validated_result, ai_result, applied=False, approved=False)
        return None

    for suggestion in validated_result["suggestions"]:
        log_trade(
            f"AI 建议修改 {suggestion['param']} -> {format_env_value(suggestion['value'])} | "
            f"原因: {suggestion['reason'] or '未提供'}"
        )

    proposal = build_ai_proposal(trigger_mode, validated_result, ai_result)
    set_pending_ai_suggestion(proposal)
    return proposal


def apply_ai_proposal(proposal):
    if proposal is None:
        return False

    validated_result = proposal["validated_result"]
    ai_result = proposal["ai_result"]
    updates = validate_edited_ai_proposal(proposal)

    apply_runtime_updates(updates)
    apply_updates_to_env_file(updates)
    save_run_config_snapshot()

    runtime_status = build_runtime_update_status(
        source="ai_apply",
        updates=updates,
        proposal_id=proposal.get("proposal_id")
    )
    runtime_status["persisted_to_env"] = True
    runtime_status["applied_in_memory"] = True
    save_runtime_status(runtime_status)

    save_ai_suggestion_record(
        proposal["trigger_mode"],
        {
            **validated_result,
            "updates": updates,
            "suggestions": proposal.get("editable_suggestions", [])
        },
        ai_result,
        applied=True,
        approved=True,
        proposal_id=proposal.get("proposal_id")
    )
    clear_pending_ai_suggestion()

    log_trade("AI 参数建议已应用：当前进程与 .env 都已更新")
    if runtime_status.get("effective_immediate"):
        log_trade(f"立即生效参数: {', '.join(runtime_status['effective_immediate'])}")
    if runtime_status.get("effective_next_cycle"):
        log_trade(f"下一周期体现参数: {', '.join(runtime_status['effective_next_cycle'])}")
    if runtime_status.get("note"):
        log_trade(runtime_status["note"])
    for key, value in updates.items():
        log_trade(f"  {key} = {format_env_value(value)}")

    return runtime_status


def approve_pending_ai_suggestion():
    proposal = get_pending_ai_suggestion_snapshot()
    if proposal is None:
        return False
    return apply_ai_proposal(proposal)


def reject_pending_ai_suggestion():
    proposal = get_pending_ai_suggestion_snapshot()
    if proposal is None:
        return False

    save_ai_suggestion_record(
        proposal["trigger_mode"],
        proposal["validated_result"],
        proposal["ai_result"],
        applied=False,
        approved=False,
        proposal_id=proposal.get("proposal_id")
    )
    clear_pending_ai_suggestion()
    log_trade("AI 参数建议已被拒绝，未应用修改")
    return True


def run_ai_parameter_optimizer(trigger_mode="manual"):
    proposal = create_ai_parameter_proposal(trigger_mode)
    if proposal is None:
        return False

    require_confirm = AI_REQUIRE_CONFIRM_ON_MANUAL if trigger_mode == "manual" else AI_REQUIRE_CONFIRM_ON_AUTO
    approved = True

    if require_confirm:
        approved = prompt_user_confirmation("是否同意应用以上 AI 参数修改？输入 y 确认，其他任意键取消: ")

    if not approved:
        reject_pending_ai_suggestion()
        return False

    return approve_pending_ai_suggestion()


def maybe_trigger_auto_ai_optimizer():
    global ai_last_analysis_trade_count

    if not AI_ENABLED or not AI_AUTO_OPTIMIZE_ENABLED:
        return False

    summary = get_summary_snapshot()
    total_trades = summary["total_trades"]
    win_rate = summary["win_rate_percent"]

    if total_trades < AI_AUTO_TRIGGER_MIN_TRADES:
        return False
    if win_rate >= AI_AUTO_TRIGGER_MIN_WIN_RATE:
        return False
    if total_trades == ai_last_analysis_trade_count:
        return False

    ai_last_analysis_trade_count = total_trades
    log_trade(
        f"触发自动 AI 参数分析 | total_trades={total_trades} | "
        f"win_rate={win_rate:.2f}% | threshold={AI_AUTO_TRIGGER_MIN_WIN_RATE:.2f}%"
    )

    proposal = create_ai_parameter_proposal(trigger_mode="auto")
    if proposal is None:
        return False

    if AI_REQUIRE_CONFIRM_ON_AUTO:
        log_trade("自动 AI 建议已生成，等待在控制面板中确认或拒绝")
        return False

    return approve_pending_ai_suggestion()


def check_manual_ai_trigger_file():
    if not os.path.exists(AI_MANUAL_TRIGGER_FLAG_FILE):
        return False

    try:
        os.remove(AI_MANUAL_TRIGGER_FLAG_FILE)
    except Exception:
        pass

    log_trade(f"检测到手动 AI 触发文件: {AI_MANUAL_TRIGGER_FLAG_FILE}")
    return create_ai_parameter_proposal(trigger_mode="manual") is not None



def maybe_trigger_auto_ai_optimizer():
    global ai_last_analysis_trade_count

    if not AI_ENABLED or not AI_AUTO_OPTIMIZE_ENABLED:
        return False

    summary = get_summary_snapshot()
    total_trades = summary["total_trades"]
    win_rate = summary["win_rate_percent"]

    if total_trades < AI_AUTO_TRIGGER_MIN_TRADES:
        return False
    if win_rate >= AI_AUTO_TRIGGER_MIN_WIN_RATE:
        return False
    if total_trades == ai_last_analysis_trade_count:
        return False

    ai_last_analysis_trade_count = total_trades
    log_trade(
        f"触发自动 AI 参数分析 | total_trades={total_trades} | "
        f"win_rate={win_rate:.2f}% | threshold={AI_AUTO_TRIGGER_MIN_WIN_RATE:.2f}%"
    )

    proposal = create_ai_parameter_proposal(trigger_mode="auto")
    if proposal is None:
        return False

    if AI_REQUIRE_CONFIRM_ON_AUTO:
        log_trade("自动 AI 建议已生成，等待在控制面板中确认或拒绝")
        return False

    return approve_pending_ai_suggestion()


def check_manual_ai_trigger_file():
    if not os.path.exists(AI_MANUAL_TRIGGER_FLAG_FILE):
        return False

    try:
        os.remove(AI_MANUAL_TRIGGER_FLAG_FILE)
    except Exception:
        pass

    log_trade(f"检测到手动 AI 触发文件: {AI_MANUAL_TRIGGER_FLAG_FILE}")
    return create_ai_parameter_proposal(trigger_mode="manual") is not None


def init_csv():
    """
    初始化交易 CSV
    """
    if not os.path.exists(TRADE_CSV_FILE):
        ensure_parent_dir(TRADE_CSV_FILE)
        with open(TRADE_CSV_FILE, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow([
                "run_id",
                "trade_id",
                "symbol",
                "side",
                "entry_time",
                "exit_time",
                "entry_price",
                "exit_price",
                "qty",
                "notional_usdt",
                "entry_notional",
                "exit_notional",
                "entry_fee",
                "exit_fee",
                "total_fee",
                "gross_pnl",
                "net_pnl",
                "pnl_pct",
                "hold_seconds",
                "status",
                "close_reason",
                "long_score",
                "short_score",
                "decision_reason",
                "trading_mode"
            ])


def http_get(url, params=None):
    """
    统一 GET 请求函数
    """
    normalized_params = normalize_request_params(params)
    cached = get_cached_market_response(url, normalized_params)
    if cached is not None:
        return cached

    wait_market_rate_limit(url)

    try:
        response = requests.get(
            url=url,
            params=normalized_params,
            timeout=15,
            proxies=PROXIES,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        response.raise_for_status()
        data = response.json()
        set_cached_market_response(url, normalized_params, data)
        return data
    except Exception as e:
        cache_label = get_market_cache_label(url)
        raise Exception(f"请求失败 | api={cache_label} | url={url} | params={normalized_params} | proxies={PROXIES} | error={e}")


# ============================================================
# 四、运行参数快照
# ============================================================

def build_run_config_snapshot():
    """
    把当前这次运行用到的关键参数保存下来
    后面你把这个文件发给我，我就知道你这次跑的是什么参数
    """
    return {
        "run_id": RUN_ID,
        "start_time_local": now_str(),
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "interval_seconds": INTERVAL_SECONDS,
        "max_hold_seconds": MAX_HOLD_SECONDS,
        "notional_usdt": NOTIONAL_USDT,
        "taker_fee_rate": TAKER_FEE_RATE,
        "leverage": LEVERAGE,
        "trade_start_time": TRADE_START_TIME,
        "trade_end_time": TRADE_END_TIME,
        "status_interval_seconds": STATUS_INTERVAL_SECONDS,
        "ema_fast": EMA_FAST,
        "ema_slow": EMA_SLOW,
        "adx_period": ADX_PERIOD,
        "volume_ma_period": VOLUME_MA_PERIOD,
        "structure_lookback": STRUCTURE_LOOKBACK,
        "enable_take_profit": ENABLE_TAKE_PROFIT,
        "take_profit_pct": TAKE_PROFIT_PCT,
        "enable_stop_loss": ENABLE_STOP_LOSS,
        "stop_loss_pct": STOP_LOSS_PCT,
        "use_proxy": USE_PROXY,
        "proxy_url": PROXY_URL,
        "cycle_trigger_window_seconds": CYCLE_TRIGGER_WINDOW_SECONDS,
        "open_retry_interval_seconds": OPEN_RETRY_INTERVAL_SECONDS,
        "trading_mode": TRADING_MODE,
        "binance": {
            "base_url": BINANCE_BASE_URL,
            "api_configured": bool(BINANCE_API_KEY and BINANCE_API_SECRET)
        },
        "ai": {
            "enabled": AI_ENABLED,
            "base_url": AI_BASE_URL,
            "model": AI_MODEL,
            "timeout_seconds": AI_TIMEOUT_SECONDS,
            "auto_optimize_enabled": AI_AUTO_OPTIMIZE_ENABLED,
            "auto_trigger_min_win_rate": AI_AUTO_TRIGGER_MIN_WIN_RATE,
            "auto_trigger_min_trades": AI_AUTO_TRIGGER_MIN_TRADES,
            "require_confirm_on_manual": AI_REQUIRE_CONFIRM_ON_MANUAL,
            "require_confirm_on_auto": AI_REQUIRE_CONFIRM_ON_AUTO,
            "allowed_params": AI_ALLOWED_PARAMS,
            "blocked_params": AI_BLOCKED_PARAMS,
            "skill_file": AI_SKILL_FILE
        },
        "files": {
            "trade_log": TRADE_LOG_FILE,
            "position_log": POSITION_LOG_FILE,
            "error_log": ERROR_LOG_FILE,
            "summary_log": SUMMARY_LOG_FILE,
            "trade_csv": TRADE_CSV_FILE,
            "trade_detail_jsonl": TRADE_DETAIL_JSONL_FILE,
            "ai_suggestion_jsonl": AI_SUGGESTION_JSONL_FILE,
            "run_config_file": RUN_CONFIG_FILE,
            "summary_json_file": SUMMARY_JSON_FILE
        }
    }


def save_run_config_snapshot():
    snapshot = build_run_config_snapshot()
    write_json_file(RUN_CONFIG_FILE, snapshot)
    write_json_file(RUN_CONFIG_LATEST_FILE, snapshot)


# ============================================================
# 五、汇总统计
# ============================================================

def update_stats(net_pnl, gross_pnl, total_fee):
    """
    每平掉一笔单，就更新一次整体统计
    """
    stats["total_trades"] += 1
    stats["total_gross_pnl"] += gross_pnl
    stats["total_net_pnl"] += net_pnl
    stats["total_fee"] += total_fee

    if stats["max_profit"] is None or net_pnl > stats["max_profit"]:
        stats["max_profit"] = net_pnl

    if stats["max_loss"] is None or net_pnl < stats["max_loss"]:
        stats["max_loss"] = net_pnl

    if net_pnl > 0:
        stats["win_trades"] += 1
        stats["current_win_streak"] += 1
        stats["current_loss_streak"] = 0
        if stats["current_win_streak"] > stats["max_win_streak"]:
            stats["max_win_streak"] = stats["current_win_streak"]

    elif net_pnl < 0:
        stats["loss_trades"] += 1
        stats["current_loss_streak"] += 1
        stats["current_win_streak"] = 0
        if stats["current_loss_streak"] > stats["max_loss_streak"]:
            stats["max_loss_streak"] = stats["current_loss_streak"]

    else:
        stats["flat_trades"] += 1
        stats["current_win_streak"] = 0
        stats["current_loss_streak"] = 0


def get_summary_snapshot():
    """
    生成当前汇总统计快照
    """
    total = stats["total_trades"]
    win = stats["win_trades"]
    loss = stats["loss_trades"]
    flat = stats["flat_trades"]

    win_rate = (win / total * 100) if total > 0 else 0
    avg_net_pnl = (stats["total_net_pnl"] / total) if total > 0 else 0

    return {
        "run_id": RUN_ID,
        "snapshot_time_local": now_str(),
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "total_trades": total,
        "win_trades": win,
        "loss_trades": loss,
        "flat_trades": flat,
        "win_rate_percent": round(win_rate, 4),
        "total_gross_pnl": round(stats["total_gross_pnl"], 8),
        "total_fee": round(stats["total_fee"], 8),
        "total_net_pnl": round(stats["total_net_pnl"], 8),
        "average_net_pnl_per_trade": round(avg_net_pnl, 8),
        "max_profit": round(stats["max_profit"], 8) if stats["max_profit"] is not None else None,
        "max_loss": round(stats["max_loss"], 8) if stats["max_loss"] is not None else None,
        "current_win_streak": stats["current_win_streak"],
        "current_loss_streak": stats["current_loss_streak"],
        "max_win_streak": stats["max_win_streak"],
        "max_loss_streak": stats["max_loss_streak"]
    }


def get_historical_summary_snapshot():
    """
    从 JSONL 聚合所有历史交易统计
    """
    if not os.path.exists(TRADE_DETAIL_JSONL_FILE):
        return None

    total = 0
    win = 0
    loss = 0
    flat = 0
    total_gross_pnl = 0.0
    total_net_pnl = 0.0
    total_fee = 0.0
    max_profit = None
    max_loss = None

    try:
        with open(TRADE_DETAIL_JSONL_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                net_pnl = record.get("net_pnl", 0)
                gross_pnl = record.get("gross_pnl", 0)
                fee = record.get("total_fee", 0)

                total += 1
                total_gross_pnl += gross_pnl
                total_net_pnl += net_pnl
                total_fee += fee

                if net_pnl > 0:
                    win += 1
                elif net_pnl < 0:
                    loss += 1
                else:
                    flat += 1

                if max_profit is None or net_pnl > max_profit:
                    max_profit = net_pnl
                if max_loss is None or net_pnl < max_loss:
                    max_loss = net_pnl
    except Exception as e:
        log_error(f"读取历史交易统计失败: {e}")
        return None

    win_rate = (win / total * 100) if total > 0 else 0
    avg_net_pnl = (total_net_pnl / total) if total > 0 else 0

    return {
        "snapshot_time_local": now_str(),
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "total_trades": total,
        "win_trades": win,
        "loss_trades": loss,
        "flat_trades": flat,
        "win_rate_percent": round(win_rate, 4),
        "total_gross_pnl": round(total_gross_pnl, 8),
        "total_fee": round(total_fee, 8),
        "total_net_pnl": round(total_net_pnl, 8),
        "average_net_pnl_per_trade": round(avg_net_pnl, 8),
        "max_profit": round(max_profit, 8) if max_profit is not None else None,
        "max_loss": round(max_loss, 8) if max_loss is not None else None
    }


def get_dual_summary_snapshot():
    """
    返回双统计：本次运行 + 历史累计
    """
    return {
        "current_run": get_summary_snapshot(),
        "historical": get_historical_summary_snapshot()
    }


def save_summary_snapshot():
    snapshot = get_summary_snapshot()
    write_json_file(SUMMARY_JSON_FILE, snapshot)
    write_json_file(SUMMARY_JSON_LATEST_FILE, snapshot)


def print_summary():
    """
    把统计信息写到 summary 日志
    """
    total = stats["total_trades"]
    win = stats["win_trades"]
    loss = stats["loss_trades"]
    flat = stats["flat_trades"]

    win_rate = (win / total * 100) if total > 0 else 0
    avg_net = (stats["total_net_pnl"] / total) if total > 0 else 0

    log_summary("========== 当前统计汇总 ==========")
    log_summary(f"总交易次数: {total}")
    log_summary(f"盈利次数: {win}")
    log_summary(f"亏损次数: {loss}")
    log_summary(f"持平次数: {flat}")
    log_summary(f"胜率: {win_rate:.2f}%")
    log_summary(f"总毛利润: {stats['total_gross_pnl']:.6f} U")
    log_summary(f"总手续费: {stats['total_fee']:.6f} U")
    log_summary(f"总净利润: {stats['total_net_pnl']:.6f} U")
    log_summary(f"平均单笔净利润: {avg_net:.6f} U")
    log_summary(f"最大单笔盈利: {(stats['max_profit'] if stats['max_profit'] is not None else 0):.6f} U")
    log_summary(f"最大单笔亏损: {(stats['max_loss'] if stats['max_loss'] is not None else 0):.6f} U")
    log_summary(f"当前连续盈利: {stats['current_win_streak']}")
    log_summary(f"当前连续亏损: {stats['current_loss_streak']}")
    log_summary(f"最大连续盈利: {stats['max_win_streak']}")
    log_summary(f"最大连续亏损: {stats['max_loss_streak']}")

    save_summary_snapshot()


# ============================================================
# 六、启动检查
# ============================================================

def test_connection():
    """
    测试是否能连到 Binance Futures
    """
    try:
        url = f"{BASE_URL}/fapi/v1/time"
        data = http_get(url)
        server_dt = datetime.fromtimestamp(data["serverTime"] / 1000, tz=timezone.utc)
        log_trade(f"连接 Binance Futures 成功 | 服务器时间={server_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        return True
    except Exception as e:
        log_error(f"连接 Binance Futures 失败: {e}")
        return False


def check_symbol_valid(symbol):
    """
    检查交易对是否真的存在于 Binance U本位合约
    """
    try:
        url = f"{BASE_URL}/fapi/v1/exchangeInfo"
        data = http_get(url)

        symbols = [item["symbol"] for item in data["symbols"]]

        if symbol in symbols:
            log_trade(f"交易对检查成功 | {symbol} 存在于 Binance Futures")
            return True
        else:
            log_error(f"交易对检查失败 | {symbol} 不存在于 Binance Futures")
            return False
    except Exception as e:
        log_error(f"检查交易对失败: {e}")
        return False


# ============================================================
# 七、行情和时间
# ============================================================

def get_server_time():
    url = f"{BASE_URL}/fapi/v1/time"
    data = http_get(url)
    return data["serverTime"]


def ms_to_dt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def get_current_price(symbol):
    url = f"{BASE_URL}/fapi/v1/ticker/price"
    params = {"symbol": symbol}
    data = http_get(url, params=params)
    return float(data["price"])


def get_klines(symbol, interval="5m", limit=200):
    """
    获取合约 K 线
    """
    url = f"{BASE_URL}/fapi/v1/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }

    data = http_get(url, params=params)

    columns = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_asset_volume",
        "number_of_trades",
        "taker_buy_base_asset_volume",
        "taker_buy_quote_asset_volume",
        "ignore"
    ]

    df = pd.DataFrame(data, columns=columns)

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)

    return df


def seconds_to_next_interval_from_server():
    """
    计算距离下一个周期边界还有多少秒
    """
    server_ms = get_server_time()
    server_dt = ms_to_dt(server_ms)

    total_seconds = server_dt.minute * 60 + server_dt.second
    passed_in_interval = total_seconds % INTERVAL_SECONDS
    wait = INTERVAL_SECONDS - passed_in_interval

    if wait == 0:
        wait = INTERVAL_SECONDS

    return wait, server_dt


def get_latest_closed_kline_open_time(df):
    """
    倒数第 2 根是刚收盘完成的 K 线
    """
    return df.iloc[-2]["open_time"]


# ============================================================
# 八、技术指标
# ============================================================

def calculate_adx(df, period=14):
    """
    计算 ADX / +DI / -DI
    """
    df = df.copy()

    high_low = df["high"] - df["low"]
    high_close_prev = (df["high"] - df["close"].shift(1)).abs()
    low_close_prev = (df["low"] - df["close"].shift(1)).abs()

    tr = pd.concat([high_low, high_close_prev, low_close_prev], axis=1).max(axis=1)

    up_move = df["high"].diff()
    down_move = -df["low"].diff()

    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)

    plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
    minus_dm[(down_move > up_move) & (down_move > 0)] = down_move

    atr = tr.rolling(window=period).mean()
    plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr.replace(0, 1e-10))
    minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr.replace(0, 1e-10))

    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-10)) * 100
    adx = dx.rolling(window=period).mean()

    return adx, plus_di, minus_di


def add_indicators(df):
    """
    给 K 线添加指标
    """
    df = df.copy()

    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()

    adx, plus_di, minus_di = calculate_adx(df, ADX_PERIOD)
    df["adx"] = adx
    df["plus_di"] = plus_di
    df["minus_di"] = minus_di

    df["volume_ma"] = df["volume"].rolling(VOLUME_MA_PERIOD).mean()

    return df


# ============================================================
# 九、交易时间窗口
# ============================================================

def parse_hhmm(hhmm):
    h, m = hhmm.split(":")
    return int(h), int(m)


def is_in_trade_window():
    """
    判断当前是否在允许交易时间范围内（本地时间）
    """
    now_local = datetime.now()

    start_h, start_m = parse_hhmm(TRADE_START_TIME)
    end_h, end_m = parse_hhmm(TRADE_END_TIME)

    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    now_minutes = now_local.hour * 60 + now_local.minute

    return start_minutes <= now_minutes <= end_minutes


# ============================================================
# 十、K线结构判断
# ============================================================

def highs_rising(df_slice):
    highs = df_slice["high"].tolist()
    for i in range(1, len(highs)):
        if highs[i] <= highs[i - 1]:
            return False
    return True


def lows_rising(df_slice):
    lows = df_slice["low"].tolist()
    for i in range(1, len(lows)):
        if lows[i] <= lows[i - 1]:
            return False
    return True


def highs_falling(df_slice):
    highs = df_slice["high"].tolist()
    for i in range(1, len(highs)):
        if highs[i] >= highs[i - 1]:
            return False
    return True


def lows_falling(df_slice):
    lows = df_slice["low"].tolist()
    for i in range(1, len(lows)):
        if lows[i] >= lows[i - 1]:
            return False
    return True


# ============================================================
# 十一、每周期强制决策
# ------------------------------------------------------------
# 这里不会返回 None
# 每个周期一定会给出 LONG 或 SHORT
# 还会把指标和得分一起记录下来，方便后面优化策略
# ============================================================

def get_forced_trade_signal(df):
    min_needed = max(EMA_SLOW + 5, VOLUME_MA_PERIOD + 5, ADX_PERIOD * 2 + 5, STRUCTURE_LOOKBACK + 5)

    # 数据不足时，也必须强制做决策
    if len(df) < 5:
        decision = {
            "side": "LONG",
            "long_score": 0,
            "short_score": 0,
            "reasons": [
                "历史K线数据太少",
                "默认强制做多"
            ],
            "indicators": {}
        }
        return decision

    latest = df.iloc[-2]
    prev = df.iloc[-3]

    if len(df) < min_needed:
        side = "LONG" if latest["close"] >= prev["close"] else "SHORT"
        reasons = [
            "历史K线数量暂时不足",
            "按最近收盘方向进行强制决策",
            f"本周期选择：{'做多' if side == 'LONG' else '做空'}"
        ]
        decision = {
            "side": side,
            "long_score": 1 if side == "LONG" else 0,
            "short_score": 1 if side == "SHORT" else 0,
            "reasons": reasons,
            "indicators": {
                "close_price": safe_round(latest["close"]),
                "prev_close": safe_round(prev["close"])
            }
        }
        return decision

    structure_slice = df.iloc[-(STRUCTURE_LOOKBACK + 2):-2]

    ema_fast = latest["ema_fast"]
    ema_slow = latest["ema_slow"]
    adx = latest["adx"]
    plus_di = latest["plus_di"]
    minus_di = latest["minus_di"]
    volume = latest["volume"]
    volume_ma = latest["volume_ma"]
    close_price = latest["close"]
    prev_close = prev["close"]
    open_price = latest["open"]

    structure_highs_rising = highs_rising(structure_slice)
    structure_lows_rising = lows_rising(structure_slice)
    structure_highs_falling = highs_falling(structure_slice)
    structure_lows_falling = lows_falling(structure_slice)

    indicators = {
        "ema_fast": safe_round(ema_fast),
        "ema_slow": safe_round(ema_slow),
        "adx": safe_round(adx),
        "plus_di": safe_round(plus_di),
        "minus_di": safe_round(minus_di),
        "volume": safe_round(volume),
        "volume_ma": safe_round(volume_ma),
        "open_price": safe_round(open_price),
        "close_price": safe_round(close_price),
        "prev_close": safe_round(prev_close),
        "structure_highs_rising": structure_highs_rising,
        "structure_lows_rising": structure_lows_rising,
        "structure_highs_falling": structure_highs_falling,
        "structure_lows_falling": structure_lows_falling
    }

    key_values = [ema_fast, ema_slow, adx, plus_di, minus_di, volume_ma]
    if any(pd.isna(v) for v in key_values):
        side = "LONG" if close_price >= prev_close else "SHORT"
        reasons = [
            "部分指标尚未准备好",
            "按照最近收盘方向强制决策",
            f"本周期选择：{'做多' if side == 'LONG' else '做空'}"
        ]
        decision = {
            "side": side,
            "long_score": 1 if side == "LONG" else 0,
            "short_score": 1 if side == "SHORT" else 0,
            "reasons": reasons,
            "indicators": indicators
        }
        return decision

    long_score = 0
    short_score = 0

    long_reasons = []
    short_reasons = []

    # 1. EMA 趋势
    if ema_fast > ema_slow:
        long_score += 1
        long_reasons.append("短期趋势偏多（快线在慢线上方）")
    elif ema_fast < ema_slow:
        short_score += 1
        short_reasons.append("短期趋势偏空（快线在慢线下方）")

    # 2. 最近收盘动量
    if close_price > prev_close:
        long_score += 1
        long_reasons.append("最近收盘价高于前一根，动量偏多")
    elif close_price < prev_close:
        short_score += 1
        short_reasons.append("最近收盘价低于前一根，动量偏空")

    # 3. K线结构
    if structure_highs_rising and structure_lows_rising:
        long_score += 1
        long_reasons.append("最近几根K线高低点整体抬高")

    if structure_highs_falling and structure_lows_falling:
        short_score += 1
        short_reasons.append("最近几根K线高低点整体下移")

    # 4. DI 力量方向
    if plus_di > minus_di:
        long_score += 1
        long_reasons.append("多头力量强于空头（+DI > -DI）")
    elif minus_di > plus_di:
        short_score += 1
        short_reasons.append("空头力量强于多头（-DI > +DI）")

    # 5. 放量后的K线方向
    if volume > volume_ma:
        if close_price > open_price:
            long_score += 1
            long_reasons.append("本根K线放量收涨，多头更主动")
        elif close_price < open_price:
            short_score += 1
            short_reasons.append("本根K线放量收跌，空头更主动")

    # 6. ADX 趋势强度
    if adx >= 20:
        if ema_fast > ema_slow:
            long_score += 1
            long_reasons.append("当前趋势强度较明显，进一步支持做多")
        elif ema_fast < ema_slow:
            short_score += 1
            short_reasons.append("当前趋势强度较明显，进一步支持做空")

    # 强制决策
    if long_score > short_score:
        reasons = [f"本周期决策：做多（多头得分 {long_score}，空头得分 {short_score}）"]
        reasons.extend(long_reasons)
        return {
            "side": "LONG",
            "long_score": long_score,
            "short_score": short_score,
            "reasons": reasons,
            "indicators": indicators
        }

    if short_score > long_score:
        reasons = [f"本周期决策：做空（空头得分 {short_score}，多头得分 {long_score}）"]
        reasons.extend(short_reasons)
        return {
            "side": "SHORT",
            "long_score": long_score,
            "short_score": short_score,
            "reasons": reasons,
            "indicators": indicators
        }

    # 平分时，大趋势优先
    if ema_fast >= ema_slow:
        reasons = [
            f"本周期多空得分相同（多头 {long_score}，空头 {short_score}）",
            "按照大趋势优先规则，当前偏多",
            "本周期强制选择做多"
        ]
        return {
            "side": "LONG",
            "long_score": long_score,
            "short_score": short_score,
            "reasons": reasons,
            "indicators": indicators
        }
    else:
        reasons = [
            f"本周期多空得分相同（多头 {long_score}，空头 {short_score}）",
            "按照大趋势优先规则，当前偏空",
            "本周期强制选择做空"
        ]
        return {
            "side": "SHORT",
            "long_score": long_score,
            "short_score": short_score,
            "reasons": reasons,
            "indicators": indicators
        }


# ============================================================
# 十二、挂起待开仓信息（解决边界时开仓失败问题）
# ============================================================

def set_pending_open(decision, signal_kline_open_time):
    """
    保存当前周期待开仓决策
    """
    global pending_open_decision, pending_signal_kline_open_time
    pending_open_decision = decision
    pending_signal_kline_open_time = signal_kline_open_time


def clear_pending_open():
    """
    清空待开仓信息
    """
    global pending_open_decision, pending_signal_kline_open_time
    pending_open_decision = None
    pending_signal_kline_open_time = None


# ============================================================
# 十三、开仓
# ============================================================

def open_position_from_decision(decision, signal_kline_open_time):
    """开仓入口 - 根据模式分发"""
    if TRADING_MODE == "SIMULATION":
        return simulate_open_position(decision, signal_kline_open_time)
    elif TRADING_MODE == "LIVE":
        return live_open_position(decision, signal_kline_open_time)
    else:
        raise ValueError(f"Unknown TRADING_MODE: {TRADING_MODE}")


def simulate_open_position(decision, signal_kline_open_time):
    """模拟开仓 - 保持当前逻辑不变"""
    global position

    side = decision["side"]
    entry_price = get_current_price(SYMBOL)

    qty = NOTIONAL_USDT / entry_price
    entry_notional = qty * entry_price
    entry_fee = entry_notional * TAKER_FEE_RATE

    trade_id = f"{SYMBOL}_{side}_{int(time.time())}"

    position = {
        "run_id": RUN_ID,
        "trade_id": trade_id,
        "symbol": SYMBOL,
        "side": side,
        "entry_time": datetime.now(),
        "entry_price": entry_price,
        "qty": qty,
        "notional_usdt": NOTIONAL_USDT,
        "entry_notional": entry_notional,
        "entry_fee": entry_fee,
        "decision_snapshot": decision,
        "signal_kline_open_time": signal_kline_open_time,
        "status": "OPEN"
    }
    persist_live_position_snapshot()

    direction_text = "做多" if side == "LONG" else "做空"

    log_trade("========== 开仓成功 ==========")
    log_trade(f"交易对: {SYMBOL}")
    log_trade(f"方向: {direction_text}")
    log_trade(f"开仓价: {entry_price:.6f}")
    log_trade(f"下单金额: {entry_notional:.6f} U")
    log_trade(f"下单数量: {qty:.6f}")
    log_trade(f"开仓手续费: {entry_fee:.6f} U")
    log_trade(f"最大持仓时间: {MAX_HOLD_SECONDS} 秒")
    log_trade(f"杠杆(仅展示): {LEVERAGE} 倍")
    log_trade(f"多头得分: {decision.get('long_score', 0)}")
    log_trade(f"空头得分: {decision.get('short_score', 0)}")
    log_trade("开仓原因:")
    for i, reason in enumerate(decision.get("reasons", []), 1):
        log_trade(f"  {i}. {reason}")


def live_open_position(decision, signal_kline_open_time):
    """实盘开仓 - 第二阶段：真实下单并记录交易所返回数据"""
    global position

    if not binance_adapter.available():
        raise ValueError("实盘模式未配置币安 API")

    side = decision["side"]
    order_side = "BUY" if side == "LONG" else "SELL"

    # 查询真实手续费率
    try:
        commission_info = binance_adapter.get_commission_rate(SYMBOL)
        taker_rate = float(commission_info['takerCommissionRate'])
        log_trade(f"[实盘模式] 查询到真实 taker 费率: {taker_rate}")
    except Exception as e:
        log_error(f"查询手续费率失败: {e}，使用配置费率")
        taker_rate = TAKER_FEE_RATE

    # 确认不存在真实持仓
    try:
        positions = binance_adapter.get_position_risk(SYMBOL)
        for pos in positions:
            if float(pos['positionAmt']) != 0:
                raise ValueError(f"检测到真实持仓未清空，拒绝重复开仓: {pos}")
    except Exception:
        raise

    mark_price = get_current_price(SYMBOL)
    raw_qty = NOTIONAL_USDT / mark_price
    qty = normalize_order_qty(SYMBOL, raw_qty)
    qty_text = format_order_qty(SYMBOL, qty)

    if float(qty_text) <= 0:
        raise ValueError("规范化后的下单数量小于等于 0，无法下单")

    log_trade(f"[实盘模式] 准备下单 | side={order_side} | qty={qty_text} | 估算价格={mark_price:.6f}")

    order = binance_adapter.new_order(
        symbol=SYMBOL,
        side=order_side,
        order_type="MARKET",
        quantity=qty_text
    )

    order_id = order.get("orderId")
    if not order_id:
        raise ValueError(f"下单返回缺少 orderId: {order}")

    order_detail = binance_adapter.get_order(SYMBOL, order_id)
    entry_trade_rows = get_order_trades(SYMBOL, order_id)

    executed_qty = float(order_detail.get("executedQty", 0) or 0)
    avg_price = float(order_detail.get("avgPrice", 0) or 0)
    cum_quote = float(order_detail.get("cumQuote", 0) or 0)
    status = order_detail.get("status", "UNKNOWN")

    if status != "FILLED":
        raise ValueError(f"市价单未完全成交，当前状态={status} | order={order_detail}")

    if executed_qty <= 0 or avg_price <= 0:
        raise ValueError(f"成交数据异常 | executedQty={executed_qty} avgPrice={avg_price}")

    entry_notional = cum_quote if cum_quote > 0 else executed_qty * avg_price

    trade_details = aggregate_order_trade_details(entry_trade_rows)
    entry_fee = trade_details["commission"]
    commission_asset = trade_details["commission_asset"]

    if entry_fee <= 0:
        entry_fee = entry_notional * taker_rate
        log_error(f"未获取到真实开仓手续费，临时按费率估算 | order_id={order_id} | fee={entry_fee}")
        fee_source = "estimated_from_rate"
    else:
        fee_source = "exchange_trade_details"

    trade_id = f"{SYMBOL}_{side}_{order_id}"

    position = {
        "run_id": RUN_ID,
        "trade_id": trade_id,
        "symbol": SYMBOL,
        "side": side,
        "entry_time": datetime.now(),
        "entry_price": avg_price,
        "qty": executed_qty,
        "notional_usdt": NOTIONAL_USDT,
        "entry_notional": entry_notional,
        "entry_fee": entry_fee,
        "entry_fee_source": fee_source,
        "entry_commission_asset": commission_asset,
        "taker_rate": taker_rate,
        "decision_snapshot": decision,
        "signal_kline_open_time": signal_kline_open_time,
        "status": "OPEN",
        "trading_mode": "LIVE",
        "entry_order_id": order_id,
        "entry_order_status": status,
        "entry_raw_order": order_detail,
        "entry_trade_details": entry_trade_rows
    }
    persist_live_position_snapshot()

    direction_text = "做多" if side == "LONG" else "做空"

    log_trade("========== 开仓成功 ==========")
    log_trade(f"[实盘] 交易对: {SYMBOL}")
    log_trade(f"方向: {direction_text}")
    log_trade(f"订单ID: {order_id}")
    log_trade(f"开仓价(真实成交均价): {avg_price:.6f}")
    log_trade(f"下单金额(真实成交额): {entry_notional:.6f} U")
    log_trade(f"下单数量(真实成交量): {executed_qty:.6f}")
    log_trade(f"开仓手续费: {entry_fee:.6f} {commission_asset}")
    log_trade(f"手续费来源: {fee_source}")
    log_trade(f"最大持仓时间: {MAX_HOLD_SECONDS} 秒")
    log_trade(f"杠杆(仅展示): {LEVERAGE} 倍")
    log_trade(f"多头得分: {decision.get('long_score', 0)}")
    log_trade(f"空头得分: {decision.get('short_score', 0)}")
    log_trade("开仓原因:")
    for i, reason in enumerate(decision.get("reasons", []), 1):
        log_trade(f"  {i}. {reason}")


def try_open_pending_position(show_retry_log=False):
    """
    如果当前周期有待开仓决策，尝试开仓
    如果成功，则清空 pending
    如果失败，则保留 pending，后续继续补开
    """
    global pending_open_decision, pending_signal_kline_open_time

    if position is not None:
        return False

    if pending_open_decision is None:
        return False

    try:
        if show_retry_log:
            log_trade("检测到当前周期应当有仓位，开始尝试补开仓")

        open_position_from_decision(pending_open_decision, pending_signal_kline_open_time)
        clear_pending_open()
        return True

    except Exception as e:
        log_error(f"当前周期开仓失败，将稍后自动重试 | error={e}")
        return False


def retry_pending_open_if_needed():
    """
    如果某个周期本该有仓位，但由于瞬时失败没开成功，
    那么在当前周期内定时重试开仓
    """
    global last_open_retry_ts

    if position is not None:
        return

    if pending_open_decision is None:
        return

    current_ts = int(time.time())

    if current_ts - last_open_retry_ts < OPEN_RETRY_INTERVAL_SECONDS:
        return

    last_open_retry_ts = current_ts
    try_open_pending_position(show_retry_log=True)


# ============================================================
# 十四、平仓
# ============================================================

def close_position(exit_price, close_reason="TIME_EXIT"):
    """平仓入口 - 根据模式分发"""
    if TRADING_MODE == "SIMULATION":
        return simulate_close_position(exit_price, close_reason)
    elif TRADING_MODE == "LIVE":
        return live_close_position(close_reason)
    else:
        raise ValueError(f"Unknown TRADING_MODE: {TRADING_MODE}")


def simulate_close_position(exit_price, close_reason):
    """模拟平仓 - 保持当前逻辑不变"""
    global position

    if position is None:
        return

    exit_time = datetime.now()
    qty = position["qty"]
    side = position["side"]

    exit_notional = qty * exit_price
    exit_fee = exit_notional * TAKER_FEE_RATE

    if side == "LONG":
        gross_pnl = (exit_price - position["entry_price"]) * qty
    else:
        gross_pnl = (position["entry_price"] - exit_price) * qty

    total_fee = position["entry_fee"] + exit_fee
    net_pnl = gross_pnl - total_fee
    pnl_pct = net_pnl / position["entry_notional"] if position["entry_notional"] != 0 else 0
    hold_seconds = int((exit_time - position["entry_time"]).total_seconds())

    result_text = "盈利" if net_pnl > 0 else "亏损" if net_pnl < 0 else "持平"
    direction_text = "做多" if side == "LONG" else "做空"

    decision_snapshot = position.get("decision_snapshot", {})
    long_score = decision_snapshot.get("long_score", 0)
    short_score = decision_snapshot.get("short_score", 0)
    reasons = decision_snapshot.get("reasons", [])
    reason_text = " | ".join(reasons)

    csv_row = [
        RUN_ID,
        position["trade_id"],
        position["symbol"],
        position["side"],
        position["entry_time"].strftime("%Y-%m-%d %H:%M:%S"),
        exit_time.strftime("%Y-%m-%d %H:%M:%S"),
        round(position["entry_price"], 6),
        round(exit_price, 6),
        round(qty, 6),
        round(position["notional_usdt"], 6),
        round(position["entry_notional"], 6),
        round(exit_notional, 6),
        round(position["entry_fee"], 6),
        round(exit_fee, 6),
        round(total_fee, 6),
        round(gross_pnl, 6),
        round(net_pnl, 6),
        round(pnl_pct, 6),
        hold_seconds,
        "CLOSED",
        close_reason,
        long_score,
        short_score,
        reason_text,
        "SIMULATION"
    ]

    ensure_parent_dir(TRADE_CSV_FILE)
    with open(TRADE_CSV_FILE, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(csv_row)

    detail_record = {
        "run_id": RUN_ID,
        "trade_id": position["trade_id"],
        "symbol": position["symbol"],
        "side": position["side"],
        "entry_time": position["entry_time"].strftime("%Y-%m-%d %H:%M:%S"),
        "exit_time": exit_time.strftime("%Y-%m-%d %H:%M:%S"),
        "entry_price": round(position["entry_price"], 8),
        "exit_price": round(exit_price, 8),
        "qty": round(qty, 8),
        "notional_usdt": round(position["notional_usdt"], 8),
        "entry_notional": round(position["entry_notional"], 8),
        "exit_notional": round(exit_notional, 8),
        "entry_fee": round(position["entry_fee"], 8),
        "exit_fee": round(exit_fee, 8),
        "total_fee": round(total_fee, 8),
        "gross_pnl": round(gross_pnl, 8),
        "net_pnl": round(net_pnl, 8),
        "pnl_pct": round(pnl_pct, 8),
        "hold_seconds": hold_seconds,
        "status": "CLOSED",
        "close_reason": close_reason,
        "signal_kline_open_time": to_text_time(position.get("signal_kline_open_time")),
        "decision_snapshot": decision_snapshot,
        "config_file": RUN_CONFIG_FILE
    }

    append_jsonl(TRADE_DETAIL_JSONL_FILE, detail_record)
    update_stats(net_pnl, gross_pnl, total_fee)

    log_trade("========== 平仓成功 ==========")
    log_trade(f"交易对: {SYMBOL}")
    log_trade(f"方向: {direction_text}")
    log_trade(f"开仓价: {position['entry_price']:.6f}")
    log_trade(f"平仓价: {exit_price:.6f}")
    log_trade(f"下单金额: {position['entry_notional']:.6f} U")
    log_trade(f"平仓金额: {exit_notional:.6f} U")
    log_trade(f"开仓手续费: {position['entry_fee']:.6f} U")
    log_trade(f"平仓手续费: {exit_fee:.6f} U")
    log_trade(f"总手续费: {total_fee:.6f} U")
    log_trade(f"毛利润: {gross_pnl:.6f} U")
    log_trade(f"净利润: {net_pnl:.6f} U")
    log_trade(f"收益率: {pnl_pct:.4%}")
    log_trade(f"结果: {result_text}")
    log_trade(f"持仓时间: {hold_seconds} 秒")
    log_trade(f"平仓原因: {close_reason}")

    print_summary()
    maybe_trigger_auto_ai_optimizer()

    position = None
    clear_live_position_snapshot()


def live_close_position(close_reason):
    """实盘平仓 - 第二阶段：真实下单并尽量使用交易所回报数据"""
    global position

    if position is None:
        return

    if not binance_adapter.available():
        raise ValueError("实盘模式未配置币安 API")

    qty = position["qty"]
    entry_price = position["entry_price"]
    side = position["side"]
    order_side = "SELL" if side == "LONG" else "BUY"
    qty_text = format_order_qty(SYMBOL, qty)

    if float(qty_text) <= 0:
        raise ValueError("平仓数量小于等于 0，无法下单")

    log_trade(f"[实盘模式] 准备平仓 | side={order_side} | qty={qty_text} | close_reason={close_reason}")

    order = binance_adapter.new_order(
        symbol=SYMBOL,
        side=order_side,
        order_type="MARKET",
        quantity=qty_text,
        reduceOnly="true"
    )

    order_id = order.get("orderId")
    if not order_id:
        raise ValueError(f"平仓返回缺少 orderId: {order}")

    order_detail = binance_adapter.get_order(SYMBOL, order_id)
    exit_trade_rows = get_order_trades(SYMBOL, order_id)

    executed_qty = float(order_detail.get("executedQty", 0) or 0)
    avg_price = float(order_detail.get("avgPrice", 0) or 0)
    cum_quote = float(order_detail.get("cumQuote", 0) or 0)
    status = order_detail.get("status", "UNKNOWN")

    if status != "FILLED":
        raise ValueError(f"平仓市价单未完全成交，当前状态={status} | order={order_detail}")

    if executed_qty <= 0 or avg_price <= 0:
        raise ValueError(f"平仓成交数据异常 | executedQty={executed_qty} avgPrice={avg_price}")

    if executed_qty - qty > 1e-10:
        raise ValueError(f"平仓成交数量超过持仓数量 | executedQty={executed_qty} | positionQty={qty}")

    exit_time = datetime.now()
    exit_notional = cum_quote if cum_quote > 0 else executed_qty * avg_price

    trade_details = aggregate_order_trade_details(exit_trade_rows)
    exit_fee = trade_details["commission"]
    commission_asset = trade_details["commission_asset"]
    realized_pnl_from_exchange = trade_details["realized_pnl"]

    if side == "LONG":
        gross_pnl = (avg_price - entry_price) * executed_qty
    else:
        gross_pnl = (entry_price - avg_price) * executed_qty

    if exit_fee <= 0:
        taker_rate = position.get("taker_rate", TAKER_FEE_RATE)
        exit_fee = exit_notional * taker_rate
        log_error(f"未获取到真实平仓手续费，临时按费率估算 | order_id={order_id} | fee={exit_fee}")
    else:
        taker_rate = exit_fee / exit_notional if exit_notional else position.get("taker_rate", TAKER_FEE_RATE)

    total_fee = position["entry_fee"] + exit_fee
    net_pnl_by_formula = gross_pnl - total_fee

    if trade_details["commission"] > 0:
        net_pnl = realized_pnl_from_exchange - position["entry_fee"]
    else:
        net_pnl = net_pnl_by_formula

    pnl_pct = net_pnl / position["entry_notional"] if position["entry_notional"] != 0 else 0
    hold_seconds = int((exit_time - position["entry_time"]).total_seconds())

    result_text = "盈利" if net_pnl > 0 else "亏损" if net_pnl < 0 else "持平"
    direction_text = "做多" if side == "LONG" else "做空"

    decision_snapshot = position.get("decision_snapshot", {})
    long_score = decision_snapshot.get("long_score", 0)
    short_score = decision_snapshot.get("short_score", 0)
    reasons = decision_snapshot.get("reasons", [])
    reason_text = " | ".join(reasons)

    csv_row = [
        RUN_ID,
        position["trade_id"],
        position["symbol"],
        position["side"],
        position["entry_time"].strftime("%Y-%m-%d %H:%M:%S"),
        exit_time.strftime("%Y-%m-%d %H:%M:%S"),
        round(entry_price, 6),
        round(avg_price, 6),
        round(executed_qty, 6),
        round(position["notional_usdt"], 6),
        round(position["entry_notional"], 6),
        round(exit_notional, 6),
        round(position["entry_fee"], 6),
        round(exit_fee, 6),
        round(total_fee, 6),
        round(gross_pnl, 6),
        round(net_pnl, 6),
        round(pnl_pct, 6),
        hold_seconds,
        "CLOSED",
        close_reason,
        long_score,
        short_score,
        reason_text,
        "LIVE"
    ]

    ensure_parent_dir(TRADE_CSV_FILE)
    with open(TRADE_CSV_FILE, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(csv_row)

    detail_record = {
        "run_id": RUN_ID,
        "trade_id": position["trade_id"],
        "symbol": position["symbol"],
        "side": position["side"],
        "entry_time": position["entry_time"].strftime("%Y-%m-%d %H:%M:%S"),
        "exit_time": exit_time.strftime("%Y-%m-%d %H:%M:%S"),
        "entry_price": round(entry_price, 8),
        "exit_price": round(avg_price, 8),
        "qty": round(executed_qty, 8),
        "notional_usdt": round(position["notional_usdt"], 8),
        "entry_notional": round(position["entry_notional"], 8),
        "exit_notional": round(exit_notional, 8),
        "entry_fee": round(position["entry_fee"], 8),
        "exit_fee": round(exit_fee, 8),
        "total_fee": round(total_fee, 8),
        "gross_pnl": round(gross_pnl, 8),
        "net_pnl": round(net_pnl, 8),
        "net_pnl_by_formula": round(net_pnl_by_formula, 8),
        "realized_pnl_from_exchange": round(realized_pnl_from_exchange, 8),
        "pnl_pct": round(pnl_pct, 8),
        "hold_seconds": hold_seconds,
        "status": "CLOSED",
        "close_reason": close_reason,
        "signal_kline_open_time": to_text_time(position.get("signal_kline_open_time")),
        "decision_snapshot": decision_snapshot,
        "config_file": RUN_CONFIG_FILE,
        "trading_mode": "LIVE",
        "entry_order_id": position.get("entry_order_id"),
        "entry_order_status": position.get("entry_order_status"),
        "entry_raw_order": position.get("entry_raw_order"),
        "entry_trade_details": position.get("entry_trade_details", []),
        "exit_order_id": order_id,
        "exit_order_status": status,
        "exit_raw_order": order_detail,
        "exit_trade_details": exit_trade_rows,
        "commission_asset": commission_asset,
        "fee_source": "exchange_trade_details" if trade_details["commission"] > 0 else "estimated_from_rate"
    }

    append_jsonl(TRADE_DETAIL_JSONL_FILE, detail_record)
    update_stats(net_pnl, gross_pnl, total_fee)

    log_trade("========== 平仓成功 ==========")
    log_trade(f"[实盘] 交易对: {SYMBOL}")
    log_trade(f"方向: {direction_text}")
    log_trade(f"结果: {result_text}")
    log_trade(f"持仓时间: {hold_seconds} 秒")
    log_trade(f"平仓原因: {close_reason}")

    position = None
    clear_live_position_snapshot()

    print_summary()
    maybe_trigger_auto_ai_optimizer()


# ============================================================
# 十五、持仓状态输出
# ============================================================

def print_position_status():
    """
    每隔一段时间输出当前持仓情况
    """
    global position

    if position is None:
        if pending_open_decision is not None:
            log_position("当前无持仓，但本周期存在待补开仓决策，程序会自动重试开仓")
        else:
            log_position("当前无持仓")
        return

    try:
        current_price = get_current_price(SYMBOL)
        qty = position["qty"]
        side = position["side"]

        if side == "LONG":
            gross_pnl = (current_price - position["entry_price"]) * qty
        else:
            gross_pnl = (position["entry_price"] - current_price) * qty

        estimated_exit_notional = qty * current_price
        estimated_exit_fee_rate = position.get("taker_rate", TAKER_FEE_RATE)
        estimated_exit_fee = estimated_exit_notional * estimated_exit_fee_rate
        total_fee_so_far = position["entry_fee"] + estimated_exit_fee
        net_pnl = gross_pnl - total_fee_so_far

        hold_seconds = int((datetime.now() - position["entry_time"]).total_seconds())
        remain_seconds = max(0, MAX_HOLD_SECONDS - hold_seconds)

        direction_text = "做多" if side == "LONG" else "做空"
        state_text = "浮盈" if net_pnl > 0 else "浮亏" if net_pnl < 0 else "持平"

        log_position("---------- 当前持仓状态 ----------")
        log_position(f"交易对: {SYMBOL}")
        log_position(f"方向: {direction_text}")
        log_position(f"开仓价: {position['entry_price']:.6f}")
        log_position(f"当前价: {current_price:.6f}")
        log_position(f"下单金额: {position['entry_notional']:.6f} U")
        log_position(f"当前毛利润: {gross_pnl:.6f} U")
        log_position(f"预计平仓手续费: {estimated_exit_fee:.6f} U")
        log_position(f"当前总手续费(开仓真实+平仓估算): {total_fee_so_far:.6f} U")
        log_position(f"当前净利润: {net_pnl:.6f} U")
        log_position(f"当前状态: {state_text}")
        log_position(f"已持仓: {hold_seconds} 秒")
        log_position(f"剩余可持仓: {remain_seconds} 秒")

    except Exception as e:
        log_error(f"获取持仓状态失败: {e}")


# ============================================================
# 十六、检查止盈 / 止损 / 超时
# ============================================================

def check_exit_conditions():
    """
    持仓期间持续检查：
    - 止盈
    - 止损
    - 最大持仓时间
    """
    global position

    if position is None:
        return

    try:
        current_price = get_current_price(SYMBOL)
        hold_seconds = int((datetime.now() - position["entry_time"]).total_seconds())
        side = position["side"]

        if side == "LONG":
            pnl_pct = (current_price - position["entry_price"]) / position["entry_price"]
        else:
            pnl_pct = (position["entry_price"] - current_price) / position["entry_price"]

        if ENABLE_TAKE_PROFIT and pnl_pct >= TAKE_PROFIT_PCT:
            close_position(current_price, "达到止盈")
            return

        if ENABLE_STOP_LOSS and pnl_pct <= -STOP_LOSS_PCT:
            close_position(current_price, "达到止损")
            return

        if hold_seconds >= MAX_HOLD_SECONDS:
            close_position(current_price, "达到最大持仓时间")
            return

    except Exception as e:
        log_error(f"检查平仓条件失败: {e}")


# ============================================================
# 十七、交易周期执行
# ------------------------------------------------------------
# 这里修复了核心 bug：
# 1. 先判断本周期是否已经处理过
# 2. 如果处理过，直接返回，不会先平仓
# 3. 如果没处理过，才执行“平旧仓 -> 记待开仓 -> 开新仓”
#
# 并且新增补开仓逻辑：
# - 如果边界时开仓失败
# - 程序会在当前周期内继续自动重试
# ============================================================

def run_trade_cycle():
    global last_trade_kline_open_time

    if not is_in_trade_window():
        return

    try:
        df = get_klines(SYMBOL, INTERVAL, 200)
        df = add_indicators(df)

        latest_closed_kline_open_time = get_latest_closed_kline_open_time(df)

        # 先判断本周期是否已经处理过
        # 如果处理过，直接返回，不做任何事
        # 这是修复“先平仓后不开仓” bug 的关键
        if (
            last_trade_kline_open_time is not None and
            latest_closed_kline_open_time == last_trade_kline_open_time
        ):
            return

        # 先算出这一个新周期的方向决策
        decision = get_forced_trade_signal(df)

        # 先平掉旧仓
        if position is not None:
            current_price = get_current_price(SYMBOL)
            close_position(current_price, "周期结束，强制平仓")

        # 把当前周期决策暂存成 pending
        # 就算瞬间开仓失败，也还能自动补开
        set_pending_open(decision, latest_closed_kline_open_time)

        # 只有走到这里，才把本周期标记为“已处理”
        last_trade_kline_open_time = latest_closed_kline_open_time

        # 立刻尝试开新仓
        try_open_pending_position(show_retry_log=False)

    except Exception as e:
        log_error(f"执行交易周期失败: {e}")


# ============================================================
# 十八、主函数
# ============================================================

def parse_cli_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ai-optimize-now",
        action="store_true",
        help="启动后立刻触发一次手动 AI 参数分析"
    )
    return parser.parse_args()


def main():
    args = parse_cli_args()
    init_csv()
    save_run_config_snapshot()
    save_summary_snapshot()

    log_trade("程序启动")
    log_trade(
        f"交易对={SYMBOL} | 周期={INTERVAL} | 周期秒数={INTERVAL_SECONDS}秒 | "
        f"每次名义金额={NOTIONAL_USDT}U | 手续费率={TAKER_FEE_RATE} | 杠杆={LEVERAGE}倍"
    )

    if PROXIES:
        log_trade(f"当前已启用代理: {PROXY_URL}")
    else:
        log_trade("当前未启用代理")

    log_trade(f"止盈开关={ENABLE_TAKE_PROFIT} | 止盈比例={TAKE_PROFIT_PCT}")
    log_trade(f"止损开关={ENABLE_STOP_LOSS} | 止损比例={STOP_LOSS_PCT}")
    log_trade(f"参数快照文件: {RUN_CONFIG_FILE}")
    log_trade(f"汇总快照文件: {SUMMARY_JSON_FILE}")

    # 检查网络
    log_trade(
        f"AI 参数优化: enabled={AI_ENABLED} | model={AI_MODEL or '未配置'} | "
        f"auto={AI_AUTO_OPTIMIZE_ENABLED} | auto_threshold={AI_AUTO_TRIGGER_MIN_WIN_RATE}% | "
        f"auto_min_trades={AI_AUTO_TRIGGER_MIN_TRADES}"
    )

    if args.ai_optimize_now:
        run_ai_parameter_optimizer(trigger_mode="manual")

    if not test_connection():
        log_error("无法连接 Binance Futures，程序退出")
        return

    # 检查交易对是否合法
    if not check_symbol_valid(SYMBOL):
        log_error("交易对无效，程序退出，请修改 .env 的 SYMBOL")
        return

    # 启动后先对齐到下一个周期
    try:
        wait_seconds, server_dt = seconds_to_next_interval_from_server()

        log_trade(
            f"当前 Binance Futures 服务器时间={server_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}，"
            f"等待 {wait_seconds} 秒后对齐到下一个 {INTERVAL} 周期"
        )

        time.sleep(wait_seconds)

    except Exception as e:
        log_error(f"首次时间对齐失败: {e}")
        log_error("程序退出")
        return

    last_status_ts = 0

    while True:
        try:
            now = datetime.now()
            current_total_seconds = now.minute * 60 + now.second

            # 周期边界执行逻辑
            # 用一个较小的触发窗口，提高稳定性
            if current_total_seconds % INTERVAL_SECONDS < CYCLE_TRIGGER_WINDOW_SECONDS:
                run_trade_cycle()
                time.sleep(1)

            check_manual_ai_trigger_file()

            # 如果当前周期本来应该有仓位，但边界时开仓失败，则自动补开
            retry_pending_open_if_needed()

            # 每隔一段时间输出一次持仓状态
            current_ts = int(time.time())
            if current_ts - last_status_ts >= STATUS_INTERVAL_SECONDS:
                print_position_status()
                last_status_ts = current_ts

            # 持续检查止盈止损和超时
            check_exit_conditions()

            time.sleep(1)

        except KeyboardInterrupt:
            log_trade("程序被手动停止")
            print_summary()
            break

        except Exception as e:
            log_error(f"主循环异常: {e}")
            time.sleep(3)


# ============================================================
# 十九、程序入口
# ============================================================

def run_strategy_service(start_ai_optimize_now=False):
    set_strategy_state(
        running=True,
        started_at=datetime.now(),
        stopped_at=None,
        last_error="",
        last_message="starting"
    )

    try:
        init_csv()
        save_run_config_snapshot()
        save_summary_snapshot()

        log_trade("程序启动")
        log_trade(
            f"交易对={SYMBOL} | 周期={INTERVAL} | 周期秒数={INTERVAL_SECONDS} | "
            f"名义金额={NOTIONAL_USDT}U | 手续费率={TAKER_FEE_RATE} | 杠杆={LEVERAGE}"
        )

        if PROXIES:
            log_trade(f"当前代理: {PROXY_URL}")
        else:
            log_trade("当前未启用代理")

        log_trade(f"止盈开关={ENABLE_TAKE_PROFIT} | 止盈比例={TAKE_PROFIT_PCT}")
        log_trade(f"止损开关={ENABLE_STOP_LOSS} | 止损比例={STOP_LOSS_PCT}")
        log_trade(f"参数快照文件: {RUN_CONFIG_FILE}")
        log_trade(f"汇总快照文件: {SUMMARY_JSON_FILE}")
        log_trade(
            f"AI 参数优化: enabled={AI_ENABLED} | model={AI_MODEL or '未配置'} | "
            f"auto={AI_AUTO_OPTIMIZE_ENABLED} | auto_threshold={AI_AUTO_TRIGGER_MIN_WIN_RATE}% | "
            f"auto_min_trades={AI_AUTO_TRIGGER_MIN_TRADES}"
        )

        if start_ai_optimize_now:
            run_ai_parameter_optimizer(trigger_mode="manual")

        if not test_connection():
            set_strategy_state(last_error="无法连接 Binance Futures", last_message="startup_failed")
            return

        restore_live_position_if_needed()

        if not check_symbol_valid(SYMBOL):
            set_strategy_state(last_error="交易对无效", last_message="startup_failed")
            return

        try:
            wait_seconds, server_dt = seconds_to_next_interval_from_server()
            log_trade(
                f"Binance 时间={server_dt.strftime('%Y-%m-%d %H:%M:%S %Z')} | "
                f"等待 {wait_seconds} 秒后对齐到下一个 {INTERVAL} 周期"
            )
            if wait_or_stop(wait_seconds):
                set_strategy_state(last_message="stopped_before_first_cycle")
                return
        except Exception as e:
            log_error(f"首次时间对齐失败: {e}")
            set_strategy_state(last_error=str(e), last_message="startup_failed")
            return

        last_status_ts = 0
        set_strategy_state(last_message="running")

        while not strategy_stop_event.is_set():
            try:
                now = datetime.now()
                current_total_seconds = now.minute * 60 + now.second

                if current_total_seconds % INTERVAL_SECONDS < CYCLE_TRIGGER_WINDOW_SECONDS:
                    run_trade_cycle()
                    if wait_or_stop(1):
                        break

                check_manual_ai_trigger_file()
                retry_pending_open_if_needed()

                current_ts = int(time.time())
                if current_ts - last_status_ts >= STATUS_INTERVAL_SECONDS:
                    print_position_status()
                    last_status_ts = current_ts

                check_exit_conditions()

                if wait_or_stop(1):
                    break

            except KeyboardInterrupt:
                log_trade("程序被手动停止")
                print_summary()
                break

            except Exception as e:
                log_error(f"主循环异常: {e}")
                set_strategy_state(last_error=str(e), last_message="loop_error")
                if wait_or_stop(3):
                    break

    finally:
        if strategy_stop_event.is_set():
            log_trade("收到停止信号，策略线程退出")

        set_strategy_state(
            running=False,
            stopped_at=datetime.now(),
            last_message="stopped"
        )


def start_strategy_background(start_ai_optimize_now=False):
    global strategy_thread

    if strategy_thread is not None and strategy_thread.is_alive():
        return False, "already_running"

    strategy_stop_event.clear()
    strategy_thread = threading.Thread(
        target=run_strategy_service,
        kwargs={"start_ai_optimize_now": start_ai_optimize_now},
        daemon=True,
        name="strategy-runner"
    )
    strategy_thread.start()
    return True, "started"


def stop_strategy_background():
    if strategy_thread is None or not strategy_thread.is_alive():
        set_strategy_state(running=False, stopped_at=datetime.now(), last_message="already_stopped")
        return False, "already_stopped"

    strategy_stop_event.set()
    set_strategy_state(last_message="stopping")
    return True, "stopping"


def reload_env_config():
    """
    重新加载 .env 配置文件并更新全局变量
    用于在不重启整个 Python 进程的情况下应用配置更改
    """
    global SYMBOL, INTERVAL, NOTIONAL_USDT, TAKER_FEE_RATE, LEVERAGE
    global TRADE_START_TIME, TRADE_END_TIME, STATUS_INTERVAL_SECONDS
    global CYCLE_TRIGGER_WINDOW_SECONDS, OPEN_RETRY_INTERVAL_SECONDS
    global INTERVAL_SECONDS, MAX_HOLD_SECONDS, PROXIES
    global TRADING_MODE, BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_BASE_URL, binance_client

    load_dotenv(ENV_FILE_PATH, override=True)

    SYMBOL = os.getenv("SYMBOL", "BTCUSDT").upper()
    INTERVAL = os.getenv("INTERVAL", "5m").lower()
    NOTIONAL_USDT = float(os.getenv("NOTIONAL_USDT", "100"))
    TAKER_FEE_RATE = float(os.getenv("TAKER_FEE_RATE", "0.0004"))
    LEVERAGE = int(os.getenv("LEVERAGE", "5"))

    TRADE_START_TIME = os.getenv("TRADE_START_TIME", "00:00")
    TRADE_END_TIME = os.getenv("TRADE_END_TIME", "23:59")

    STATUS_INTERVAL_SECONDS = int(os.getenv("STATUS_INTERVAL_SECONDS", "60"))
    CYCLE_TRIGGER_WINDOW_SECONDS = int(os.getenv("CYCLE_TRIGGER_WINDOW_SECONDS", "5"))
    OPEN_RETRY_INTERVAL_SECONDS = int(os.getenv("OPEN_RETRY_INTERVAL_SECONDS", "5"))

    INTERVAL_SECONDS = interval_to_seconds(INTERVAL)

    max_hold_raw = os.getenv("MAX_HOLD_SECONDS", "").strip()
    if max_hold_raw:
        MAX_HOLD_SECONDS = int(max_hold_raw)
    else:
        MAX_HOLD_SECONDS = INTERVAL_SECONDS

    PROXIES = get_proxies()

    TRADING_MODE = os.getenv("TRADING_MODE", "SIMULATION").upper()
    BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
    BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()
    BINANCE_BASE_URL = os.getenv("BINANCE_BASE_URL", "https://fapi.binance.com").strip()

    if BINANCE_API_KEY and BINANCE_API_SECRET and BINANCE_BASE_URL:
        binance_client = BinanceClient(
            api_key=BINANCE_API_KEY,
            api_secret=BINANCE_API_SECRET,
            base_url=BINANCE_BASE_URL,
            proxies=PROXIES
        )
        binance_adapter.bind(binance_client)
    else:
        binance_client = None


def restart_strategy_background():
    """
    重启策略线程：重新加载配置 -> 停止线程 -> 启动线程
    """
    reload_env_config()

    if strategy_thread is not None and strategy_thread.is_alive():
        strategy_stop_event.set()
        set_strategy_state(last_message="restarting")
        strategy_thread.join(timeout=5)

    strategy_stop_event.clear()
    return start_strategy_background()


def main():
    args = parse_cli_args()
    strategy_stop_event.clear()
    run_strategy_service(start_ai_optimize_now=args.ai_optimize_now)


if __name__ == "__main__":
    main()
