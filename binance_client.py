# -*- coding: utf-8 -*-
"""
币安 U 本位合约 API 客户端
支持账户查询、持仓查询、手续费率查询、下单等功能
"""

import hmac
import hashlib
import time
import threading
import requests
from urllib.parse import urlencode


class BinanceClient:
    """币安 Futures API 客户端"""

    def __init__(self, api_key, api_secret, base_url, proxies=None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.proxies = proxies
        self._rate_limit_lock = threading.Lock()
        self._last_request_at_by_key = {}
        self._cache_lock = threading.Lock()
        self._response_cache = {}
        self._min_interval_seconds = {
            ('GET', '/fapi/v2/account'): 5.0,
            ('GET', '/fapi/v3/positionRisk'): 2.0,
            ('GET', '/fapi/v1/commissionRate'): 30.0,
            ('GET', '/fapi/v1/exchangeInfo'): 600.0,
            ('GET', '/fapi/v1/order'): 0.2,
            ('GET', '/fapi/v1/userTrades'): 0.2,
            ('POST', '/fapi/v1/order'): 0.2,
            ('DELETE', '/fapi/v1/order'): 0.2,
        }
        self._cache_ttl_seconds = {
            ('GET', '/fapi/v2/account'): 5.0,
            ('GET', '/fapi/v3/positionRisk'): 2.0,
            ('GET', '/fapi/v1/commissionRate'): 300.0,
            ('GET', '/fapi/v1/exchangeInfo'): 3600.0,
        }
        self._api_labels = {
            ('GET', '/fapi/v2/account'): 'account',
            ('GET', '/fapi/v3/positionRisk'): 'position_risk',
            ('GET', '/fapi/v1/commissionRate'): 'commission_rate',
            ('GET', '/fapi/v1/exchangeInfo'): 'exchange_info',
            ('GET', '/fapi/v1/order'): 'order_query',
            ('GET', '/fapi/v1/userTrades'): 'user_trades',
            ('POST', '/fapi/v1/order'): 'new_order',
            ('DELETE', '/fapi/v1/order'): 'cancel_order',
        }

    def _sign(self, params):
        """生成签名"""
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature

    def _normalize_params(self, params):
        if params is None:
            return {}
        return dict(params)

    def _build_cache_key(self, method, endpoint, params):
        normalized = tuple(sorted((k, str(v)) for k, v in (params or {}).items()))
        return method.upper(), endpoint, normalized

    def get_cache_ttl(self, method, endpoint):
        return self._cache_ttl_seconds.get((method.upper(), endpoint), 0)

    def get_min_interval(self, method, endpoint):
        return self._min_interval_seconds.get((method.upper(), endpoint), 0)

    def get_rate_limit_snapshot(self):
        snapshot = {}
        for key, min_interval in self._min_interval_seconds.items():
            method, endpoint = key
            label = self._api_labels.get(key, f"{method} {endpoint}")
            snapshot[label] = {
                "method": method,
                "endpoint": endpoint,
                "min_interval_seconds": min_interval,
                "cache_ttl_seconds": self.get_cache_ttl(method, endpoint)
            }
        return snapshot

    def _get_cached_response(self, method, endpoint, params):
        ttl = self._cache_ttl_seconds.get((method.upper(), endpoint))
        if not ttl:
            return None

        cache_key = self._build_cache_key(method, endpoint, params)
        now_ts = time.time()
        with self._cache_lock:
            cached = self._response_cache.get(cache_key)
            if not cached:
                return None
            cached_at, data = cached
            if now_ts - cached_at > ttl:
                self._response_cache.pop(cache_key, None)
                return None
            return data

    def _set_cached_response(self, method, endpoint, params, data):
        ttl = self._cache_ttl_seconds.get((method.upper(), endpoint))
        if not ttl:
            return

        cache_key = self._build_cache_key(method, endpoint, params)
        with self._cache_lock:
            self._response_cache[cache_key] = (time.time(), data)

    def _wait_rate_limit(self, method, endpoint):
        min_interval = self._min_interval_seconds.get((method.upper(), endpoint), 0)
        if min_interval <= 0:
            return

        key = (method.upper(), endpoint)
        with self._rate_limit_lock:
            now_ts = time.time()
            last_ts = self._last_request_at_by_key.get(key, 0)
            wait_seconds = min_interval - (now_ts - last_ts)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            self._last_request_at_by_key[key] = time.time()

    def _request(self, method, endpoint, params=None, signed=False):
        """统一请求封装"""
        method = method.upper()
        params = self._normalize_params(params)

        cached = self._get_cached_response(method, endpoint, params)
        if cached is not None:
            return cached

        self._wait_rate_limit(method, endpoint)

        url = f"{self.base_url}{endpoint}"
        headers = {"X-MBX-APIKEY": self.api_key} if signed else {}
        request_params_for_cache = dict(params)

        if signed:
            params['timestamp'] = int(time.time() * 1000)
            params['signature'] = self._sign(params)

        try:
            response = requests.request(
                method, url,
                params=params if method in ['GET', 'DELETE'] else None,
                json=params if method == 'POST' else None,
                headers=headers,
                proxies=self.proxies,
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            self._set_cached_response(method, endpoint, request_params_for_cache, data)
            return data
        except Exception as e:
            api_label = self._api_labels.get((method, endpoint), f"{method} {endpoint}")
            raise Exception(f"Binance API 请求失败 | api={api_label} | endpoint={endpoint} | params={request_params_for_cache} | error={e}")

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

    def get_user_trades(self, symbol, order_id=None, limit=100):
        """查询用户成交明细"""
        params = {'symbol': symbol, 'limit': limit}
        if order_id is not None:
            params['orderId'] = order_id
        return self._request('GET', '/fapi/v1/userTrades', params, signed=True)

    def get_exchange_info(self):
        """查询交易规则"""
        return self._request('GET', '/fapi/v1/exchangeInfo')

    def cancel_order(self, symbol, order_id):
        """撤销订单"""
        params = {'symbol': symbol, 'orderId': order_id}
        return self._request('DELETE', '/fapi/v1/order', params, signed=True)
