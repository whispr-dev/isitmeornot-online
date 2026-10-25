# isitmeornot.online — 1.0.1

A standalone single/bulk URL status checker for lon1, with SkeleTrace Suite advertising.
Copyright © 2026 whispr.dev. Existing logos remain whispr.dev brand artwork.

This package serves the checker at **https://isitmeornot.online/**. It uses its own application,
Linux account, release directory, loopback port, Nginx host, certificate and renewal timer.
It does not deploy to or modify IP Answers, Screen Fit, Litehaus or Caddy.

## Deploy from Windows

Requirements: PowerShell 7, Windows OpenSSH/tar, the working `ssh lon1` alias, sudo access,
Python 3.10+ and the active Nginx system service on London. There are no pip/npm runtime dependencies.
Certbot is installed from Ubuntu packages if it is missing.

Point the isitmeornot.online A record to lon1: **138.68.142.181**, from the provided server output.
Add AAAA only for an IPv6 address that lon1 actually serves. Port 80 must reach Nginx for certificate
validation. For initial HTTPS issuance, use Cloudflare DNS-only; afterwards restore proxying with
Full (strict). Keep Cloudflare Pseudo IPv4 off or in Add Header mode. No www hostname is configured.

```powershell
Set-Location "$env:USERPROFILE\Downloads"
tar -xzf .\isitmeornot-online-1.0.1.tar.gz
Set-Location .\isitmeornot
.\Deploy.ps1
```

Enter the server sudo password when prompted. The installer checks the existing Nginx configuration,
creates a release and unprivileged app account, starts the app, checks its identity/health, requests
HTTPS through a webroot challenge and reloads Nginx after syntax checks. It never stops Nginx.

| Item | Dedicated location/name |
| --- | --- |
| Site | https://isitmeornot.online/ |
| Backend | 127.0.0.1:8765 (loopback only) |
| Service / Linux account | isitmeornot.service / isitmeornot |
| Releases | /opt/isitmeornot/releases/ |
| Current release | /opt/isitmeornot/current |
| Nginx host | /etc/nginx/conf.d/isitmeornot.conf, or the standard sites-enabled include |
| Certificate | /etc/letsencrypt/live/isitmeornot.online/ |
| Renewal timer | isitmeornot-tls-renew.timer |
| Backups | /var/backups/isitmeornot/<UTC timestamp>/ |

The installer refuses unmanaged/conflicting hostname routes, an occupied app port on first install,
and nonstandard Nginx include layouts. Re-running updates only this application's managed files.
If final activation fails, it restores the previous managed host, service and release. Other sites'
files are not edited. Issued certificates, the Linux account and installed OS packages remain after
rollback. A rollback error is printed explicitly with the backup path.

Certbot reuses an existing ACME account where possible. If registration is needed, it registers
without an email and accepts the Let's Encrypt terms as part of certificate provisioning.
A dedicated timer checks this domain's certificate twice daily, reloading Nginx after renewal.

The operational dependency is DNS/HTTP validation reaching lon1; wrong DNS, a broken AAAA record,
blocked port 80 or forced edge HTTPS before an origin certificate exists can prevent issuance.
The error explains this and rolls back the new route. Avoid forced Cloudflare caching of /api/status;
the app sends browser/CDN no-store headers and Nginx proxy caching is off.

## Certbot concurrency fix in 1.0.1

Issuance and this app's renewal job retry only Certbot's explicit "Another instance of Certbot is
already running" error. The helper waits ten seconds between retries, for a lock-wait window of up
to ten minutes. It keeps Certbot's own locking intact and never deletes a lock, kills a process or
stops another domain's certificate service. Certbot output remains visible and uses a fixed locale
so the lock message is recognised reliably.

Other certificate failures return immediately with the real Certbot output rather than being
misdiagnosed as DNS failures. If contention lasts beyond ten minutes, the installer reports that
specific condition and restores this app's prior managed state. Let the existing job finish and
rerun. The renewal service also uses this helper, with a 35-minute overall systemd timeout.

## Features

- Check a single URL or up to 20, one per line. Bare hostnames default to HTTPS; duplicates are removed.
- Six explicit shortcuts: Google, GitHub, Wikipedia, Cloudflare, Microsoft and YouTube.
- Results appear as completed, with at most three browser requests in parallel.
- Actual HTTP code, response time, redirect count, final URL and a readable explanation.
- Copy URL, Copy result, Copy all and CSV export with formula-injection protection.
- Desktop/mobile layout, actual whispr.dev/SkeleTrace logos and suite promotion.
- No checks automatically on load; no recurring website-monitoring jobs are installed.

The browser fetches the same-origin /api/status endpoint. The server makes HTTP GET requests and
consumes only response headers; it neither reads nor relays page bodies, runs remote scripts,
logs into targets or forwards visitors' cookies. Browser CORS restrictions therefore do not get
mislabelled as target downtime. Targets see London's server address and the checker user-agent.

2xx means Available. 401/403/429 mean Access limited, since a service can restrict automated probes.
Other 4xx/5xx replies are HTTP errors: the endpoint was reachable. DNS, TLS, connection, timeout and
internal checker failures are distinguished. A single response from London is a snapshot, not a
global outage verdict or proof that all features work. Timing covers DNS/TCP/TLS/headers, not ICMP.

## Safeguards and privacy

Only public HTTP/HTTPS targets on standard ports 80/443 are allowed. Credential-bearing URLs,
private/reserved addresses, cloud metadata addresses and IPv6 transition paths are rejected.
Every DNS answer is checked. Connections pin a validated numeric address, while TLS uses the
original hostname and normal certificate verification. Every redirect is revalidated; at most
three redirects are followed. The transport ignores environment HTTP proxies and uses fixed headers.

Each probe has a killable eight-second wall deadline. Linux workers additionally have four CPU
seconds and a 128 MiB address-space limit. Four workers run concurrently with at most 24 queued/
reserved jobs. Limits are 60 checks per IP/minute and 240 globally/minute; counters are bounded and
expire after their window. The app has a 256 MiB systemd memory ceiling. Busy/limited work receives
an explicit error instead of a fabricated outage result.

Only same-origin browser JSON POST is accepted; no cross-origin allowance is enabled. API batches
contain 1–20 URLs and at most 64 KiB of JSON. No arbitrary response bodies or headers are exposed.
Submitted URLs/results are transient, with no app lookup-history database or access logs.
In-memory IP counters enforce limits. URLs may contain sensitive tokens: the privacy dialog tells
visitors not to submit confidential or credential-bearing links. Hosting/proxy infrastructure still
handles connection data; Cloudflare security features may set their own cookies. No app cookies,
analytics, remote fonts or advertising trackers are used. Promotional links are ordinary links.

## Local use and tests

Windows, Python 3.10+:

```powershell
py -3 .\server.py --port 8765
```

Linux:

```sh
python3 server.py --port 8765
```

Open http://127.0.0.1:8765. The local app can check public targets if the host has working DNS/outbound
connectivity. --behind-proxy is exclusively for the installed loopback Nginx proxy.

No installation is needed for Python tests. Node.js 20+ is optional for JavaScript development tests.
OpenSSL CLI enables the real local TLS fixture; that fixture is skipped if OpenSSL is unavailable.

```powershell
py -3 -m unittest discover -s tests -v
node --test .\tests\status-format.test.mjs .\tests\status-controls.test.mjs
```

```sh
python3 -m unittest discover -s tests -v
node --test tests/status-format.test.mjs tests/status-controls.test.mjs
```

Operations from PowerShell:

```powershell
ssh lon1 'systemctl status isitmeornot.service isitmeornot-tls-renew.timer --no-pager'
ssh lon1 'curl -fsS http://127.0.0.1:8765/healthz'
ssh lon1 'sudo journalctl -u isitmeornot.service -n 50 --no-pager'
ssh lon1 'sudo nginx -t'
```

## Verification and limits

38 Python tests cover target parsing, special/private addresses, DNS pinning, TLS, redirect safety,
resource/rate limits, standalone API/static isolation, Nginx host management and rollback isolation.
Real local TLS fixtures cover verified HTTPS, actual restricted responses and private redirects.
Eight Certbot guard tests cover lock retry/success, bounded waiting, immediate non-lock failures,
command preservation and deployment/renewal wiring. Five unchanged Node tests cover form submission, copy/export data, bulk parsing/limits and CSV safety.
A real local Nginx process served the standalone app via HTTPS, passed a status POST through to a
subprocess worker, rejected a private target and kept a neighbouring virtual host working. Direct
and simulated Cloudflare paths were checked. The branded root page was inspected in Chrome.
The responsive layout and bulk/single UI were previously checked with clearly labelled local fixtures.

Public DNS is unavailable in the build environment, so no simulated value is presented as a live
public-service result. Live London probes, real domain issuance and full root deployment still need
verification after running Deploy.ps1. No API/library versions were changed during this repackaging.

## References

https://docs.python.org/3.10/library/http.client.html
https://docs.python.org/3.10/library/socket.html
https://docs.python.org/3.10/library/ssl.html
https://docs.python.org/3.10/library/subprocess.html
https://nginx.org/en/docs/http/ngx_http_proxy_module.html
https://nginx.org/en/docs/http/ngx_http_realip_module.html
https://eff-certbot.readthedocs.io/en/stable/using.html#webroot
https://www.cloudflare.com/ips-v4
https://www.cloudflare.com/ips-v6

Branding: https://skeletrace.com/home/ and https://whispr.dev/products/
Destinations: https://skeletrace.com/download/ and https://skeletrace.com/works-with/
SkeleTrace mark: whisprer-specops/skeletrace assets/skeletrace-mark.svg.
whispr.dev mark: whispr-dev/website-whispr-dev whispr-dev-site/site/assets/whispr-logo.svg.
