from __future__ import annotations
import unittest
from mechanical_librarian.application.batch import BatchError, BatchSelection, CatalogueSnapshot, batch_fingerprint, manifest, parse_manifest, select
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.configuration import ApplicationConfiguration, LibraryConfiguration, ReportLimits, TargetConnectionConfiguration

def entry(item="one", eligible=True):
    return CatalogueEntry("primary","books","library",item,1,"Title",(),True,False,"singular-supported" if eligible else "needs-attention","a"*64,"book.epub","epub","books" if eligible else None)

class BatchTests(unittest.TestCase):
 def test_selection_preserves_requested_order_and_all_eligible(self):
  snapshot=CatalogueSnapshot(1,"a"*64,(entry("one"),entry("two",False)))
  self.assertEqual([x.item for x in select(snapshot,(("primary","books","library","one"),),False)],["one"])
  self.assertEqual([x.item for x in select(snapshot,(),True)],["one"])
 def test_invalid_selection_fails(self):
  with self.assertRaises(BatchError): select(CatalogueSnapshot(1,"a"*64,(entry(),)),(),False)
 def test_manifest_is_canonical_and_private(self):
  value=manifest(CatalogueSnapshot(1,"a"*64,(entry(),)),"b"*64,(BatchSelection("primary","books","library","one","a"*64,"singular-supported",True),),("catalogue",))
  self.assertNotIn(b"Title",value); self.assertIn(b'"schema":"batch-manifest-v1"',value)
  self.assertEqual(parse_manifest(value)["id"],parse_manifest(value)["id"])
 def test_manifest_rejects_tamper_and_config_fingerprint_excludes_secret_path(self):
  raw=manifest(CatalogueSnapshot(1,"a"*64,()),"b"*64,(),())
  with self.assertRaises(BatchError): parse_manifest(raw.replace(b'"entries":[]',b'"entries":[{}]'))
  first=ApplicationConfiguration((TargetConnectionConfiguration("primary","https://example.invalid","/private/a"),),(LibraryConfiguration("books","primary","library","book",None,()),),(),(),ReportLimits())
  second=ApplicationConfiguration((TargetConnectionConfiguration("primary","https://example.invalid","/private/b"),),(LibraryConfiguration("books","primary","library","book",None,()),),(),(),ReportLimits())
  self.assertEqual(batch_fingerprint(first),batch_fingerprint(second))
