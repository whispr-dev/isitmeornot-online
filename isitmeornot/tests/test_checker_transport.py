"""Real local HTTP/TLS fixtures; no external DNS or public requests."""
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import checker


@unittest.skipUnless(shutil.which('openssl'),'OpenSSL CLI is needed for the local verified-TLS fixture')
class VerifiedTransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory();root=Path(cls.temp.name)
        cls.cert=root/'cert.pem';key=root/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=example.com','-addext','subjectAltName=DNS:example.com','-keyout',str(key),'-out',str(cls.cert)],check=True,capture_output=True)
        cls.observed=[]
        class Fixture(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_GET(self):
                cls.observed.append((self.path,self.headers.get('Host'),self.headers.get('Cookie')))
                if self.path=='/redirect':
                    self.send_response(302);self.send_header('Location','/ok')
                elif self.path=='/private':
                    self.send_response(302);self.send_header('Location','http://127.0.0.1/admin')
                elif self.path=='/restricted':self.send_response(403)
                else:self.send_response(200)
                self.send_header('Content-Length','0');self.end_headers()
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cls.cert,key)
        cls.server.socket=context.wrap_socket(cls.server.socket,server_side=True)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.context=ssl.create_default_context(cafile=str(cls.cert))
        original_socket=socket.socket
        destination=cls.server.server_port
        class RoutedSocket(original_socket):
            def connect(self,address):
                if address != ('8.8.8.8',443):raise AssertionError('Unexpected outbound address '+str(address))
                return super().connect(('127.0.0.1',destination))
        cls.routed=RoutedSocket
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()
    def run_probe(self,url,context=None):
        records=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))]
        with patch.object(checker.socket,'getaddrinfo',return_value=records),patch.object(checker.socket,'socket',self.routed),patch.object(checker.ssl,'create_default_context',return_value=context or self.context):
            return checker.probe(url)
    def test_verified_https_and_redirect(self):
        row=self.run_probe('https://example.com/redirect')
        self.assertEqual((row['state'],row['status'],row['redirects']),('up',200,1))
        self.assertEqual(row['final_url'],'https://example.com/ok')
        self.assertEqual(self.observed[-1],('/ok','example.com',None))
        self.assertTrue(self.context.check_hostname);self.assertEqual(self.context.verify_mode,ssl.CERT_REQUIRED)
    def test_real_restricted_response(self):
        row=self.run_probe('https://example.com/restricted');self.assertEqual((row['state'],row['status']),('restricted',403))
    def test_private_redirect_is_not_contacted(self):
        count=len(self.observed);row=self.run_probe('https://example.com/private')
        self.assertEqual(row['state'],'blocked');self.assertEqual(len(self.observed)-count,1)
    def test_untrusted_certificate_is_rejected(self):
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname=True;context.verify_mode=ssl.CERT_REQUIRED
        row=self.run_probe('https://example.com/ok',context)
        self.assertEqual(row['state'],'tls_error')

if __name__=='__main__':unittest.main()
