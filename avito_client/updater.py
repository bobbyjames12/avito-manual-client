"""Verified release downloads; replacement runs only after this process exits."""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
import zipfile

VERSION = "0.3.0"
REPO = "bobbyjames12/avito-manual-client"
ASSET = "AvitoManualClient-Windows.zip"


def version(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value)
    if not match:
        raise ValueError("Неподдерживаемый номер версии")
    return tuple(map(int, match.groups()))


def latest():
    request = Request(f"https://api.github.com/repos/{REPO}/releases/latest", headers={"User-Agent": "AvitoManualClient", "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=15) as response:
        release = json.load(response)
    if release.get("draft") or release.get("prerelease") or version(release["tag_name"]) <= version(VERSION):
        return None
    asset = next((a for a in release["assets"] if a["name"] == ASSET), None)
    if not asset or not re.fullmatch(r"sha256:[0-9a-f]{64}", asset.get("digest") or ""):
        raise ValueError("У релиза нет архива с контрольной суммой SHA-256")
    expected = f"https://github.com/{REPO}/releases/download/{release['tag_name']}/{ASSET}"
    if asset["browser_download_url"] != expected:
        raise ValueError("Неверный адрес обновления")
    return {"tag": release["tag_name"], "url": expected, "sha256": asset["digest"][7:], "size": asset["size"]}


def extract_release(archive, directory):
    with zipfile.ZipFile(archive) as z:
        total = 0
        for entry in z.infolist():
            path = PurePosixPath(entry.filename)
            if ("\\" in entry.filename or ":" in entry.filename or path.is_absolute() or ".." in path.parts
                    or not path.parts or path.parts[0] != "AvitoManualClient"
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("Небезопасный путь в архиве обновления")
            total += entry.file_size
            if total > 1500 * 1024 * 1024:
                raise ValueError("Архив обновления слишком большой")
        z.extractall(directory)
    folder = Path(directory) / "AvitoManualClient"
    if not (folder / "AvitoManualClient.exe").is_file() or not (folder / "_internal").is_dir():
        raise ValueError("Неполная сборка клиента")
    return folder


def download(info, progress=lambda message: None):
    if not getattr(sys, "frozen", False):
        raise ValueError("Установка обновлений доступна в EXE-сборке")
    target = Path(sys.executable).resolve().parent
    stage = Path(tempfile.mkdtemp(prefix=".avito-update-", dir=target.parent))
    archive = stage / "update.zip"
    try:
        digest = hashlib.sha256()
        size = 0
        with urlopen(Request(info["url"], headers={"User-Agent": "AvitoManualClient"}), timeout=30) as response, archive.open("wb") as out:
            if urlsplit(response.url).scheme != "https":
                raise ValueError("Обновление требует HTTPS")
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 300 * 1024 * 1024:
                    raise ValueError("Обновление слишком большое")
                digest.update(chunk)
                out.write(chunk)
                progress(f"Скачивание обновления: {size // (1024 * 1024)} МБ")
        if size != info["size"] or digest.hexdigest() != info["sha256"]:
            raise ValueError("Контрольная сумма обновления не совпала. Попробуйте ещё раз.")
        folder = extract_release(archive, stage)
        progress("Проверка запуска новой версии…")
        smoke_result = stage / "smoke-result.txt"
        subprocess.run([str(folder / "AvitoManualClient.exe"), "--smoke-test", str(smoke_result)],
                       timeout=45, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if not smoke_result.is_file() or not smoke_result.read_text(encoding="utf-8").startswith("PASS:"):
            raise ValueError("Новая версия не прошла проверку запуска")
        profile = target / "settings.avitoconfig"
        if profile.is_file():
            shutil.copy2(profile, folder / profile.name)
        return folder
    except Exception:
        shutil.rmtree(stage)
        raise


def install_script(folder, target, process_id):
    # Literals are PowerShell-quoted, never interpolated as executable shell text.
    def literal(path):
        return "'" + str(path).replace("'", "''") + "'"
    folder, target = Path(folder).resolve(), Path(target).resolve()
    if folder.parent.parent != target.parent or not folder.parent.name.startswith(".avito-update-"):
        raise ValueError("Неверная папка обновления")
    backup = target.with_name(target.name + ".previous-" + folder.parent.name[14:])
    return f'''$ErrorActionPreference = 'Stop'
$targetDir = {literal(target)}
$sourceDir = {literal(folder)}
$backupDir = {literal(backup)}
$logFile = Join-Path $env:LOCALAPPDATA 'AvitoManualClient\\update-error.txt'
try {{
    Wait-Process -Id {int(process_id)} -Timeout 120 -ErrorAction SilentlyContinue
    if (Get-Process -Id {int(process_id)} -ErrorAction SilentlyContinue) {{ throw 'Client is still running' }}
    Move-Item -LiteralPath $targetDir -Destination $backupDir
    try {{ Move-Item -LiteralPath $sourceDir -Destination $targetDir }}
    catch {{ Move-Item -LiteralPath $backupDir -Destination $targetDir; throw }}
    Start-Process -FilePath (Join-Path $targetDir 'AvitoManualClient.exe') -WorkingDirectory $targetDir -WindowStyle Hidden
}} catch {{
    $_ | Out-String | Set-Content -LiteralPath $logFile
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show('Не удалось установить обновление. Предыдущая версия сохранена. Подробности: ' + $logFile, 'Обновление клиента')
}}
'''


def launch_install(folder):
    script = install_script(folder, Path(sys.executable).parent, os.getpid())
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-EncodedCommand", encoded],
                     cwd=str(Path(sys.executable).parent.parent), creationflags=subprocess.CREATE_NO_WINDOW)
