from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFilter

from flows.firma_digital_flow.firma_flow import _png_menor_a_limite, _procesar_firma_imagen


def _case_white_thin() -> Image.Image:
    img = Image.new("RGB", (900, 420), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.line((70, 250, 220, 180, 360, 270, 520, 160, 770, 230), fill=(40, 40, 40), width=2)
    d.line((300, 300, 640, 300), fill=(70, 70, 70), width=1)
    return img


def _case_gray_noise() -> Image.Image:
    img = Image.new("RGB", (900, 420), (220, 220, 220))
    d = ImageDraw.Draw(img)
    d.line((80, 240, 230, 160, 370, 260, 540, 150, 790, 220), fill=(55, 55, 55), width=3)
    for x in range(40, 860, 22):
        y = 40 + (x % 90)
        d.point((x, y), fill=(180, 180, 180))
    return img.filter(ImageFilter.GaussianBlur(radius=0.6))


def _case_complex_background() -> Image.Image:
    img = Image.new("RGB", (900, 420), (255, 255, 255))
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            g = int(170 + (x / img.width) * 70)
            b = int(175 + (y / img.height) * 60)
            px[x, y] = (g, g, b)
    d = ImageDraw.Draw(img)
    # sombra suave
    d.line((90, 255, 250, 180, 390, 285, 560, 175, 810, 240), fill=(120, 120, 120), width=7)
    # trazo principal
    d.line((85, 245, 245, 170, 385, 275, 555, 165, 805, 230), fill=(35, 35, 35), width=4)
    return img.filter(ImageFilter.GaussianBlur(radius=0.8))


def _case_thick_stroke() -> Image.Image:
    img = Image.new("RGB", (900, 420), (248, 248, 248))
    d = ImageDraw.Draw(img)
    d.line((70, 250, 220, 190, 360, 290, 520, 170, 770, 240), fill=(20, 20, 20), width=8)
    return img


def run_min_test() -> int:
    out_dir = Path("data/firma_digital/test_processing")
    out_dir.mkdir(parents=True, exist_ok=True)

    cases = {
        "white_thin": _case_white_thin(),
        "gray_noise": _case_gray_noise(),
        "complex_background": _case_complex_background(),
        "thick_stroke": _case_thick_stroke(),
    }

    ok = 0
    review = 0
    fail = 0

    for name, img in cases.items():
        original_path = out_dir / f"{name}_input.png"
        original_path.write_bytes(b"")
        img.save(original_path)

        try:
            processed, detail, review_manual, thickened = _procesar_firma_imagen(img)
            png_bytes, png_detail, within = _png_menor_a_limite(processed, target_bytes=80 * 1024)

            output_path = out_dir / f"{name}_output.png"
            output_path.write_bytes(png_bytes)

            if review_manual:
                review += 1
                print(f"[REVIEW] {name} | detail={detail} | thickened={thickened} | {png_detail} | within={within}")
            else:
                ok += 1
                print(f"[OK] {name} | detail={detail} | thickened={thickened} | {png_detail} | within={within}")
        except Exception as exc:
            fail += 1
            print(f"[FAIL] {name} | exc={exc}")

    print(f"\nResumen -> ok={ok} review={review} fail={fail} out_dir={out_dir}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run_min_test())
