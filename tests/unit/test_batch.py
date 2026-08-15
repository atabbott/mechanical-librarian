from __future__ import annotations

import unittest

from mechanical_librarian.application.batch import (
    MAX_MANIFEST_BYTES,
    BatchError,
    BatchSelection,
    CatalogueSnapshot,
    batch_fingerprint,
    manifest,
    parse_manifest,
    select,
)
from mechanical_librarian.application.catalogue import CatalogueEntry
from mechanical_librarian.application.configuration import (
    ApplicationConfiguration,
    LibraryConfiguration,
    ProviderConnectionConfiguration,
    ReportLimits,
    TargetConnectionConfiguration,
)


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
  value=manifest(CatalogueSnapshot(1,"a"*64,(entry(),)),"b"*64,(BatchSelection("primary","books","library","one","a"*64,"singular-supported",True),),("catalogue",),("hardcover",))
  self.assertNotIn(b"Title",value); self.assertIn(b'"schema":"batch-manifest-v5"',value)
  self.assertEqual(parse_manifest(value)["id"],parse_manifest(value)["id"])
 def test_manifest_rejects_tamper_and_config_fingerprint_excludes_secret_path(self):
  raw=manifest(CatalogueSnapshot(1,"a"*64,()),"b"*64,(),(),())
  with self.assertRaises(BatchError): parse_manifest(raw.replace(b'"entries":[]',b'"entries":[{}]'))
  with self.assertRaises(BatchError): parse_manifest(raw.replace(b"batch-manifest-v5",b"batch-manifest-v4"))
  first=ApplicationConfiguration((TargetConnectionConfiguration("primary","https://example.invalid","/private/a"),),(LibraryConfiguration("books","primary","library","book",None,()),),(),(),ReportLimits())
  second=ApplicationConfiguration((TargetConnectionConfiguration("primary","https://example.invalid","/private/b"),),(LibraryConfiguration("books","primary","library","book",None,()),),(),(),ReportLimits())
  self.assertEqual(batch_fingerprint(first),batch_fingerprint(second))
 def test_maximum_selection_round_trips_inside_bound(self):
  values=tuple(BatchSelection("primary","books","library",("🙂"*508)+f"{index:04d}","a"*64,"singular-supported",True) for index in range(10_000))
  raw=manifest(CatalogueSnapshot(1,"a"*64,()),"b"*64,values,(),())
  self.assertLessEqual(len(raw),MAX_MANIFEST_BYTES);self.assertEqual(len(parse_manifest(raw)["entries"]),10_000)
 def test_closed_manifest_entry_and_provider_validation(self):
  base=CatalogueSnapshot(1,"a"*64,())
  valid=BatchSelection("primary","books","library","item","a"*64,"singular-supported",False)
  self.assertEqual(len(parse_manifest(manifest(base,"b"*64,(valid,),("catalogue",),("hardcover",)))["entries"]),1)
  for value in (BatchSelection("Bad Name","books","library","item","a"*64,"singular-supported",True),BatchSelection("primary","books","bad/path","item","a"*64,"singular-supported",True),BatchSelection("primary","books","library","item",None,"singular-supported",True),BatchSelection("primary","books","library","item",None,"unknown",False)):
   with self.assertRaises(BatchError):manifest(base,"b"*64,(value,),(),())
  with self.assertRaises(BatchError):manifest(base,"b"*64,(valid,),("Bad Name",),("hardcover",))
  with self.assertRaises(BatchError):manifest(base,"b"*64,(valid,),("catalogue",),("unknown",))
 def test_fingerprint_binds_selected_provider_references(self):
  selected=(BatchSelection("primary","books","library","item","a"*64,"singular-supported",True),)
  no_provider=ApplicationConfiguration((TargetConnectionConfiguration("primary","https://example.invalid","/target"),),(LibraryConfiguration("books","primary","library","book",None,()),),(),())
  provider=ApplicationConfiguration((TargetConnectionConfiguration("primary","https://example.invalid","/other-target"),),(LibraryConfiguration("books","primary","library","book",None,("catalogue",)),),(),(ProviderConnectionConfiguration("catalogue",True,False,"/provider"),))
  cover=ApplicationConfiguration((TargetConnectionConfiguration("primary","https://example.invalid","/third-target"),),(LibraryConfiguration("books","primary","library","book",None,("catalogue",)),),(),(ProviderConnectionConfiguration("catalogue",True,True,"/provider-two"),))
  self.assertNotEqual(batch_fingerprint(no_provider,selected),batch_fingerprint(provider,selected));self.assertNotEqual(batch_fingerprint(provider,selected),batch_fingerprint(cover,selected))
 def test_duplicate_manifest_identity_rejects_at_generation_and_parse(self):
  base=CatalogueSnapshot(1,"a"*64,()); value=BatchSelection("primary","books","library","item","a"*64,"singular-supported",True)
  with self.assertRaises(BatchError):manifest(base,"b"*64,(value,value),(),())

 def test_open_library_call_maxima_cover_bounded_descriptor_fan_out_and_contact_is_not_fingerprinted(self):
  base=CatalogueSnapshot(1,"a"*64,()); selected=(BatchSelection("primary","books","library","item","a"*64,"singular-supported",True),)
  raw=manifest(base,"b"*64,selected,("catalogue","open"),("hardcover","open_library"))
  self.assertEqual(parse_manifest(raw)["call_maxima"],{"target_item_observations":6,"target_raw_cover_requests":4,"provider_metadata_requests":76,"remote_cover_requests":24})
  open_only=manifest(base,"b"*64,selected,("open",),("open_library",))
  self.assertEqual(parse_manifest(open_only)["call_maxima"]["remote_cover_requests"],4)
  google=manifest(base,"b"*64,selected,("google",),("google_books",))
  self.assertEqual(parse_manifest(google)["call_maxima"],{"target_item_observations":6,"target_raw_cover_requests":4,"provider_metadata_requests":54,"remote_cover_requests":0})
  first=ApplicationConfiguration((TargetConnectionConfiguration("primary","https://example.invalid","/target"),),(LibraryConfiguration("books","primary","library","book",None,("open",)),),(),(ProviderConnectionConfiguration("open",True,False,None,"open_library","first@example.org"),))
  second=ApplicationConfiguration((TargetConnectionConfiguration("primary","https://example.invalid","/other-target"),),(LibraryConfiguration("books","primary","library","book",None,("open",)),),(),(ProviderConnectionConfiguration("open",True,False,None,"open_library","second@example.org"),))
  self.assertEqual(batch_fingerprint(first,selected),batch_fingerprint(second,selected))
