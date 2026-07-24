from __future__ import annotations
import unittest
from mechanical_librarian.application.batch import BatchError, BatchSelection, CatalogueSnapshot, MAX_MANIFEST_BYTES, batch_fingerprint, manifest, parse_manifest, select
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.configuration import ApplicationConfiguration, LibraryConfiguration, ProviderConnectionConfiguration, ReportLimits, TargetConnectionConfiguration

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
 def test_maximum_selection_round_trips_inside_bound(self):
  values=tuple(BatchSelection("primary","books","library",("🙂"*508)+f"{index:04d}","a"*64,"singular-supported",True) for index in range(10_000))
  raw=manifest(CatalogueSnapshot(1,"a"*64,()),"b"*64,values,())
  self.assertLessEqual(len(raw),MAX_MANIFEST_BYTES);self.assertEqual(len(parse_manifest(raw)["entries"]),10_000)
 def test_closed_manifest_entry_and_provider_validation(self):
  base=CatalogueSnapshot(1,"a"*64,())
  valid=BatchSelection("primary","books","library","item","a"*64,"singular-supported",False)
  self.assertEqual(len(parse_manifest(manifest(base,"b"*64,(valid,),("catalogue",)))["entries"]),1)
  for value in (BatchSelection("Bad Name","books","library","item","a"*64,"singular-supported",True),BatchSelection("primary","books","bad/path","item","a"*64,"singular-supported",True),BatchSelection("primary","books","library","item",None,"singular-supported",True),BatchSelection("primary","books","library","item",None,"unknown",False)):
   with self.assertRaises(BatchError):manifest(base,"b"*64,(value,),())
  with self.assertRaises(BatchError):manifest(base,"b"*64,(valid,),("Bad Name",))
 def test_fingerprint_binds_selected_provider_references(self):
  selected=(BatchSelection("primary","books","library","item","a"*64,"singular-supported",True),)
  no_provider=ApplicationConfiguration((TargetConnectionConfiguration("primary","https://example.invalid","/target"),),(LibraryConfiguration("books","primary","library","book",None,()),),(),())
  provider=ApplicationConfiguration((TargetConnectionConfiguration("primary","https://example.invalid","/other-target"),),(LibraryConfiguration("books","primary","library","book",None,("catalogue",)),),(),(ProviderConnectionConfiguration("catalogue",True,False,"/provider"),))
  cover=ApplicationConfiguration((TargetConnectionConfiguration("primary","https://example.invalid","/third-target"),),(LibraryConfiguration("books","primary","library","book",None,("catalogue",)),),(),(ProviderConnectionConfiguration("catalogue",True,True,"/provider-two"),))
  self.assertNotEqual(batch_fingerprint(no_provider,selected),batch_fingerprint(provider,selected));self.assertNotEqual(batch_fingerprint(provider,selected),batch_fingerprint(cover,selected))
