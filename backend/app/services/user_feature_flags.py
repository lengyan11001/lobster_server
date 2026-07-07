from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import UserSkillVisibility

DOUYIN_LEADS_FEATURE_ID = "douyin_leads"
DOUYIN_LEADS_ACCESS_KEY = "douyin_leads_access"
REDDIT_LEADS_FEATURE_ID = "reddit_leads"
REDDIT_LEADS_ACCESS_KEY = "reddit_leads_access"
X_LEADS_FEATURE_ID = "x_leads"
X_LEADS_ACCESS_KEY = "x_leads_access"
TIKTOK_LEADS_FEATURE_ID = "tiktok_leads"
TIKTOK_LEADS_ACCESS_KEY = "tiktok_leads_access"
OPENAI_OFFICIAL_IMAGE_CHANNEL_FEATURE_ID = "openai_official_image_channel"
OPENAI_OFFICIAL_IMAGE_CHANNEL_ACCESS_KEY = "openai_official_image_channel_access"

FEATURE_FLAG_PACKAGES: tuple[dict, ...] = (
    {
        "id": DOUYIN_LEADS_FEATURE_ID,
        "name": "抖音获客入口",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": DOUYIN_LEADS_ACCESS_KEY,
    },
    {
        "id": REDDIT_LEADS_FEATURE_ID,
        "name": "Reddit线索采集",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 1,
        "feature_key": REDDIT_LEADS_ACCESS_KEY,
    },
    {
        "id": X_LEADS_FEATURE_ID,
        "name": "X线索采集",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 1,
        "feature_key": X_LEADS_ACCESS_KEY,
    },
    {
        "id": TIKTOK_LEADS_FEATURE_ID,
        "name": "TikTok线索采集",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 1,
        "feature_key": TIKTOK_LEADS_ACCESS_KEY,
    },
    {
        "id": OPENAI_OFFICIAL_IMAGE_CHANNEL_FEATURE_ID,
        "name": "OpenAI 官方图片通道",
        "store_visibility": "管理权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": OPENAI_OFFICIAL_IMAGE_CHANNEL_ACCESS_KEY,
    },
)


def user_has_feature(db: Session, user_id: int, feature_id: str) -> bool:
    if not user_id or not feature_id:
        return False
    row = (
        db.query(UserSkillVisibility.id)
        .filter(
            UserSkillVisibility.user_id == int(user_id),
            UserSkillVisibility.package_id == feature_id,
        )
        .first()
    )
    return row is not None


def user_feature_flags(db: Session, user_id: int) -> dict[str, bool]:
    douyin_leads = user_has_feature(db, user_id, DOUYIN_LEADS_FEATURE_ID)
    reddit_leads = user_has_feature(db, user_id, REDDIT_LEADS_FEATURE_ID)
    x_leads = user_has_feature(db, user_id, X_LEADS_FEATURE_ID)
    tiktok_leads = user_has_feature(db, user_id, TIKTOK_LEADS_FEATURE_ID)
    openai_official_image_channel = user_has_feature(
        db, user_id, OPENAI_OFFICIAL_IMAGE_CHANNEL_FEATURE_ID
    )
    return {
        DOUYIN_LEADS_ACCESS_KEY: douyin_leads,
        DOUYIN_LEADS_FEATURE_ID: douyin_leads,
        REDDIT_LEADS_ACCESS_KEY: reddit_leads,
        REDDIT_LEADS_FEATURE_ID: reddit_leads,
        X_LEADS_ACCESS_KEY: x_leads,
        X_LEADS_FEATURE_ID: x_leads,
        TIKTOK_LEADS_ACCESS_KEY: tiktok_leads,
        TIKTOK_LEADS_FEATURE_ID: tiktok_leads,
        OPENAI_OFFICIAL_IMAGE_CHANNEL_ACCESS_KEY: openai_official_image_channel,
        OPENAI_OFFICIAL_IMAGE_CHANNEL_FEATURE_ID: openai_official_image_channel,
    }
