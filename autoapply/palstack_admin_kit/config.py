"""Configuration by value: each pal keeps its own env var names and passes them in."""
import os
from dataclasses import dataclass, field


class KitConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class KitConfig:
    admin_url: str
    product_id: str
    secret: str = field(repr=False)  # never in logs or reprs
    timeout: float = 10.0

    @classmethod
    def from_env(cls, url_var='PALSTACK_MANAGER_URL', product_var='PALSTACK_PRODUCT_ID',
                 secret_var='PALSTACK_MANAGER_SECRET', product_default=None, timeout=10.0):
        values = {}
        for key, var, default in (('admin_url', url_var, None), ('product_id', product_var, product_default),
                                  ('secret', secret_var, None)):
            value = os.environ.get(var) or default
            if not value:
                raise KitConfigError(f'palstack-admin-kit: {var} is not set')
            values[key] = value
        return cls(admin_url=values['admin_url'].rstrip('/'), product_id=values['product_id'],
                   secret=values['secret'], timeout=timeout)
