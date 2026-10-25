#!/usr/bin/env python3
"""Install isitmeornot.online alongside the existing London Nginx sites."""
from __future__ import annotations
import datetime as dt
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from nginx_frontend import NginxFrontend,atomic

SOURCE=Path(__file__).resolve().parent
BASE=Path('/opt/isitmeornot')
UNIT=Path('/etc/systemd/system/isitmeornot.service')
CF_FALLBACK='173.245.48.0/20 103.21.244.0/22 103.22.200.0/22 103.31.4.0/22 141.101.64.0/18 108.162.192.0/18 190.93.240.0/20 188.114.96.0/20 197.234.240.0/22 198.41.128.0/17 162.158.0.0/15 104.16.0.0/13 104.24.0.0/14 172.64.0.0/13 131.0.72.0/22 2400:cb00::/32 2606:4700::/32 2803:f800::/32 2405:b500::/32 2405:8100::/32 2a06:98c0::/29 2c0f:f248::/32'.split()

def cloudflare_ranges():
    try:
        ranges = []
        for family in ('v4','v6'):
            req = urllib.request.Request('https://www.cloudflare.com/ips-'+family, headers={'User-Agent':'IPAnswers installer'})
            with urllib.request.urlopen(req, timeout=15) as response:
                values = response.read(16384).decode().split()
            if len(values) < 5:
                raise ValueError('Incomplete Cloudflare ranges')
            ranges.extend(str(ipaddress.ip_network(value)) for value in values)
        return ranges
    except Exception:
        print('Cloudflare range download unavailable; using official ranges verified 2026-10-25.')
        return CF_FALLBACK


def run(*args):
    return subprocess.run(args,check=True)


def main():
    os.umask(0o022)
    if os.geteuid()!=0:
        raise RuntimeError('Run with sudo python3 deploy-server.py')
    for name in ('nginx','systemctl','python3','useradd'):
        if not shutil.which(name):raise RuntimeError('Required command missing: '+name)
    run('systemctl','is-active','--quiet','nginx')
    frontend=NginxFrontend()
    frontend.preflight()
    python=shutil.which('python3')
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+',python):raise RuntimeError('Unsupported Python executable path')
    if (BASE/'current').exists() and not (BASE/'current').is_symlink():
        raise RuntimeError(str(BASE/'current')+' must be a managed release link')
    previous=os.readlink(BASE/'current') if (BASE/'current').is_symlink() else None
    prior=UNIT.exists()
    if not prior:
        try:
            with socket.socket() as connection:connection.bind(('127.0.0.1',8765))
        except OSError as error:
            raise RuntimeError('Loopback port 8765 is occupied. No application or web configuration changed.') from error
    for name in ('server.py','checker.py','certbot_guard.py','site/index.html','site/app.js','site/format.js','site/style.css'):
        if not (SOURCE/name).is_file():raise RuntimeError('Missing package file: '+name)
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup=Path('/var/backups/isitmeornot')/stamp
    backup.mkdir(parents=True)
    frontend.snapshot(backup)
    if frontend.path.exists():shutil.copy2(frontend.path,backup/'nginx-site.conf')
    if prior:shutil.copy2(UNIT,backup/'isitmeornot.service')
    (backup/'state.json').write_text(json.dumps({'previous_release':previous,'prior_service':prior,'nginx_site':str(frontend.path)}))
    release=BASE/'releases'/stamp
    release.mkdir(parents=True)
    for name in ('server.py','checker.py','certbot_guard.py'):shutil.copy2(SOURCE/name,release/name)
    shutil.copytree(SOURCE/'site',release/'site')
    for path in [BASE,BASE/'releases',release,*release.rglob('*')]:path.chmod(0o755 if path.is_dir() else 0o644)
    if subprocess.run(['id','-u','isitmeornot'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
        run('useradd','--system','--home-dir','/nonexistent','--shell','/usr/sbin/nologin','isitmeornot')
    service=f'''[Unit]
Description=Is It Me Or Not website status checker
After=network.target
[Service]
Type=simple
User=isitmeornot
Group=isitmeornot
WorkingDirectory=/opt/isitmeornot/current
ExecStart={python} /opt/isitmeornot/current/server.py --behind-proxy
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
CapabilityBoundingSet=
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
MemoryMax=256M
TasksMax=64
UMask=0027
[Install]
WantedBy=multi-user.target
'''
    ranges=cloudflare_ranges()
    changed=False
    try:
        changed=True
        link=BASE/'current-next';link.unlink(missing_ok=True);link.symlink_to(release);os.replace(link,BASE/'current')
        atomic(UNIT,service)
        run('systemctl','daemon-reload')
        run('systemctl','enable','--now','isitmeornot.service')
        run('systemctl','restart','isitmeornot.service')
        for attempt in range(30):
            try:
                with urllib.request.urlopen('http://127.0.0.1:8765/healthz',timeout=2) as response:
                    data=json.load(response)
                    if data=={'ok':True,'app':'isitmeornot'}:break
            except Exception:pass
            time.sleep(.5)
        else:raise RuntimeError('Is It Me Or Not health check failed')
        run('systemctl','is-active','--quiet','isitmeornot.service')
        frontend.activate(ranges)
        print('\nInstalled successfully: https://isitmeornot.online/')
        print('Backup: '+str(backup))
        print('IP Answers, Screen Fit and Litehaus were not modified.')
    except Exception:
        if changed:
            print('Restoring the previous Is It Me Or Not release and managed Nginx host.')
            if previous:
                (BASE/'current').unlink(missing_ok=True);(BASE/'current').symlink_to(previous)
            else:(BASE/'current').unlink(missing_ok=True)
            try:frontend.restore()
            except Exception as error:print('Web rollback needs attention: '+str(error)+'; backup: '+str(backup),file=sys.stderr)
            subprocess.run(['systemctl','stop','isitmeornot.service'])
            if prior:shutil.copy2(backup/'isitmeornot.service',UNIT)
            else:
                subprocess.run(['systemctl','disable','isitmeornot.service'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                UNIT.unlink(missing_ok=True)
            subprocess.run(['systemctl','daemon-reload'])
            if prior:subprocess.run(['systemctl','restart','isitmeornot.service'])
        raise

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('ERROR: '+str(error),file=sys.stderr)
        sys.exit(1)
