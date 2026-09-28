"""shop 独立站主题预设：同一套版式，换主题就换气质（农产/科技/家电/美妆…）。

商家 `shop_merchants.theme` 存 {"preset": "fresh_green", "accent": "#2f7d46", ...}；
未显式指定时按商品类目映射默认主题；前端只读 theme_css_vars() 的输出。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

THEME_PRESETS: Dict[str, Dict[str, Any]] = {
    "fresh_green": {  # 农产品 / 生鲜 / 土特产
        "label": "田野鲜绿",
        "bg": "#faf8f2", "ink": "#26291f", "dim": "#7c8072",
        "accent": "#2f7d46", "accent_soft": "rgba(47,125,70,.14)",
        "card": "#fffdf9", "line": "rgba(47,125,70,.18)",
        "radius": "20px", "heading_font": "serif",
        "imagery": "自然光、果园实拍、木箱与麻布质感",
    },
    "dark_gold": {  # 高端 / 数码 / 企业服务
        "label": "墨金质感",
        "bg": "#08090c", "ink": "#f4f1ea", "dim": "#9c978c",
        "accent": "#c8a45c", "accent_soft": "rgba(200,164,92,.2)",
        "card": "#0d0f14", "line": "rgba(200,164,92,.22)",
        "radius": "26px", "heading_font": "sans",
        "imagery": "暗场棚拍、金属反光、深色渐变",
    },
    "clean_blue": {  # 家电 / 3C / 母婴
        "label": "清透蓝白",
        "bg": "#f7f9fc", "ink": "#1d2430", "dim": "#77808f",
        "accent": "#2f6fed", "accent_soft": "rgba(47,111,237,.12)",
        "card": "#ffffff", "line": "rgba(47,111,237,.16)",
        "radius": "18px", "heading_font": "sans",
        "imagery": "纯白背景、产品几何阴影、留白",
    },
    "warm_beige": {  # 美妆 / 服饰 / 家居
        "label": "暖调奶油",
        "bg": "#fbf7f3", "ink": "#2b2521", "dim": "#8b8079",
        "accent": "#c2703d", "accent_soft": "rgba(194,112,61,.14)",
        "card": "#fffdfb", "line": "rgba(194,112,61,.18)",
        "radius": "22px", "heading_font": "serif",
        "imagery": "柔光、布艺与陶瓷、暖色阴影",
    },
}
DEFAULT_PRESET = "fresh_green"

CATEGORY_PRESET_HINTS = (
    (("农产", "生鲜", "水果", "土特产", "粮油", "茶叶", "food", "fresh"), "fresh_green"),
    (("数码", "3c", "企业服务", "软件", "saas", "科技", "高定"), "dark_gold"),
    (("家电", "母婴", "食品机械", "运动", "健康"), "clean_blue"),
    (("美妆", "服饰", "家居", "饰品", "母婴服"), "warm_beige"),
)


def preset_for_category(category: str, keywords: str = "") -> str:
    text = f"{category or ''} {keywords or ''}".lower()
    for words, preset in CATEGORY_PRESET_HINTS:
        if any(word.lower() in text for word in words):
            return preset
    return DEFAULT_PRESET


def resolve_theme(merchant_theme: Optional[dict], category: str = "", keywords: str = "") -> Dict[str, Any]:
    """商家自定义 > 类目默认 > 全局默认；返回可直接用的主题字典。"""
    stored = merchant_theme if isinstance(merchant_theme, dict) else {}
    preset_name = str(stored.get("preset") or "").strip() or preset_for_category(category, keywords)
    base = dict(THEME_PRESETS.get(preset_name) or THEME_PRESETS[DEFAULT_PRESET])
    base["preset"] = preset_name if preset_name in THEME_PRESETS else DEFAULT_PRESET
    for key in ("bg", "ink", "dim", "accent", "card", "line", "radius", "heading_font", "imagery"):
        value = stored.get(key)
        if isinstance(value, str) and value.strip():
            base[key] = value.strip()
    return base


def theme_css_vars(theme: Dict[str, Any]) -> str:
    mapping = {"bg": "--bg", "ink": "--ink", "dim": "--dim", "accent": "--gold",
               "card": "--card", "line": "--line", "radius": "--radius"}
    return ";".join(f"{css}:{theme.get(key, '')}" for key, css in mapping.items() if theme.get(key))


def asset_prompt_style(theme: Dict[str, Any]) -> str:
    """给素材生成通道的风格提示（各品类气质不同，提示词随之变化）。"""
    return f"{theme.get('label', '')}风格：{theme.get('imagery', '')}"
