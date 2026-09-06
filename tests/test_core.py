import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import xml.etree.ElementTree as ET
import zipfile
from urllib.error import HTTPError

from avito_client.model import new_ad, build_xml, validate
from avito_client.store import Store
from avito_client.publisher import publish


def listing(title="Футболка"):
    ad = new_ad()
    ad["fields"].update(Title=title, Description="Хлопок & принт <тест>", Price="1200",
        Condition="Новое с биркой", Size="48 (M)", Brand="Без бренда", Color="Белый",
        AdType="Продаю своё", Address="Москва", ContactPhone="+79990000000")
    ad["photos"] = ["photo.jpg"]
    return ad


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_edit_does_not_change_sent_or_queued_snapshot(self):
        ad = listing()
        self.store.enqueue(ad)
        job = self.store.begin_job(self.store.snapshot())
        self.store.complete_job(job, "https://example.com/feed.xml")
        edited = copy.deepcopy(ad)
        edited["fields"]["Title"] = "Новый заголовок"
        self.store.save(edited)
        self.assertEqual(self.store.snapshot(), [ad])
        self.store.enqueue(edited)
        self.assertEqual(self.store.snapshot(), [edited])
        self.assertEqual(edited["id"], ad["id"])

    def test_feed_retains_previous_ads_after_new_batch(self):
        ad1, ad2 = listing("Первая"), listing("Вторая")
        self.store.enqueue(ad1)
        job = self.store.begin_job(self.store.snapshot())
        self.store.complete_job(job, "url")
        self.store.enqueue(ad2)
        self.assertEqual({a["id"] for a in self.store.snapshot()}, {ad1["id"], ad2["id"]})

    def test_new_queued_revision_survives_completion_of_older_job(self):
        ad = listing()
        self.store.enqueue(ad)
        job = self.store.begin_job(self.store.snapshot())
        newer = copy.deepcopy(ad)
        newer["fields"]["Price"] = "1500"
        self.store.enqueue(newer)
        self.store.complete_job(job, "url")
        row = self.store.get(ad["id"])
        self.assertEqual(row["queued"], newer)
        self.assertEqual(row["sent"], ad)

    def test_failure_and_restart_preserve_queue(self):
        ad = listing()
        self.store.enqueue(ad)
        self.store.begin_job(self.store.snapshot())
        self.store.close()
        self.store = Store(self.temp.name)
        self.store.recover_jobs()
        self.assertEqual(self.store.snapshot(), [ad])
        self.assertEqual(self.store.jobs()[0]["status"], "unknown")

    def test_photo_is_copied_and_changes_get_new_address(self):
        source = Path(self.temp.name) / "input.jpg"
        source.write_bytes(b"original")
        first = self.store.import_photo(source)
        source.write_bytes(b"changed")
        second = self.store.import_photo(source)
        source.unlink()
        self.assertNotEqual(first, second)
        self.assertEqual((self.store.root / "photos" / first).read_bytes(), b"original")

    def test_backup_contains_current_database_and_photos(self):
        ad = listing()
        self.store.save(ad)
        (self.store.root / "photos" / "photo.jpg").write_bytes(b"image")
        path = Path(self.temp.name) / "backup.zip"
        self.store.backup(path)
        with zipfile.ZipFile(path) as archive:
            self.assertIn("client.sqlite3", archive.namelist())
            self.assertIn("photos/photo.jpg", archive.namelist())


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        (Path(self.temp.name) / "photos").mkdir()
        (Path(self.temp.name) / "photos" / "photo.jpg").write_bytes(b"photo")
        self.config = dict(endpoint="https://s3.example.com", public_base="https://cdn.example.com/bucket",
                           bucket="bucket", access_key="key", secret_key="secret", prefix="manual/test")

    def tearDown(self):
        self.temp.cleanup()

    def test_xml_last_and_public_links_with_escaped_description(self):
        client = Mock()
        check = Mock()
        url = publish([listing()], self.temp.name, self.config, client=client, public_check=check)
        calls = client.put_object.call_args_list
        self.assertTrue(calls[0].kwargs["Key"].endswith("photo.jpg"))
        self.assertTrue(calls[-1].kwargs["Key"].endswith("feed.xml"))
        root = ET.fromstring(calls[-1].kwargs["Body"])
        self.assertEqual(root.findtext("Ad/Description"), "Хлопок & принт <тест>")
        self.assertTrue(root.find("Ad/Images/Image").attrib["url"].startswith("https://"))
        self.assertEqual(url, "https://cdn.example.com/bucket/manual/test/feed.xml")

    def test_photo_upload_failure_does_not_replace_feed(self):
        client = Mock()
        client.put_object.side_effect = OSError("network failure")
        with self.assertRaisesRegex(RuntimeError, "Ошибка записи в S3"):
            publish([listing()], self.temp.name, self.config, client=client, public_check=Mock())
        self.assertEqual(client.put_object.call_count, 1)
        self.assertNotIn("feed.xml", client.put_object.call_args.kwargs["Key"])

    def test_private_photo_does_not_replace_feed(self):
        client = Mock()
        with self.assertRaises(PermissionError):
            publish([listing()], self.temp.name, self.config, client=client,
                    public_check=Mock(side_effect=PermissionError("403")))
        self.assertEqual(client.put_object.call_count, 1)

    def test_public_404_identifies_photo_url_and_keeps_feed_untouched(self):
        client = Mock()
        error = HTTPError("https://cdn.example.com/missing", 404, "Not Found", {}, None)
        with self.assertRaisesRegex(RuntimeError, "Фотография") as raised:
            publish([listing()], self.temp.name, self.config, client=client,
                    public_check=Mock(side_effect=error))
        self.assertIn("HTTP 404", str(raised.exception))
        self.assertIn("https://cdn.example.com/bucket/manual/test/photos/photo.jpg", str(raised.exception))
        self.assertEqual(client.put_object.call_count, 1)

    def test_feed_404_identifies_xml_after_successful_photo_check(self):
        client = Mock()
        error = HTTPError("https://cdn.example.com/missing", 404, "Not Found", {}, None)
        with self.assertRaisesRegex(RuntimeError, "XML-фид") as raised:
            publish([listing()], self.temp.name, self.config, client=client,
                    public_check=Mock(side_effect=[None, error]))
        self.assertIn("manual/test/feed.xml", str(raised.exception))
        self.assertEqual(client.put_object.call_count, 2)

    def test_missing_photo_fails_before_any_upload(self):
        ad = listing()
        ad["photos"] = ["missing.jpg"]
        client = Mock()
        with self.assertRaises(ValueError):
            publish([ad], self.temp.name, self.config, client=client)
        client.put_object.assert_not_called()

    def test_invalid_listing_and_duplicate_id_rejected(self):
        ad = listing()
        for price in ["NaN", "Infinity", "-1", "1.5", "oops"]:
            ad["fields"]["Price"] = price
            self.assertTrue(validate(ad))
        ad = listing()
        with self.assertRaises(ValueError):
            build_xml([ad, ad], lambda name: "https://example.com/" + name)


if __name__ == "__main__":
    unittest.main()
