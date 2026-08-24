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
HOME_AI_CHAT_ENTRY_ID = "home_ai_chat_entry"
H5_CHAT_ENTRY_ID = "h5_chat_entry"
SKILL_STORE_ENTRY_ID = "skill_store_entry"
PUBLISH_CENTER_ENTRY_ID = "publish_center_entry"
ASSET_LIBRARY_ENTRY_ID = "asset_library_entry"
SCHEDULED_TASKS_ENTRY_ID = "scheduled_tasks_entry"
PRODUCTION_RECORDS_ENTRY_ID = "production_records_entry"
BILLING_ENTRY_ID = "billing_entry"
SYS_CONFIG_ENTRY_ID = "sys_config_entry"
LOGS_ENTRY_ID = "logs_entry"
PERSONAL_SETTINGS_ENTRY_ID = "personal_settings_entry"
AI_SECRETARY_ENTRY_ID = "ai_secretary_entry"
AGENT_ENTRY_ID = "agent_entry"
MY_AI_EMPLOYEES_ENTRY_ID = "my_ai_employees_entry"
AI_MARKETING_ENTRY_ID = "ai_marketing_entry"
TUTORIAL_ENTRY_ID = "tutorial_entry"
LOCAL_BESTSELLER_SKILL_ID = "local_bestseller_skill"
VIRAL_VIDEO_REMIX_SKILL_ID = "viral_video_remix_skill"
MULTI_CLIP_MIXER_SKILL_ID = "multi_clip_mixer_skill"
BIHUO_25_VIDEO_SKILL_ID = "bihuo_25_video_skill"
HOMEPAGE_FEATURE_GATES_MARKER = "__homepage_feature_gates_v1"
HOMEPAGE_ENTRY_SEEDED_MARKER = "__homepage_entry_permissions_seeded_v1"
RETIRED_PACKAGE_IDS = frozenset({
    "production_records_entry",
    "openclaw_weixin_channel",
    "openclaw_memory_skill",
    "browser_use_skill",
    "computer_use_skill",
    "media_edit_skill",
})
HOMEPAGE_DEFAULT_ENTRY_FEATURE_IDS = (
    HOME_AI_CHAT_ENTRY_ID,
    H5_CHAT_ENTRY_ID,
    SKILL_STORE_ENTRY_ID,
    PUBLISH_CENTER_ENTRY_ID,
    ASSET_LIBRARY_ENTRY_ID,
    SCHEDULED_TASKS_ENTRY_ID,
    BILLING_ENTRY_ID,
    SYS_CONFIG_ENTRY_ID,
    LOGS_ENTRY_ID,
    PERSONAL_SETTINGS_ENTRY_ID,
    AGENT_ENTRY_ID,
    MY_AI_EMPLOYEES_ENTRY_ID,
    AI_MARKETING_ENTRY_ID,
    TUTORIAL_ENTRY_ID,
    LOCAL_BESTSELLER_SKILL_ID,
)

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
    {
        "id": HOME_AI_CHAT_ENTRY_ID,
        "name": "首页：AI 对话入口",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": HOME_AI_CHAT_ENTRY_ID,
    },
    {
        "id": H5_CHAT_ENTRY_ID,
        "name": "首页：手机对话入口",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": H5_CHAT_ENTRY_ID,
    },
    {
        "id": SKILL_STORE_ENTRY_ID,
        "name": "首页：技能商店入口",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": SKILL_STORE_ENTRY_ID,
    },
    {
        "id": PUBLISH_CENTER_ENTRY_ID,
        "name": "首页：发布中心入口",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": PUBLISH_CENTER_ENTRY_ID,
    },
    {
        "id": ASSET_LIBRARY_ENTRY_ID,
        "name": "首页：素材库入口",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": ASSET_LIBRARY_ENTRY_ID,
    },
    {
        "id": SCHEDULED_TASKS_ENTRY_ID,
        "name": "首页：定时任务入口",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": SCHEDULED_TASKS_ENTRY_ID,
    },
    {
        "id": PRODUCTION_RECORDS_ENTRY_ID,
        "name": "首页：生成历史入口",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": PRODUCTION_RECORDS_ENTRY_ID,
    },
    {
        "id": BILLING_ENTRY_ID,
        "name": "首页：消费记录入口",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": BILLING_ENTRY_ID,
    },
    {
        "id": SYS_CONFIG_ENTRY_ID,
        "name": "首页：系统配置入口",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": SYS_CONFIG_ENTRY_ID,
    },
    {
        "id": LOGS_ENTRY_ID,
        "name": "首页：日志入口",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": LOGS_ENTRY_ID,
    },
    {
        "id": PERSONAL_SETTINGS_ENTRY_ID,
        "name": "首页：个人设置入口",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": PERSONAL_SETTINGS_ENTRY_ID,
    },
    {
        "id": AI_SECRETARY_ENTRY_ID,
        "name": "AI秘书入口",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": AI_SECRETARY_ENTRY_ID,
    },
    {
        "id": AGENT_ENTRY_ID,
        "name": "首页：AI 执行台入口",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": AGENT_ENTRY_ID,
    },
    {
        "id": MY_AI_EMPLOYEES_ENTRY_ID,
        "name": "我的 AI 员工",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": MY_AI_EMPLOYEES_ENTRY_ID,
    },
    {
        "id": AI_MARKETING_ENTRY_ID,
        "name": "AI 营销创作",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": AI_MARKETING_ENTRY_ID,
    },
    {
        "id": TUTORIAL_ENTRY_ID,
        "name": "教程入口",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": TUTORIAL_ENTRY_ID,
    },
    {
        "id": LOCAL_BESTSELLER_SKILL_ID,
        "name": "同城爆款入口",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": LOCAL_BESTSELLER_SKILL_ID,
    },
    {
        "id": VIRAL_VIDEO_REMIX_SKILL_ID,
        "name": "爆款复刻入口",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": VIRAL_VIDEO_REMIX_SKILL_ID,
    },
    {
        "id": MULTI_CLIP_MIXER_SKILL_ID,
        "name": "多段视频混剪入口",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": MULTI_CLIP_MIXER_SKILL_ID,
    },
    {
        "id": BIHUO_25_VIDEO_SKILL_ID,
        "name": "必火2.5入口",
        "store_visibility": "入口权限",
        "unlock_price_yuan": None,
        "unlock_price_credits": None,
        "capabilities_count": 0,
        "feature_key": BIHUO_25_VIDEO_SKILL_ID,
    },
)

FEATURE_FLAG_PACKAGE_ALIASES = {
    str(pkg.get("id") or ""): str(pkg.get("feature_key") or "")
    for pkg in FEATURE_FLAG_PACKAGES
    if pkg.get("id") and pkg.get("feature_key")
}


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


def _ensure_homepage_entry_permissions_seeded(db: Session, user_id: int, visible: set[str]) -> set[str]:
    if not user_id:
        return visible
    if HOMEPAGE_ENTRY_SEEDED_MARKER in visible:
        return visible
    for feature_id in HOMEPAGE_DEFAULT_ENTRY_FEATURE_IDS:
        if feature_id not in visible:
            db.add(UserSkillVisibility(user_id=int(user_id), package_id=feature_id))
            visible.add(feature_id)
    db.add(UserSkillVisibility(user_id=int(user_id), package_id=HOMEPAGE_ENTRY_SEEDED_MARKER))
    visible.add(HOMEPAGE_ENTRY_SEEDED_MARKER)
    try:
        db.commit()
    except Exception:
        db.rollback()
    return visible


def user_feature_flags(db: Session, user_id: int) -> dict[str, bool]:
    if not user_id:
        return {HOMEPAGE_FEATURE_GATES_MARKER: True}

    try:
        from ..api.skills import _default_visible_packages_for_request, _user_has_custom_visibility
        from ..models import User

        user = db.query(User).filter(User.id == int(user_id)).first()
        has_custom = _user_has_custom_visibility(db, int(user_id))
        if user is not None and not has_custom:
            from ..api.skills import _expand_group_visibility
            visible = _expand_group_visibility(set(_default_visible_packages_for_request(bool(getattr(user, "is_overseas_user", False)))))
        else:
            visible = {
                row[0]
                for row in db.query(UserSkillVisibility.package_id)
                .filter(UserSkillVisibility.user_id == int(user_id))
                .all()
            }
            visible = _ensure_homepage_entry_permissions_seeded(db, int(user_id), set(visible))
    except Exception:
        visible = {
            row[0]
            for row in db.query(UserSkillVisibility.package_id)
            .filter(UserSkillVisibility.user_id == int(user_id))
            .all()
        }

    flags: dict[str, bool] = {HOMEPAGE_FEATURE_GATES_MARKER: True}
    for package in FEATURE_FLAG_PACKAGES:
        package_id = str(package.get("id") or "").strip()
        feature_key = str(package.get("feature_key") or "").strip()
        if package_id:
            flags.setdefault(package_id, False)
        if feature_key:
            flags.setdefault(feature_key, False)
    for package_id in RETIRED_PACKAGE_IDS:
        flags.setdefault(package_id, False)
    for package_id in visible:
        key = str(package_id or "").strip()
        if not key or key in RETIRED_PACKAGE_IDS:
            continue
        flags[key] = True
        alias = FEATURE_FLAG_PACKAGE_ALIASES.get(key)
        if alias:
            flags[alias] = True

    return flags
