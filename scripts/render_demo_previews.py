from pathlib import Path

import pypdfium2 as pdfium


ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "test_data" / "enterprise_demo"
OUT = ROOT / "data" / "demo_previews"

TARGETS = {
    "00_采购文件_XX市信息化平台升级建设项目.pdf": [1, 4, 5],
    "B_博远信息技术有限公司_投标响应文件.pdf": [1, 9],
    "C_新联科技有限公司_投标响应文件.pdf": [1, 9],
    "D_天远科技有限公司_投标响应文件.pdf": [1, 8, 12, 15],
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for filename, pages in TARGETS.items():
        document = pdfium.PdfDocument(str(DEMO / filename))
        try:
            for page_number in pages:
                page = document[page_number - 1]
                bitmap = page.render(scale=1.6)
                image = bitmap.to_pil().convert("RGB")
                try:
                    image.save(OUT / f"{Path(filename).stem}_p{page_number:02d}.png")
                finally:
                    image.close()
                    bitmap.close()
                    page.close()
        finally:
            document.close()
    print(OUT)


if __name__ == "__main__":
    main()
