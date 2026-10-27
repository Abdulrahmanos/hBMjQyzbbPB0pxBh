"""Prove sidecar isolation, fill idempotency and hourly cached configuration."""
import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from bitcoin_agent.config import load_config
from bitcoin_agent.integration_io import DeliveryError
from bitcoin_agent.notifications import DemoInbox
from bitcoin_agent.paper_engine import PaperEngine
from bitcoin_agent.paper_store import connection
from bitcoin_agent.remote_config import control_rows, ConfigManager
from tools.cloud_connectors import telegram_cycle, sheets_cycle, ServiceAccountRows, service_account, SCOPE, TOKEN_URL
from tools.install_cloud_connectors import compose_config, telegram_credentials
from test_paper_engine import quote, features

NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)

class CloudConnectorsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / 'paper'; self.runtime.mkdir()
        (self.runtime / 'cloud-manifest.json').write_text(json.dumps({'run_id':'a'*32,'fingerprint':{'frozen':True}}))
        self.config = load_config(ROOT / 'config/demo.json')
        self.engine = PaperEngine(self.runtime / 'ledger.sqlite3', self.config)
        self.state = self.root / 'connector'
        self.sender = DemoInbox(self.root / 'inbox')
        self.freeze = patch('tools.cloud_gmail.fingerprint', return_value={'frozen':True})
        self.freeze.start(); self.addCleanup(self.freeze.stop)

    def hash(self):
        return hashlib.sha256((self.runtime / 'ledger.sqlite3').read_bytes()).hexdigest()

    def source(self, rows=None):
        rows = rows or control_rows(self.config)
        return SimpleNamespace(fetch=lambda: rows)

    def test_backfill_then_new_fill_and_restart_never_duplicate_or_write_ledger(self):
        self.engine.tick(quote(NOW-timedelta(days=1)), features(NOW-timedelta(days=1)), NOW-timedelta(days=1))
        before = self.hash()
        first = telegram_cycle(self.runtime, self.state, NOW, self.sender)
        self.assertEqual(len(first['outbox']), 1)
        self.assertEqual(self.hash(), before)
        payload = json.loads(next((self.root / 'inbox').glob('*.json')).read_text())
        self.assertTrue(payload['backfill'])
        self.assertIn('catch-up', payload['text'])
        self.engine.tick(quote(NOW+timedelta(days=1)), features(NOW+timedelta(days=1)), NOW+timedelta(days=1))
        before = self.hash()
        second = telegram_cycle(self.runtime, self.state, NOW+timedelta(days=1), self.sender)
        self.assertEqual(len(second['outbox']), 2)
        self.assertEqual(telegram_cycle(self.runtime,self.state,NOW+timedelta(days=1,seconds=30),self.sender)['delivery_results'], [])
        self.assertEqual(self.hash(), before)
        with connection(self.runtime / 'ledger.sqlite3') as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='notification_outbox'").fetchone())

    def test_ambiguous_delivery_is_preserved_for_review(self):
        self.engine.tick(quote(NOW), features(NOW), NOW)
        class Unknown:
            local = False
            def send(self,*args): raise DeliveryError('network_failure', ambiguous=True)
        self.assertTrue(telegram_cycle(self.runtime,self.state,NOW,Unknown())['needs_attention'])
        again = telegram_cycle(self.runtime,self.state,NOW+timedelta(minutes=5),self.sender)
        self.assertEqual(again['delivery_results'], [])
        self.assertFalse((self.root/'inbox').exists())

    def test_run_change_freeze_change_and_state_overlap_rejected(self):
        telegram_cycle(self.runtime,self.state,NOW,self.sender)
        with self.assertRaises(ValueError): telegram_cycle(self.runtime,self.runtime/'state',NOW,self.sender)
        with patch('tools.cloud_gmail.fingerprint',return_value={'changed':True}):
            with self.assertRaises(ValueError): telegram_cycle(self.runtime,self.state,NOW,self.sender)
        manifest = self.runtime/'cloud-manifest.json'
        manifest.write_text(json.dumps({'run_id':'b'*32,'fingerprint':{'frozen':True}}))
        with self.assertRaises(ValueError): telegram_cycle(self.runtime,self.state,NOW,self.sender)

    def test_hourly_read_restart_cache_and_invalid_edit_fallback(self):
        before = self.hash()
        rows = control_rows(self.config)
        for row in rows:
            if row[0] == 'dca.enabled': row[1] = False
        one = sheets_cycle(self.runtime,self.state,NOW,self.source(rows),baseline=self.config)
        self.assertFalse(one['applied_to_trades'])
        self.assertTrue(one['changed'])
        cached = ConfigManager(self.config,self.state/'validated-config.json')
        self.assertFalse(cached.current.data['dca']['enabled'])
        self.assertEqual(cached.current.funding_checksum,self.config.funding_checksum)
        def fail(): raise ValueError('DO NOT LOG PRIVATE PROVIDER RESPONSE')
        bad = SimpleNamespace(fetch=fail)
        self.assertEqual(sheets_cycle(self.runtime,self.state,NOW+timedelta(minutes=59),bad,baseline=self.config),one)
        two = sheets_cycle(self.runtime,self.state,NOW+timedelta(hours=1),bad,baseline=self.config)
        self.assertEqual(two['status'],'remote_rejected_using_last_valid')
        self.assertEqual(two['checksum'],one['checksum'])
        self.assertNotIn('PRIVATE',json.dumps(two))
        self.assertEqual(self.hash(), before)
        self.assertTrue(self.engine.config.data['dca']['enabled'])

    def test_excess_risk_edit_rejected_and_corrupt_cache_uses_baseline(self):
        rows=control_rows(self.config)
        for row in rows:
            if row[0]=='swing.risk_fraction_of_equity': row[1]=0.99
        result=sheets_cycle(self.runtime,self.state,NOW,self.source(rows),baseline=self.config)
        self.assertTrue(result['needs_attention'])
        self.assertEqual(result['checksum'],self.config.checksum)
        (self.state/'validated-config.json').write_text('invalid')
        self.assertEqual(ConfigManager(self.config,self.state/'validated-config.json').current.checksum,self.config.checksum)

    def test_service_account_fixed_endpoint_and_read_only_claims(self):
        cred={'type':'service_account','client_email':'reader@demo-project.iam.gserviceaccount.com',
              'token_uri':TOKEN_URL,'private_key_id':'a'*40,'private_key':'-----BEGIN ' 'PRIVATE KEY-----\nfake\n'}
        with self.assertRaises(ValueError): service_account({**cred,'token_uri':'https://elsewhere.invalid'})
        p=self.root/'secret.json'; p.write_text(json.dumps({'sheet_id':'test_sheet_id','service_account':cred}))
        class Response:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,*args): return b'{"access_token":"test-access"}'
        with patch('tools.cloud_connectors.subprocess.run',return_value=SimpleNamespace(stdout=b'signed')) as signing, \
             patch('tools.cloud_connectors.urlopen',return_value=Response()) as request, \
             patch('tools.cloud_connectors.http_json',return_value={'values':[['Setting','Value']]}) as api:
            self.assertEqual(ServiceAccountRows(p).fetch(),[['Setting','Value']])
        import base64
        assertion=parse_qs(request.call_args.args[0].data.decode())['assertion'][0]
        encoded=assertion.split('.')[1]
        claims=json.loads(base64.urlsafe_b64decode(encoded+'='*((4-len(encoded)%4)%4)))
        self.assertEqual(claims['scope'],SCOPE); self.assertNotIn('sub',claims)
        self.assertEqual(claims['exp']-claims['iat'],600)
        self.assertNotIn('fake',repr(signing.call_args.args))
        self.assertTrue(api.call_args.args[0].startswith('https://sheets.googleapis.com/'))

    def test_mounts_and_secrets_are_separate_and_image_immutable(self):
        conf=compose_config('sha256:'+'a'*64,'evidence',self.root)
        self.assertEqual(set(conf['services']),{'telegram','sheets'})
        for spec in conf['services'].values():
            self.assertIn('paper_evidence:/app/evidence:ro',spec['volumes'])
            self.assertEqual(spec['user'],'10001:10001')
            self.assertEqual(spec['pull_policy'],'never'); self.assertNotIn('ports',spec)
        self.assertNotIn('env_file',conf['services']['sheets'])
        self.assertFalse(any('sheets.json' in m for m in conf['services']['telegram']['volumes']))
        with self.assertRaises(ValueError): telegram_credentials({'TELEGRAM_BOT_TOKEN':'bad','TELEGRAM_CHAT_ID':'1'})

if __name__=='__main__': unittest.main()
