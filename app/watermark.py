"""
Visible provenance watermark, applied to every image before archival.
"""
import io
from datetime import datetime, timezone

from PIL import Image, ImageDraw

from app.config import WATERMARK_TEXT_TEMPLATE


def apply_watermark(image_bytes: bytes, doc_id: str) -> bytes:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    text = WATERMARK_TEXT_TEMPLATE.format(doc_id=doc_id, timestamp=timestamp)

    # Position bottom-right with a small margin; semi-transparent gray text.
    margin = 10
    text_bbox = draw.textbbox((0, 0), text)
    text_w = text_bbox[2] - text_bbox[0]
    text_h = text_bbox[3] - text_bbox[1]
    position = (img.width - text_w - margin, img.height - text_h - margin)

    draw.text(position, text, fill=(128, 128, 128, 180))

    watermarked = Image.alpha_composite(img, overlay).convert("RGB")
    buf = io.BytesIO()
    watermarked.save(buf, format="PNG")
    return buf.getvalue()
