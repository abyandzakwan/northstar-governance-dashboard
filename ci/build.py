"""Build one deterministic Lambda ZIP and SPA archive for the central portal release."""

from pathlib import Path
import base64
import gzip
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
PACKAGE = BUILD / "package"
BUILD.mkdir(exist_ok=True)
shutil.rmtree(PACKAGE, ignore_errors=True)
subprocess.run(
    [
        "python",
        "-m",
        "pip",
        "install",
        "--target",
        str(PACKAGE),
        "--platform",
        "manylinux_2_28_x86_64",
        "--platform",
        "manylinux2014_x86_64",
        "--implementation",
        "cp",
        "--python-version",
        "3.12",
        "--only-binary=:all:",
        "-r",
        str(ROOT / "requirements.txt"),
    ],
    check=True,
)
shutil.copytree(
    ROOT / "app/functions/portal", PACKAGE / "portal", ignore=shutil.ignore_patterns("__pycache__")
)
shutil.copytree(ROOT / "governance", PACKAGE / "governance")
with zipfile.ZipFile(BUILD / "functions.zip", "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(PACKAGE.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            info = zipfile.ZipInfo(path.relative_to(PACKAGE).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    if sum(i.file_size for i in archive.infolist()) > 250 * 1024 * 1024:
        raise RuntimeError("Lambda bundle exceeds AWS uncompressed size limit")
shutil.rmtree(PACKAGE)
subprocess.run(["npm", "ci"], cwd=ROOT / "app/frontend", check=True)
subprocess.run(["npm", "run", "build"], cwd=ROOT / "app/frontend", check=True)
with (BUILD / "frontend.tar.gz").open("wb") as raw:
    with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for path in sorted((ROOT / "app/frontend/dist").rglob("*")):
                if path.is_file():
                    data = path.read_bytes()
                    info = tarfile.TarInfo(
                        "frontend/" + path.relative_to(ROOT / "app/frontend/dist").as_posix()
                    )
                    info.size, info.mtime, info.mode = len(data), 0, 0o644
                    archive.addfile(info, io.BytesIO(data))
manifest = {
    name: {
        "sha256": hashlib.sha256((BUILD / name).read_bytes()).hexdigest(),
        "base64sha256": base64.b64encode(hashlib.sha256((BUILD / name).read_bytes()).digest()).decode(),
    }
    for name in ("functions.zip", "frontend.tar.gz")
}
(BUILD / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print(json.dumps(manifest, indent=2))
