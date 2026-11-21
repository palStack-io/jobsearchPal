"""palkit: vendor the kit into a pal, or check a pal's wiring against adminPal.

    palkit vendor <backend dir>   copy palstack_admin_kit/ into it (pinned VERSION)
    palkit selftest               uses PALKIT_URL, PALKIT_PRODUCT, PALKIT_SECRET
    palkit version
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

from . import __version__
from .client import AdminClient
from .config import KitConfig

PKG = Path(__file__).resolve().parent


def _parse(v):
    return tuple(int(x) for x in v.strip().split('.'))


def vendor(dest: str) -> int:
    target = Path(dest) / 'palstack_admin_kit'
    existing = target / 'VERSION'
    if existing.exists() and _parse(existing.read_text()) > _parse(__version__):
        print(f'palkit: {target} has {existing.read_text().strip()}, newer than {__version__}; not replacing',
              file=sys.stderr)
        return 1
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(PKG, target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'tests'))
    print(f'palkit: vendored palstack-admin-kit {__version__} into {target}')
    return 0


def selftest() -> int:
    missing = [v for v in ('PALKIT_URL', 'PALKIT_PRODUCT', 'PALKIT_SECRET') if not os.environ.get(v)]
    if missing:
        print(f'palkit: set {", ".join(missing)}', file=sys.stderr)
        return 2
    cfg = KitConfig(admin_url=os.environ['PALKIT_URL'].rstrip('/'), product_id=os.environ['PALKIT_PRODUCT'],
                    secret=os.environ['PALKIT_SECRET'])
    result = AdminClient(cfg).selftest()
    if not result.ok:
        checks = [(False, f'signed connection ({result.status}: {result.error})')]
    else:
        body = result.data or {}
        prefix = body.get('issue_prefix')
        checks = [
            (True, 'signed connection'),
            (bool(prefix), f'issue prefix {prefix}' if prefix else 'issue prefix (not set in adminPal)'),
            (bool(body.get('stats_feature')),
             'stats feature' if body.get('stats_feature') else 'stats feature (add "stats" to the pal in adminPal)'),
        ]
    for ok, label in checks:
        print(f'{"PASS" if ok else "FAIL"} {label}')
    return 0 if all(ok for ok, _ in checks) else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='palkit')
    sub = parser.add_subparsers(dest='cmd', required=True)
    v = sub.add_parser('vendor')
    v.add_argument('dest')
    sub.add_parser('selftest')
    sub.add_parser('version')
    args = parser.parse_args(argv)
    if args.cmd == 'vendor':
        return vendor(args.dest)
    if args.cmd == 'selftest':
        return selftest()
    print(__version__)
    return 0


if __name__ == '__main__':
    sys.exit(main())
