#!/usr/bin/env python3
"""Standalone public-URL checker; standard library only, loopback behind Nginx."""
from __future__ import annotations
import argparse
import ipaddress
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.parse import urlsplit
import checker

ROOT=Path(__file__).resolve().parent
CSP="default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"

def parse_address(value: str):
    address=ipaddress.ip_address(value.strip())
    if isinstance(address,ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address

class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64
    def __init__(self, address, handler, trusted_proxy=False):
        self.trusted_proxy = trusted_proxy
        self.slots = threading.BoundedSemaphore(48)
        self.static = {'/':('text/html; charset=utf-8',(ROOT/'site/index.html').read_bytes())}
        for name,mime in [('style.css','text/css'),('app.js','text/javascript'),('format.js','text/javascript')]:
            self.static['/'+name]=(mime+'; charset=utf-8',(ROOT/'site'/name).read_bytes())
        for path in (ROOT/'site/assets').iterdir():
            if path.suffix=='.svg':
                self.static['/assets/'+path.name]=('image/svg+xml',path.read_bytes())
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        print('Is It Me Or Not: request failed (visitor details omitted)', flush=True)


class Handler(BaseHTTPRequestHandler):
    server_version = 'IsItMeOrNot'
    sys_version = ''
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_):
        pass

    def send_error(self, code, message=None, explain=None):
        self.reply(code, 'text/plain; charset=utf-8', b'Request rejected\n')

    def reply(self, status: int, content_type: str, content: bytes):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'private, no-store, max-age=0')
        self.send_header('CDN-Cache-Control', 'no-store')
        self.send_header('Cloudflare-CDN-Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Permissions-Policy', 'geolocation=(), camera=(), microphone=()')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(content)

    def do_POST(self):
        def error(code, message):
            self.reply(code,'application/json; charset=utf-8',json.dumps({'error':message}).encode())
        if urlsplit(self.path).path != '/api/status':
            error(404,'Not found')
            return
        origin=self.headers.get('Origin')
        host=self.headers.get('Host','')
        if origin and origin not in ('https://'+host,'http://'+host):
            error(403,'Use this endpoint from its own site.')
            return
        if self.headers.get_content_type() != 'application/json':
            error(415,'Send application/json.')
            return
        lengths=self.headers.get_all('Content-Length',[])
        if len(lengths)!=1 or self.headers.get('Transfer-Encoding'):
            error(411,'A single Content-Length is required.')
            return
        try:
            length=int(lengths[0])
            if not 0<length<=65536:
                error(413,'Request too large.')
                return
            data=json.loads(self.rfile.read(length))
            values=data.get('urls') if isinstance(data,dict) else None
            if not isinstance(values,list) or not 1<=len(values)<=checker.MAX_URLS or any(not isinstance(v,str) or not v.strip() or len(v)>checker.MAX_URL_LENGTH for v in values):
                error(400,'Send between 1 and 20 URLs, at most 2048 characters each.')
                return
        except (ValueError,UnicodeError):
            error(400,'Invalid JSON.')
            return
        peer=parse_address(self.client_address[0])
        ip=self.headers.get('X-IsItMeOrNot-IP','') if self.server.trusted_proxy and peer.is_loopback else str(peer)
        try:key=str(parse_address(ip))
        except ValueError:
            error(400,'The connection address could not be verified.')
            return
        if not checker.authorize(key,len(values)):
            error(429,'Check limit reached. Wait a minute and try again.')
            return
        results=checker.check_batch(values)
        if results is None:
            error(503,'The checker is busy. Try again shortly.')
            return
        self.reply(200,'application/json; charset=utf-8',json.dumps({'results':results,'vantage':'London server','method':'HTTP GET headers'},ensure_ascii=True).encode())

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path=urlsplit(self.path).path
        if path in ('/status','/status/'):
            self.send_response(308)
            self.send_header('Location','/')
            self.send_header('Content-Length','0')
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            return
        if path in self.server.static:
            mime,body=self.server.static[path]
            self.reply(200,mime,body)
            return
        if path=='/healthz':
            self.reply(200,'application/json',b'{"ok":true,"app":"isitmeornot"}')
            return
        if path=='/robots.txt':
            self.reply(200,'text/plain',b'User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /healthz\n')
            return
        self.reply(404,'text/plain',b'Not found\n')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--behind-proxy',action='store_true')
    args=parser.parse_args()
    server=Server(('127.0.0.1',args.port),Handler,args.behind_proxy)
    print(f'Is It Me Or Not listening on 127.0.0.1:{args.port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
