# -*- coding: utf-8 -*-
"""
币安 U 本位合约 API 客户端
支持账户查询、持仓查询、手续费率查询、下单等功能
"""

import hmac
import hashlib
import time
import requests
from urllib.parse import urlencode


class BinanceClient:
    """币安 Futures API 客户端"""

    def __init__(self, api_key, api_secret, base_url, proxies=None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.proxies = proxies

    def _sign(self, params):
        """生成签名"""
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature

    def _request(self, method, endpoint, params=None, signed=False):
        """统一请求封装"""
        url = f"{self.base_url}{endpoint}"
        headers = {"X-MBX-APIKEY": self.api_key} if signed else {}

        if params is None:
            params = {}

        if signed:
            params['timestamp'] = int(time.time() * 1000)
            params['signature'] = self._sign(params)

        response = requests.request(
            method, url,
            params=params if method == 'GET' else None,
            json=params if method == 'POST' else None,
            headers=headers,
            proxies=self.proxies,
            timeout=10
        )
        response.raise_for_status()
        return response.json()

    def get_account(self):
        """查询账户信息"""
        return self._request('GET', '/fapi/v2/account', signed=True)

    def get_position_risk(self, symbol=None):
        """查询持仓风险"""
        params = {'symbol': symbol} if symbol else {}
        return self._request('GET', '/fapi/v3/positionRisk', params, signed=True)

    def get_commission_rate(self, symbol):
        """查询手续费率"""
        return self._request('GET', '/fapi/v1/commissionRate', {'symbol': symbol}, signed=True)

    def new_order(self, symbol, side, order_type, **kwargs):
        """下单"""
        params = {
            'symbol': symbol,
            'side': side,
            'type': order_type,
            **kwargs
        }
        return self._request('POST', '/fapi/v1/order', params, signed=True)

    def get_order(self, symbol, order_id):
        """查询订单"""
        params = {'symbol': symbol, 'orderId': order_id}
        return self._request('GET', '/fapi/v1/order', params, signed=True)

    def get_exchange_info(self):
        """查询交易规则"""
        return self._request('GET', '/fapi/v1/exchangeInfo')

    def cancel_order(self, symbol, order_id):
        """撤销订单"""
        params = {'symbol': symbol, 'orderId': order_id}
        return self._request('DELETE', '/fapi/v1/order', params, signed=True)
