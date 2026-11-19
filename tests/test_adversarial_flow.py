"""Reproduce delivery regressions using real controller transitions and captured pages."""
from datetime import datetime, timezone
from pathlib import Path
import json
import shutil
import unittest
from unittest.mock import patch

import test_workflow as fixtures
from workflow import WorkflowError
from workflow_definition import facts
from workflow_teaching import ALIPAY_URL, GOOGLE_IMAGES, ROOT
from workflow_view import compact
import network_server


class AdversarialFlowTests(unittest.TestCase):
    setUp = fixtures.WorkflowTests.setUp
    data = fixtures.WorkflowTests.data
    payload = fixtures.WorkflowTests.payload
    reach = fixtures.WorkflowTests.reach
    advance = fixtures.WorkflowTests.advance

    def test_google_questions_and_personal_login_do_not_show_delivery_materials(self):
        self.reach('google')
        self.assertNotIn('customer_images', self.flow.next())
        result = self.flow.submit(self.payload({'account_available': True}))
        self.assertNotIn('customer_images', result)
        self.flow.submit(self.payload({'account_origin': 'self_used'}))
        self.reach('login')
        self.assertNotIn('customer_images', self.flow.next())
        self.assertEqual(self.flow.next()['verification_images'], GOOGLE_IMAGES[1:])
        self.assertEqual(self.flow.guide('google_2fa')['customer_images'], GOOGLE_IMAGES[1:])

    def test_selected_purchase_channel_keeps_complete_preview(self):
        self.reach('google')
        result = self.flow.submit(self.payload({'account_available': False, 'purchase_channel': 'chuhai91'}))
        self.assertEqual(len(result['customer_images']), 6)
        self.assertTrue(all(p in result['customer_images'] for p in GOOGLE_IMAGES))
        result = self.flow.submit(self.payload({'account_available': True, 'account_origin': 'purchased'}))
        self.assertEqual(result['stage'], 'google')
        self.assertEqual(result['customer_images'], GOOGLE_IMAGES)

    def test_phone_gallery_and_explicit_support_branch(self):
        self.reach('phone_proxy')
        setup = ['assets/images/network/14.jpg', 'assets/images/browser-iphone/10.jpg']
        expected = [setup[0], 'assets/step-guides/phone-qr-import.png', setup[1]]
        for update in ({}, {'client_available': True}):
            result = self.flow.submit(self.payload(update)) if update else self.flow.next()
            self.assertEqual(result['customer_images'], expected)
        result = self.flow.submit(self.payload({'client_available': False, 'account_origin': 'purchased'}))
        self.assertEqual(result['customer_images'], setup + ['assets/images/apple/09.jpg'])
        result = self.flow.submit(self.payload({'purchase_restricted': True}))
        self.assertEqual(result['customer_images'], ['assets/images/apple/18.jpg', 'assets/images/apple/19.jpg'])
        result = self.flow.submit(self.payload({'purchase_restricted': False}))
        self.assertNotIn('assets/images/apple/18.jpg', result['customer_images'])
        result = self.flow.submit(self.payload({'teaching_topic':'apple_support'}))
        self.assertNotEqual(result.get('default_teaching', {}).get('topic'), 'apple_support')
        self.assertNotIn('assets/images/apple/18.jpg', result['customer_images'])

    def test_phone_specific_help_returns_diagram_and_openable_link(self):
        self.reach('phone_proxy')
        for topic, filename in (('phone_dns', 'phone-dns-save.png'), ('phone_backup', 'phone-config-backup.png')):
            with self.subTest(topic=topic):
                result = self.flow.guide(topic)
                self.assertEqual(result['customer_images'], [f'assets/step-guides/{filename}'])
                self.assertIn(result['image_links'][0], result['customer_message'])
                self.assertEqual(self.flow.next()['stage'], 'phone_proxy')

    def test_payment_existing_account_and_channel_galleries(self):
        self.reach('payment_preparation')
        result = self.flow.submit(self.payload({'account_origin':'self_used', 'account_type':'longstanding', 'region':'US'}))
        self.assertEqual(result['customer_images'], [f'assets/images/apple/{i:02}.jpg' for i in (9,14,15)])
        result = self.flow.submit(self.payload({'store_signed_in': True}))
        self.assertEqual(result['customer_images'], [f'assets/images/apple/{i:02}.jpg' for i in (14,15)])
        p = self.payload({'gift_channel': 'apple_official'}); p['source']['kind'] = 'user'
        result = self.flow.submit(p)
        self.assertEqual(result['customer_images'], [f'assets/images/apple/{i:02}.jpg' for i in (10,11,12,13,15)])
        p = self.payload({'gift_channel': 'already_funded'}); p['source']['kind'] = 'user'
        result = self.flow.submit(p)
        self.assertNotIn('customer_images', result)

    def test_register_and_region_explicit_selection_keep_whole_gallery(self):
        self.reach('payment_preparation')
        result = self.flow.submit(self.payload({'teaching_topic': 'apple_register'}))
        self.assertEqual(result['customer_images'], ['assets/images/apple/01.jpg', 'assets/images/apple/02.jpg'])
        result = self.flow.submit(self.payload({'teaching_topic': 'apple_region'}))
        self.assertEqual(result['customer_images'], [f'assets/images/apple/{i:02}.jpg' for i in range(3,9)])

    def test_phone_account_change_preserves_proxy_and_resets_account(self):
        self.reach('phone_proxy')
        proxy = {'node_label':'node-a', 'client_available': True, 'vpn_connected': True, 'global_route':True}
        self.flow.submit(self.payload({**proxy, 'apple_account_ref':'apple-a', 'store_signed_in':True, 'account_origin':'self_used'}))
        result = self.flow.submit(self.payload({'apple_account_ref':'apple-b'}))
        actual = facts(self.flow.state, 'phone_proxy')
        for key,value in proxy.items(): self.assertEqual(actual[key],value)
        self.assertNotIn('store_signed_in', actual)
        self.assertNotIn('account_origin', actual)
        self.assertEqual(result['stage'], 'phone_proxy')

    def test_browser_change_preserves_explanation_but_requires_new_verification(self):
        self.reach('browser')
        self.flow.submit(self.payload({'profile_ref':'old', 'checked_profile_ref':'old', 'usage_explained':True, 'proxy_configured':True}))
        result = self.flow.submit(self.payload({'profile_ref':'new'}))
        self.assertEqual(facts(self.flow.state, 'browser'), {'profile_ref':'new', 'usage_explained':True})
        self.assertIn('proxy_configured', result['missing'])
        self.assertIn('checked_profile_ref', result['missing'])

    def test_two_scores_and_user_continue_still_require_third_site(self):
        for stage in ('browser','phone_network'):
            self.reach(stage)
            data = self.data(stage)
            data['scores']['netcoffee'] = {'value':None, 'result':'检测网站超时，另外两站已取得实际结果'}
            data.update(score_review='continue', score_review_note='客户明确希望继续')
            p=self.payload(data);p['source']['kind']='user'
            result=self.flow.submit(p)
            self.assertEqual(result['stage'], stage)
            self.assertIn('scores', result['missing'])
            result=self.flow.submit(self.payload({'scores':self.data(stage)['scores']}))
            self.assertNotEqual(result['stage'],stage)

    def test_existing_to_purchase_split_cannot_bypass_capture(self):
        from test_vps_purchase import observation
        self.reach('vps')
        self.flow.submit(self.payload({'vps_origin':'existing', 'access_ready':True}))
        item=observation(); item['checked_at']=self.now.isoformat()
        with self.assertRaisesRegex(WorkflowError, '先核对'):
            self.flow.submit(self.payload({'vps_origin':'purchase', 'purchase_checks':{'dmit':item}, 'node_label':'node-a','node_timezone':'America/Los_Angeles'}))
        self.assertEqual(self.flow.next()['stage'], 'vps')
        self.assertEqual(facts(self.flow.state,'vps')['vps_origin'], 'existing')

    def test_current_offer_capture_recheck_and_purchase(self):
        from vps_purchase import CURRENT_OFFER
        self.reach('vps')
        shutil.copytree(ROOT/'scripts', self.root/'scripts', ignore=shutil.ignore_patterns('__pycache__'))
        url=CURRENT_OFFER['url']
        snapshot=self.root/'.local/page.json'
        snapshot.write_text(json.dumps({'url':url,'text':'Configure Product\nUSCA_9 2 GB\nBilling Cycle\nQuarterly $89.99 USD\nRenewal $89.99 USD Quarterly\nContinue'}))
        inputs=self.root/'.local/input.json'
        inputs.write_text(json.dumps({'vendor':'bandwagon','url':url,'browser_file':str(snapshot)}))
        execution=self.flow.run('vps_capture', inputs)['execution']
        self.assertTrue(execution['success'])
        self.now=datetime.now(timezone.utc);page=execution['result']
        item={**CURRENT_OFFER,'page':'configuration','status':'available','checked_at':page['checked_at'],'receipt_id':execution['id'],
              'evidence':'USCA_9 2 GB','billing_evidence':'Quarterly $89.99 USD\nRenewal $89.99 USD Quarterly'}
        result=self.flow.submit(self.payload({'vps_origin':'purchase','purchase_checks':{'bandwagon':item}}))
        self.assertTrue(result['purchase_decision']['can_purchase'])
        self.assertEqual(result['purchase_decision']['plan'],'USCA_9-2GB')
        self.assertNotIn('DMIT →', result['next_action'])
        self.assertEqual(self.flow.submit(self.payload({'access_ready':True,'node_label':'node-a','node_timezone':'America/Los_Angeles'}))['stage'],'server')
        self.assertIn('server_readback',self.flow.next()['missing'])

    def test_current_offer_recheck_rejects_wrong_size_and_preserves_sold_out(self):
        from vps_purchase import CURRENT_OFFER, check_observation, evaluate
        item={**CURRENT_OFFER, 'page':'configuration','status':'available','checked_at':self.now.isoformat(), 'evidence':'USCA_9 1 GB'}
        self.assertEqual(check_observation('bandwagon',item,self.now), 'plan_evidence_required')
        item.update(status='sold_out',evidence='USCA_9 2 GB Out of Stock')
        result=evaluate({'bandwagon':item},self.now)
        self.assertFalse(result['can_purchase'])
        self.assertEqual(result['checked'], [{'vendor':'bandwagon','status':'sold_out'}])
        self.assertEqual(result['source'],'current_offer_check')

    def test_changed_checks_cannot_reuse_partial_ready_flag(self):
        from test_vps_purchase import observation
        self.reach('vps')
        self.flow.submit(self.payload({'vps_origin':'purchase', 'access_ready':True}))
        item=observation(); item['checked_at']=self.now.isoformat()
        with self.assertRaisesRegex(WorkflowError, '先核对'):
            self.flow.submit(self.payload({'purchase_checks':{'dmit':item}, 'node_label':'node-a','node_timezone':'America/Los_Angeles'}))
        self.assertEqual(self.flow.next()['stage'],'vps')

    def test_alipay_and_compact_actions_do_not_depend_on_prose_slicing(self):
        self.assertIn(ALIPAY_URL, (ROOT/'references/03-apple.md').read_text())
        view=compact({'stage':'google','next_action':'保留 按当前操作从 teaching_topics 这段原始动作', 'teaching_next_action':'独立教学说明'})
        self.assertEqual(view['next_action'],'保留 按当前操作从 teaching_topics 这段原始动作')
        self.assertNotIn('teaching_next_action',view)


class ListenerTests(unittest.TestCase):
    def test_ipv4_listener_without_ipv6_file_and_public_listener_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            def proc_path(value):return folder/Path(value).name
            (folder/'tcp').write_text('header\n0: 0100007F:497B 00000000:0000 0A\n')
            with patch.object(network_server, 'Path', side_effect=proc_path):
                network_server.panel_listen_verified(18811)
                (folder/'tcp6').write_text('header\n0: 00000000000000000000000000000000:497B 00000000000000000000000000000000:0000 0A\n')
                with self.assertRaisesRegex(network_server.Failure,'panel_not_loopback_only'):
                    network_server.panel_listen_verified(18811)
