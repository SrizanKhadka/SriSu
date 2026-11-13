"""Disposable performance-cache tools, deliberately unsuitable for authorization."""
import hashlib
import json
import logging
from uuid import uuid4

from django.db import transaction

logger = logging.getLogger("srisu.cache")


def cache_key(namespace, *, version, scope, resource):
    if not namespace.replace("-", "").isalnum() or not scope:
        raise ValueError("An explicit namespace and scope are required")
    digest = hashlib.sha256(json.dumps([scope, resource], sort_keys=True).encode()).hexdigest()
    return f"srisu:{namespace}:v{version}:{digest}"


class PerformanceCache:
    """No cached permissions. Call only after authorization or for public metadata.

    A generation key prevents a slow old loader from repopulating the current
    generation after invalidation. Outage means a fresh authoritative read.
    """
    def __init__(self, backend, namespace, *, version=1, ttl=300):
        self.backend, self.namespace, self.version, self.ttl = backend, namespace, version, ttl

    def generation_key(self, scope):
        return cache_key(self.namespace, version=self.version, scope=scope, resource="generation")

    def read(self, *, scope, resource, loader):
        try:
            generation = self.backend.get_or_set(self.generation_key(scope), str(uuid4()), timeout=None)
            key = cache_key(self.namespace, version=self.version, scope=scope, resource=[generation, resource])
            cached = self.backend.get(key)
            if cached is not None:
                logger.debug("cache_hit", extra={"namespace": self.namespace})
                return cached
        except Exception:
            logger.warning("cache_read_unavailable", extra={"namespace": self.namespace})
            return loader()
        logger.debug("cache_miss", extra={"namespace": self.namespace})
        value = loader()
        try:
            self.backend.set(key, value, timeout=self.ttl)
        except Exception:
            logger.warning("cache_write_unavailable", extra={"namespace": self.namespace})
        return value

    def invalidate_on_commit(self, *, scope):
        def invalidate():
            try:
                self.backend.set(self.generation_key(scope), str(uuid4()), timeout=None)
            except Exception:
                logger.warning("cache_invalidation_unavailable", extra={"namespace": self.namespace})
        transaction.on_commit(invalidate)
