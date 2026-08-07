from __future__ import annotations
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace
from mechanical_librarian.application.batch import BatchError
from mechanical_librarian.bootstrap import composition
from mechanical_librarian.bootstrap.composition import run_batch
from mechanical_librarian.application.configuration import ApplicationConfiguration,LibraryConfiguration,MediaMountConfiguration,ProviderConnectionConfiguration,TargetConnectionConfiguration
from mechanical_librarian.application.batch import BatchSelection
from mechanical_librarian.bootstrap.composition import _load_batch_credentials
from mechanical_librarian.domain.metadata_report import ProviderStatus

class BatchCompositionTests(unittest.TestCase):
 def test_provider_retry_after_becomes_bounded_durable_recovery_delay(self):
  transient=SimpleNamespace(providers=(SimpleNamespace(status=ProviderStatus.RATE_LIMITED),))
  attempts=[SimpleNamespace(delay_seconds=12.25),SimpleNamespace(delay_seconds=90_000.0)]
  self.assertEqual(composition._provider_recovery_delay([SimpleNamespace(attempt_records=attempts)],transient),86_400)
  healthy=SimpleNamespace(providers=(SimpleNamespace(status=ProviderStatus.CANDIDATES),))
  self.assertEqual(composition._provider_recovery_delay([SimpleNamespace(attempt_records=attempts)],healthy),0)

 def test_google_books_keyless_keyed_and_disabled_composition(self):
  providers=(
   ProviderConnectionConfiguration("disabled",False,False,None,"google_books"),
   ProviderConnectionConfiguration("keyed",True,False,"/google.secret","google_books"),
   ProviderConnectionConfiguration("keyless",True,False,None,"google_books"),
  )
  config=ApplicationConfiguration(
   (TargetConnectionConfiguration("primary","https://example.invalid","/target"),),
   (LibraryConfiguration("books","primary","library","book","media",("disabled","keyed","keyless")),),
   (MediaMountConfiguration("media","/media","/books",True),),
   providers,
  )
  service=unittest.mock.Mock()
  service.run.return_value="report"
  constructed=[]
  with patch.object(composition,"CoverArtifactStore"),patch.object(composition,"AudiobookshelfItemReader"),patch.object(composition,"google_books_client",side_effect=(object(),object())) as clients,patch.object(composition,"read_secret",return_value="private-key") as secret,patch.object(composition,"GoogleBooksMetadataProvider",side_effect=lambda *args,**kwargs:constructed.append((args,kwargs)) or object()),patch.object(composition,"GenerateMetadataReport",return_value=service) as generate:
   self.assertEqual("report",composition.run_metadata_report_loaded(config,Path("/reports"),Path("/artifacts"),"primary","books","item",0))
   bindings=generate.call_args.kwargs["providers"]
   self.assertEqual((False,True,True),tuple(binding.enabled for binding in bindings))
   for binding in bindings:
    if binding.enabled:
     binding.factory()
  self.assertEqual(2,clients.call_count)
  secret.assert_called_once_with("/google.secret")
  self.assertEqual(("private-key",None),tuple(item[1]["api_key"] for item in constructed))

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
 def test_foreground_report_shares_one_open_library_pacer(self):
  config=ApplicationConfiguration(
   (TargetConnectionConfiguration("primary","https://example.invalid","/target"),),
   (LibraryConfiguration("books","primary","library","book","media",("first","second")),),
   (MediaMountConfiguration("media","/media","/books",True),),
   (
    ProviderConnectionConfiguration("first",True,False,None,"open_library","first@example.org"),
    ProviderConnectionConfiguration("second",True,False,None,"open_library","second@example.org"),
   ),
  )
  shared=object()
  constructed=[]
  service=unittest.mock.Mock()
  service.run.return_value="report"
  with patch.object(composition,"CoverArtifactStore"),patch.object(composition,"AudiobookshelfItemReader"),patch.object(composition,"OpenLibraryPacer",return_value=shared) as pacer,patch.object(composition,"open_library_client",side_effect=(object(),object())),patch.object(composition,"OpenLibraryMetadataProvider",side_effect=lambda *args,**kwargs:constructed.append(kwargs["pacer"]) or object()),patch.object(composition,"GenerateMetadataReport",return_value=service) as generate:
   self.assertEqual("report",composition.run_metadata_report_loaded(config,Path("/reports"),Path("/artifacts"),"primary","books","item",0))
   bindings=generate.call_args.kwargs["providers"]
   for binding in bindings:
    binding.factory()
  pacer.assert_called_once_with()
  self.assertEqual([shared,shared],constructed)

 def test_batch_shares_one_hardcover_pacer_across_entries(self):
  config=ApplicationConfiguration(
   (TargetConnectionConfiguration("primary","https://example.invalid","/target"),),
   (LibraryConfiguration("books","primary","library","book",None,("hardcover",)),),
   (),
   (ProviderConnectionConfiguration("hardcover",True,False,"/provider","hardcover"),),
  )
  entries=tuple(
   {"connection":"primary","library":"books","library_id":"library","item":f"item-{index}","file_identity":f"{index}"*64,"classification":"singular-supported","eligible":True}
   for index in (1,2)
  )
  value={"id":"a"*64,"providers":["hardcover"],"provider_adapters":["hardcover"],"entries":list(entries),"batch_config_fingerprint":"b"*64,"catalogue_generation":1,"catalogue_fingerprint":"c"*64}
  snapshot=SimpleNamespace(generation=1,fingerprint="c"*64)
  catalogue=unittest.mock.Mock()
  catalogue.snapshot.return_value=snapshot
  artifacts=SimpleNamespace(identity=object())
  repository=unittest.mock.Mock()
  repository.identity=object()
  shared=object()
  loaded=unittest.mock.Mock(return_value="{}")
  report_value=SimpleNamespace(providers=(),report_id="report")

  def foreground(**kwargs):
   runner=unittest.mock.Mock()
   runner.run.side_effect=lambda: [
    kwargs["report"](SimpleNamespace(connection="primary",library="books",item_id=f"item-{index}",file_identity=f"{index}"*64))
    for index in (1,2)
   ] and True
   return runner

  with patch.object(composition,"read_batch_manifest",return_value=b"x"),patch.object(composition,"parse_manifest",return_value=value),patch.object(composition,"load_configuration_file",return_value=config),patch.object(composition,"batch_fingerprint",return_value="b"*64),patch.object(composition,"_batch_provider_names",return_value=("hardcover",)),patch.object(composition,"_batch_provider_adapters",return_value=("hardcover",)),patch.object(composition,"_require_open_library_batch_envelope"),patch.object(composition,"open_catalogue",return_value=catalogue),patch.object(composition,"CoverArtifactStore",return_value=artifacts),patch.object(composition,"open_repository",return_value=repository),patch.object(composition,"_load_batch_credentials",return_value={}),patch.object(composition,"HardcoverPacer",return_value=shared) as pacer,patch.object(composition,"run_metadata_report_loaded",loaded),patch.object(composition,"ForegroundBatchRun",side_effect=foreground),patch("mechanical_librarian.domain.metadata_report.MetadataReport.from_json",return_value=report_value):
   self.assertTrue(run_batch(Path("/config"),Path("/catalogue"),Path("/manifest"),"a"*64,Path("/reports"),Path("/artifacts"),lambda _:None))
  pacer.assert_called_once_with()
  self.assertEqual(2,loaded.call_count)
  self.assertTrue(all(call.kwargs["hardcover_pacer"] is shared for call in loaded.call_args_list))

 def test_foreground_report_shares_one_hardcover_pacer(self):
  config=ApplicationConfiguration(
   (TargetConnectionConfiguration("primary","https://example.invalid","/target"),),
   (LibraryConfiguration("books","primary","library","book","media",("first","second")),),
   (MediaMountConfiguration("media","/media","/books",True),),
   (
    ProviderConnectionConfiguration("first",True,False,"/first.secret","hardcover"),
    ProviderConnectionConfiguration("second",True,False,"/second.secret","hardcover"),
   ),
  )
  shared=object()
  constructed=[]
  service=unittest.mock.Mock()
  service.run.return_value="report"
  with patch.object(composition,"CoverArtifactStore"),patch.object(composition,"AudiobookshelfItemReader"),patch.object(composition,"HardcoverPacer",return_value=shared) as pacer,patch.object(composition,"hardcover_client",side_effect=(object(),object())),patch.object(composition,"HardcoverMetadataProvider",side_effect=lambda *args,**kwargs:constructed.append(kwargs["pacer"]) or object()),patch.object(composition,"GenerateMetadataReport",return_value=service) as generate:
   self.assertEqual("report",composition.run_metadata_report_loaded(config,Path("/reports"),Path("/artifacts"),"primary","books","item",0))
   bindings=generate.call_args.kwargs["providers"]
   for binding in bindings:
    binding.factory()
  pacer.assert_called_once_with()
  self.assertEqual([shared,shared],constructed)
