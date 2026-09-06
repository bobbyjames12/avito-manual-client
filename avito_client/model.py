from __future__ import annotations

from decimal import Decimal, InvalidOperation
from uuid import uuid4
import xml.etree.ElementTree as ET


FIELDS = {
    "Title": "Заголовок", "Price": "Цена, ₽", "Description": "Описание",
    "Category": "Категория", "GoodsType": "Раздел", "Apparel": "Тип одежды",
    "GoodsSubType": "Вид", "Condition": "Состояние", "Size": "Размер",
    "Brand": "Бренд", "Color": "Цвет", "MaterialsOdezhda": "Материал",
    "AdType": "Тип объявления", "Address": "Адрес",
    "ManagerName": "Контактное лицо", "ContactPhone": "Телефон",
    "ContactMethod": "Способ связи",
}


def new_ad():
    return {"id": "manual-" + uuid4().hex, "fields": {
        "Category": "Одежда, обувь, аксессуары", "GoodsType": "Мужская одежда",
        "Apparel": "Кофты и футболки", "GoodsSubType": "Футболка",
        "ContactMethod": "По телефону и в сообщениях",
    }, "photos": []}


def validate(ad):
    f = ad["fields"]
    required = ["Title", "Description", "Price", "Category", "GoodsType", "Apparel",
                "GoodsSubType", "Condition", "Size", "Brand", "Color", "AdType", "Address"]
    if f.get("ContactMethod") != "В сообщениях":
        required.append("ContactPhone")
    errors = [f"Заполните поле «{FIELDS[k]}»." for k in required if not f.get(k, "").strip()]
    if any(any(ord(c) < 32 and c not in "\t\n\r" for c in value) for value in f.values()):
        errors.append("В тексте есть недопустимые для XML управляющие символы. Удалите их.")
    if len(f.get("Title", "")) > 50:
        errors.append("Заголовок должен содержать не более 50 символов (шаблон пайплайна).")
    try:
        price = Decimal(f.get("Price", "").replace(",", "."))
        if not price.is_finite() or price < 0 or price != price.to_integral_value():
            raise InvalidOperation
    except InvalidOperation:
        errors.append("Укажите цену целым числом, не меньше нуля.")
    if not ad.get("photos"):
        errors.append("Добавьте хотя бы одну фотографию.")
    return errors


def build_xml(ads, image_url):
    root = ET.Element("Ads", formatVersion="3", target="Avito.ru")
    ids = set()
    for ad in ads:
        errors = validate(ad)
        if errors:
            raise ValueError("\n".join(errors))
        if ad["id"] in ids:
            raise ValueError("Повторяющийся идентификатор объявления.")
        ids.add(ad["id"])
        item = ET.SubElement(root, "Ad")
        ET.SubElement(item, "Id").text = ad["id"]
        for key in FIELDS:
            value = ad["fields"].get(key, "").strip()
            if value:
                ET.SubElement(item, key).text = value
        images = ET.SubElement(item, "Images")
        for photo in ad["photos"]:
            ET.SubElement(images, "Image", url=image_url(photo))
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
