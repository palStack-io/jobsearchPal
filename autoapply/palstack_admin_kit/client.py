"""Signed calls from a pal to adminPal. Never raises to the caller."""
import json
from dataclasses import dataclass
from typing import Optional

import requests

from .signing import sign_v2


@dataclass(frozen=True)
class ReportResult:
    ok: bool
    status: int
    ref: Optional[str]
    error: Optional[str]
    data: Optional[dict] = None  # adminPal's JSON answer, when there is one


class AdminClient:
    def __init__(self, config, session=None):
        self.config = config
        self.session = session or requests.Session()

    # (connect, read): a phone client gives up after ~10 s; we answer before it does.
    TIMEOUT = (3, 5)

    def _post(self, path: str, payload: dict):
        body = json.dumps(payload, separators=(',', ':')).encode()
        headers = sign_v2(self.config, 'POST', path, body)
        return self.session.post(self.config.admin_url + path, data=body, headers=headers,
                                 timeout=self.TIMEOUT)

    def _result(self, path, payload, ok_status) -> ReportResult:
        try:
            resp = self._post(path, payload)
        except requests.RequestException:
            return ReportResult(False, 503, None, 'adminPal is not reachable')
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if resp.status_code in ok_status:
            return ReportResult(True, resp.status_code, data.get('ref'), None, data)
        return ReportResult(False, resp.status_code, None, data.get('error') or f'adminPal answered {resp.status_code}')

    def send_report(self, payload: dict) -> ReportResult:
        return self._result(f'/api/internal/issues/report/{self.config.product_id}', payload, (200, 202))

    def send_server_error(self, payload: dict) -> bool:
        return self._result(f'/api/internal/issues/report/{self.config.product_id}', payload, (200, 202)).ok

    def selftest(self) -> ReportResult:
        return self._result(f'/api/internal/issues/selftest/{self.config.product_id}', {}, (200,))
