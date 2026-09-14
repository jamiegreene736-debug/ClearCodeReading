"""Render one PDF page in a bounded process; never execute embedded PDF actions."""

import sys
from io import BytesIO


def main() -> None:
    if sys.platform == "linux":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
    import pypdfium2 as pdfium

    data = sys.stdin.buffer.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise ValueError("PDF is too large")
    with pdfium.PdfDocument(data) as document:
        page = document[0]
        width, height = page.get_size()
        if width <= 0 or height <= 0:
            raise ValueError("PDF page has no dimensions")
        bitmap = page.render(scale=min(1400 / max(width, height), 2))
        image = bitmap.to_pil().convert("RGB")
        output = BytesIO()
        image.save(output, format="JPEG", quality=85)
        image.close()
        bitmap.close()
        sys.stdout.buffer.write(output.getvalue())


if __name__ == "__main__":
    main()
