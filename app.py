from flask import Flask, request, send_file
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import requests

try:
    from fontTools.ttLib import TTFont
except ImportError:
    raise SystemExit("Please install fonttools: pip install fonttools")

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
except ImportError:
    raise SystemExit("Please install: pip install arabic-reshaper python-bidi")

app = Flask(__name__)

AVATAR_SIZE = (125, 125)
FONT_PRIMARY = "Tajawal-Bold.ttf"
FONT_FALLBACKS = [
    "DejaVuSans.ttf",
    "NotoSans-Regular.ttf",
    "ARIAL.TTF",
    "NotoSansArabic-Regular.ttf",
    "NotoSansSymbols2-Regular.ttf",
    "NotoSansCJKjp-Regular.otf",
    "unifont-15.0.01.ttf"
]
SECRET_KEY = "JAGWAR"

# ---------------------------------------------------------------------------
# Real glyph-coverage check using the font's cmap table.
# ImageFont.getmask()/getbbox() is NOT reliable for this: most TTF/OTF fonts
# have a visible ".notdef" tofu-box glyph, which returns a non-None bbox even
# when the character isn't actually supported. That's why unsupported chars
# (fullwidth Latin, Hangul filler U+3164, CJK, etc.) were rendering as boxes
# instead of falling through to a working fallback font.
# ---------------------------------------------------------------------------
_cmap_cache = {}

def get_cmap(font_path):
    if font_path not in _cmap_cache:
        try:
            tt = TTFont(font_path, fontNumber=0, lazy=True)
            _cmap_cache[font_path] = tt.getBestCmap() or {}
        except Exception as e:
            print(f"Could not read cmap for {font_path}: {e}")
            _cmap_cache[font_path] = {}
    return _cmap_cache[font_path]

def char_in_font(char, font_path):
    if char.isspace():
        return True
    cmap = get_cmap(font_path)
    return ord(char) in cmap

# ---------------------------------------------------------------------------
# RTL language support (Arabic, and Arabic-family scripts).
# PIL has no built-in text shaping engine (no HarfBuzz), so it draws every
# character in isolated form and in raw logical order. That's fine for
# Latin/CJK, but it visually breaks Arabic, which needs:
#   1) Shaping  -> letters change glyph form based on neighbors (arabic_reshaper)
#   2) Reordering -> RTL runs must be flipped for correct left-to-right drawing
#      (python-bidi's get_display implements the Unicode Bidirectional Algorithm)
# ---------------------------------------------------------------------------
_RTL_RANGES = [
    (0x0590, 0x05FF),  # Hebrew
    (0x0600, 0x06FF),  # Arabic
    (0x0700, 0x074F),  # Syriac
    (0x0750, 0x077F),  # Arabic Supplement
    (0x08A0, 0x08FF),  # Arabic Extended-A
    (0xFB1D, 0xFB4F),  # Hebrew Presentation Forms
    (0xFB50, 0xFDFF),  # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),  # Arabic Presentation Forms-B
]

def contains_rtl(text):
    return any(any(start <= ord(c) <= end for start, end in _RTL_RANGES) for c in text)

def prepare_text_for_render(text):
    """Reshape + bidi-reorder any RTL text so it displays correctly when
    drawn left-to-right by PIL. Leaves pure LTR/CJK text untouched."""
    if not text or not contains_rtl(text):
        return text
    try:
        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    except Exception as e:
        print(f"RTL shaping failed, falling back to raw text: {e}")
        return text


# LOAD FONTS
def load_fonts(sizes):
    fonts = {"primary": {}, "fallbacks": []}
    for size in sizes:
        try:
            fonts["primary"][size] = ImageFont.truetype(FONT_PRIMARY, size)
        except Exception as e:
            print(f"Primary font load failed ({size}): {e}")
            fonts["primary"][size] = ImageFont.load_default()

    for font_path in FONT_FALLBACKS:
        fallback_fonts = {"path": font_path, "sizes": {}}
        for size in sizes:
            try:
                fallback_fonts["sizes"][size] = ImageFont.truetype(font_path, size)
            except Exception as e:
                print(f"Fallback font load failed ({font_path}, {size}): {e}")
                fallback_fonts["sizes"][size] = None
        fonts["fallbacks"].append(fallback_fonts)
    return fonts

fonts = load_fonts([30, 35, 40, 50])

def smart_draw_text(draw, position, text, font_dict, size, fill):
    x, y = position
    primary_font = font_dict["primary"][size]

    for char in text:
        font_to_use = None

        if char_in_font(char, FONT_PRIMARY):
            font_to_use = primary_font
        else:
            for fb in font_dict["fallbacks"]:
                fb_font = fb["sizes"].get(size)
                if fb_font and char_in_font(char, fb["path"]):
                    font_to_use = fb_font
                    break

        if not font_to_use:
            # last resort: draw with primary anyway so layout doesn't break
            font_to_use = primary_font

        draw.text((x, y), char, font=font_to_use, fill=fill)
        bbox = font_to_use.getbbox(char)
        char_width = (bbox[2] - bbox[0]) if bbox else font_to_use.getbbox(" ")[2]
        # add a tiny bit of spacing so wide/CJK glyphs don't collide
        x += char_width if char_width > 0 else 10

def measure_text_width(text, font_dict, size):
    total = 0
    primary_font = font_dict["primary"][size]
    for char in text:
        font_to_use = None
        if char_in_font(char, FONT_PRIMARY):
            font_to_use = primary_font
        else:
            for fb in font_dict["fallbacks"]:
                fb_font = fb["sizes"].get(size)
                if fb_font and char_in_font(char, fb["path"]):
                    font_to_use = fb_font
                    break
        if not font_to_use:
            font_to_use = primary_font
        bbox = font_to_use.getbbox(char)
        total += (bbox[2] - bbox[0]) if bbox else 10
    return total

def measure_and_fit(text, font_dict, size_options, max_width):
    """Return the largest size (from size_options, descending) whose rendered
    width fits within max_width. Falls back to the smallest size if none fit."""
    for size in sorted(size_options, reverse=True):
        if size not in font_dict["primary"]:
            continue
        if measure_text_width(text, font_dict, size) <= max_width:
            return size
    return min(size_options)

def fetch_image(url, size=None):
    try:
        res = requests.get(url, timeout=5)
        res.raise_for_status()
        img = Image.open(BytesIO(res.content)).convert("RGBA")
        if size:
            img = img.resize(size, Image.LANCZOS)
        return img
    except Exception as e:
        print(f"Error fetching image: {e}")
        return None

@app.route('/bnr')
def generate_avatar_only():
    uid = request.args.get("uid")
    key = request.args.get("key")

    if key != SECRET_KEY:
        return "KEY ERROR", 403
    if not uid:
        return "INVALID UID", 400

    try:
        api_url = f"https://jagwar-info-api.vercel.app/player-info?uid={uid}"
        res = requests.get(api_url, timeout=5)
        res.raise_for_status()
        data = res.json()

        account_info = data.get("basicInfo", {})
        nickname = prepare_text_for_render(account_info.get("nickname", "Unknown"))
        likes = account_info.get("liked", 0)
        level = account_info.get("level", 0)
        avatar_id = account_info.get("headPic")

        print(f"Head Pic ID: {avatar_id}")

    except Exception as e:
        return f"API INFO ERROR: {e}", 500

    bg_img = fetch_image("https://i.postimg.cc/L4PQBgmx/IMG-20250807-042134-670.jpg")
    if not bg_img:
        return "BACKGROUND IMAGE ERROR", 500

    img = bg_img.copy()
    draw = ImageDraw.Draw(img)

    if avatar_id:
        avatar_url = f"https://pika-ffitmes-api.vercel.app/?item_id={avatar_id}&watermark=TaitanApi&key=PikaApis"
        avatar_img = fetch_image(avatar_url, AVATAR_SIZE)
    else:
        avatar_img = None

    avatar_x, avatar_y = 90, 82
    if avatar_img:
        img.paste(avatar_img, (avatar_x, avatar_y), avatar_img)
    else:
        draw.rectangle([avatar_x, avatar_y, avatar_x + AVATAR_SIZE[0], avatar_y + AVATAR_SIZE[1]], outline="gray", width=2)
        draw.text((avatar_x + 20, avatar_y + 40), "No Avatar", fill="gray")

    level_text = f"Lv. {level}"
    level_x = avatar_x - 40
    level_y = avatar_y + 160
    smart_draw_text(draw, (level_x, level_y), level_text, fonts, 50, "black")

    nickname_x = avatar_x + AVATAR_SIZE[0] + 80
    nickname_y = avatar_y - 3
    max_nickname_width = img.size[0] - nickname_x - 40  # keep 40px right margin

    nickname_size = measure_and_fit(nickname, fonts, [50, 40, 35, 30], max_nickname_width)
    smart_draw_text(draw, (nickname_x, nickname_y), nickname, fonts, nickname_size, "black")

    bbox_uid = fonts["primary"][35].getbbox(uid)
    text_w = bbox_uid[2] - bbox_uid[0]
    text_h = bbox_uid[3] - bbox_uid[1]
    img_w, img_h = img.size
    text_x = img_w - text_w - 110
    text_y = img_h - text_h - 17
    smart_draw_text(draw, (text_x, text_y), uid, fonts, 35, "white")

    likes_text = f"{likes}"
    bbox_likes = fonts["primary"][40].getbbox(likes_text)
    likes_w = bbox_likes[2] - bbox_likes[0]
    likes_y = text_y - (bbox_likes[3] - bbox_likes[1]) - 25
    likes_x = img_w - likes_w - 60
    smart_draw_text(draw, (likes_x, likes_y), likes_text, fonts, 40, "black")

    dev_text = "DEV BY : JAGWAR"
    bbox_dev = fonts["primary"][30].getbbox(dev_text)
    dev_w = bbox_dev[2] - bbox_dev[0]
    padding = 30
    dev_x = img_w - dev_w - padding
    dev_y = padding
    smart_draw_text(draw, (dev_x, dev_y), dev_text, fonts, 30, "white")

    output = BytesIO()
    img.save(output, format='PNG')
    output.seek(0)
    return send_file(output, mimetype='image/png')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
