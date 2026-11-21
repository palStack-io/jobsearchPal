"""palstack-admin-kit: what every palStack pal needs to talk to adminPal.

Vendored into each pal with `palkit vendor <backend dir>`; see README.md.
"""
from pathlib import Path

__version__ = (Path(__file__).with_name('VERSION').read_text().strip())
