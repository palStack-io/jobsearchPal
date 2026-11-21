"""Flask wiring (propertyPal, finPal).

    kit = init_flask(app, KitConfig.from_env(...), user_ref=current_user_id_or_none)

user_ref must return the signed-in user's id, or None. A catch-all errorhandler(Exception) in the
pal would stop got_request_exception from firing (and so stop error capture).

Adds POST {url_prefix}/support/report and captures unhandled exceptions through Flask's
got_request_exception signal, so the pal's own error handlers and responses are unchanged.
"""
from flask import got_request_exception, jsonify, request

from .client import AdminClient
from .errors import ErrorReporter
from .routes import MAX_BODY_BYTES, Kit, handle_report


def init_flask(app, config, *, user_ref, url_prefix='/api', require_user=True, app_root=None,
               app_version=None, capture_errors=True) -> Kit:
    """capture_errors=False wires only the report route (finPal: its promise is no crash reporter)."""
    client = AdminClient(config)
    kit = Kit(client=client, errors=ErrorReporter(client, app_version=app_version,
                                                   app_root=app_root or app.root_path),
              capture_errors=capture_errors)

    def support_report():
        if (request.content_length or 0) > MAX_BODY_BYTES:
            return jsonify({'error': 'The report is too large.'}), 413
        try:
            ref = user_ref()
        except Exception:  # noqa: BLE001 — an auth helper that raises means "not signed in"
            ref = None
        status, body = handle_report(request.get_json(silent=True), str(ref) if ref else None,
                                     kit.client, require_user)
        return jsonify(body), status

    app.add_url_rule(f'{url_prefix.rstrip("/")}/support/report', 'palkit_support_report',
                     support_report, methods=['POST'])

    def _on_exception(sender, exception, **extra):
        kit.errors.capture(exception, route=request.path if request else None)

    if capture_errors:
        got_request_exception.connect(_on_exception, app, weak=False)
    return kit
