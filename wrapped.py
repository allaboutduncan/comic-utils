"""
Yearly Wrapped - Comic Reading Statistics Image Generator

Generates shareable "Spotify Wrapped" style images showing yearly reading stats.
Images are 1080x1920 pixels (9:16 aspect ratio) using the user's current theme colors.
"""

import os
import io
import sqlite3
import hashlib
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps, ImageChops
from database import get_db_connection
from app_logging import app_logger
from config import config

# Image dimensions (9:16 aspect ratio for social sharing)
IMAGE_WIDTH = 1080
IMAGE_HEIGHT = 1920

# Theme color mappings for all Bootswatch themes
THEME_COLORS = {
    'default': {'primary': '#0d6efd', 'secondary': '#6c757d', 'success': '#198754', 'info': '#0dcaf0', 'warning': '#ffc107', 'danger': '#dc3545', 'bg': '#ffffff', 'bg_secondary': '#f8f9fa', 'text': '#212529', 'text_muted': '#6c757d', 'is_dark': False},
    'cerulean': {'primary': '#2fa4e7', 'secondary': '#e9ecef', 'success': '#73a839', 'info': '#033c73', 'warning': '#dd5600', 'danger': '#c71c22', 'bg': '#ffffff', 'bg_secondary': '#f8f9fa', 'text': '#212529', 'text_muted': '#6c757d', 'is_dark': False},
    'cosmo': {'primary': '#2780e3', 'secondary': '#373a3c', 'success': '#3fb618', 'info': '#9954bb', 'warning': '#ff7518', 'danger': '#ff0039', 'bg': '#ffffff', 'bg_secondary': '#f8f9fa', 'text': '#373a3c', 'text_muted': '#6c757d', 'is_dark': False},
    'cyborg': {'primary': '#2a9fd6', 'secondary': '#555555', 'success': '#77b300', 'info': '#93c', 'warning': '#f80', 'danger': '#c00', 'bg': '#060606', 'bg_secondary': '#222222', 'text': '#ffffff', 'text_muted': '#888888', 'is_dark': True},
    'darkly': {'primary': '#375a7f', 'secondary': '#444444', 'success': '#00bc8c', 'info': '#3498db', 'warning': '#f39c12', 'danger': '#e74c3c', 'bg': '#222222', 'bg_secondary': '#303030', 'text': '#ffffff', 'text_muted': '#aaaaaa', 'is_dark': True},
    'flatly': {'primary': '#2c3e50', 'secondary': '#95a5a6', 'success': '#18bc9c', 'info': '#3498db', 'warning': '#f39c12', 'danger': '#e74c3c', 'bg': '#ffffff', 'bg_secondary': '#ecf0f1', 'text': '#212529', 'text_muted': '#6c757d', 'is_dark': False},
    'journal': {'primary': '#eb6864', 'secondary': '#aaaaaa', 'success': '#22b24c', 'info': '#336699', 'warning': '#f5e625', 'danger': '#f57a00', 'bg': '#ffffff', 'bg_secondary': '#f8f9fa', 'text': '#212529', 'text_muted': '#6c757d', 'is_dark': False},
    'litera': {'primary': '#4582ec', 'secondary': '#adb5bd', 'success': '#02b875', 'info': '#17a2b8', 'warning': '#f0ad4e', 'danger': '#d9534f', 'bg': '#ffffff', 'bg_secondary': '#f8f9fa', 'text': '#343a40', 'text_muted': '#6c757d', 'is_dark': False},
    'lumen': {'primary': '#158cba', 'secondary': '#f0f0f0', 'success': '#28b62c', 'info': '#75caeb', 'warning': '#ff851b', 'danger': '#ff4136', 'bg': '#ffffff', 'bg_secondary': '#f6f6f6', 'text': '#212529', 'text_muted': '#6c757d', 'is_dark': False},
    'lux': {'primary': '#1a1a2e', 'secondary': '#c0c0c0', 'success': '#4bbf73', 'info': '#1f9bcf', 'warning': '#f0ad4e', 'danger': '#d9534f', 'bg': '#ffffff', 'bg_secondary': '#f8f9fa', 'text': '#1a1a2e', 'text_muted': '#6c757d', 'is_dark': False},
    'materia': {'primary': '#2196f3', 'secondary': '#757575', 'success': '#4caf50', 'info': '#9c27b0', 'warning': '#ff9800', 'danger': '#e51c23', 'bg': '#ffffff', 'bg_secondary': '#f5f5f5', 'text': '#212529', 'text_muted': '#6c757d', 'is_dark': False},
    'minty': {'primary': '#78c2ad', 'secondary': '#f3969a', 'success': '#56cc9d', 'info': '#6cc3d5', 'warning': '#ffce67', 'danger': '#ff7851', 'bg': '#ffffff', 'bg_secondary': '#f8f9fa', 'text': '#5a5a5a', 'text_muted': '#6c757d', 'is_dark': False},
    'morph': {'primary': '#378dfc', 'secondary': '#adb5bd', 'success': '#43cc29', 'info': '#5b62f4', 'warning': '#ffc107', 'danger': '#e52527', 'bg': '#f0f5fa', 'bg_secondary': '#dee2e6', 'text': '#373a3c', 'text_muted': '#6c757d', 'is_dark': False},
    'pulse': {'primary': '#593196', 'secondary': '#a991d4', 'success': '#13b955', 'info': '#009cdc', 'warning': '#efa31d', 'danger': '#fc3939', 'bg': '#ffffff', 'bg_secondary': '#f8f9fa', 'text': '#444444', 'text_muted': '#6c757d', 'is_dark': False},
    'quartz': {'primary': '#e83283', 'secondary': '#a942e5', 'success': '#3cf281', 'info': '#45c4fd', 'warning': '#fcce42', 'danger': '#fd726d', 'bg': '#1a1a2e', 'bg_secondary': '#242439', 'text': '#e9ecf2', 'text_muted': '#8d8da3', 'is_dark': True},
    'sandstone': {'primary': '#325d88', 'secondary': '#8e8c84', 'success': '#93c54b', 'info': '#29abe0', 'warning': '#f47c3c', 'danger': '#d9534f', 'bg': '#ffffff', 'bg_secondary': '#f8f5f0', 'text': '#3e3f3a', 'text_muted': '#6c757d', 'is_dark': False},
    'simplex': {'primary': '#d9230f', 'secondary': '#777777', 'success': '#469408', 'info': '#029acf', 'warning': '#d9831f', 'danger': '#9b479f', 'bg': '#ffffff', 'bg_secondary': '#f8f9fa', 'text': '#212529', 'text_muted': '#6c757d', 'is_dark': False},
    'sketchy': {'primary': '#333333', 'secondary': '#555555', 'success': '#28a745', 'info': '#17a2b8', 'warning': '#ffc107', 'danger': '#dc3545', 'bg': '#ffffff', 'bg_secondary': '#f8f9fa', 'text': '#212529', 'text_muted': '#6c757d', 'is_dark': False},
    'slate': {'primary': '#3a3f44', 'secondary': '#7a8288', 'success': '#62c462', 'info': '#5bc0de', 'warning': '#f89406', 'danger': '#ee5f5b', 'bg': '#272b30', 'bg_secondary': '#3a3f44', 'text': '#c8c8c8', 'text_muted': '#999999', 'is_dark': True},
    'solar': {'primary': '#b58900', 'secondary': '#839496', 'success': '#2aa198', 'info': '#268bd2', 'warning': '#cb4b16', 'danger': '#dc322f', 'bg': '#002b36', 'bg_secondary': '#073642', 'text': '#839496', 'text_muted': '#657b83', 'is_dark': True},
    'spacelab': {'primary': '#446e9b', 'secondary': '#999999', 'success': '#3cb521', 'info': '#3399f3', 'warning': '#d47500', 'danger': '#cd0200', 'bg': '#ffffff', 'bg_secondary': '#f8f9fa', 'text': '#212529', 'text_muted': '#6c757d', 'is_dark': False},
    'superhero': {'primary': '#df691a', 'secondary': '#4e5d6c', 'success': '#5cb85c', 'info': '#5bc0de', 'warning': '#f0ad4e', 'danger': '#d9534f', 'bg': '#2b3e50', 'bg_secondary': '#3e5368', 'text': '#ebebeb', 'text_muted': '#aaaaaa', 'is_dark': True},
    'united': {'primary': '#e95420', 'secondary': '#aea79f', 'success': '#38b44a', 'info': '#17a2b8', 'warning': '#efb73e', 'danger': '#df382c', 'bg': '#ffffff', 'bg_secondary': '#f8f9fa', 'text': '#212529', 'text_muted': '#6c757d', 'is_dark': False},
    'vapor': {'primary': '#6e40c9', 'secondary': '#ea39b8', 'success': '#3cf281', 'info': '#1ba2f6', 'warning': '#ffb86c', 'danger': '#ff6b6b', 'bg': '#1a1a2e', 'bg_secondary': '#16213e', 'text': '#eef0f2', 'text_muted': '#8d8da3', 'is_dark': True},
    'yeti': {'primary': '#008cba', 'secondary': '#adb5bd', 'success': '#43ac6a', 'info': '#5bc0de', 'warning': '#e99002', 'danger': '#f04124', 'bg': '#ffffff', 'bg_secondary': '#f8f9fa', 'text': '#222222', 'text_muted': '#6c757d', 'is_dark': False},
    'zephyr': {'primary': '#3459e6', 'secondary': '#ffffff', 'success': '#2fb380', 'info': '#287bb5', 'warning': '#f4bd61', 'danger': '#da292e', 'bg': '#ffffff', 'bg_secondary': '#f8f9fa', 'text': '#495057', 'text_muted': '#6c757d', 'is_dark': False}
}


def get_theme_colors(theme_name: str) -> dict:
    """Return color palette for the given theme."""
    return THEME_COLORS.get(theme_name.lower(), THEME_COLORS['default'])


def hex_to_rgb(hex_color: str) -> tuple:
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 3:
        hex_color = ''.join([c*2 for c in hex_color])
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


class ImageUtils:
    @staticmethod
    def get_thumbnails_dir():
        return os.path.join(config.get("SETTINGS", "CACHE_DIR", fallback="/cache"), "thumbnails")

    @staticmethod
    def get_thumbnail_path(file_path):
        """Get path to the generated thumbnail for a file."""
        if not file_path:
            return None
        path_hash = hashlib.md5(file_path.encode('utf-8')).hexdigest()
        shard_dir = path_hash[:2]
        filename = f"{path_hash}.jpg"
        return os.path.join(ImageUtils.get_thumbnails_dir(), shard_dir, filename)

    @staticmethod
    def get_series_cover(series_path):
        """Find a cover image for a series."""
        if not series_path:
            return None

        # 1. Check for folder images
        for ext in ['png', 'jpg', 'jpeg']:
            folder_img = os.path.join(series_path, f"folder.{ext}")
            if os.path.exists(folder_img):
                return folder_img

        # 2. If not found, try to find a cover.jpg
        for ext in ['png', 'jpg', 'jpeg']:
            cover_img = os.path.join(series_path, f"cover.{ext}")
            if os.path.exists(cover_img):
                return cover_img

        return None
        
    @staticmethod
    def get_logo_path():
        """Get path to the CLU logo."""
        # Use simple os.getcwd() to find images directory, which is robust in Docker/standard layouts
        return os.path.join(os.getcwd(), 'images', 'clu-logo-360.png')


def create_gradient(width: int, height: int, color1: str, color2: str, vertical: bool = True) -> Image.Image:
    """Create a gradient image from color1 to color2."""
    img = Image.new('RGB', (width, height))
    draw = ImageDraw.Draw(img)
    rgb1 = hex_to_rgb(color1)
    rgb2 = hex_to_rgb(color2)

    if vertical:
        for i in range(height):
            ratio = i / height
            r = int(rgb1[0] + (rgb2[0] - rgb1[0]) * ratio)
            g = int(rgb1[1] + (rgb2[1] - rgb1[1]) * ratio)
            b = int(rgb1[2] + (rgb2[2] - rgb1[2]) * ratio)
            draw.line([(0, i), (width, i)], fill=(r, g, b))
    else:
        for i in range(width):
            ratio = i / width
            r = int(rgb1[0] + (rgb2[0] - rgb1[0]) * ratio)
            g = int(rgb1[1] + (rgb2[1] - rgb1[1]) * ratio)
            b = int(rgb1[2] + (rgb2[2] - rgb1[2]) * ratio)
            draw.line([(i, 0), (i, height)], fill=(r, g, b))

    return img


# ==========================================
# Data Query Functions (Same as before)
# ==========================================

def get_years_with_reading_data() -> list:
    try:
        conn = get_db_connection()
        cursor = conn.execute("SELECT DISTINCT strftime('%Y', read_at) as year FROM issues_read WHERE read_at IS NOT NULL ORDER BY year DESC")
        years = [int(row[0]) for row in cursor.fetchall() if row[0]]
        conn.close()
        return years
    except Exception:
        return []

def get_yearly_total_read(year: int) -> int:
    try:
        conn = get_db_connection()
        cursor = conn.execute("SELECT COUNT(*) FROM issues_read WHERE strftime('%Y', read_at) = ?", (str(year),))
        result = cursor.fetchone()[0]
        conn.close()
        return result or 0
    except Exception:
        return 0

def get_most_read_series(year: int, limit: int = 1) -> list:
    import re
    from collections import Counter
    try:
        conn = get_db_connection()
        cursor = conn.execute("SELECT issue_path FROM issues_read WHERE strftime('%Y', read_at) = ?", (str(year),))
        rows = cursor.fetchall()
        conn.close()
        series_counter = Counter()
        for row in rows:
            path = row[0].replace('\\', '/')
            series_path = '/'.join(path.split('/')[:-1])
            series_counter[series_path] += 1
        results = []
        for series_path, count in series_counter.most_common(limit):
            parts = series_path.rstrip('/').split('/')
            series_name = parts[-1] if parts else 'Unknown'
            series_name = re.sub(r'\s*v\d{4}$', '', series_name)
            results.append({'name': series_name, 'count': count, 'path': series_path})
        return results
    except Exception:
        return [{'name': 'Unknown', 'count': 0, 'path': ''}]

def get_busiest_day(year: int) -> dict:
    try:
        conn = get_db_connection()
        cursor = conn.execute("SELECT date(read_at) as read_date, COUNT(*) as count FROM issues_read WHERE strftime('%Y', read_at) = ? GROUP BY read_date ORDER BY count DESC LIMIT 1", (str(year),))
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            date_obj = datetime.strptime(row[0], '%Y-%m-%d')
            return {'date': date_obj.strftime('%B %d, %Y'), 'date_short': date_obj.strftime('%b %d'), 'count': row[1]}
    except Exception:
        pass
    return {'date': 'No data', 'date_short': 'N/A', 'count': 0}

def get_busiest_month(year: int) -> dict:
    try:
        conn = get_db_connection()
        cursor = conn.execute("SELECT strftime('%m', read_at) as month_num, COUNT(*) as count FROM issues_read WHERE strftime('%Y', read_at) = ? GROUP BY month_num ORDER BY count DESC LIMIT 1", (str(year),))
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            month_names = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
            month_idx = int(row[0]) - 1
            return {'month': month_names[month_idx], 'month_short': month_names[month_idx][:3], 'count': row[1]}
    except Exception:
        pass
    return {'month': 'No data', 'month_short': 'N/A', 'count': 0}

def get_top_series_with_thumbnails(year: int, limit: int = 6) -> list:
    import re
    from collections import Counter
    try:
        conn = get_db_connection()
        cursor = conn.execute("SELECT issue_path FROM issues_read WHERE strftime('%Y', read_at) = ? ORDER BY issue_path", (str(year),))
        rows = cursor.fetchall()
        conn.close()
        series_counter = Counter()
        first_issues = {}
        for row in rows:
            path = row[0].replace('\\', '/')
            series_path = '/'.join(path.split('/')[:-1])
            series_counter[series_path] += 1
            if series_path not in first_issues:
                first_issues[series_path] = path
        results = []
        for series_path, count in series_counter.most_common(limit):
            parts = series_path.rstrip('/').split('/')
            series_name = parts[-1] if parts else 'Unknown'
            series_name = re.sub(r'\s*v\d{4}$', '', series_name)
            results.append({'name': series_name, 'count': count, 'first_issue_path': first_issues.get(series_path, ''), 'series_path': series_path})
        return results
    except Exception as e:
        app_logger.error(f"Error getting top series: {e}")
        return []

def get_all_wrapped_stats(year: int) -> dict:
    return {
        'year': year,
        'total_read': get_yearly_total_read(year),
        'most_read_series': get_most_read_series(year, limit=1),
        'busiest_day': get_busiest_day(year),
        'busiest_month': get_busiest_month(year),
        'top_series': get_top_series_with_thumbnails(year, limit=6)
    }

# ==========================================
# Image Generation Functions
# ==========================================

def get_font(size: int, bold: bool = False):
    font_candidates = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
        '/usr/share/fonts/truetype/freefont/FreeSansBold.ttf' if bold else '/usr/share/fonts/truetype/freefont/FreeSans.ttf',
        'C:/Windows/Fonts/arialbd.ttf' if bold else 'C:/Windows/Fonts/arial.ttf',
        'C:/Windows/Fonts/segoeui.ttf',
    ]
    for font_path in font_candidates:
        try:
            return ImageFont.truetype(font_path, size)
        except (IOError, OSError):
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()

def draw_centered_text(draw: ImageDraw, text: str, y: int, font: ImageFont, fill: tuple,
                       max_width: int = None, image_width: int = IMAGE_WIDTH, shadow: bool = False, img_obj: Image.Image = None):
    """
    Draw text to the image object. 
    If img_obj is provided and shadow is True, uses a separate layer for high-quality shadow.
    """
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    
    lines = []
    if max_width and text_width > max_width:
        words = text.split()
        current_line = []
        for word in words:
            test_line = ' '.join(current_line + [word])
            bbox = draw.textbbox((0, 0), test_line, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                current_line = [word]
        if current_line:
            lines.append(' '.join(current_line))
    else:
        lines = [text]

    current_y = y
    line_height = bbox[3] - bbox[1] + 15
    end_y = y
    
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (image_width - (bbox[2] - bbox[0])) // 2
        
        if shadow and img_obj:
            # High-quality blurred shadow using separate layer
            shadow_layer = Image.new('RGBA', img_obj.size, (0, 0, 0, 0))
            shadow_draw = ImageDraw.Draw(shadow_layer)
            # Use distinct drop shadow: 4px offset, darker, tighter blur
            shadow_color = (0, 0, 0, 240) # Nearly opaque black
            shadow_draw.text((x+2, current_y+2), line, font=font, fill=shadow_color)
            
            # Tighter blur
            shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(2)) 
            
            # Composite shadow onto image
            img_obj.paste(shadow_layer, (0, 0), shadow_layer)
            
        draw.text((x, current_y), line, font=font, fill=fill)
        current_y += line_height
        end_y = current_y

    return end_y

def create_base_image(theme_colors: dict, bg_image_path: str = None) -> Image.Image:
    if bg_image_path and os.path.exists(bg_image_path):
        try:
            bg = Image.open(bg_image_path).convert('RGB')
            ratio = max(IMAGE_WIDTH / bg.width, IMAGE_HEIGHT / bg.height)
            new_size = (int(bg.width * ratio), int(bg.height * ratio))
            bg = bg.resize(new_size, Image.Resampling.LANCZOS)
            left = (bg.width - IMAGE_WIDTH) // 2
            top = (bg.height - IMAGE_HEIGHT) // 2
            bg = bg.crop((left, top, left + IMAGE_WIDTH, top + IMAGE_HEIGHT))
            bg = bg.filter(ImageFilter.GaussianBlur(radius=30))
            
            overlay_color = theme_colors['bg'] if not theme_colors['is_dark'] else '#000000'
            overlay_opacity = 0.7 if not theme_colors['is_dark'] else 0.8
            overlay = Image.new('RGBA', bg.size, hex_to_rgb(overlay_color) + (int(255 * overlay_opacity),))
            bg.paste(overlay, (0, 0), overlay)
            return bg
        except Exception:
            pass
    
    if theme_colors['is_dark']:
        img = create_gradient(IMAGE_WIDTH, IMAGE_HEIGHT, theme_colors['bg'], theme_colors['bg_secondary'])
    else:
        primary_rgb = hex_to_rgb(theme_colors['primary'])
        light_primary = '#{:02x}{:02x}{:02x}'.format(min(255, primary_rgb[0] + 200), min(255, primary_rgb[1] + 200), min(255, primary_rgb[2] + 200))
        img = create_gradient(IMAGE_WIDTH, IMAGE_HEIGHT, theme_colors['bg'], light_primary)
    return img

def add_branding(img: Image.Image, draw: ImageDraw, theme_colors: dict, year: int):
    primary_color = hex_to_rgb(theme_colors['primary'])
    
    # Add Logo
    logo_path = ImageUtils.get_logo_path()
    if os.path.exists(logo_path):
        try:
            logo = Image.open(logo_path).convert('RGBA')
            # Maintain aspect ratio, max width 400
            ratio = 400 / logo.width
            new_h = int(logo.height * ratio)
            logo = logo.resize((400, new_h), Image.Resampling.LANCZOS)
            
            x_pos = (IMAGE_WIDTH - 400) // 2
            img.paste(logo, (x_pos, 80), logo)
        except Exception:
             # Fallback text
            text_color = hex_to_rgb(theme_colors['text'])
            font_title = get_font(48, bold=True)
            draw_centered_text(draw, "Comic Library Utilities", 80, font_title, text_color, shadow=True, img_obj=img)
    else:
        # Fallback text
        text_color = hex_to_rgb(theme_colors['text'])
        font_title = get_font(48, bold=True)
        draw_centered_text(draw, "Comic Library Utilities", 80, font_title, text_color, shadow=True, img_obj=img)

    # Year badge at bottom
    font_year = get_font(72, bold=True)
    y_pos = IMAGE_HEIGHT - 200
    draw_centered_text(draw, f"{year} WRAPPED", y_pos, font_year, primary_color, shadow=True, img_obj=img)

    font_footer = get_font(28)
    muted_color = hex_to_rgb(theme_colors['text_muted'])
    draw_centered_text(draw, "Your Year in Comics", IMAGE_HEIGHT - 100, font_footer, muted_color, shadow=True, img_obj=img)

def generate_summary_slide(year: int, theme: str) -> bytes:
    """Combine Total Read, Busiest Month, and Busiest Day into one slide."""
    try:
        theme_colors = get_theme_colors(theme)
        total = get_yearly_total_read(year)
        busiest_day = get_busiest_day(year)
        busiest_month = get_busiest_month(year)
        
        most_read = get_most_read_series(year, limit=1)
        bg_image = ImageUtils.get_series_cover(most_read[0]['path']) if most_read else None
        
        img = create_base_image(theme_colors, bg_image)
        draw = ImageDraw.Draw(img)
        
        primary_color = hex_to_rgb(theme_colors['primary'])
        text_color = hex_to_rgb(theme_colors['text'])
        muted_color = hex_to_rgb(theme_colors['text_muted'])

        # Top Start: Total Issues
        font_big = get_font(250, bold=True)
        draw_centered_text(draw, str(total), 400, font_big, primary_color, shadow=True, img_obj=img)
        font_label = get_font(64, bold=True)
        draw_centered_text(draw, "ISSUES READ", 680, font_label, text_color, shadow=True, img_obj=img)
        
        # Horizontal Split Line
        draw.line([(100, 900), (IMAGE_WIDTH - 100, 900)], fill=text_color, width=3)
        
        # Bottom Sections: Day and Month
        # Day
        font_header = get_font(40, bold=True)
        draw_centered_text(draw, "BIGGEST READING DAY", 980, font_header, muted_color, shadow=True, img_obj=img)
        font_day = get_font(80, bold=True)
        draw_centered_text(draw, busiest_day['date_short'], 1040, font_day, primary_color, shadow=True, img_obj=img)
        font_sub = get_font(40)
        draw_centered_text(draw, f"{busiest_day['count']} issues", 1140, font_sub, text_color, shadow=True, img_obj=img)
        
        # Month
        draw_centered_text(draw, "MARATHON MONTH", 1300, font_header, muted_color, shadow=True, img_obj=img)
        font_month = get_font(100, bold=True)
        draw_centered_text(draw, busiest_month['month'], 1360, font_month, hex_to_rgb(theme_colors['info']), shadow=True, img_obj=img)
        draw_centered_text(draw, f"{busiest_month['count']} issues", 1480, font_sub, text_color, shadow=True, img_obj=img)
        
        add_branding(img, draw, theme_colors, year)
        
        buffer = io.BytesIO()
        img.save(buffer, format='PNG', quality=95)
        buffer.seek(0)
        return buffer.getvalue()
    except Exception as e:
        app_logger.error(f"Error generating summary slide: {e}", exc_info=True)
        raise

def generate_most_read_series_slide(year: int, theme: str) -> bytes:
    """Generate the most read series slide with cover art taking 50% of frame."""
    theme_colors = get_theme_colors(theme)
    series_data = get_most_read_series(year, limit=1)
    bg_image_path = None
    if series_data:
        bg_image_path = ImageUtils.get_series_cover(series_data[0]['path'])

    img = create_base_image(theme_colors, bg_image_path)
    draw = ImageDraw.Draw(img)

    text_color = hex_to_rgb(theme_colors['text'])
    primary_color = hex_to_rgb(theme_colors['primary'])

    if series_data:
        series = series_data[0]
        
        # Header higher up
        font_header = get_font(48, bold=True)
        draw_centered_text(draw, "MOST READ SERIES", 230, font_header, hex_to_rgb(theme_colors['text_muted']), shadow=True, img_obj=img)

        # Draw Cover Art Card - Massive (50% of height = ~960px)
        current_y = 350
        if bg_image_path and os.path.exists(bg_image_path):
            try:
                cover = Image.open(bg_image_path).convert('RGB')
                
                # Target height 50% of screen
                target_h = int(IMAGE_HEIGHT * 0.5)
                # Max width ~900 to leave padding
                target_w = 900
                
                # Resize containing within box
                cover = ImageOps.contain(cover, (target_w, target_h), Image.Resampling.LANCZOS)
                
                # Create mask
                mask = Image.new("L", cover.size, 0)
                draw_mask = ImageDraw.Draw(mask)
                draw_mask.rounded_rectangle([(0, 0), cover.size], radius=30, fill=255)
                
                x_pos = (IMAGE_WIDTH - cover.width) // 2
                
                # Shadow
                shadow = Image.new("RGBA", (cover.width + 60, cover.height + 60), (0,0,0,0))
                shadow_draw = ImageDraw.Draw(shadow)
                shadow_draw.rounded_rectangle([(20, 20), (cover.width+40, cover.height+40)], radius=40, fill=(0,0,0,120))
                shadow = shadow.filter(ImageFilter.GaussianBlur(25))
                img.paste(shadow, (x_pos - 30, current_y - 20), shadow)
                
                img.paste(cover, (x_pos, current_y), mask)
                current_y += cover.height + 80
            except Exception:
                current_y += 200

        font_series = get_font(80, bold=True)
        y_after = draw_centered_text(draw, series['name'], current_y, font_series, primary_color, max_width=950, shadow=True, img_obj=img)
        font_count = get_font(60)
        draw_centered_text(draw, f"{series['count']} issues", y_after + 40, font_count, text_color, shadow=True, img_obj=img)
    else:
        draw_centered_text(draw, "No series data", 600, get_font(48), text_color)

    add_branding(img, draw, theme_colors, year)
    buffer = io.BytesIO()
    img.save(buffer, format='PNG', quality=95)
    buffer.seek(0)
    return buffer.getvalue()

def generate_series_highlights_slide(year: int, theme: str) -> bytes:
    """Generate grid of top series with full-fit images."""
    theme_colors = get_theme_colors(theme)
    top_series = get_top_series_with_thumbnails(year, limit=6)

    img = create_base_image(theme_colors)
    draw = ImageDraw.Draw(img)

    text_color = hex_to_rgb(theme_colors['text'])
    primary_color = hex_to_rgb(theme_colors['primary'])

    font_header = get_font(56, bold=True)
    draw_centered_text(draw, "TOP SERIES REWIND", 150, font_header, primary_color, shadow=True, img_obj=img)

    cols = 2
    rows = 3
    
    # Updated size for >25% larger images usage (1.25x area roughly)
    card_width = 480
    card_height = 520 
    col_spacing = 30
    row_spacing = 30
    
    start_x = (IMAGE_WIDTH - (cols * card_width + (cols-1)*col_spacing)) // 2
    start_y = 220 # Moved up to fit taller cards

    font_name = get_font(28, bold=True)
    font_count = get_font(24)

    for idx, series in enumerate(top_series[:6]):
        row = idx // cols
        col = idx % cols
        x = start_x + col * (card_width + col_spacing)
        y = start_y + row * (card_height + row_spacing)

        # Card container
        # Use ImageOps.contain to fit image inside card_width x (card_height - text_space)
        img_space_h = card_height - 90
        
        series_cover = ImageUtils.get_series_cover(series['series_path'])
        thumb_path = ImageUtils.get_thumbnail_path(series['first_issue_path'])
        img_to_show = series_cover if (series_cover and os.path.exists(series_cover)) else (thumb_path if (thumb_path and os.path.exists(thumb_path)) else None)
        
        if img_to_show:
            try:
                cover_art = Image.open(img_to_show).convert('RGBA')
                # Contain within box
                cover_art = ImageOps.contain(cover_art, (card_width - 20, img_space_h - 20), Image.Resampling.LANCZOS)
                
                # Center the contained image in the available space
                img_x = x + (card_width - cover_art.width) // 2
                img_y = y + (img_space_h - cover_art.height) // 2 + 10 # +10 padding top
                
                # Shadow for the book itself
                shadow = Image.new("RGBA", (cover_art.width + 20, cover_art.height + 20), (0,0,0,0))
                shadow_draw = ImageDraw.Draw(shadow)
                shadow_draw.rounded_rectangle([(10, 10), (cover_art.width+10, cover_art.height+10)], radius=10, fill=(0,0,0,120))
                shadow = shadow.filter(ImageFilter.GaussianBlur(8))
                img.paste(shadow, (img_x - 10, img_y - 5), shadow)
                
                img.paste(cover_art, (img_x, img_y), cover_art)
            except Exception:
                pass
        
        # Text
        text_y = y + img_space_h + 10
        name = series['name']
        if len(name) > 22:
             name = name[:20] + "..."
        
        # Center text in card area
        draw.text((x + 20, text_y), name, font=font_name, fill=text_color)
        draw.text((x + 20, text_y + 35), f"{series['count']} issues", font=font_count, fill=hex_to_rgb(theme_colors['text_muted']))

    add_branding(img, draw, theme_colors, year)
    buffer = io.BytesIO()
    img.save(buffer, format='PNG', quality=95)
    buffer.seek(0)
    return buffer.getvalue()

def generate_all_wrapped_images(year: int, theme: str) -> list:
    """Generate all wrapped slides."""
    # We now have fewer slides
    slides = [
        ('01_summary.png', generate_summary_slide(year, theme)),
        ('02_most_read_series.png', generate_most_read_series_slide(year, theme)),
        ('03_series_highlights.png', generate_series_highlights_slide(year, theme)),
    ]
    return slides
