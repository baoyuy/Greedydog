# -*- coding: utf-8 -*-
"""
轻量级进程内状态事件总线
用于把策略状态变化推送给 Dashboard，而不是让前端高频轮询驱动后端聚合。
"""

import json
import threading
import time


class StateBus:
    """线程安全的轻量级状态事件总线"""

    def __init__(self):
        self._condition = threading.Condition()
        self._sequence = 0
        self._last_event = self._build_event("init", {"message": "state_bus_ready"})

    def _build_event(self, event_type, data=None):
        return {
            "seq": self._sequence,
            "type": event_type,
            "ts": time.time(),
            "data": data or {}
        }

    def publish(self, event_type, data=None):
        with self._condition:
            self._sequence += 1
            self._last_event = self._build_event(event_type, data)
            self._condition.notify_all()
            return self._last_event

    def snapshot(self):
        with self._condition:
            return dict(self._last_event)

    def wait_for_event(self, last_seq=0, timeout=15.0):
        def changed():
            return self._last_event["seq"] > last_seq

        with self._condition:
            if not changed():
                self._condition.wait(timeout=timeout)
            return dict(self._last_event)

    def stream_sse(self, snapshot_provider, last_seq=0, heartbeat_seconds=15.0):
        current_seq = last_seq
        while True:
            event = self.wait_for_event(last_seq=current_seq, timeout=heartbeat_seconds)
            if event["seq"] > current_seq:
                current_seq = event["seq"]
                payload = {
                    "event": event,
                    "snapshot": snapshot_provider()
                }
                yield "event: state\\n"
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\\n\\n"
            else:
                heartbeat = {
                    "event": {
                        "seq": current_seq,
                        "type": "heartbeat",
                        "ts": time.time(),
                        "data": {}
                    }
                }
                yield "event: heartbeat\\n"
                yield f"data: {json.dumps(heartbeat, ensure_ascii=False)}\\n\\n"


state_bus = StateBus()
