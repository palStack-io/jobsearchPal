"""Verify adminPal's calls TO a pal (GET /api/admin/stats and any other /api/admin/* route).

adminPal's ProductClient signs v1 (no body hash, no nonce):
    message = "{METHOD}:{path}:{ts}:{staff_id}:{staff_role}", HMAC-SHA256 with the pal's secret
    X-PalStack-Sig = "{product_id}.{hexdigest}", X-PalStack-Ts, X-PalStack-Staff-Id, X-PalStack-Staff-Role
The path is the one adminPal requested (e.g. /api/admin/stats), raw, without the query string. The nightly
metrics pull signs as role "system".
"""
import hashlib
import hmac
import time

MAX_SKEW = 120


def _get(headers, name):
    try:
        return headers.get(name) or headers.get(name.lower())
    except AttributeError:
        return None


def verify_manager(config, method, path, headers, *, roles=None, max_skew=MAX_SKEW, now=None):
    """{'staff_id', 'role'} when adminPal signed this request for this pal, else None. Never raises.
    roles: allowed X-PalStack-Staff-Role values (None = any role adminPal signs)."""
    sig_header = _get(headers, 'X-PalStack-Sig') or ''
    ts = _get(headers, 'X-PalStack-Ts') or ''
    staff_id = _get(headers, 'X-PalStack-Staff-Id') or ''
    role = _get(headers, 'X-PalStack-Staff-Role') or ''
    if not (sig_header and ts and staff_id and role) or '.' not in sig_header:
        return None
    product, _, sig = sig_header.rpartition('.')
    if product != config.product_id:
        return None
    try:
        if abs((now if now is not None else time.time()) - int(ts)) > max_skew:
            return None
    except ValueError:
        return None
    if roles is not None and role not in roles:
        return None
    msg = f'{method.upper()}:{path.split("?", 1)[0]}:{ts}:{staff_id}:{role}'.encode()
    want = hmac.new(config.secret.encode(), msg, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, sig):
        return None
    return {'staff_id': staff_id, 'role': role}
