"""The finish box is opt-in for Docker installs, and never reachable from outside the machine."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text())


def test_finish_is_opt_in():
    c = _load("docker-compose.yml")
    svc = c["services"]
    for name in ("finish", "finish-proxy"):
        assert svc[name]["profiles"] == ["finish"], name
        assert "ports" not in svc[name], name
    assert c["networks"]["finish-inner"]["internal"] is True
    assert svc["finish"]["networks"] == ["finish-inner"]
    assert "finish-inner" in svc["web"]["networks"]
    assert "finish-inner" not in (svc["scheduler"].get("networks") or [])


def test_plain_up_starts_only_the_app():
    svc = _load("docker-compose.yml")["services"]
    assert sorted(n for n, s in svc.items() if not s.get("profiles")) == ["scheduler", "web"]


def test_finish_compose_binds_loopback():
    for name, s in _load("docker-compose.finish.yml")["services"].items():
        for p in s.get("ports", []):
            assert str(p).startswith("127.0.0.1:"), (name, p)


def test_workflow_publishes_both_finish_images():
    text = (ROOT / ".github/workflows/docker.yml").read_text()
    for image in ("jobsearchpal-finish", "jobsearchpal-finish-proxy", "finish-box/box", "finish-box/proxy"):
        assert image in text, image


def test_digest_patterns_do_not_overlap():
    """jobsearchpal's merge must not pick up jobsearchpal-finish digests (a prefix of the same name)."""
    import fnmatch
    names = [f"digests-{i}--{a}" for i in ("jobsearchpal", "jobsearchpal-finish", "jobsearchpal-finish-proxy")
             for a in ("amd64", "arm64")]
    text = (ROOT / ".github/workflows/docker.yml").read_text()
    assert "pattern: digests-${{ matrix.image }}--*" in text
    for image in ("jobsearchpal", "jobsearchpal-finish", "jobsearchpal-finish-proxy"):
        got = [n for n in names if fnmatch.fnmatch(n, f"digests-{image}--*")]
        assert got == [f"digests-{image}--amd64", f"digests-{image}--arm64"], image


def test_docker_install_leaves_the_subnet_to_docker():
    """Every Docker install gets finish-inner (web joins it), so a fixed range could clash on upgrade for people who
    never turn Finish here on. Crowded hosts set a range in an override file instead (docs/setup.md)."""
    assert "ipam" not in _load("docker-compose.yml")["networks"]["finish-inner"]
