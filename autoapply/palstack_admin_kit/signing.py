"""adminPal v2 request signing (must match adminPal's verify_v2).

message = "{METHOD}:{path}:{ts}:{sha256(body)}:{nonce}"; the path is signed without its query string.
"""
import hashlib
import hmac
import time
import uuid


def sign_v2(config, method: str, path: str, body: bytes = b'') -> dict:
    path = path.split('?', 1)[0]
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex
    body_hash = hashlib.sha256(body or b'').hexdigest()
    msg = f'{method.upper()}:{path}:{ts}:{body_hash}:{nonce}'
    sig = hmac.new(config.secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return {'X-PalStack-Sig': f'{config.product_id}.{sig}', 'X-PalStack-Ts': ts,
            'X-PalStack-Nonce': nonce, 'Content-Type': 'application/json'}
