from __future__ import annotations

from decimal import Decimal, InvalidOperation
from uuid import uuid4
from pathlib import Path
import json
import re
import difflib
import xml.etree.ElementTree as ET

CATALOG = json.loads(Path(__file__).with_name("catalog.json").read_text(encoding="utf-8"))
CHOICES = CATALOG["choices"]
MULTIPLE = {"Delivery", "MaterialsOdezhda"}
SUPPORTED_DELIVERY = {"Выключена", "ПВЗ", "Курьер", "Постамат"}
FIELDS = {
    "Title": "Заголовок", "Price": "Цена, ₽", "Description": "Описание",
    "Category": "Категория", "GoodsType": "Раздел", "Apparel": "Тип одежды",
    "GoodsSubType": "Вид", "Condition": "Состояние", "Size": "Размер",
    "Brand": "Бренд", "Color": "Цвет", "MaterialsOdezhda": "Материал",
    "AdType": "Тип объявления", "Delivery": "Доставка", "Address": "Адрес",
    "ManagerName": "Контактное лицо", "ContactPhone": "Телефон",
    "ContactMethod": "Способ связи",
}
REQUIRED = {"Title", "Description", "Price", "Category", "GoodsType", "Apparel",
            "GoodsSubType", "Condition", "Size", "Brand", "AdType", "Address", "Delivery"}
LIMITS = {"Title": 50, "Description": 7500, "ManagerName": 40, "Address": 256}


def new_ad():
    return {"id": "manual-" + uuid4().hex, "fields": {
        "Category": "Одежда, обувь, аксессуары", "GoodsType": "Мужская одежда",
        "Apparel": "Кофты и футболки", "GoodsSubType": "Футболка",
        "ContactMethod": "По телефону и в сообщениях",
    }, "photos": []}


def values(text):
    return [v.strip().replace("\xa0", " ") for v in text.split("|") if v.strip()]


def field_issues(ad):
    f = ad.get("fields", {})
    errors = {}
    def error(key, message):
        errors.setdefault(key, []).append(message)
    for key in FIELDS:
        value = f.get(key, "")
        if not isinstance(value, str):
            error(key, "Неверный формат. Откройте поле и заполните его заново.")
            continue
        value = value.strip().replace("\xa0", " ")
        if key in REQUIRED and not value:
            error(key, "Выберите значение из списка." if key in CHOICES else "Заполните поле.")
            continue
        if key in LIMITS and len(value) > LIMITS[key]:
            error(key, f"Не более {LIMITS[key]} символов; сейчас {len(value)}. Сократите текст.")
        if any(ord(c) < 32 and c not in "\t\n\r" for c in value):
            error(key, "Удалите управляющие символы, недопустимые в XML.")
        if value and key in CHOICES:
            selected = values(value) if key in MULTIPLE else [value]
            for option in selected:
                if option not in CHOICES[key]:
                    nearby = difflib.get_close_matches(option, CHOICES[key], n=3, cutoff=0.65)
                    hint = " Возможные варианты: " + ", ".join(nearby) + "." if nearby else ""
                    if key == "Brand":
                        hint += " Выберите бренд с бирки; если бренда нет — «Без бренда»."
                    elif len(CHOICES[key]) <= 10:
                        hint += " Допустимо: " + ", ".join(CHOICES[key]) + "."
                    error(key, f"«{option}» отсутствует в справочнике Авито.{hint}")
            if len(selected) != len(set(selected)):
                error(key, "Удалите повторяющиеся значения.")
        if key == "Price" and value:
            try:
                price = Decimal(value.replace(",", "."))
                if not price.is_finite() or price < 0 or price != price.to_integral_value():
                    raise InvalidOperation
            except InvalidOperation:
                error(key, "Укажите целое число рублей, не меньше нуля, например 3000.")
        if key == "ContactPhone" and value:
            digits = re.sub(r"\D", "", value)
            if re.search(r"[^0-9+() \-]", value) or not ((len(digits) == 11 and digits[0] in "78") or (len(digits) == 10 and digits[0] == "9")):
                error(key, "Укажите один российский номер: +7 900 123-45-67 или 8 900 123-45-67.")
        if key == "Title" and re.search(r"\bпродам\b", value, re.I):
            error(key, "Уберите слово «продам» из заголовка — правило Авито.")
    delivery = values(f.get("Delivery", "")) if isinstance(f.get("Delivery", ""), str) else []
    if "Выключена" in delivery and len(delivery) > 1:
        error("Delivery", "«Выключена» не сочетается с другими способами. Снимите её или отключите остальные.")
    if any(v in CHOICES["Delivery"] and v not in SUPPORTED_DELIVERY for v in delivery):
        error("Delivery", "Собственная доставка требует дополнительных настроек, которых пока нет в клиенте. Выберите ПВЗ, Курьер, Постамат или Выключена.")
    photos = ad.get("photos", [])
    if not photos:
        error("photos", "Добавьте хотя бы одну фотографию JPEG или PNG.")
    elif len(photos) > 10:
        error("photos", f"Максимум 10 фотографий. Удалите лишние {len(photos) - 10}.")
    if not re.fullmatch(r"[0-9A-Za-zА-Яа-яЁё ,\\/()\[\]\-=]{1,100}", str(ad.get("id", ""))):
        error("id", "Недопустимый ID объявления. Создайте новую карточку, не меняя ID уже отправленной.")
    return errors


def validate(ad):
    return [f"{FIELDS.get(key, 'Фотографии' if key == 'photos' else 'ID')}: {message}"
            for key, messages in field_issues(ad).items() for message in messages]


def validate_assets(ad, root):
    errors = []
    for index, name in enumerate(ad.get("photos", []), 1):
        path = Path(root) / "photos" / name
        if Path(name).name != name or not path.is_file():
            errors.append(f"Фото {index}: файл не найден. Удалите его из карточки и добавьте заново.")
        elif path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            errors.append(f"Фото {index}: разрешены только JPEG и PNG.")
        elif path.stat().st_size > 25 * 1024 * 1024:
            errors.append(f"Фото {index}: превышен размер 25 МБ. Уменьшите файл и добавьте заново.")
    return errors


def build_xml(ads, image_url):
    root = ET.Element("Ads", formatVersion="3", target="Avito.ru")
    ids = set()
    for ad in ads:
        errors = validate(ad)
        if errors:
            raise ValueError((ad.get("fields", {}).get("Title") or ad["id"]) + ":\n" + "\n".join(errors))
        if ad["id"] in ids:
            raise ValueError("Повторяющийся идентификатор объявления.")
        ids.add(ad["id"])
        item = ET.SubElement(root, "Ad")
        ET.SubElement(item, "Id").text = ad["id"]
        for key in FIELDS:
            value = ad["fields"].get(key, "").strip()
            if value:
                if key in MULTIPLE:
                    element = ET.SubElement(item, key)
                    for option in values(value):
                        ET.SubElement(element, "Option").text = option
                elif key == "Price":
                    ET.SubElement(item, key).text = str(int(Decimal(value.replace(",", "."))))
                else:
                    ET.SubElement(item, key).text = value.replace("\xa0", " ") if key in CHOICES else value
        images = ET.SubElement(item, "Images")
        for photo in ad["photos"]:
            ET.SubElement(images, "Image", url=image_url(photo))
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
