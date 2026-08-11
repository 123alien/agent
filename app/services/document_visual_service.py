from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class VisualDetection:
    page: int
    detection_type: str
    bbox: tuple[int, int, int, int]
    confidence: float
    recognized_text: str = ""
    ocr_confidence: float = 0.0
    detector: str = "red-seal-rule-v1"


@dataclass
class VisualAnalysisResult:
    detections: list[VisualDetection] = field(default_factory=list)
    analyzed_pages: int = 0
    warnings: list[str] = field(default_factory=list)


def _render_pdf(path: Path, scale: float = 1.8):
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise RuntimeError("视觉检测需要安装 pypdfium2") from exc

    document = pdfium.PdfDocument(str(path))
    try:
        for index in range(len(document)):
            page = document[index]
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil().convert("RGB")
            try:
                yield index + 1, image
            finally:
                image.close()
                bitmap.close()
                page.close()
    finally:
        document.close()


def _detect_red_seals(image, page_number: int) -> list[VisualDetection]:
    import cv2
    import numpy as np

    rgb = np.asarray(image)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    mask1 = cv2.inRange(hsv, np.array([0, 65, 50]), np.array([12, 255, 255]))
    mask2 = cv2.inRange(hsv, np.array([165, 65, 50]), np.array([180, 255, 255]))
    mask = cv2.bitwise_or(mask1, mask2)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    height, width = mask.shape
    page_area = float(height * width)
    detections: list[VisualDetection] = []
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        box_area = w * h
        if box_area < page_area * 0.001 or box_area > page_area * 0.25:
            continue
        aspect = w / max(h, 1)
        if not 0.45 <= aspect <= 2.2:
            continue
        red_ratio = cv2.countNonZero(mask[y : y + h, x : x + w]) / max(box_area, 1)
        if red_ratio < 0.035:
            continue
        shape_score = max(0.0, 1.0 - abs(1.0 - min(aspect, 1 / aspect)))
        size_score = min(1.0, box_area / (page_area * 0.015))
        confidence = min(0.98, 0.48 + red_ratio * 1.5 + shape_score * 0.12 + size_score * 0.12)
        crop = image.crop((max(0, x - 8), max(0, y - 8), min(width, x + w + 8), min(height, y + h + 8)))
        try:
            from app.services.ocr_service import recognize_image

            recognized_text, ocr_confidence = recognize_image(crop)
        except Exception:
            recognized_text, ocr_confidence = "", 0.0
        finally:
            crop.close()
        detections.append(
            VisualDetection(
                page=page_number,
                detection_type="seal",
                bbox=(x, y, x + w, y + h),
                confidence=round(confidence, 4),
                recognized_text=recognized_text,
                ocr_confidence=round(ocr_confidence, 4),
            )
        )
    return detections


_yolo_models: dict[str, object] = {}


def _detect_with_yolo(image, page_number: int, model_path: str) -> list[VisualDetection]:
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError("已配置视觉模型，但未安装 ultralytics") from exc
    model = _yolo_models.get(model_path)
    if model is None:
        model = YOLO(model_path)
        _yolo_models[model_path] = model
    detections: list[VisualDetection] = []
    results = model.predict(image, verbose=False)
    for result in results:
        names = result.names
        for box in result.boxes:
            class_id = int(box.cls[0])
            label = str(names[class_id]).lower()
            detection_type = "signature" if "sign" in label or "签名" in label else "seal"
            x1, y1, x2, y2 = (int(value) for value in box.xyxy[0].tolist())
            crop = image.crop((x1, y1, x2, y2))
            try:
                from app.services.ocr_service import recognize_image

                recognized_text, ocr_confidence = recognize_image(crop)
            except Exception:
                recognized_text, ocr_confidence = "", 0.0
            finally:
                crop.close()
            detections.append(VisualDetection(
                page=page_number,
                detection_type=detection_type,
                bbox=(x1, y1, x2, y2),
                confidence=round(float(box.conf[0]), 4),
                recognized_text=recognized_text,
                ocr_confidence=round(ocr_confidence, 4),
                detector=f"yolo:{Path(model_path).name}",
            ))
    return detections


def analyze_document_visuals(
    path: str | Path,
    max_pages: int = 100,
    model_path: str = "",
) -> VisualAnalysisResult:
    file_path = Path(path)
    if file_path.suffix.lower() != ".pdf":
        return VisualAnalysisResult(warnings=["当前视觉检测仅支持 PDF，其他格式未执行印章检测"])

    result = VisualAnalysisResult()
    try:
        for page_number, image in _render_pdf(file_path):
            if page_number > max_pages:
                result.warnings.append(f"文档超过 {max_pages} 页，仅检测前 {max_pages} 页")
                break
            result.analyzed_pages += 1
            if model_path:
                result.detections.extend(_detect_with_yolo(image, page_number, model_path))
            else:
                result.detections.extend(_detect_red_seals(image, page_number))
    except Exception as exc:
        result.warnings.append(f"视觉检测失败: {exc}")
    return result
