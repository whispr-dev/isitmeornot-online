import subprocess
import unittest
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from nginx_frontend import NginxFrontend, MARKER, directives, nginx_config


class NginxContracts(unittest.TestCase):
    def test_only_cloudflare_socket_peers_supply_forwarded_ip(self):
        config = nginx_config(['173.245.48.0/20'],True)
        self.assertIn('geo $realip_remote_addr $isitmeornot_cloudflare_peer',config)
        self.assertIn('default $realip_remote_addr;',config)
        self.assertIn('1 $http_cf_connecting_ip;',config)
        self.assertIn('proxy_set_header X-IsItMeOrNot-IP $isitmeornot_client_ip;',config)
        self.assertNotIn('set_real_ip_from',config)
        self.assertIn('proxy_cache off;',config)
        self.assertNotIn('default_server',config)

    def test_bootstrap_exposes_challenge_without_personal_data(self):
        config = nginx_config(['173.245.48.0/20'],False,'$remote_addr')
        self.assertIn('location ^~ /.well-known/acme-challenge/',config)
        self.assertIn('return 503;',config)
        self.assertNotIn('listen 443',config)
        self.assertNotIn('proxy_pass',config)

    def test_quoted_directives_and_comments(self):
        config = '# server_name ignored;\nserver { server_name "isitmeornot.online" example.com; }'
        self.assertEqual(list(directives(config,'server_name')),[['isitmeornot.online','example.com']])

    def test_reject_existing_domain_elsewhere(self):
        dump = '# configuration file /etc/nginx/nginx.conf:\nhttp { include /etc/nginx/conf.d/*.conf; }\n# configuration file /etc/nginx/sites-enabled/old:\nserver { server_name isitmeornot.online; }\n'
        def result(*args):
            return subprocess.CompletedProcess(args,0,stdout=dump if '-T' in args else '',stderr='--with-http_realip_module')
        with patch('nginx_frontend.command',side_effect=result),patch.object(Path,'exists',return_value=False),patch.object(Path,'is_symlink',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'already declares'):
                NginxFrontend().preflight()

    def test_existing_managed_config_is_redeployable(self):
        path='/etc/nginx/conf.d/isitmeornot.conf'
        config=nginx_config(['173.245.48.0/20'],True)
        dump='# configuration file /etc/nginx/nginx.conf:\nhttp { include /etc/nginx/conf.d/*.conf; }\n# configuration file '+path+':\n'+config
        def result(*args):
            return subprocess.CompletedProcess(args,0,stdout=dump if '-T' in args else '',stderr='--with-http_realip_module')
        with patch('nginx_frontend.command',side_effect=result),patch.object(Path,'exists',return_value=True),patch.object(Path,'is_symlink',return_value=False),patch.object(Path,'read_text',return_value=config):
            frontend=NginxFrontend();frontend.preflight()
            self.assertEqual(frontend.path,Path(path))
            self.assertEqual(frontend.peer,'$realip_remote_addr')

    def test_unmanaged_destination_is_not_overwritten(self):
        dump='# configuration file /etc/nginx/nginx.conf:\nhttp { include /etc/nginx/conf.d/*.conf; }\n'
        with patch('nginx_frontend.command',return_value=subprocess.CompletedProcess([],0,stdout=dump,stderr='')),patch.object(Path,'exists',return_value=True),patch.object(Path,'is_symlink',return_value=False),patch.object(Path,'read_text',return_value='unmanaged'):
            with self.assertRaisesRegex(RuntimeError,'not managed'):
                NginxFrontend().preflight()

    def test_restore_preserves_unrelated_site(self):
        with TemporaryDirectory() as directory:
            base=Path(directory)
            site=base/'isitmeornot.conf';site.write_text(MARKER+'\nnew')
            other=base/'litehaus.conf';other.write_text('existing site')
            auxiliary=tuple(base/name for name in ('renew.service','renew.timer','reload.sh'))
            auxiliary[0].write_text('previous service')
            backup=base/'backup';backup.mkdir()
            frontend=NginxFrontend();frontend.path=site;frontend.original=None
            with patch('nginx_frontend.AUXILIARY',auxiliary):
                frontend.snapshot(backup)
                for p in auxiliary:p.write_text('changed')
                with patch('nginx_frontend.command'),patch('nginx_frontend.subprocess.run'):
                    frontend.restore()
            self.assertFalse(site.exists())
            self.assertEqual(other.read_text(),'existing site')
            self.assertEqual(auxiliary[0].read_text(),'previous service')
            self.assertFalse(auxiliary[1].exists())
            self.assertFalse(auxiliary[2].exists())

if __name__=='__main__':
    unittest.main()
