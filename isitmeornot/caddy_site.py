"""Caddy helpers for the LON1 deploy scripts (ipanswers, isitmeornot, mdconv, whatdoesmyscreen).

LON1 moved from nginx to Caddy on 30 Oct 2026. The Caddyfile belongs to the lon1-caddy-kit,
so these helpers never edit it. They only:
  * tell which web server is actually running,
  * read Caddy's live config and confirm it already sends a site where the deploy puts it,
  * fetch a page through Caddy over HTTPS (real certificate check) to prove the deploy is live.

Standard library only; drop this file next to deploy-server.py in each repo.
"""
from __future__ import annotations

import http.client
import json
import shutil
import socket
import ssl
import subprocess
import urllib.request
from pathlib import Path

CADDYFILE = Path('/etc/caddy/Caddyfile')
ADMIN = 'http://127.0.0.1:2019/config/'


class CaddyCheckError(RuntimeError):
    """Raised before anything changes when Caddy doesn't route a site the way the deploy expects."""


def _active(unit: str) -> bool:
    return subprocess.run(['systemctl', 'is-active', '--quiet', unit], check=False).returncode == 0


def web_server() -> str:
    """'nginx' while nginx is the running server (e.g. after the kit's 4-rollback.sh), else 'caddy'.

    Raises if neither is running, so a deploy never starts a competing server.
    """
    if _active('nginx') and shutil.which('nginx'):
        return 'nginx'
    if _active('caddy') and shutil.which('caddy'):
        return 'caddy'
    raise CaddyCheckError('Neither nginx nor Caddy is running. Nothing was changed. '
                          'Check with: systemctl status caddy nginx')


def live_config() -> dict:
    """Caddy's running config from its admin API; if that's switched off, the Caddyfile adapted."""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(ADMIN, timeout=5) as response:
            config = json.load(response)
        if isinstance(config, dict) and config:
            return config
    except Exception:
        pass
    result = subprocess.run(['caddy', 'adapt', '--config', str(CADDYFILE), '--adapter', 'caddyfile'],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        raise CaddyCheckError('Could not read Caddy\'s config (admin API or caddy adapt). Nothing was changed.\n'
                              + result.stderr.strip()[-600:])
    return json.loads(result.stdout)


def _host_matches(pattern: str, host: str) -> bool:
    if pattern.lower() == host.lower():
        return True
    return pattern.startswith('*.') and host.lower().endswith(pattern[1:].lower()) and host.count('.') == pattern.count('.')


def route_targets(host: str, config: dict | None = None) -> dict[str, list[str]]:
    """Everything Caddy uses for `host`: {'root': [...], 'dial': [...]} from routes matching that host."""
    config = live_config() if config is None else config
    found: dict[str, list[str]] = {'root': [], 'dial': []}
    seen_route = False

    def walk(node, inside: bool) -> None:
        nonlocal seen_route
        if isinstance(node, dict):
            match = node.get('match')
            if isinstance(match, list):
                hosts = [h for m in match if isinstance(m, dict) for h in (m.get('host') or [])]
                if hosts:
                    inside = any(_host_matches(str(h), host) for h in hosts)
                    seen_route = seen_route or inside
            for key, value in node.items():
                if inside and key in ('root', 'dial') and isinstance(value, str):
                    if value not in found[key]:
                        found[key].append(value)
                else:
                    walk(value, inside)
        elif isinstance(node, list):
            for item in node:
                walk(item, inside)

    walk(config.get('apps', {}).get('http', {}), False)
    if not seen_route:
        found = {'root': [], 'dial': []}
    found['_routed'] = ['yes'] if seen_route else []
    return found


def require_route(host: str, *, root: str | Path | None = None, port: int | None = None) -> None:
    """Stop (nothing changed) unless Caddy sends `host` to the folder `root` or the local `port`."""
    targets = route_targets(host)
    if not targets['_routed']:
        raise CaddyCheckError(f'Caddy has no site for {host}. Nothing was changed. '
                              f'Add it to /etc/caddy/Caddyfile first (the lon1-caddy-kit has the pattern).')
    if root is not None:
        want = str(root).rstrip('/')
        if not any(r.rstrip('/') == want or r.rstrip('/').startswith(want + '/') for r in targets['root']):
            raise CaddyCheckError(f'Caddy serves {host} from {targets["root"] or "nowhere on disk"}, '
                                  f'but this deploy writes to {want}. Nothing was changed.')
    if port is not None:
        ok = {f'127.0.0.1:{port}', f'localhost:{port}', f'[::1]:{port}'}
        if not any(d in ok for d in targets['dial']):
            raise CaddyCheckError(f'Caddy sends {host} to {targets["dial"] or "no local app"}, '
                                  f'but this app listens on 127.0.0.1:{port}. Nothing was changed.')


def fetch_https(host: str, path: str = '/', port: int = 443, context: ssl.SSLContext | None = None,
                limit: int = 5_000_000) -> tuple[int, bytes]:
    """GET https://host/path from this machine through Caddy, checking the real certificate."""
    context = context or ssl.create_default_context()
    raw = socket.create_connection(('127.0.0.1', port), timeout=15)
    try:
        secure = context.wrap_socket(raw, server_hostname=host)
    except Exception:
        raw.close()
        raise
    connection = http.client.HTTPConnection(host, timeout=15)
    connection.sock = secure
    try:
        connection.request('GET', path, headers={'Host': host, 'Accept-Encoding': 'identity',
                                                 'Cache-Control': 'no-cache', 'User-Agent': 'lon1-deploy-check'})
        response = connection.getresponse()
        return response.status, response.read(limit)
    finally:
        connection.close()


def verify_static(host: str, release: Path, files: list[str], port: int = 443,
                  context: ssl.SSLContext | None = None) -> None:
    """Prove Caddy now serves the new release: each file must come back byte-for-byte identical."""
    for name in files:
        path = '/' if name == 'index.html' else '/' + name
        status, body = fetch_https(host, path, port=port, context=context)
        if status != 200:
            raise RuntimeError(f'https://{host}{path} answered {status} through Caddy')
        if body != (release / name).read_bytes():
            raise RuntimeError(f'https://{host}{path} through Caddy is not the file just deployed')
