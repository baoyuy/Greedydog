# -*- coding: utf-8 -*-
"""
Binance 访问适配层
统一收口对 BinanceClient 的调用，后续可在这里扩展用户数据流、补偿查询和对账策略。
"""


class BinanceAdapter:
    def __init__(self, client=None):
        self.client = client

    def bind(self, client):
        self.client = client
        return self

    def available(self):
        return self.client is not None

    def require_client(self):
        if not self.client:
            raise ValueError("实盘模式未配置币安 API")
        return self.client

    def get_account(self):
        return self.require_client().get_account()

    def get_position_risk(self, symbol=None):
        return self.require_client().get_position_risk(symbol=symbol)

    def get_commission_rate(self, symbol):
        return self.require_client().get_commission_rate(symbol)

    def new_order(self, symbol, side, order_type, **kwargs):
        return self.require_client().new_order(symbol=symbol, side=side, order_type=order_type, **kwargs)

    def get_order(self, symbol, order_id):
        return self.require_client().get_order(symbol=symbol, order_id=order_id)

    def get_user_trades(self, symbol, order_id=None, limit=100):
        return self.require_client().get_user_trades(symbol=symbol, order_id=order_id, limit=limit)

    def cancel_order(self, symbol, order_id):
        return self.require_client().cancel_order(symbol=symbol, order_id=order_id)

    def get_exchange_info(self):
        return self.require_client().get_exchange_info()

    def get_rate_limit_snapshot(self):
        client = self.require_client()
        return client.get_rate_limit_snapshot()

    def get_cache_ttl(self, method, endpoint):
        client = self.require_client()
        return client.get_cache_ttl(method, endpoint)
