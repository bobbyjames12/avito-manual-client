import copy
import unittest
import xml.etree.ElementTree as ET
from test_core import listing
from avito_client.model import field_issues, build_xml, validate, CHOICES


class OfficialRulesTests(unittest.TestCase):
    def test_report_errors_rejected_before_upload(self):
        for key, value in [("Brand", "Южный парк"), ("Condition", "Новое без бирки"),
                           ("Size", "52 (XL)"), ("GoodsType", "Женская одежда"),
                           ("AdType", "Продаю своё"), ("Delivery", "")]:
            with self.subTest(key=key):
                ad = listing()
                ad["fields"][key] = value
                self.assertIn(key, field_issues(ad))

    def test_all_ten_subtypes_supported(self):
        ad = listing()
        for value in CHOICES["GoodsSubType"]:
            ad["fields"]["GoodsSubType"] = value
            self.assertFalse(validate(ad))

    def test_delivery_conflict_and_custom_courier(self):
        ad = listing()
        for value in ["Выключена | ПВЗ", "Свой курьер", "ПВЗ | ПВЗ"]:
            ad["fields"]["Delivery"] = value
            self.assertIn("Delivery", field_issues(ad))

    def test_multi_value_xml(self):
        ad = listing()
        ad["fields"].update(Delivery="ПВЗ | Курьер", MaterialsOdezhda="Хлопок | Полиэстер")
        root = ET.fromstring(build_xml([ad], lambda x: "https://example.com/photo.png"))
        self.assertEqual([x.text for x in root.findall("Ad/Delivery/Option")], ["ПВЗ", "Курьер"])
        self.assertEqual([x.text for x in root.findall("Ad/MaterialsOdezhda/Option")], ["Хлопок", "Полиэстер"])

    def test_limits(self):
        for key, length in [("Title", 50), ("Description", 7500), ("ManagerName", 40), ("Address", 256)]:
            ad = listing()
            ad["fields"][key] = "а" * length
            self.assertNotIn(key, field_issues(ad))
            ad["fields"][key] += "а"
            self.assertIn(key, field_issues(ad))

    def test_photo_limit(self):
        ad = listing()
        ad["photos"] = [f"{i}.jpg" for i in range(11)]
        self.assertIn("photos", field_issues(ad))

    def test_price_serializes_as_integer(self):
        ad = listing()
        ad["fields"]["Price"] = "3000,0"
        root = ET.fromstring(build_xml([ad], lambda x: "https://example.com/photo.png"))
        self.assertEqual(root.findtext("Ad/Price"), "3000")

    def test_phone_and_title(self):
        ad = listing()
        ad["fields"]["ContactPhone"] = "+7 900 123-45-67"
        self.assertNotIn("ContactPhone", field_issues(ad))
        ad["fields"]["ContactPhone"] = "123"
        self.assertIn("ContactPhone", field_issues(ad))
        ad["fields"]["Title"] = "Продам худи"
        self.assertIn("Title", field_issues(ad))
