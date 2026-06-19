"""三层缓存 — 热(内存30s)/温(内存300s)/冷(内存600s) + 写穿"""
import time
import threading
from collections import OrderedDict
from typing import Any, Optional, Dict, Tuple


class TieredCache:
    """分层缓存，三种TTL等级"""

    TIERS = {
        1: {"ttl": 30, "label": "L1热"},
        2: {"ttl": 300, "label": "L2温"},
        3: {"ttl": 600, "label": "L3冷"},
    }

    def __init__(self, max_size: int = 2000):
        self.max_size = max_size
        self._store: OrderedDict = OrderedDict()
        self._meta: Dict[str, Tuple[Any, int, float]] = {}
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
                self._store.pop(key, None)
                self._misses += 1
                return None
            self._store.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: str, value: Any, tier: int = 2):
        with self._lock:
            if tier > 2:
                tier = 2
            self._meta[key] = (value, tier, time.time())
            self._store[key] = value
            self._store.move_to_end(key)
            if len(self._store) > self.max_size:
                self._store.popitem(last=False)
                oldest = next(iter(self._store))
                self._meta.pop(oldest, None)

    def invalidate(self, prefix: str = None):
        with self._lock:
            if prefix is None:
                self._store.clear()
                self._meta.clear()
                return
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                self._store.pop(k, None)
                del self._meta[k]

    def stats(self) -> dict:
        now = time.time()
        tiers_count = {1: 0, 2: 0}
        active_items = 0
        for key, (value, tier, ts) in list(self._meta.items()):
            ttl = self.TIERS.get(tier, {}).get("ttl", 300)
            if now - ts <= ttl:
                tiers_count[tier] = tiers_count.get(tier, 0) + 1
                active_items += 1
        return {
            "total_items": len(self._store),
            "active_items": active_items,
            "max_size": self.max_size,
            "hit_rate": self._hit_rate(),
            "hits": self._hits,
            "misses": self._misses,
            "tiers": tiers_count,
        }

    def _hit_rate(self) -> float:
        total = self._hits + self._misses
        if total == 0:
            return 0.0
        return self._hits / total

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None


# 全局单例
tiered_cache = TieredCache()
