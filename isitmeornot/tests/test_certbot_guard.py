import contextlib
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from certbot_guard import CertbotBusy,run_certbot

class CertbotRetry(unittest.TestCase):
    def setUp(self):
        self.time=0
        self.delays=[]
        self.calls=[]
    def clock(self):return self.time
    def sleep(self,seconds):self.delays.append(seconds);self.time+=seconds
    def runner(self,results):
        values=iter(results)
        def run(command,**kwargs):
            self.calls.append((command,kwargs))
            return next(values)
        return run
    def run_guard(self,results,**kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return run_certbot(['certbot','certonly','--domain','isitmeornot.online'],clock=self.clock,sleeper=self.sleep,runner=self.runner(results),**kwargs)
    def test_lock_contention_waits_then_succeeds(self):
        busy=subprocess.CompletedProcess([],1,'Another instance of Certbot is already running.\n')
        ok=subprocess.CompletedProcess([],0,'Certificate issued.\n')
        self.assertEqual(self.run_guard([busy,busy,ok]).returncode,0)
        self.assertEqual(self.delays,[10,10]);self.assertEqual(len(self.calls),3)
    def test_non_lock_failure_is_never_retried(self):
        failure=subprocess.CompletedProcess([],1,'Domain validation failed: unauthorized\n')
        self.assertEqual(self.run_guard([failure]).returncode,1)
        self.assertEqual(self.delays,[]);self.assertEqual(len(self.calls),1)
    def test_wait_limit_reports_contention(self):
        busy=subprocess.CompletedProcess([],1,'Another instance of Certbot is already running.')
        with self.assertRaisesRegex(CertbotBusy,'stayed locked'):
            self.run_guard([busy,busy,busy],wait_seconds=15,retry_seconds=10)
        self.assertEqual(self.delays,[10,5]);self.assertEqual(self.time,15)
    def test_zero_wait_has_no_sleep(self):
        busy=subprocess.CompletedProcess([],1,'Another instance of Certbot is already running.')
        with self.assertRaises(CertbotBusy):self.run_guard([busy],wait_seconds=0)
        self.assertEqual(self.delays,[])
    def test_success_message_containing_old_lock_text_does_not_retry(self):
        result=subprocess.CompletedProcess([],0,'Resolved: another instance of certbot is already running')
        self.assertEqual(self.run_guard([result]).returncode,0);self.assertEqual(self.delays,[])
    def test_command_and_environment_preserved_without_shell(self):
        self.run_guard([subprocess.CompletedProcess([],0,'')])
        command,options=self.calls[0]
        self.assertEqual(command,['certbot','certonly','--domain','isitmeornot.online'])
        self.assertNotIn('shell',options);self.assertEqual(options['env']['LC_ALL'],'C')
        self.assertEqual(options['stderr'],subprocess.STDOUT)
    def test_renewal_and_upload_use_guard(self):
        root=Path(__file__).resolve().parents[1]
        self.assertIn('checker.py certbot_guard.py deploy-server.py',(root/'Deploy.ps1').read_text())
        self.assertIn("('server.py','checker.py','certbot_guard.py'):shutil.copy2",(root/'deploy-server.py').read_text())
        source=(root/'nginx_frontend.py').read_text()
        self.assertIn('ExecStart={python} {guard} --wait-seconds 600 -- {certbot} renew',source)
    def test_invalid_retry_values_are_rejected(self):
        with self.assertRaises(ValueError):self.run_guard([],wait_seconds=-1)
        with self.assertRaises(ValueError):self.run_guard([],retry_seconds=0)

if __name__=='__main__':unittest.main()
