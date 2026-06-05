from flask import Flask, request, jsonify, send_file
import requests
from PIL import Image
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
import os
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
main_key = "JOT-TEAM"
executor = ThreadPoolExecutor(max_workers=20)

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
}

IMAGE_CACHE = {}

# ===============================
#      FETCH IMAGE (WITH CACHE)
# ===============================
def fetch_and_process_image(url, size=None):
    try:
        key = f"{url}_{size}"
        if key in IMAGE_CACHE:
            return IMAGE_CACHE[key].copy()

        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=7, verify=False)
        if resp.status_code != 200:
            print(f"[Warn] Can't load image {url}")
            return None

        img = Image.open(BytesIO(resp.content)).convert("RGBA")
        if size:
            img = img.resize(size)

        IMAGE_CACHE[key] = img
        return img.copy()

    except Exception as e:
        print(f"[Error] fetch image failed: {e}")
        return None


# ===============================
#      FETCH PLAYER INFO (NEW API)
# ===============================
def fetch_player_info(uid):
    try:
        url = f"https://jagwar-info.vercel.app/player-info?uid={uid}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return resp.json()
        return None
    except Exception as e:
        print(f"[Error] fetch player info failed: {e}")
        return None


# ===============================
#   LOAD FIXED BACKGROUND ONCE
# ===============================
BACKGROUND = fetch_and_process_image(
    "https://iili.io/Btk83Zb.jpg",
    size=(1024, 1024)
)

if BACKGROUND is None:
    raise RuntimeError("❌ FAILED TO LOAD FIXED BACKGROUND IMAGE")


@app.route('/outfit-image', methods=['GET'])
def outfit_image():
    uid = request.args.get("uid")
    key = request.args.get("key")

    if not uid:
        return jsonify({"error": "Missing UID"}), 400
    if key != main_key:
        return jsonify({"error": "Invalid API key"}), 403

    data = fetch_player_info(uid)
    if not data:
        return jsonify({"error": "Failed to fetch player info"}), 500

    background_image = BACKGROUND.copy()

    # ============================
    #   PLAYER DATA (NEW API STRUCTURE)
    # ============================
    
    # Basic Info (معلومات أساسية)
    basic_info = data.get("basicInfo", {})
    
    # Profile Info (معلومات الملف الشخصي)
    profile_info = data.get("profileInfo", {})
    
    # Captain Basic Info (معلومات الكابتن الأساسية)
    captain_basic_info = data.get("captainBasicInfo", {})
    
    # Equipped outfits from profileInfo (الأزياء المجهزة)
    equipped = profile_info.get("clothes", [])
    
    # Avatar ID from profileInfo (معرف الصورة الرمزية)
    avatar_id = profile_info.get("avatarId", 102000012)
    
    # Weapon skin shows from captainBasicInfo (أسلحة الكابتن)
    weapon_ids = captain_basic_info.get("weaponSkinShows", [])
    weapon_id = weapon_ids[0] if weapon_ids else None

    # ============================
    #   OUTFITS
    # ============================
    required_starts = ["214", "211", "211", "203", "204", "205", "203"]
    fallback_ids = [
        "214000000", "211000000", "211000000",
        "203000000", "204000000", "205000000", "203000000"
    ]

    used_ids = set()
    outfit_tasks = []

    def get_outfit(idx, code):
        match = None
        for oid in equipped:
            if str(oid).startswith(code) and oid not in used_ids:
                match = oid
                used_ids.add(oid)
                break

        if match is None:
            match = fallback_ids[idx]

        url = f"https://iconapi.wasmer.app/{match}"
        return fetch_and_process_image(url, size=(170, 170))

    for i, c in enumerate(required_starts):
        outfit_tasks.append(executor.submit(get_outfit, i, c))

    positions = [
        {'x': 130, 'y': 138, 'w': 170, 'h': 170},
        {'x': 727, 'y': 180, 'w': 170, 'h': 170},
        {'x': 820, 'y': 380, 'w': 170, 'h': 170},
        {'x': 45,  'y': 345, 'w': 170, 'h': 170},
        {'x': 55, 'y': 590, 'w': 170, 'h': 170},
        {'x': 180, 'y': 760, 'w': 170, 'h': 170},
        {'x': 714, 'y': 730, 'w': 170, 'h': 170},
    ]

    for i, t in enumerate(outfit_tasks):
        outfit = t.result()
        if outfit:
            p = positions[i]
            resized = outfit.resize((p["w"], p["h"]))
            background_image.paste(resized, (p["x"], p["y"]), resized)

    # ============================
    #   AVATAR
    # ============================
    avatar_url = f"https://raw.githubusercontent.com/saarthak703/character-api-danger/main/pngs/{avatar_id}.png"
    avatar = fetch_and_process_image(avatar_url, size=(650, 780))
    if avatar:
        cx = (1024 - 650) // 2
        background_image.paste(avatar, (cx, 145), avatar)

    # ============================
    #   WEAPON
    # ============================
    if weapon_id:
        w_url = f"https://iconapi.wasmer.app/{weapon_id}"
        weapon = fetch_and_process_image(w_url, size=(330, 200))
        if weapon:
            background_image.paste(weapon, (670, 564), weapon)

    # ============================
    #   SEND FILE
    # ============================
    img_io = BytesIO()
    background_image.save(img_io, "PNG")
    img_io.seek(0)
    return send_file(img_io, mimetype="image/png")


# ============================
#   NEW ENDPOINT: جلب المعلومات النصية فقط
# ============================
@app.route('/player-info', methods=['GET'])
def get_player_info():
    uid = request.args.get("uid")
    key = request.args.get("key")
    
    if not uid:
        return jsonify({"error": "Missing UID"}), 400
    if key != main_key:
        return jsonify({"error": "Invalid API key"}), 403
    
    data = fetch_player_info(uid)
    if not data:
        return jsonify({"error": "Failed to fetch player info"}), 500
    
    # إرجاع المعلومات كاملة
    return jsonify(data)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
