from pathlib import Path
import mimetypes
import re
from urllib.parse import quote, urlsplit
from urllib.request import urlopen
from .model import build_xml


def check_config(config):
    for key in ("endpoint", "bucket", "access_key", "secret_key", "prefix", "public_base"):
        if not config.get(key, "").strip():
            raise ValueError(f"Заполните настройку S3: {key}")
    for key in ("endpoint", "public_base"):
        parsed = urlsplit(config[key])
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment or parsed.username:
            raise ValueError(f"{key}: требуется постоянный HTTPS-адрес без пароля и параметров.")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9/_-]*", config["prefix"]) or "//" in config["prefix"]:
        raise ValueError("Префикс: используйте латинские буквы, цифры, /, - и _.")


def public_url(config, key):
    return config["public_base"].rstrip("/") + "/" + quote(key, safe="/")


def publish(ads, root, config, progress=lambda message: None, client=None, public_check=None):
    check_config(config)
    if not client:
        import boto3
        from botocore.config import Config
        client = boto3.client("s3", endpoint_url=config["endpoint"],
            aws_access_key_id=config["access_key"], aws_secret_access_key=config["secret_key"],
            region_name=config.get("region") or "ru-central1",
            config=Config(signature_version="s3v4", connect_timeout=15, read_timeout=45,
                retries={"max_attempts": 3}, s3={"addressing_style": "path", "payload_signing_enabled": False},
                request_checksum_calculation="when_required", response_checksum_validation="when_required"))
    prefix = config["prefix"].strip("/")
    photo_key = lambda name: f"{prefix}/photos/{name}"
    xml = build_xml(ads, lambda name: public_url(config, photo_key(name)))
    photos = sorted({name for ad in ads for name in ad["photos"]})
    for name in photos:
        if Path(name).name != name or not (Path(root) / "photos" / name).is_file():
            raise ValueError("Фотография не найдена в локальной библиотеке: " + name)

    def check(url, expected=None):
        if public_check:
            return public_check(url, expected)
        with urlopen(url, timeout=30) as response:
            if response.status != 200:
                raise RuntimeError("Файл недоступен Авито по публичной ссылке.")
            if expected is not None and response.read() != expected:
                raise RuntimeError("Публичный XML ещё не обновился. Проверьте кэш S3/CDN и повторите отправку.")
            if expected is None:
                response.read(1)

    def put(key, data, content_type):
        kwargs = dict(Bucket=config["bucket"], Key=key, Body=data, ContentType=content_type)
        if config.get("public_acl"):
            kwargs["ACL"] = "public-read"
        kwargs["CacheControl"] = "no-cache" if content_type.startswith("application/xml") else "public, max-age=31536000, immutable"
        client.put_object(**kwargs)

    for index, name in enumerate(photos, 1):
        progress(f"Фотографии: {index} из {len(photos)}")
        path = Path(root) / "photos" / name
        with path.open("rb") as source:
            put(photo_key(name), source, mimetypes.guess_type(name)[0] or "image/jpeg")
        check(public_url(config, photo_key(name)))
    # Commit the complete feed only after every referenced photo is available.
    progress("Обновление полного XML-фида…")
    key = f"{prefix}/feed.xml"
    put(key, xml, "application/xml; charset=utf-8")
    url = public_url(config, key)
    check(url, xml)
    return url
