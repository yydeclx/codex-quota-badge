import unittest
from update_client import version_tuple
from pathlib import Path
import json
from unittest.mock import MagicMock
from update_client import fetch_latest
class UpdateClientTests(unittest.TestCase):
    def test_gateway_attachment_is_never_an_update_package(self):
        response=MagicMock();response.status=200
        response.read.return_value=json.dumps({'tag_name':'v1.5.0','assets':[
            {'name':'opencodex-telemetry-2.92.0-1.5.0.zip','browser_download_url':'https://example.com/gateway'},
            {'name':'codex-quota-badge-1.5.0.zip','browser_download_url':'https://example.com/sidebar'}]}).encode()
        response.__enter__.return_value=response
        self.assertEqual(fetch_latest({'owner':'owner','repo':'repo'},lambda *a,**k:response)['name'],'codex-quota-badge-1.5.0.zip')
        response.read.return_value=json.dumps({'tag_name':'v1.5.0','assets':[
            {'name':'opencodex-telemetry-2.92.0-1.5.0.zip','browser_download_url':'https://example.com/gateway'}]}).encode()
        with self.assertRaisesRegex(ValueError,'No portable ZIP'):fetch_latest({'owner':'owner','repo':'repo'},lambda *a,**k:response)
    def test_versions(self):
        self.assertGreater(version_tuple('v1.2.0'),version_tuple('1.1.9'));self.assertEqual(version_tuple('x'),(0,0,0))
    def test_update_helper_is_silent_on_success_and_reports_failure(self):
        text=Path('update_prompt.py').read_text(encoding='utf-8')
        self.assertIn('process.wait()',text);self.assertIn('messagebox.showerror',text);self.assertNotIn('askyesno',text)
if __name__=='__main__':unittest.main()
