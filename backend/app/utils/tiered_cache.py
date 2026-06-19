"""分层缓存 — 两级缓存策略"""
import threading
import time
from typing import Any, Optional, Dict
from collections import OrderedDict
from app.config import settings


class TieredCache:
    """
    两级分层缓存:
    L1 实时:  60s    (实时盘口、分钟K线、资金流)
    L2 日内:  600s   (K线、成交量分析、估值指标、搜索)
    """

    TIERS = {
        1: {"ttl": 60,   "label": "L1实时"},
        2: {"ttl": 600,  "label": "L2日内"},
    }

    def __init__(self, max_size: int = 2000):
        self.max_size = max_size
        self._store: OrderedDict = OrderedDict()
        self._meta: Dict[str, tuple] = {}  # key -> (value, tier, timestamp)
        self._hits: int = 0
        self._misses: int = 0
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key not in self._meta:
                self._misses += 1
                return None
            value, tier, ts = self._meta[key]
            ttl = self.TIERS.get(tier, {}).get("ttl", 300)
            if time.time() - ts > ttl:
                del self._meta[key]
                if key in self._store:
                    del self._store[key]
                self._misses += 1
                return None
            self._store.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: str, value: Any, tier: int = 2):
        # map legacy tier 3/4 callers to tier 2
        if tier > 2:
            tier = 2
        with self._lock:
            if len(self._store) >= self.max_size:
                oldest = next(iter(self._store))
                del self._store[oldest]
                if oldest in self._meta:
                    del self._meta[oldest]

            self._store[key] = value
            self._meta[key] = (value, tier, time.time())
            self._store.move_to_end(key)

    def invalidate(self, prefix: str = None):
        """按前缀清除缓存"""
        if prefix is None:
            self._store.clear()
            self._meta.clear()
            return
        keys = [k for k in self._store if k.startswith(prefix)]
        for k in keys:
            del self._store[k]
            del self._meta[k]

    def stats(self) -> dict:
        now = time.time()
        tiers_count = {1: 0, 2: 0}
        for key, (_, tier, ts) in self._meta.items():
            ttl = self.TIERS.get(tier, {}).get("ttl", 300)
            if now - ts <= ttl:
                tiers_count[tier] = tiers_count.get(tier, 0) + 1
        return {
            "total": len(self._store),
            "by_tier": tiers_count,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hit_rate(),
        }

    def _hit_rate(self) -> float:
        total = self._hits + self._misses
        if total == 0:
            return 0
        return self._hits / total

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

tiered_cache = TieredCache()
