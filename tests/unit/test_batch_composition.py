from __future__ import annotations
import unittest
from unittest.mock import patch
from mechanical_librarian.application.configuration import ApplicationConfiguration,LibraryConfiguration,ProviderConnectionConfiguration,TargetConnectionConfiguration
from mechanical_librarian.application.batch import BatchSelection
from mechanical_librarian.bootstrap.composition import _load_batch_credentials

class BatchCompositionTests(unittest.TestCase):
 def test_namespaced_equal_name_credentials(self):
  config=ApplicationConfiguration((TargetConnectionConfiguration("shared","https://example.invalid","/target"),),(LibraryConfiguration("books","shared","library","book",None,("shared",)),),(),(ProviderConnectionConfiguration("shared",True,False,"/provider"),))
  with patch("mechanical_librarian.bootstrap.composition.read_secret",side_effect=lambda path:"target-token" if path=="/target" else "provider-token"):
   self.assertEqual(_load_batch_credentials(config,{"shared"},{"shared"}),{"target:shared":"target-token","provider:shared":"provider-token"})
