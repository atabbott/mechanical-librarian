from __future__ import annotations
import unittest
from unittest.mock import patch
from mechanical_librarian.application.batch import BatchError
from mechanical_librarian.bootstrap.composition import run_batch
from mechanical_librarian.application.configuration import ApplicationConfiguration,LibraryConfiguration,ProviderConnectionConfiguration,TargetConnectionConfiguration
from mechanical_librarian.application.batch import BatchSelection
from mechanical_librarian.bootstrap.composition import _load_batch_credentials

class BatchCompositionTests(unittest.TestCase):
 def test_namespaced_equal_name_credentials(self):
  config=ApplicationConfiguration((TargetConnectionConfiguration("shared","https://example.invalid","/target"),),(LibraryConfiguration("books","shared","library","book",None,("shared",)),),(),(ProviderConnectionConfiguration("shared",True,False,"/provider"),))
  with patch("mechanical_librarian.bootstrap.composition.read_secret",side_effect=lambda path:"target-token" if path=="/target" else "provider-token"):
   self.assertEqual(_load_batch_credentials(config,{"shared"},{"shared"}),{"target:shared":"target-token","provider:shared":"provider-token"})
 def test_provider_mismatch_precedes_secret_read(self):
  config=ApplicationConfiguration((TargetConnectionConfiguration("primary","https://example.invalid","/target"),),(LibraryConfiguration("books","primary","library","book",None,()),),(),())
  value={"id":"a"*64,"providers":["extra"],"entries":[{"connection":"primary","library":"books","library_id":"library","item":"item","file_identity":"a"*64,"classification":"singular-supported","eligible":True}],"batch_config_fingerprint":"b"*64}
  with patch("mechanical_librarian.bootstrap.composition.read_batch_manifest",return_value=b"x"),patch("mechanical_librarian.bootstrap.composition.parse_manifest",return_value=value),patch("mechanical_librarian.bootstrap.composition.load_configuration_file",return_value=config),patch("mechanical_librarian.bootstrap.composition.batch_fingerprint",return_value="b"*64),patch("mechanical_librarian.bootstrap.composition.read_secret") as secret:
   with self.assertRaises(BatchError):run_batch(__import__("pathlib").Path("/config"),__import__("pathlib").Path("/catalogue"),__import__("pathlib").Path("/manifest"),"a"*64,__import__("pathlib").Path("/reports"),__import__("pathlib").Path("/artifacts"),lambda _:None)
   secret.assert_not_called()
