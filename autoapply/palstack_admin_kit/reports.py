"""Building a 'Report a problem' payload, checked with the same limits adminPal applies."""
import base64

MAX_TEXT = 5000
MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024
ATTACHMENT_TYPES = ('image/png', 'image/jpeg', 'image/webp')
PLATFORMS = ('web', 'ios', 'android', 'api')


class ReportError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def build_report(text, *, consent, platform, app_version=None, route=None, reporter_ref=None,
                 attachment=None) -> dict:
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ReportError(422, f'Please describe the problem in up to {MAX_TEXT} characters.')
    if consent is not True:
        raise ReportError(422, 'The report can only be sent with your agreement.')
    if platform not in PLATFORMS:
        raise ReportError(422, 'platform must be web, ios, android or api')
    payload = {'source': 'in_app', 'text': text.strip(), 'consent': True, 'platform': platform}
    for key, value, limit in (('app_version', app_version, 64), ('route', route, 300),
                              ('reporter_ref', reporter_ref, 128)):
        if value is not None:
            payload[key] = str(value)[:limit]
    if attachment is not None:
        data, content_type = attachment
        if content_type not in ATTACHMENT_TYPES:
            raise ReportError(422, 'Screenshots must be PNG, JPEG or WebP images.')
        if len(data) > MAX_ATTACHMENT_BYTES:
            raise ReportError(413, 'The screenshot is larger than 2 MB.')
        ext = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'}[content_type]
        payload['attachment'] = {'filename': f'screenshot.{ext}', 'content_type': content_type,
                                 'data_b64': base64.b64encode(data).decode()}
    return payload
