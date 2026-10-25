"""Add one isolated Nginx virtual host, with webroot TLS and rollback."""
from __future__ import annotations
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from certbot_guard import run_certbot

DOMAIN = 'isitmeornot.online'
MARKER = '# ISITMEORNOT.ONLINE MANAGED NGINX CONFIGURATION'
WEBROOT = Path('/var/lib/isitmeornot-acme')
CERTDIR = Path('/etc/letsencrypt/live/isitmeornot.online')
AUXILIARY = (
    Path('/etc/systemd/system/isitmeornot-tls-renew.service'),
    Path('/etc/systemd/system/isitmeornot-tls-renew.timer'),
    Path('/opt/isitmeornot/tls-reload.sh'),
)


def command(*args):
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    if result.returncode:
        detail = result.stderr.strip() or 'command returned '+str(result.returncode)
        raise RuntimeError(' '.join(args)+': '+detail)
    return result


def atomic(path: Path, content: str, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.isitmeornot-tmp')
    temp.write_text(content)
    temp.chmod(mode)
    temp.replace(path)


def directives(config: str, directive: str):
    lexer = shlex.shlex(config, posix=True, punctuation_chars='{};')
    lexer.whitespace_split = True
    lexer.commenters = '#'
    tokens = iter(lexer)
    for token in tokens:
        if token == directive:
            values = []
            for item in tokens:
                if ';' in item or '{' in item or '}' in item:
                    break
                values.append(item)
            yield values


def nginx_config(ranges: list[str], ssl: bool, original_peer='$realip_remote_addr',
                 webroot=WEBROOT, certdir=CERTDIR) -> str:
    # $realip_remote_addr preserves the socket peer even if other sites use real_ip.
    networks = '\n'.join('    '+network+' 1;' for network in ranges)
    text = MARKER + '\n' + f'''geo {original_peer} $isitmeornot_cloudflare_peer {{
    default 0;
{networks}
}}
map $isitmeornot_cloudflare_peer $isitmeornot_client_ip {{
    default {original_peer};
    1 $http_cf_connecting_ip;
}}
server {{
    listen 80;
    listen [::]:80;
    server_name {DOMAIN};
    access_log off;
    location ^~ /.well-known/acme-challenge/ {{
        root {webroot};
        default_type text/plain;
        try_files $uri =404;
    }}
    location / {{
'''
    text += f'        return 308 https://{DOMAIN}$request_uri;\n' if ssl else '        return 503;\n'
    text += '    }\n}\n'
    if ssl:
        text += f'''server {{
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name {DOMAIN};
    ssl_certificate {certdir}/fullchain.pem;
    ssl_certificate_key {certdir}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    access_log off;
    location ^~ /.well-known/acme-challenge/ {{
        root {webroot};
        default_type text/plain;
        try_files $uri =404;
    }}
    location / {{
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host {DOMAIN};
        proxy_set_header X-IsItMeOrNot-IP $isitmeornot_client_ip;
        proxy_set_header X-Forwarded-For "";
        client_max_body_size 64k;
        proxy_cache off;
        proxy_buffering off;
        proxy_connect_timeout 5s;
        proxy_read_timeout 65s;
    }}
}}
'''
    return text


class NginxFrontend:
    name = 'nginx'
    def __init__(self):
        self.path = None
        self.original = None
        self.peer = '$remote_addr'
        self.backup = None

    def preflight(self):
        command('nginx','-t')
        dump = command('nginx','-T').stdout
        includes = [item[0] for item in directives(dump, 'include') if item]
        for pattern, target in (
            ('/etc/nginx/conf.d/*.conf', '/etc/nginx/conf.d/isitmeornot.conf'),
            ('conf.d/*.conf', '/etc/nginx/conf.d/isitmeornot.conf'),
            ('/etc/nginx/sites-enabled/*', '/etc/nginx/sites-enabled/isitmeornot.conf'),
            ('sites-enabled/*', '/etc/nginx/sites-enabled/isitmeornot.conf'),
        ):
            if pattern in includes:
                self.path = Path(target)
                break
        if self.path is None:
            raise RuntimeError('Nginx has no standard conf.d/*.conf or sites-enabled/* include. No configuration changed.')
        if self.path.is_symlink():
            raise RuntimeError(str(self.path)+' is a symlink; refusing to replace an existing site')
        if self.path.exists():
            self.original = self.path.read_text()
            if not self.original.startswith(MARKER+'\n'):
                raise RuntimeError(str(self.path)+' exists and is not managed by Is It Me Or Not')
        # nginx -T labels each source file; reject an exact domain already owned elsewhere.
        for section in re.split(r'^# configuration file ', dump, flags=re.M):
            first, _, body = section.partition('\n')
            source = first.removesuffix(':').strip()
            if source == str(self.path):
                continue
            if any(DOMAIN in names for names in directives(body, 'server_name')):
                raise RuntimeError('An existing Nginx site already declares isitmeornot.online: '+source)
        version = command('nginx','-V')
        if '--with-http_realip_module' in version.stdout+version.stderr:
            self.peer = '$realip_remote_addr'
        print('Using the active Nginx service; site file: '+str(self.path), flush=True)

    def snapshot(self, directory: Path):
        self.backup = directory
        saved = []
        for index, path in enumerate(AUXILIARY):
            exists = path.exists()
            if path.is_symlink():
                raise RuntimeError('Refusing to replace auxiliary symlink: '+str(path))
            if exists:
                shutil.copy2(path,directory/f'nginx-aux-{index}')
            saved.append({'path':str(path),'exists':exists})
        (directory/'nginx-aux.json').write_text(json.dumps(saved))

    def reload(self):
        command('nginx','-t')
        command('systemctl','reload','nginx')

    def activate(self, ranges):
        if not shutil.which('certbot'):
            subprocess.run(['apt-get','update'],check=True)
            subprocess.run(['apt-get','install','-y','certbot'],check=True)
        WEBROOT.mkdir(parents=True,exist_ok=True)
        WEBROOT.chmod(0o755)
        challenge = WEBROOT/'.well-known/acme-challenge'
        challenge.mkdir(parents=True,exist_ok=True)
        (WEBROOT/'.well-known').chmod(0o755)
        challenge.chmod(0o755)
        has_certificate = all((CERTDIR/name).is_file() for name in ('fullchain.pem','privkey.pem'))
        atomic(self.path,nginx_config(ranges,has_certificate,self.peer))
        self.reload()
        print('Obtaining HTTPS certificate for isitmeornot.online through Nginx. DNS must point to lon1.',flush=True)
        result = run_certbot([
            'certbot','certonly','--webroot','--webroot-path',str(WEBROOT),
            '--cert-name',DOMAIN,'--domain',DOMAIN,'--keep-until-expiring',
            '--non-interactive','--agree-tos','--register-unsafely-without-email',
        ])
        if result.returncode:
            raise RuntimeError(f'Certbot failed to issue the HTTPS certificate (exit {result.returncode}). '
                               'Its output above contains the actual reason. Existing sites will be restored unchanged.')
        atomic(self.path,nginx_config(ranges,True,self.peer))
        self.reload()
        certbot = shutil.which('certbot')
        python = sys.executable
        guard = '/opt/isitmeornot/current/certbot_guard.py'
        nginx = shutil.which('nginx')
        systemctl = shutil.which('systemctl')
        # Paths come from the host's executable search and are quoted in the shell hook.
        hook = '#!/bin/sh\nset -eu\n'+shlex.quote(nginx)+' -t\n'+shlex.quote(systemctl)+' reload nginx\n'
        atomic(AUXILIARY[2],hook,0o755)
        for value in (certbot, python, guard, str(AUXILIARY[2])):
            if not re.fullmatch(r'/[A-Za-z0-9_./-]+',value):
                raise RuntimeError('Unsupported executable path for TLS renewal')
        service = f'''[Unit]
Description=Renew the Is It Me Or Not HTTPS certificate
Wants=network-online.target
After=network-online.target
[Service]
Type=oneshot
ExecStart={python} {guard} --wait-seconds 600 -- {certbot} renew --cert-name {DOMAIN} --quiet --deploy-hook {AUXILIARY[2]}
TimeoutStartSec=35min
'''
        timer = '''[Unit]
Description=Check Is It Me Or Not HTTPS certificate twice daily
[Timer]
OnCalendar=*-*-* 03,15:15:00 UTC
RandomizedDelaySec=30min
Persistent=true
[Install]
WantedBy=timers.target
'''
        atomic(AUXILIARY[0],service)
        atomic(AUXILIARY[1],timer)
        command('systemctl','daemon-reload')
        command('systemctl','enable','--now','isitmeornot-tls-renew.timer')

    def restore(self):
        if self.original is None:
            self.path.unlink(missing_ok=True)
        else:
            atomic(self.path,self.original)
        if self.backup:
            saved = json.loads((self.backup/'nginx-aux.json').read_text())
            subprocess.run(['systemctl','stop','isitmeornot-tls-renew.timer','isitmeornot-tls-renew.service'],
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            if not saved[1]['exists']:
                subprocess.run(['systemctl','disable','isitmeornot-tls-renew.timer'],
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            for index,item in enumerate(saved):
                path = Path(item['path'])
                if item['exists']:
                    shutil.copy2(self.backup/f'nginx-aux-{index}',path)
                else:
                    path.unlink(missing_ok=True)
            command('systemctl','daemon-reload')
            if saved[1]['exists']:
                command('systemctl','start','isitmeornot-tls-renew.timer')
        self.reload()
