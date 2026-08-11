from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class OcrPageResult:
    page: int
    text: str
    confidence: float = 0.0


@dataclass
class OcrDocumentResult:
    pages: list[OcrPageResult] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text)

    @property
    def confidence(self) -> float:
        values = [page.confidence for page in self.pages if page.text]
        return sum(values) / len(values) if values else 0.0


_engine = None
_engine_lock = threading.Lock()


def _get_engine():
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                try:
                    from rapidocr import RapidOCR
                except ImportError as exc:
                    raise RuntimeError("OCR 需要安装 rapidocr 和 onnxruntime") from exc
                _engine = RapidOCR()
    return _engine


def recognize_image(image) -> tuple[str, float]:
    """识别 PIL/NumPy 图像，返回按检测顺序拼接的文本和平均置信度。"""
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("OCR 图像处理需要安装 numpy") from exc

    output = _get_engine()(np.asarray(image))
    texts = list(getattr(output, "txts", ()) or ())
    scores = [float(score) for score in (getattr(output, "scores", ()) or ())]
    text = "\n".join(item.strip() for item in texts if item and item.strip())
    confidence = sum(scores) / len(scores) if scores else 0.0
    return text, confidence


def ocr_pdf(path: str | Path, scale: float = 2.0) -> OcrDocumentResult:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RuntimeError("OCR PDF 页面渲染需要安装 pypdfium2") from exc

    document = pdfium.PdfDocument(str(path))
    pages: list[OcrPageResult] = []
    try:
        for index in range(len(document)):
            page = document[index]
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil()
            text, confidence = recognize_image(image)
            pages.append(
                OcrPageResult(
                    page=index + 1,
                    text=text,
                    confidence=confidence,
                )
            )
            image.close()
            bitmap.close()
            page.close()
    finally:
        document.close()
    return OcrDocumentResult(pages=pages)
