import hashlib
import logging
import re
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from PIL import Image, UnidentifiedImageError

from apps.resources.models import Asset

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_BATCH_SIZE = 50 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".txt",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}


def validated_upload(upload: UploadedFile) -> tuple[bytes, str, str]:
    name = Path(upload.name).name[:200]
    extension = Path(name).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationError("Choose a PDF, DOCX, PPTX, TXT, JPEG, PNG or WebP file.")
    if upload.size > MAX_FILE_SIZE:
        raise ValidationError("Each file must be 10 MB or smaller.")
    data = upload.read(MAX_FILE_SIZE + 1)
    if not data or len(data) > MAX_FILE_SIZE:
        raise ValidationError("The file is empty or larger than 10 MB.")
    content_type = ""
    if extension in {".jpg", ".jpeg", ".png", ".webp"}:
        try:
            with Image.open(BytesIO(data)) as image:
                if image.width * image.height > 25_000_000:
                    raise ValidationError("Choose an image smaller than 25 megapixels.")
                image.load()
                image.thumbnail((1600, 1600))
                output = BytesIO()
                image.convert("RGB").save(output, format="JPEG", quality=88)
                data = output.getvalue()
                name = f"{Path(name).stem[:190]}.jpg"
                content_type = "image/jpeg"
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValidationError(
                "This image could not be opened. Choose another image."
            ) from exc
    elif extension == ".pdf":
        if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-4096:]:
            raise ValidationError("This does not appear to be a complete PDF file.")
        content_type = "application/pdf"
    elif extension == ".txt":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationError("Save the text file as UTF-8 and try again.") from exc
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text):
            raise ValidationError("This text file contains unsupported binary content.")
        content_type = "text/plain"
    else:
        try:
            with ZipFile(BytesIO(data)) as archive:
                entries = archive.infolist()
                names = {entry.filename for entry in entries}
                document = (
                    "word/document.xml"
                    if extension == ".docx"
                    else "ppt/presentation.xml"
                )
                if (
                    len(entries) > 2000
                    or sum(entry.file_size for entry in entries) > 100 * 1024 * 1024
                    or document not in names
                    or "[Content_Types].xml" not in names
                    or any(
                        "vbaproject" in name.lower() or "../" in name for name in names
                    )
                ):
                    raise ValidationError(
                        "Choose a standard document without macros or oversized contents."
                    )
        except BadZipFile as exc:
            raise ValidationError(
                "This document is damaged or has the wrong file type."
            ) from exc
        suffix = (
            "wordprocessingml.document"
            if extension == ".docx"
            else "presentationml.presentation"
        )
        content_type = f"application/vnd.openxmlformats-officedocument.{suffix}"
    return data, content_type, name


@transaction.atomic
def save_upload(
    upload: UploadedFile, user, *, image_only: bool = False, render_timeout: float = 8
) -> Asset:
    data, content_type, name = validated_upload(upload)
    if image_only and not content_type.startswith("image/"):
        raise ValidationError("Choose a JPEG, PNG or WebP cover image.")
    digest = hashlib.sha256(data).hexdigest()
    # Deduplication is owner-scoped so it never reveals another contributor's uploads.
    asset = Asset.objects.defer("data").filter(owner=user, digest=digest).first()
    if asset is not None:
        return asset
    preview = None
    if content_type == "application/pdf":
        try:
            rendered = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("pdf_preview.py"))],
                input=data,
                capture_output=True,
                timeout=render_timeout,
                check=True,
            ).stdout
        except (
            subprocess.TimeoutExpired,
            subprocess.CalledProcessError,
            OSError,
        ) as exc:
            logging.getLogger(__name__).warning(
                "PDF preview failed: %s", type(exc).__name__
            )
            raise ValidationError(
                "This PDF could not be previewed. Upload an unlocked PDF or save a new copy and try again."
            ) from exc
        preview = Asset.objects.create(
            owner=user,
            name=f"{Path(name).stem[:180]}-preview.jpg",
            content_type="image/jpeg",
            size=len(rendered),
            data=rendered,
            digest=hashlib.sha256(rendered).hexdigest(),
        )
    return Asset.objects.create(
        owner=user,
        preview=preview,
        name=name,
        data=data,
        size=len(data),
        content_type=content_type,
        digest=digest,
    )
