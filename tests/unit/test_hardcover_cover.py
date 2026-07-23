import unittest
from mechanical_librarian.adapters.providers.hardcover_cover import parse, query

class HardcoverCoverTests(unittest.TestCase):
 def test_closed_descriptor_response(self):
  self.assertEqual((1,2), tuple(__import__('json').loads(query((1,2)))["variables"]["ids"]))
  value=parse(b'{"data":{"editions":[{"id":1,"image":{"url":"https://example.com/a.jpg"}}]}}')
  self.assertEqual(1,value[0].edition_id)
 def test_rejects_extra_response_shape(self):
  with self.assertRaises(ValueError): parse(b'{"data":{"editions":[],"other":1}}')
