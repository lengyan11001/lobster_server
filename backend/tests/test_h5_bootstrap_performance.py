from pathlib import Path


def test_h5_bootstrap_reuses_office_summary_request():
    js = (Path(__file__).resolve().parents[2] / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    start = js.index("async function loadAuthenticatedBootstrapData()")
    end = js.index("async function validateAuthAndBootstrap", start)
    bootstrap_data = js[start:end]

    assert "refreshOfficeSummary()" in bootstrap_data
    assert "loadHistory({ includeEvents: false })" in bootstrap_data
    assert "loadTasks({ reset: true })" not in bootstrap_data
    assert "loadRuns({ reset: true, limit: 20, compact: true })" not in bootstrap_data

    summary_start = js.index("function refreshOfficeSummary()")
    summary_end = js.index("function switchTab(tab)", summary_start)
    office_summary = js[summary_start:summary_end]
    assert "loadTasks(" not in office_summary
    assert "loadRuns({ reset: true, limit: 20, compact: true })" in office_summary


def test_h5_bootstrap_shows_cached_user_before_background_validation():
    js = (Path(__file__).resolve().parents[2] / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    start = js.index("async function loadMe()")
    end = js.index("function renderProfileDeviceSelect()", start)
    load_me = js[start:end]
    validate_start = js.index("async function validateAuthAndBootstrap")
    validate_end = js.index("async function refreshCachedAuthInBackground", validate_start)
    validate = js[validate_start:validate_end]

    assert 'const H5_USER_CACHE_KEY = brandStorageKey("lobster_h5_user");' in js
    assert "user: readCachedH5User()" in js
    assert "writeCachedH5User(state.user)" in js
    assert "showAuthenticatedShell();" in load_me
    assert "refreshCachedAuthInBackground().catch" in load_me
    assert "loadAuthenticatedBootstrapData().catch" in validate
    assert "await loadAuthenticatedBootstrapData()" not in validate
    assert "loadH5Branding().catch(() => {});" in js
    assert "await loadH5Branding();" not in js


def test_h5_branding_does_not_fallback_to_bihuo_for_other_brands():
    js = (Path(__file__).resolve().parents[2] / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    apply_start = js.index("function applyH5Branding")
    apply_end = js.index("async function loadH5Branding", apply_start)
    apply_branding = js[apply_start:apply_end]

    assert 'const H5_BRANDING_CACHE_KEY = brandStorageKey("lobster_h5_branding");' in js
    assert "function currentH5BrandingConfig()" in js
    assert 'H5_BRAND_MARK === "bihuo" ? H5_BRAND_FALLBACKS.bihuo : {}' in js
    assert "H5_BRAND_FALLBACKS[H5_BRAND_MARK] || H5_BRAND_FALLBACKS.bihuo" not in js
    assert "if (logo && (cfg.icon_32 || cfg.icon_128))" in apply_branding
    assert "if (appleIcon && (cfg.icon_256 || cfg.icon_128))" in apply_branding
    assert 'office: [currentH5BrandTitle() || "", "我的AI员工办公室"]' in js
    assert 'office: ["必火AI员工", "我的AI员工办公室"]' not in js


def test_h5_message_history_defers_event_payloads_until_messages_view():
    js = (Path(__file__).resolve().parents[2] / "h5_static" / "h5-app.js").read_text(encoding="utf-8")

    assert 'include_events=${includeEvents ? "true" : "false"}' in js
    assert 'loadHistory({ includeEvents: true }).catch(() => {});' in js
