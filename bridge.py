import threading
import json
import os
import datetime

from flask import Flask, request, jsonify

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "predict_data.jsonl")


def make_bridge(state: dict):
    app = Flask("predict_bridge")

    @app.after_request
    def cors(resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
        return resp

    @app.route("/api/prediction", methods=["POST", "OPTIONS"])
    def recv():
        if request.method == "OPTIONS":
            return ("", 204)
        data = request.get_json(silent=True) or {}
        data["received_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sym = data.get("sym", "BTC")
        if sym == "ETH":
            state["predict_eth"] = data
        else:
            state["predict"] = data
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass
        return jsonify({"ok": True})

    @app.route("/api/status", methods=["GET"])
    def status():
        return jsonify({"listening": True, "last_received": bool(state.get("predict"))})

    def start():
        t = threading.Thread(
            target=lambda: app.run(host="127.0.0.1", port=8051, debug=False, use_reloader=False),
            daemon=True,
        )
        t.start()

    return start