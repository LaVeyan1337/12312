from pathlib import Path
from urllib.request import Request, urlopen
from PIL import Image

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)

LOGO_URL = "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcTa7kzHPt10lgDjYUbcJSX082Zq-o7ZtpH5Kmu3yKvLrQ&s=10"
LOGO = ASSETS / "encev_logo.png"
ICON = ASSETS / "ENCTakip.ico"


def main():
    # Verilen ENÇEV logosunu build sırasında doğrudan kaynaktan al.
    req = Request(LOGO_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=20) as response:
        data = response.read()
    LOGO.write_bytes(data)

    with Image.open(LOGO) as img:
        img = img.convert("RGBA")
        # Windows kısayolu/EXE ikonu için çoklu çözünürlük.
        img.save(
            ICON,
            format="ICO",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )

    print(f"Logo hazır: {LOGO}")
    print(f"Windows ikonu hazır: {ICON}")


if __name__ == "__main__":
    main()
