import os
from pathlib import Path

# --- Confidence routing ---------------------------------------------------
REVIEW_THRESHOLD = float(os.getenv("REVIEW_THRESHOLD", "0.75"))

# --- Models -----------------------------------------------------------------
VISION_MODEL = os.getenv("VISION_MODEL", "gpt-4o")
MODERATION_MODEL = os.getenv("MODERATION_MODEL", "omni-moderation-latest")

# --- Moderation score thresholds --------------------------------------------
MODERATION_BLOCK_THRESHOLD = float(os.getenv("MODERATION_BLOCK_THRESHOLD", "0.5"))
MODERATION_REVIEW_THRESHOLD = float(os.getenv("MODERATION_REVIEW_THRESHOLD", "0.2"))

# --- Cost tracking (USD per 1K tokens, approximate, gpt-4o vision) ---------
COST_PER_1K_INPUT_TOKENS = float(os.getenv("COST_PER_1K_INPUT_TOKENS", "0.005"))
COST_PER_1K_OUTPUT_TOKENS = float(os.getenv("COST_PER_1K_OUTPUT_TOKENS", "0.015"))

# --- Storage -----------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", str(BASE_DIR / "uploads")))
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'ledgerlens.db'}")

# --- Image pre-processing ----------------------------------------------------
MAX_IMAGE_DIMENSION_PX = int(os.getenv("MAX_IMAGE_DIMENSION_PX", "2048"))

# --- Watermark ----------------------------------------------------------------
WATERMARK_TEXT_TEMPLATE = "{doc_id} | {timestamp}"
