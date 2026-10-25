"""Bounded public-URL checks. Each probe runs in a killable subprocess."""
from __future__ import annotations
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import http.client
import ipaddress
import json
import re
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

MAX_URLS = 20
MAX_URL_LENGTH = 2048
PROBE_TIMEOUT = 8
POOL = ThreadPoolExecutor(max_workers=4,thread_name_prefix='url-probe')
CAPACITY = threading.BoundedSemaphore(24)
LIMIT_LOCK = threading.Lock()
LIMITS = OrderedDict()
GLOBAL = [time.monotonic(),0]
BLOCKED_V4 = tuple(ipaddress.ip_network(v) for v in (
    '0.0.0.0/8','10.0.0.0/8','100.64.0.0/10','127.0.0.0/8','169.254.0.0/16',
    '172.16.0.0/12','192.0.0.0/24','192.0.2.0/24','192.88.99.0/24','192.168.0.0/16',
    '198.18.0.0/15','198.51.100.0/24','203.0.113.0/24','224.0.0.0/4','240.0.0.0/4',
    '168.63.129.16/32'))
BLOCKED_V6 = tuple(ipaddress.ip_network(v) for v in ('2001::/23','2001:db8::/32','2002::/16'))


class InvalidTarget(ValueError):
    pass


def public_address(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_loopback or ip.is_link_local:
        return False
    if ip.version == 4:
        return not any(ip in network for network in BLOCKED_V4)
    return ip in ipaddress.ip_network('2000::/3') and not any(ip in network for network in BLOCKED_V6)


def normalize_url(value: str):
    if not isinstance(value,str) or not value.strip() or len(value)>MAX_URL_LENGTH:
        raise InvalidTarget('Enter a URL of at most 2048 characters.')
    value=value.strip()
    if any(ord(c)<33 or ord(c)==127 for c in value) or '\\' in value:
        raise InvalidTarget('Spaces, control characters and backslashes are not allowed in URLs.')
    if '://' not in value:
        value='https://'+value
    try:
        parts=urlsplit(value)
        if parts.scheme.lower() not in ('http','https') or not parts.hostname:
            raise InvalidTarget('Only public HTTP and HTTPS URLs are supported.')
        if parts.username is not None or parts.password is not None:
            raise InvalidTarget('URLs containing login credentials are not supported.')
        host=parts.hostname.rstrip('.').encode('idna').decode('ascii').lower()
        if '%' in host:
            raise InvalidTarget('Scoped or encoded hostnames are not supported.')
        port=parts.port if parts.port is not None else (443 if parts.scheme.lower()=='https' else 80)
        if port != (443 if parts.scheme.lower()=='https' else 80):
            raise InvalidTarget('Only standard HTTP port 80 and HTTPS port 443 are supported.')
        try:
            literal=ipaddress.ip_address(host)
        except ValueError:
            literal=None
            labels=host.split('.')
            if len(labels)<2 or len(host)>253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',p) for p in labels):
                raise InvalidTarget('Enter a valid public hostname.')
        if literal and not public_address(host):
            raise InvalidTarget('Private, reserved and special-use addresses are blocked.')
        authority=f'[{host}]' if literal and literal.version==6 else host
        path=quote(parts.path or '/',safe="/%:@!$&'()*+,;=-._~")
        query=quote(parts.query,safe="/%?:@!$&'()*+,;=-._~")
        normalized=urlunsplit((parts.scheme.lower(),authority,path,query,''))
        if len(normalized)>MAX_URL_LENGTH:
            raise InvalidTarget('The encoded URL is too long.')
        return normalized,host,port,path+('?' + query if query else '')
    except (UnicodeError,ValueError) as error:
        if isinstance(error,InvalidTarget):
            raise
        raise InvalidTarget('The URL could not be parsed.') from error


def resolve_public(host: str,port: int):
    records=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
    addresses=[]
    for family,kind,protocol,_,address in records:
        ip=address[0]
        if not public_address(ip):
            raise InvalidTarget('The hostname resolves to a private or special-use address.')
        if (family,address) not in addresses:
            addresses.append((family,address))
    if not addresses:
        raise socket.gaierror('No address records')
    # Prefer IPv4 on a small origin that may not have outbound IPv6, while validating every record.
    return sorted(addresses,key=lambda row:row[0]!=socket.AF_INET)


def fetch_headers(url: str):
    normalized,host,port,target=normalize_url(url)
    addresses=resolve_public(host,port)
    last_error=None
    for family,address in addresses[:4]:
        sock=socket.socket(family,socket.SOCK_STREAM)
        connection=None
        try:
            sock.settimeout(3)
            sock.connect(address)  # Numeric address only: DNS cannot change after validation.
            if normalized.startswith('https:'):
                sock=ssl.create_default_context().wrap_socket(sock,server_hostname=host)
            connection=http.client.HTTPConnection(host,port,timeout=3)
            connection.sock=sock
            connection.request('GET',target,headers={
                'Host':f'[{host}]' if ':' in host else host,
                'User-Agent':'IsItMeOrNot/1.0 (+https://isitmeornot.online/)',
                'Accept':'*/*','Connection':'close',
            })
            response=connection.getresponse()
            status=response.status
            location=response.getheader('Location')
            response.close()  # Status only: never read, store or relay the response body.
            return normalized,status,location
        except ssl.SSLCertVerificationError:
            raise
        except (OSError,http.client.HTTPException) as error:
            last_error=error
        finally:
            if connection:
                connection.close()
            sock.close()
    if last_error:
        raise last_error
    raise OSError('No working public address')


def probe(value: str):
    started=time.monotonic()
    result={'input':value,'url':value,'final_url':None,'status':None,'state':'unreachable','label':'No response','elapsed_ms':None,'redirects':0,'detail':'','checked_at':dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}
    try:
        url,_,_,_=normalize_url(value)
        result['url']=url
        seen=set()
        for hop in range(4):
            if url in seen:
                result.update(state='redirect',label='Redirect loop',detail='The server responded, but redirects form a loop.')
                break
            seen.add(url)
            final,status,location=fetch_headers(url)
            result.update(final_url=final,status=status,redirects=hop)
            if status in (301,302,303,307,308) and location:
                if hop==3:
                    result.update(state='redirect',label='Redirect limit',detail='The server responded; stopped after three redirects.')
                    break
                url,_,_,_=normalize_url(urljoin(final,location))
                continue
            if 200<=status<300:
                result.update(state='up',label='Available',detail='A successful HTTP response was received.')
            elif status in (401,403,429):
                result.update(state='restricted',label='Access limited',detail='The server responded but may require login, block automated checks or limit requests.')
            elif 300<=status<400:
                result.update(state='redirect',label='HTTP response',detail='The server responded without a followable redirect.')
            else:
                result.update(state='http_error',label='HTTP error',detail='The endpoint returned an HTTP error; the server was reachable.')
            break
    except InvalidTarget as error:
        result.update(state='blocked',label='Blocked target',detail=str(error))
    except ssl.SSLCertVerificationError:
        result.update(state='tls_error',label='TLS error',detail='The HTTPS certificate could not be verified.')
    except ssl.SSLError:
        result.update(state='tls_error',label='TLS error',detail='The TLS handshake failed.')
    except socket.gaierror:
        result.update(state='dns_error',label='DNS error',detail='No usable DNS response was obtained from this server.')
    except (TimeoutError,socket.timeout):
        result.update(state='timeout',label='Timed out',detail='The connection did not respond within the time limit.')
    except (OSError,http.client.HTTPException,ValueError):
        result.update(state='unreachable',label='No response',detail='No valid HTTP response was obtained from this server.')
    result['elapsed_ms']=round((time.monotonic()-started)*1000)
    return result


def limited_probe(value: str):
    started=time.monotonic()
    timed_out=False
    try:
        completed=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--worker'],input=json.dumps(value),capture_output=True,text=True,timeout=PROBE_TIMEOUT)
        if completed.returncode==0:
            result=json.loads(completed.stdout)
            if isinstance(result,dict) and result.get('input')==value:
                return result
    except subprocess.TimeoutExpired:
        timed_out=True
    except (ValueError,OSError):
        pass
    if not timed_out:
        return {'input':value,'url':value,'final_url':None,'status':None,'state':'checker_error','label':'Check failed','elapsed_ms':round((time.monotonic()-started)*1000),'redirects':0,'detail':'The check could not be completed. Try again.','checked_at':dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}
    return {'input':value,'url':value,'final_url':None,'status':None,'state':'timeout','label':'Timed out','elapsed_ms':PROBE_TIMEOUT*1000,'redirects':0,'detail':'No result within the eight-second check limit.','checked_at':dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}


def authorize(key: str,count: int):
    now=time.monotonic()
    with LIMIT_LOCK:
        if now-GLOBAL[0]>=60:
            GLOBAL[:]=[now,0]
        for expired in [address for address,entry in LIMITS.items() if now-entry[0]>=60]:
            LIMITS.pop(expired,None)
        entry=LIMITS.get(key,(now,0))
        if now-entry[0]>=60:
            entry=(now,0)
        if entry[1]+count>60 or GLOBAL[1]+count>240:
            return False
        LIMITS[key]=(entry[0],entry[1]+count)
        LIMITS.move_to_end(key)
        while len(LIMITS)>4096:
            LIMITS.popitem(last=False)
        GLOBAL[1]+=count
        return True


def check_batch(values: list[str]):
    reserved=0
    for _ in values:
        if not CAPACITY.acquire(blocking=False):
            for _ in range(reserved):CAPACITY.release()
            return None
        reserved+=1
    def run_one(value):
        try:return limited_probe(value)
        finally:CAPACITY.release()
    futures=[]
    try:
        for value in values:
            futures.append(POOL.submit(run_one,value))
            reserved-=1
        return [future.result() for future in futures]
    finally:
        for _ in range(reserved):CAPACITY.release()


if __name__=='__main__' and sys.argv[1:]==['--worker']:
    try:
        # Parent timeout bounds DNS, TLS, headers and redirects together, including slow peers.
        if sys.platform!='win32':
            import resource
            resource.setrlimit(resource.RLIMIT_CPU,(4,4))
            resource.setrlimit(resource.RLIMIT_AS,(128*1024*1024,128*1024*1024))
        value=json.loads(sys.stdin.read(32768))
        if not isinstance(value,str) or len(value)>MAX_URL_LENGTH:
            raise ValueError('Bad worker input')
        print(json.dumps(probe(value),ensure_ascii=True))
    except Exception:
        sys.exit(1)
