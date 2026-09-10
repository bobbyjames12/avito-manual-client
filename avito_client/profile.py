"""Portable private S3 settings. Never publish these files with a release."""
import json
from pathlib import Path
from .publisher import check_config
from .secrets import protect


def read_profile(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("format") != "avito-s3-profile-v1":
        raise ValueError("Это не файл настроек клиента Авито.")
    config = data.get("s3")
    keys = ("endpoint", "bucket", "region", "prefix", "public_base", "access_key", "secret_key")
    if not isinstance(config, dict) or any(not isinstance(config.get(k), str) for k in keys):
        raise ValueError("В файле не хватает настроек S3.")
    result = {k: config[k].strip() for k in keys}
    result["public_acl"] = config.get("public_acl") is True
    check_config(result)
    return result


def save_profile(store, config):
    encrypted = dict(config)
    for key in ("access_key", "secret_key"):
        encrypted[key] = protect(config[key])
    store.setting("s3", json.dumps(encrypted))
    store.setting("feed_prefix", config["prefix"])
