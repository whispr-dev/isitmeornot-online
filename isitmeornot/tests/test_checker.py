import json
import socket
import ssl
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch,MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import checker
from server import Server,Handler


class TargetSafety(unittest.TestCase):
    def test_normalize_https_unicode_and_fragment(self):
        url,host,port,target=checker.normalize_url(' example.com/café?q=one#discard ')
        self.assertEqual(url,'https://example.com/caf%C3%A9?q=one')
        self.assertEqual((host,port,target),('example.com',443,'/caf%C3%A9?q=one'))

    def test_private_special_and_transition_ranges(self):
        for address in ['127.0.0.1','10.1.2.3','169.254.169.254','100.100.100.200','168.63.129.16','192.0.0.8','192.0.2.1','224.0.0.1','::1','fe80::1','fc00::1','::ffff:127.0.0.1','64:ff9b::a00:1','2002:0a00:0001::','2001:db8::1']:
            self.assertFalse(checker.public_address(address),address)
        self.assertTrue(checker.public_address('8.8.8.8'))
        self.assertTrue(checker.public_address('2606:4700:4700::1111'))

    def test_invalid_schemes_credentials_and_ports(self):
        for value in ['file:///etc/passwd','gopher://example.com','http://user:pass@example.com','https://example.com:8443','https://example.com:0','http://127.0.0.1','https://[::1]/','http://localhost','https://example.com/\r\nHeader:value','https://example.com\\@127.0.0.1']:
            with self.assertRaises(checker.InvalidTarget,msg=value):checker.normalize_url(value)

    def test_mixed_dns_answers_fail_closed(self):
        answers=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443)),(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
        with patch.object(checker.socket,'getaddrinfo',return_value=answers):
            with self.assertRaises(checker.InvalidTarget):checker.resolve_public('example.com',443)

    def test_numeric_aliases_are_rejected_after_resolution(self):
        answers=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
        for value in ['https://127.1/','https://2130706433/','https://0x7f000001/']:
            result=checker.probe(value) if '.' not in value.split('//')[1].split('/')[0] else None
            if result:self.assertEqual(result['state'],'blocked')
        with patch.object(checker.socket,'getaddrinfo',return_value=answers),patch.object(checker.socket,'socket') as connect:
            self.assertEqual(checker.probe('https://127.1/')['state'],'blocked')
            connect.assert_not_called()

    def test_socket_uses_pinned_ip_and_tls_checks_hostname(self):
        answers=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))]
        connection=MagicMock();response=connection.getresponse.return_value;response.status=200;response.getheader.return_value=None
        context=MagicMock();sock=MagicMock()
        with patch.object(checker.socket,'getaddrinfo',return_value=answers) as dns,patch.object(checker.socket,'socket',return_value=sock),patch.object(checker.ssl,'create_default_context',return_value=context),patch.object(checker.http.client,'HTTPConnection',return_value=connection):
            result=checker.fetch_headers('https://example.com/path')
            sock.connect.assert_called_once_with(('8.8.8.8',443))
            context.wrap_socket.assert_called_once_with(sock,server_hostname='example.com')
            self.assertEqual(dns.call_count,1)
            self.assertEqual(result[1],200)
            response.read.assert_not_called()
            self.assertEqual(connection.request.call_args.args[:2],('GET','/path'))
            self.assertNotIn('Cookie',connection.request.call_args.kwargs['headers'])

    def test_redirect_into_private_network_never_fetches_it(self):
        with patch.object(checker,'fetch_headers',return_value=('https://example.com/',302,'http://127.0.0.1/admin')) as fetch:
            row=checker.probe('https://example.com')
            self.assertEqual(row['state'],'blocked');self.assertEqual(fetch.call_count,1)

    def test_restrictions_and_http_errors_are_not_network_failure(self):
        for status,state in [(200,'up'),(206,'up'),(403,'restricted'),(429,'restricted'),(404,'http_error'),(503,'http_error')]:
            with patch.object(checker,'fetch_headers',return_value=('https://example.com/',status,None)):
                row=checker.probe('https://example.com')
                self.assertEqual(row['state'],state);self.assertEqual(row['status'],status)

    def test_redirects_validated_and_limited(self):
        def fetch(url):return url,302,url+'next/'
        with patch.object(checker,'fetch_headers',side_effect=fetch) as transport:
            row=checker.probe('https://example.com')
            self.assertEqual(row['state'],'redirect');self.assertEqual(row['redirects'],3);self.assertEqual(transport.call_count,4)

    def test_dns_tls_and_timeout_are_distinct(self):
        for error,state in [(socket.gaierror(),'dns_error'),(ssl.SSLCertVerificationError(),'tls_error'),(TimeoutError(),'timeout')]:
            with patch.object(checker,'fetch_headers',side_effect=error):self.assertEqual(checker.probe('https://example.com')['state'],state)

    def test_parent_has_hard_worker_timeout_and_no_shell(self):
        with patch.object(checker.subprocess,'run',side_effect=subprocess.TimeoutExpired('probe',8)) as run:
            row=checker.limited_probe('https://example.com')
            self.assertEqual(row['state'],'timeout');self.assertEqual(run.call_args.kwargs['timeout'],8)
            self.assertNotIn('shell',run.call_args.kwargs)
            self.assertNotIn('example.com',' '.join(run.call_args.args[0]))

    def test_worker_crash_is_not_labelled_an_outage(self):
        with patch.object(checker.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','')):
            self.assertEqual(checker.limited_probe('https://example.com')['state'],'checker_error')

    def test_limits_and_queue_capacity(self):
        with patch.object(checker,'LIMITS',checker.OrderedDict()),patch.object(checker,'GLOBAL',[checker.time.monotonic(),0]):
            checker.LIMITS['expired']=(checker.time.monotonic()-61,60)
            self.assertTrue(checker.authorize('a',60));self.assertNotIn('expired',checker.LIMITS);self.assertFalse(checker.authorize('a',1))
            self.assertTrue(checker.authorize('b',60));self.assertTrue(checker.authorize('c',60));self.assertTrue(checker.authorize('d',60));self.assertFalse(checker.authorize('e',1))
        with patch.object(checker,'CAPACITY',threading.BoundedSemaphore(1)):
            self.assertIsNone(checker.check_batch(['a','b']))
            self.assertTrue(checker.CAPACITY.acquire(blocking=False));checker.CAPACITY.release()


class StatusAPI(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.server=Server(('127.0.0.1',0),Handler,False)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.base=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def post(self,data,extra=None):
        return urllib.request.urlopen(urllib.request.Request(self.base+'/api/status',data=json.dumps(data).encode(),headers={'Content-Type':'application/json',**(extra or {})}))
    def test_status_page_and_redirect(self):
        for path in ['/','/status','/app.js','/format.js','/style.css']:
            with urllib.request.urlopen(self.base+path) as response:self.assertEqual(response.status,200)
    def test_standalone_identity_and_private_source_isolation(self):
        with urllib.request.urlopen(self.base+'/healthz') as response:
            self.assertEqual(json.load(response),{'ok':True,'app':'isitmeornot'})
        for path in ['/api/me','/ip','/checker.py','/../server.py']:
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(self.base+path)
            self.assertEqual(error.exception.code,404)

    def test_payload_shape_and_size(self):
        for data in [{},{'urls':[]},{'urls':['x']*21},{'urls':[{'url':'x'}]}]:
            with self.assertRaises(urllib.error.HTTPError) as error:self.post(data)
            self.assertEqual(error.exception.code,400)
    def test_cross_origin_rejected_before_work(self):
        with patch.object(checker,'check_batch') as check:
            with self.assertRaises(urllib.error.HTTPError) as error:self.post({'urls':['https://example.com']},{'Origin':'https://other.example'})
            self.assertEqual(error.exception.code,403);check.assert_not_called()
    def test_results_and_no_store(self):
        fixture={'url':'https://example.com/','status':200,'state':'up'}
        with patch.object(checker,'authorize',return_value=True),patch.object(checker,'check_batch',return_value=[fixture]):
            with self.post({'urls':['https://example.com']}) as response:
                self.assertIn('no-store',response.headers['Cache-Control']);self.assertEqual(json.load(response)['results'][0]['state'],'up')
    def test_busy_and_rate_limited(self):
        with patch.object(checker,'authorize',return_value=False):
            with self.assertRaises(urllib.error.HTTPError) as error:self.post({'urls':['https://example.com']})
            self.assertEqual(error.exception.code,429)
        with patch.object(checker,'authorize',return_value=True),patch.object(checker,'check_batch',return_value=None):
            with self.assertRaises(urllib.error.HTTPError) as error:self.post({'urls':['https://example.com']})
            self.assertEqual(error.exception.code,503)

if __name__=='__main__':unittest.main()
