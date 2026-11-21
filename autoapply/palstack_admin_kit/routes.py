"""The 'Report a problem' request, framework-free. Both adapters call handle_report()."""
import base64
import binascii
import logging
from dataclasses import dataclass

from .client import AdminClient
from .errors import ErrorReporter
from .reports import ReportError, build_report


@dataclass
class Kit:
    """What init_flask / the FastAPI helpers hand back to the pal."""
    client: AdminClient
    errors: ErrorReporter
    capture_errors: bool = True


MAX_BODY_BYTES = 4 * 1024 * 1024
_NO_USER = {'', 'none', 'null', 'undefined', 'anonymous'}
_PASS_THROUGH = {413, 422, 429}  # things the user can act on; anything else is our fault


def handle_report(body, reporter_ref, client, require_user=True):
    """Return (status, json). The reporter is whoever the pal says is signed in; never the body."""
    if reporter_ref is not None and str(reporter_ref).strip().lower() in _NO_USER:
        reporter_ref = None
    if require_user and not reporter_ref:
        return 401, {'error': 'Please sign in to send a report.'}
    if not isinstance(body, dict):
        return 400, {'error': 'Send the report as a JSON object.'}
    attachment = None
    raw = body.get('attachment')
    if raw:
        if not isinstance(raw, dict) or not isinstance(raw.get('data_b64'), str):
            return 422, {'error': 'The screenshot could not be read.'}
        if len(raw['data_b64']) > 3 * 1024 * 1024:
            return 413, {'error': 'The screenshot is larger than 2 MB.'}
        try:
            attachment = (base64.b64decode(raw['data_b64'], validate=True), raw.get('content_type'))
        except (binascii.Error, ValueError):
            return 422, {'error': 'The screenshot could not be read.'}
    try:
        payload = build_report(body.get('text'), consent=body.get('consent'), platform=body.get('platform'),
                               app_version=body.get('app_version'), route=body.get('route'),
                               reporter_ref=reporter_ref, attachment=attachment)
    except ReportError as err:
        return err.status, {'error': err.message}
    result = client.send_report(payload)
    if result.ok:
        return 202, {'ref': result.ref}
    if result.status in _PASS_THROUGH:
        return result.status, {'error': result.error}
    # adminPal refused or failed (wrong secret, clock, outage): never a 401/403 the app would read as
    # "signed out", and never adminPal's own words.
    logging.getLogger('palstack_admin_kit').warning('report not delivered: adminPal answered %s', result.status)
    return 503, {'error': 'We could not send your report right now. Please try again in a few minutes.'}
