#!/usr/bin/env python3
"""Install isitmeornot.online on LON1.

LON1 is served by Caddy since 30 Oct 2026; the Caddyfile belongs to the lon1-caddy-kit and is
never edited here. With Caddy running, this checks Caddy already sends isitmeornot.online to the
app's port (127.0.0.1:8765), deploys and health-checks the app, then checks the site through
Caddy. If nginx is the running server instead (the kit's 4-rollback.sh), the original nginx
route runs.
"""
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
from caddy_site import fetch_https, require_route, web_server

SOURCE=Path(__file__).resolve().parent
HOST='isitmeornot.online'
PORT=8765
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


def atomic(path:Path,content:str):
    tmp=path.with_name(path.name+'.isitmeornot-tmp')
    tmp.write_text(content)
    tmp.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644)
    os.replace(tmp,path)


def main():
    os.umask(0o022)
    if os.geteuid()!=0:
        raise RuntimeError('Run with sudo python3 deploy-server.py')
    for name in ('systemctl','python3','useradd'):
        if not shutil.which(name):raise RuntimeError('Required command missing: '+name)
    server=web_server()
    frontend=None
    if server=='nginx':
        from nginx_frontend import NginxFrontend
        frontend=NginxFrontend()
        frontend.preflight()
    else:
        require_route(HOST,port=PORT)
    python=shutil.which('python3')
    if not re.fullmatch(r'/[A-Za-z0-9_./-]+',python):raise RuntimeError('Unsupported Python executable path')
    if (BASE/'current').exists() and not (BASE/'current').is_symlink():
        raise RuntimeError(str(BASE/'current')+' must be a managed release link')
    previous=os.readlink(BASE/'current') if (BASE/'current').is_symlink() else None
    prior=UNIT.exists()
    if not prior:
        try:
            with socket.socket() as connection:connection.bind(('127.0.0.1',PORT))
        except OSError as error:
            raise RuntimeError(f'Loopback port {PORT} is occupied. No application or web configuration changed.') from error
    needed=['server.py','checker.py','site/index.html','site/app.js','site/format.js','site/style.css']
    if frontend:needed.append('certbot_guard.py')
    for name in needed:
        if not (SOURCE/name).is_file():raise RuntimeError('Missing package file: '+name)
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup=Path('/var/backups/isitmeornot')/stamp
    backup.mkdir(parents=True)
    if frontend:
        frontend.snapshot(backup)
        if frontend.path.exists():shutil.copy2(frontend.path,backup/'nginx-site.conf')
    if prior:shutil.copy2(UNIT,backup/'isitmeornot.service')
    (backup/'state.json').write_text(json.dumps({'previous_release':previous,'prior_service':prior,'web_server':server,'nginx_site':str(frontend.path) if frontend else None}))
    release=BASE/'releases'/stamp
    release.mkdir(parents=True)
    for name in ('server.py','checker.py','certbot_guard.py'):
        if (SOURCE/name).is_file():shutil.copy2(SOURCE/name,release/name)
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
    ranges=cloudflare_ranges() if frontend else None
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
                with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/healthz',timeout=2) as response:
                    data=json.load(response)
                    if data=={'ok':True,'app':'isitmeornot'}:break
            except Exception:pass
            time.sleep(.5)
        else:raise RuntimeError('Is It Me Or Not health check failed')
        run('systemctl','is-active','--quiet','isitmeornot.service')
        if frontend:
            frontend.activate(ranges)
        else:
            # The app itself is healthy (checked above) and Caddy's config was not touched,
            # so a failure here is reported rather than rolled back.
            try:
                status,body=fetch_https(HOST,'/healthz')
                if status==200 and json.loads(body)=={'ok':True,'app':'isitmeornot'}:
                    print('Checked through Caddy: https://'+HOST+'/healthz answers OK.')
                else:
                    print(f'WARNING: through Caddy, /healthz answered {status}. The app is healthy on 127.0.0.1:{PORT}; check Caddy\'s isitmeornot.online block.',file=sys.stderr)
            except Exception as error:
                print('WARNING: could not check the site through Caddy ('+str(error)+'). The app is healthy on 127.0.0.1:'+str(PORT)+'.',file=sys.stderr)
        print('\nInstalled successfully: https://isitmeornot.online/  (served by '+server+')')
        print('Backup: '+str(backup))
        print('IP Answers, Screen Fit and Litehaus were not modified.')
    except Exception:
        if changed:
            print('Restoring the previous Is It Me Or Not release and managed Nginx host.')
            if previous:
                (BASE/'current').unlink(missing_ok=True);(BASE/'current').symlink_to(previous)
            else:(BASE/'current').unlink(missing_ok=True)
            if frontend:
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
