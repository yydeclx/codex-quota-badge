import unittest
from sidebar_details import diagnostic
from pathlib import Path


class DiagnosticTests(unittest.TestCase):
    def test_copy_schema_excludes_sensitive_fields(self):
        snapshot={'updatedAt':1,'chains':{'openai':{
            'state':'idle','health':[None,None],
            'latest':{'status':200,'servedModel':'gpt-test','prompt':'secret','token':'secret'},
            'requestBody':'secret'}}}
        result=diagnostic(snapshot,'openai')
        self.assertIn('gpt-test',result)
        self.assertNotIn('secret',result)
    def test_status_center_exposes_version_badge_states(self):
        text=Path('sidebar_details.py').read_text(encoding='utf-8')
        for label in ('最新','待更新','未检查','检查异常'):self.assertIn(label,text)
        self.assertIn('version_badge',text)


if __name__=='__main__':unittest.main()
