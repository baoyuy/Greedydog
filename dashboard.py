import argparse
import os

from flask import Flask, jsonify, render_template, request

import man


app = Flask(__name__)


def env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ["1", "true", "yes", "y", "on"]


def make_json(ok, message="", **extra):
    payload = {"ok": ok, "message": message}
    payload.update(extra)
    return jsonify(payload)


@app.get("/")
def index():
    return render_template("logs.html")


@app.get("/logs")
def logs_page():
    return render_template("logs.html")


@app.get("/config")
def config_page():
    return render_template("config.html")


@app.get("/api/state")
def api_state():
    return make_json(True, data=man.get_dashboard_snapshot())


@app.post("/api/start")
def api_start():
    ok, status = man.start_strategy_background()
    return make_json(ok, message=status, data=man.get_dashboard_snapshot())


@app.post("/api/stop")
def api_stop():
    ok, status = man.stop_strategy_background()
    return make_json(ok, message=status, data=man.get_dashboard_snapshot())


@app.post("/api/restart")
def api_restart():
    ok, status = man.restart_strategy_background()
    return make_json(ok, message=status, data=man.get_dashboard_snapshot())


@app.post("/api/ai/analyze")
def api_ai_analyze():
    proposal = man.create_ai_parameter_proposal(trigger_mode="manual")
    pending = man.get_pending_ai_suggestion_snapshot()
    if proposal is None and pending is None:
        return make_json(False, message="no_change_or_failed", data=man.get_dashboard_snapshot())
    return make_json(True, message="proposal_ready", data=man.get_dashboard_snapshot())


@app.post("/api/ai/proposal/update")
def api_ai_proposal_update():
    payload = request.get_json(silent=True) or {}
    try:
        proposal = man.update_pending_ai_proposal_edits(payload)
        return make_json(True, message="proposal_updated", proposal=proposal, data=man.get_dashboard_snapshot())
    except Exception as e:
        return make_json(False, message=str(e), data=man.get_dashboard_snapshot())


@app.post("/api/ai/apply")
def api_ai_apply():
    ok = man.approve_pending_ai_suggestion()
    if ok:
        status = man.get_runtime_status()
        msg = status.get("note", "参数已应用") if status else "参数已应用到当前进程和 .env 文件"
    else:
        msg = "没有待审批的 AI 建议"
    return make_json(ok, message=msg, data=man.get_dashboard_snapshot())


@app.post("/api/ai/reject")
def api_ai_reject():
    ok = man.reject_pending_ai_suggestion()
    return make_json(ok, message="rejected" if ok else "no_pending_proposal", data=man.get_dashboard_snapshot())


@app.get("/api/config/schema")
def api_config_schema():
    return make_json(True, schema=man.get_config_schema_snapshot())


@app.get("/api/config/current")
def api_config_current():
    return make_json(True, current=man.get_current_config_snapshot())


@app.post("/api/config/update")
def api_config_update():
    payload = request.get_json(silent=True) or {}
    try:
        status = man.apply_general_config_updates(payload)
        message = status.get("note", "配置已保存") if status else "配置已保存"
        return make_json(True, message=message, data=man.get_dashboard_snapshot(), current=man.get_current_config_snapshot())
    except Exception as e:
        return make_json(False, message=str(e), data=man.get_dashboard_snapshot(), current=man.get_current_config_snapshot())


@app.post("/api/ai/config")
def api_ai_config():
    payload = request.get_json(silent=True) or {}
    api_key = str(payload.get("AI_API_KEY", "")).strip()
    updates = {
        "AI_ENABLED": payload.get("AI_ENABLED", False),
        "AI_BASE_URL": payload.get("AI_BASE_URL", ""),
        "AI_API_KEY": api_key if api_key else man.AI_API_KEY,
        "AI_MODEL": payload.get("AI_MODEL", ""),
        "AI_TIMEOUT_SECONDS": payload.get("AI_TIMEOUT_SECONDS", 60),
        "AI_AUTO_OPTIMIZE_ENABLED": payload.get("AI_AUTO_OPTIMIZE_ENABLED", False),
        "AI_AUTO_TRIGGER_MIN_WIN_RATE": payload.get("AI_AUTO_TRIGGER_MIN_WIN_RATE", 35),
        "AI_AUTO_TRIGGER_MIN_TRADES": payload.get("AI_AUTO_TRIGGER_MIN_TRADES", 20),
        "AI_REQUIRE_CONFIRM_ON_MANUAL": payload.get("AI_REQUIRE_CONFIRM_ON_MANUAL", True),
        "AI_REQUIRE_CONFIRM_ON_AUTO": payload.get("AI_REQUIRE_CONFIRM_ON_AUTO", True),
    }
    man.apply_ai_runtime_config_updates(updates)
    status = man.get_runtime_status()
    message = status.get("note", "AI 配置已保存并写入当前进程") if status else "AI 配置已保存并写入当前进程"
    return make_json(True, message=message, data=man.get_dashboard_snapshot())


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("DASHBOARD_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("DASHBOARD_PORT", "8080")))
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--auto-start-strategy",
        action="store_true",
        default=env_bool("DASHBOARD_AUTO_START_STRATEGY", False),
        help="启动 dashboard 时自动启动策略线程"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.auto_start_strategy:
        man.start_strategy_background()
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
