import io
import math
import aiohttp
from PIL import Image, ImageDraw, ImageFont, ImageFilter

WIDTH, HEIGHT = 1024, 400
AVATAR_SIZE = 190
CORNER_RADIUS = 28
FONT_DIR = "assets/fonts"


def _load_font(name: str, size: int):
    try:
        return ImageFont.truetype(f"{FONT_DIR}/{name}", size)
    except OSError:
        print(f"⚠️  Font {name} not found in {FONT_DIR}, falling back to default (will look ugly)")
        return ImageFont.load_default()


async def _fetch_avatar_bytes(url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.read()


def _make_gradient_background(width, height, top_color, bottom_color):
    base = Image.new("RGB", (width, height), top_color)
    draw = ImageDraw.Draw(base)
    top_r, top_g, top_b = top_color
    bot_r, bot_g, bot_b = bottom_color
    for y in range(height):
        ratio = y / height
        r = int(top_r + (bot_r - top_r) * ratio)
        g = int(top_g + (bot_g - top_g) * ratio)
        b = int(top_b + (bot_b - top_b) * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))
    return base


def _add_dot_grid(base: Image.Image, spacing=28, radius=1, opacity=18):
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for x in range(0, base.width, spacing):
        for y in range(0, base.height, spacing):
            draw.ellipse((x, y, x + radius, y + radius), fill=(255, 255, 255, opacity))
    return Image.alpha_composite(base.convert("RGBA"), overlay)


def _add_diagonal_accent(base: Image.Image, accent_color):
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = base.size
    # A soft diagonal band on the right side of the card
    points = [(w * 0.72, 0), (w, 0), (w, h), (w * 0.60, h)]
    draw.polygon(points, fill=(*accent_color, 22))
    points2 = [(w * 0.82, 0), (w * 0.92, 0), (w * 0.78, h), (w * 0.68, h)]
    draw.polygon(points2, fill=(*accent_color, 30))
    return Image.alpha_composite(base.convert("RGBA"), overlay)


def _round_corners(img: Image.Image, radius: int):
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width, img.height), radius=radius, fill=255)
    img.putalpha(mask)
    return img


def _avatar_glow(size: int, color):
    glow_size = size * 2
    glow = Image.new("RGBA", (glow_size, glow_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    pad = size // 2
    draw.ellipse((pad, pad, pad + size, pad + size), fill=(*color, 130))
    glow = glow.filter(ImageFilter.GaussianBlur(size // 6))
    return glow


def _circle_avatar(avatar_bytes: bytes, size: int, ring_color):
    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
    avatar = avatar.resize((size, size), Image.LANCZOS)

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    avatar.putalpha(mask)

    ring_size = size + 14
    ring = Image.new("RGBA", (ring_size, ring_size), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((0, 0, ring_size, ring_size), fill=ring_color)
    ring.paste(avatar, (7, 7), avatar)
    return ring


def _draw_pill(draw, xy, text, font, fg, bg, pad_x=18, pad_y=8):
    x, y = xy
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    box = (x, y, x + text_w + pad_x * 2, y + text_h + pad_y * 2)
    draw.rounded_rectangle(box, radius=(text_h + pad_y * 2) // 2, fill=bg)
    draw.text((x + pad_x, y + pad_y - bbox[1]), text, font=font, fill=fg)
    return box[2] - box[0]  # width consumed


async def generate_card(member, member_count: int, kind: str = "welcome") -> io.BytesIO:
    is_welcome = kind == "welcome"
    top_color = (24, 34, 51) if is_welcome else (40, 20, 20)
    bottom_color = (12, 17, 28) if is_welcome else (18, 9, 9)
    accent = (74, 222, 128) if is_welcome else (248, 113, 113)

    bg = _make_gradient_background(WIDTH, HEIGHT, top_color, bottom_color)
    bg = _add_dot_grid(bg)
    bg = _add_diagonal_accent(bg, accent)
    draw = ImageDraw.Draw(bg)

    # Avatar with glow behind it
    avatar_bytes = await _fetch_avatar_bytes(member.display_avatar.replace(size=256).url)
    avatar_pos = (70, (HEIGHT - AVATAR_SIZE) // 2 - 7)

    glow = _avatar_glow(AVATAR_SIZE, accent)
    glow_pos = (avatar_pos[0] - AVATAR_SIZE // 2, avatar_pos[1] - AVATAR_SIZE // 2)
    bg.paste(glow, glow_pos, glow)

    avatar_img = _circle_avatar(avatar_bytes, AVATAR_SIZE, accent)
    bg.paste(avatar_img, avatar_pos, avatar_img)

    title_font = _load_font("Poppins-Bold.ttf", 50)
    name_font = _load_font("Poppins-Bold.ttf", 30)
    pill_font = _load_font("Poppins-Regular.ttf", 20)

    text_x = avatar_pos[0] + avatar_img.width + 55

    title_text = "WELCOME" if is_welcome else "GOODBYE"
    draw.text((text_x, 100), title_text, font=title_font, fill=accent)
    draw.text((text_x + 2, 165), member.display_name, font=name_font, fill=(255, 255, 255))

    if is_welcome:
        pill_text = f"MEMBER #{member_count}"
    else:
        pill_text = f"{member_count} MEMBERS LEFT"
    _draw_pill(
        draw, (text_x, 225), pill_text, pill_font,
        fg=(255, 255, 255), bg=(*accent, 40)
    )

    server_name_font = _load_font("Poppins-Regular.ttf", 18)
    draw.text((text_x, 275), member.guild.name.upper(), font=server_name_font, fill=(140, 148, 160))

    bg = _round_corners(bg, CORNER_RADIUS)

    buffer = io.BytesIO()
    bg.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer