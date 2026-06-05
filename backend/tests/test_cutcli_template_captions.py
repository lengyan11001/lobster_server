from backend.app.api import cutcli_templates as templates
from backend.app.models import CreativeGenerationJob


def _caption_texts(stt_data):
    style = templates._caption_style_for_template(
        templates._TEMPLATES[templates._AUTO_CAPTION_TEMPLATE_ID]
    )
    captions = templates._captions_from_stt(
        stt_data,
        video_duration_sec=3.0,
        caption_style=style,
    )
    return [item["text"] for item in captions]


def _captions_for_template(template_id, stt_data, video_width=None):
    style = templates._caption_style_for_template(templates._TEMPLATES[template_id])
    return templates._captions_from_stt(
        stt_data,
        video_duration_sec=3.0,
        caption_style=style,
        video_width=video_width,
    )


def test_caption_split_keeps_next_sentence_initial_out_of_previous_caption():
    stt_data = {
        "output": {
            "utterances": [
                {
                    "text": "\u4f60\u7684\u9700\u6c42\u3002\u5b57\u5e55\u914d\u97f3",
                    "start_time": 0,
                    "end_time": 1200,
                    "words": [
                        {"text": "\u4f60\u7684", "start_time": 0, "end_time": 260},
                        {"text": "\u9700\u6c42\u3002\u5b57", "start_time": 260, "end_time": 720},
                        {"text": "\u5e55\u914d\u97f3", "start_time": 720, "end_time": 1200},
                    ],
                }
            ]
        }
    }

    assert _caption_texts(stt_data) == [
        "\u4f60\u7684\u9700\u6c42",
        "\u5b57\u5e55\u914d\u97f3",
    ]


def test_caption_split_keeps_stt_utterance_boundaries():
    stt_data = {
        "output": {
            "utterances": [
                {
                    "text": "\u4e0d\u7528\u62cd",
                    "start_time": 0,
                    "end_time": 420,
                    "words": [
                        {"text": "\u4e0d\u7528\u62cd", "start_time": 0, "end_time": 420},
                    ],
                },
                {
                    "text": "\u5e2e\u4f60\u81ea\u52a8\u526a",
                    "start_time": 430,
                    "end_time": 980,
                    "words": [
                        {"text": "\u5e2e\u4f60", "start_time": 430, "end_time": 620},
                        {"text": "\u81ea\u52a8\u526a", "start_time": 620, "end_time": 980},
                    ],
                },
            ]
        }
    }

    assert _caption_texts(stt_data) == [
        "\u4e0d\u7528\u62cd",
        "\u5e2e\u4f60\u81ea\u52a8\u526a",
    ]


def test_caption_does_not_split_single_stt_sentence_for_width_only():
    text = "\u4e00\u4e2a\u4e13\u4e1a\u9760\u8c31\u7684\u8d22\u7a0e\u987e\u95ee"
    stt_data = {
        "output": {
            "utterances": [
                {
                    "text": text,
                    "start_time": 0,
                    "end_time": 1600,
                    "words": [
                        {"text": text, "start_time": 0, "end_time": 1600},
                    ],
                }
            ]
        }
    }

    captions = _captions_for_template(
        templates._AUTO_CAPTION_TEMPLATE_ID,
        stt_data,
        video_width=720,
    )

    assert len(captions) == 1
    assert captions[0]["text"].replace("\n", "") == text
    assert "\n" in captions[0]["text"]


def test_caption_wrap_keeps_required_phrase_together():
    text = "\u4e00\u4e2a\u4e13\u4e1a\u9760\u8c31\u7684\u8d22\u7a0e\u987e\u95ee\u9700\u8981\u5177\u5907"
    stt_data = {
        "output": {
            "utterances": [
                {
                    "text": text,
                    "start_time": 0,
                    "end_time": 2200,
                    "words": [
                        {"text": text, "start_time": 0, "end_time": 2200},
                    ],
                }
            ]
        }
    }

    captions = _captions_for_template(
        templates._AUTO_CAPTION_TEMPLATE_ID,
        stt_data,
        video_width=720,
    )
    caption_text = "|".join(item["text"] for item in captions)

    assert caption_text.replace("\n", "").replace("|", "") == text
    assert "\u5177\n\u5907" not in caption_text
    assert "\u5177|\u5907" not in caption_text
    assert any(
        line.endswith("\u9700\u8981\u5177\u5907") or line.endswith("\u5177\u5907")
        for item in captions
        for line in item["text"].split("\n")
    )


def test_caption_preserves_english_word_spaces():
    stt_data = {
        "output": {
            "utterances": [
                {
                    "text": "we had a lot of",
                    "start_time": 0,
                    "end_time": 1400,
                    "words": [
                        {"text": "we", "start_time": 0, "end_time": 180},
                        {"text": "had", "start_time": 180, "end_time": 360},
                        {"text": "a", "start_time": 360, "end_time": 480},
                        {"text": "lot", "start_time": 480, "end_time": 720},
                        {"text": "of", "start_time": 720, "end_time": 980},
                    ],
                }
            ]
        }
    }

    captions = _captions_for_template(
        templates._AUTO_CAPTION_TEMPLATE_ID,
        stt_data,
        video_width=720,
    )
    text = " ".join(item["text"].replace("\n", " ") for item in captions)

    raw = "|".join(item["text"] for item in captions)
    assert "we had a lot of" in text
    assert "wehadalotof" not in raw
    assert all(" " in item["text"] or len(item["text"].split()) <= 1 for item in captions)


def test_caption_wrap_does_not_break_english_words():
    text = "we had a lot of international customers today"
    stt_data = {
        "output": {
            "utterances": [
                {
                    "text": text,
                    "start_time": 0,
                    "end_time": 2200,
                    "words": [
                        {"text": "we", "start_time": 0, "end_time": 150},
                        {"text": "had", "start_time": 150, "end_time": 300},
                        {"text": "a", "start_time": 300, "end_time": 420},
                        {"text": "lot", "start_time": 420, "end_time": 620},
                        {"text": "of", "start_time": 620, "end_time": 760},
                        {"text": "international", "start_time": 760, "end_time": 1320},
                        {"text": "customers", "start_time": 1320, "end_time": 1760},
                        {"text": "today", "start_time": 1760, "end_time": 2200},
                    ],
                }
            ]
        }
    }

    captions = _captions_for_template(
        templates._AUTO_CAPTION_CLEAN_TEMPLATE_ID,
        stt_data,
        video_width=1280,
    )
    rendered = " ".join(item["text"].replace("\n", " ") for item in captions)

    assert rendered == text
    for token in text.split():
        assert token in rendered


def test_caption_templates_have_distinct_design_layouts():
    styles = {
        template_id: templates._caption_style_for_template(template)
        for template_id, template in templates._TEMPLATES.items()
    }

    assert styles[templates._AUTO_CAPTION_TEMPLATE_ID]["ass_layout"] == "right_vertical_card"
    assert styles[templates._AUTO_CAPTION_CLEAN_TEMPLATE_ID]["ass_layout"] == "education_focus_bar"
    assert styles[templates._AUTO_CAPTION_NEON_TEMPLATE_ID]["ass_layout"] == "tea_center_title"
    assert styles[templates._AUTO_CAPTION_PUNCH_TEMPLATE_ID]["ass_layout"] == "red_yellow_hook"
    assert styles[templates._AUTO_CAPTION_HEALTH_BANNER_TEMPLATE_ID]["ass_layout"] == "health_banner"
    assert styles[templates._AUTO_CAPTION_QUOTE_FOCUS_TEMPLATE_ID]["ass_layout"] == "quote_focus"
    assert styles[templates._AUTO_CAPTION_MARKET_LABEL_TEMPLATE_ID]["ass_layout"] == "market_label"
    assert styles[templates._AUTO_CAPTION_BLACK_GOLD_TEMPLATE_ID]["ass_layout"] == "black_gold_quote"
    assert styles[templates._AUTO_CAPTION_TCM_WAIST_TEMPLATE_ID]["ass_layout"] == "tcm_waist_banner"
    assert styles[templates._AUTO_CAPTION_NEWS_BRIEF_TEMPLATE_ID]["ass_layout"] == "news_brief"
    assert len({style["ass_layout"] for style in styles.values()}) == 10
    assert styles[templates._AUTO_CAPTION_PUNCH_TEMPLATE_ID]["font_size"] > styles[templates._AUTO_CAPTION_CLEAN_TEMPLATE_ID]["font_size"]
    assert styles[templates._AUTO_CAPTION_PUNCH_TEMPLATE_ID]["caption_max_chars"] < styles[templates._AUTO_CAPTION_CLEAN_TEMPLATE_ID]["caption_max_chars"]


def test_template_captions_apply_position_and_font_differences():
    stt_data = {
        "output": {
            "utterances": [
                {
                    "text": "第一句第二句",
                    "start_time": 0,
                    "end_time": 1700,
                    "words": [
                        {"text": "第一句", "start_time": 0, "end_time": 500},
                        {"text": "第二句", "start_time": 1100, "end_time": 1700},
                    ],
                }
            ]
        }
    }

    business_caps = _captions_for_template(templates._AUTO_CAPTION_TEMPLATE_ID, stt_data)
    punch_caps = _captions_for_template(templates._AUTO_CAPTION_PUNCH_TEMPLATE_ID, stt_data)
    clean_caps = _captions_for_template(templates._AUTO_CAPTION_CLEAN_TEMPLATE_ID, stt_data)

    assert len(business_caps) == 2
    assert all(float(item["transformX"]) == 0.0 for item in business_caps)
    assert all(float(item["transformY"]) <= -0.65 for item in business_caps)
    assert max(item["fontSize"] for item in punch_caps) > max(item["fontSize"] for item in clean_caps)


def test_overlay_title_preserves_book_and_quote_marks():
    style = templates._caption_style_for_template(
        templates._TEMPLATES[templates._AUTO_CAPTION_NEON_TEMPLATE_ID]
    )
    overlay_texts = {"title": "\u300a\u6ce1\u8336\u300b", "subtitle": "\u30105\u79cd\u98df\u7269\u3011"}

    assert templates._overlay_text_value(overlay_texts, style, "title") == "\u300a\u6ce1\u8336\u300b"
    assert templates._overlay_text_value(overlay_texts, style, "subtitle") == "\u30105\u79cd\u98df\u7269\u3011"

    rendered = "\n".join(
        templates._overlay_dialogues(
            style,
            play_width=1080,
            play_height=1920,
            duration_sec=1.0,
            overlay_texts=overlay_texts,
        )
    )
    assert "\u300a\u6ce1\u8336\u300b" in rendered
    assert "\u30105\u79cd\u98df\u7269\u3011" in rendered


def test_large_yellow_caption_wraps_inside_one_caption():
    text = "\u4e00\u4e2a\u4e13\u4e1a\u9760\u8c31\u7684\u8d22\u7a0e\u987e\u95ee"
    stt_data = {
        "output": {
            "utterances": [
                {
                    "text": text,
                    "start_time": 0,
                    "end_time": 1800,
                    "words": [
                        {"text": text, "start_time": 0, "end_time": 1800},
                    ],
                }
            ]
        }
    }
    style = templates._caption_style_for_template(
        templates._TEMPLATES[templates._AUTO_CAPTION_TEMPLATE_ID]
    )

    captions = _captions_for_template(
        templates._AUTO_CAPTION_TEMPLATE_ID,
        stt_data,
        video_width=720,
    )
    caption_texts = [item["text"] for item in captions]

    assert len(caption_texts) == 1
    assert "\n" in caption_texts[0]
    assert caption_texts[0].replace("\n", "") == text
    quality, errors, _warnings = templates._validate_caption_quality(
        captions,
        caption_style=style,
        video_width=720,
    )
    assert quality["visual_overflow_count"] == 0
    assert "caption_visual_overflow" not in errors


def test_caption_quality_rejects_visual_overflow():
    style = templates._caption_style_for_template(
        templates._TEMPLATES[templates._AUTO_CAPTION_TEMPLATE_ID]
    )
    captions = [
        {
            "text": "\u4e00\u4e2a\u4e13\u4e1a\u9760\u8c31\u7684\u8d22\u7a0e\u987e\u95ee",
            "start": 0,
            "end": 1_200_000,
            "fontSize": 15,
        }
    ]

    quality, errors, _warnings = templates._validate_caption_quality(
        captions,
        caption_style=style,
        video_width=720,
    )

    assert quality["visual_overflow_count"] == 1
    assert "caption_visual_overflow" in errors


def test_cutcli_job_public_payload_uses_database_row(db_session, db_session_factory, monkeypatch):
    monkeypatch.setattr(templates, "SessionLocal", db_session_factory)
    template = templates._TEMPLATES[templates._AUTO_CAPTION_TEMPLATE_ID]
    row = templates._create_cutcli_job(
        db_session,
        job_id="20260604000000_abcdef12",
        user_id=7,
        template=template,
        source_asset_id="src123",
        source_name="source.mp4",
        source_info={"width": 720, "height": 1280, "duration": 12.3},
        quality_policy={"expected_caption_tracks": 1},
    )
    templates._update_cutcli_job(
        row.job_id,
        status="completed",
        stage="completed",
        asset_ids=["asset123"],
        result_updates={
            "preview_url": "https://cdn.example/final.mp4",
            "open_url": "https://cdn.example/final.mp4",
            "preview_asset_id": "asset123",
            "caption_count": 4,
            "render_strategy": "cutcli_cloud",
        },
        meta_updates={"local_workspace_cleanup": {"removed_bytes": 123}},
    )

    db_session.expire_all()
    saved = (
        db_session.query(CreativeGenerationJob)
        .filter(CreativeGenerationJob.job_id == row.job_id)
        .first()
    )
    payload = templates._job_row_to_public(saved)

    assert payload["job_id"] == row.job_id
    assert payload["status"] == "completed"
    assert payload["template_id"] == template["id"]
    assert payload["preview_asset_id"] == "asset123"
    assert payload["preview_url"] == "https://cdn.example/final.mp4"
    assert payload["caption_count"] == 4
    assert payload["audio_url"] == ""
    assert payload["local_workspace_cleanup"]["removed_bytes"] == 123


def test_auto_caption_job_uses_client_audio_url_without_server_extract(
    db_session,
    db_session_factory,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(templates, "SessionLocal", db_session_factory)
    monkeypatch.setattr(templates, "_JOBS_DIR", tmp_path)
    job_id = "20260604000001_abcdef12"
    (tmp_path / job_id).mkdir(parents=True)
    template = templates._TEMPLATES[templates._AUTO_CAPTION_TEMPLATE_ID]
    audio_url = "https://cdn.example/audio.wav"
    templates._create_cutcli_job(
        db_session,
        job_id=job_id,
        user_id=7,
        template=template,
        source_asset_id=None,
        source_name="source.mp4",
        source_info={"width": 720, "height": 1280, "duration": 2.0},
        quality_policy={"expected_caption_tracks": 1},
        audio_url=audio_url,
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("server audio extraction should be skipped")

    monkeypatch.setattr(templates, "_find_cutcli_bin", lambda: "cutcli")
    monkeypatch.setattr(templates, "_find_ffmpeg_bin", fail_if_called)
    monkeypatch.setattr(templates, "_extract_audio_wav", fail_if_called)
    monkeypatch.setattr(templates, "_upload_job_file_to_tos", fail_if_called)
    monkeypatch.setattr(templates, "_load_sutui_token_for_stt", lambda *_args, **_kwargs: ("tok", "env.server"))
    monkeypatch.setattr(templates, "_stt_create_task", lambda token, url, *, job_dir: {"task_id": "stt1"})
    monkeypatch.setattr(templates, "_stt_poll_task", lambda token, task_id, *, job_dir: {"output": {}})
    monkeypatch.setattr(
        templates,
        "_captions_from_stt",
        lambda *_args, **_kwargs: [
            {"text": "hello", "start": 0, "end": 1_000_000, "fontSize": 12}
        ],
    )
    monkeypatch.setattr(
        templates,
        "_validate_caption_quality",
        lambda *_args, **_kwargs: ({"caption_count": 1}, [], []),
    )
    monkeypatch.setattr(
        templates,
        "_build_auto_caption_cutcli_draft",
        lambda **_kwargs: ("draft1", {"draft_id": "draft1"}, {"actual_caption_count": 1}, []),
    )
    monkeypatch.setattr(
        templates,
        "_render_cutcli_cloud",
        lambda *_args, **_kwargs: {"url": "https://cdn.example/final.mp4", "job_id": "cloud1"},
    )
    monkeypatch.setattr(
        templates,
        "_mirror_video_url_to_tos",
        lambda *_args, **_kwargs: ("https://tos.example/final.mp4", 123, []),
    )
    monkeypatch.setattr(templates, "_save_auto_caption_asset", lambda **_kwargs: "asset-final")
    monkeypatch.setattr(templates, "_cleanup_auto_caption_workspace_and_record", lambda job_id: None)

    templates._run_auto_caption_job_sync(
        job_id=job_id,
        user_id=7,
        template_id=template["id"],
        source="https://cdn.example/source.mp4",
        source_asset_id=None,
        source_name="source.mp4",
        source_info={"width": 720, "height": 1280, "duration": 2.0},
        audio_url=audio_url,
    )

    db_session.expire_all()
    saved = (
        db_session.query(CreativeGenerationJob)
        .filter(CreativeGenerationJob.job_id == job_id)
        .first()
    )
    payload = templates._job_row_to_public(saved)
    assert payload["status"] == "completed"
    assert payload["audio_url"] == audio_url
    assert payload["preview_url"] == "https://tos.example/final.mp4"
    assert saved.meta["audio_source"] == "client_audio_url"


def test_cutcli_workspace_cleanup_removes_job_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(templates, "_JOBS_DIR", tmp_path)
    job_id = "20260604000000_abcdef12"
    job_dir = tmp_path / job_id
    nested = job_dir / "cutcli_drafts" / "draft_a"
    nested.mkdir(parents=True)
    (job_dir / "source.mp4").write_bytes(b"source")
    (job_dir / "audio.wav").write_bytes(b"audio")
    (nested / "draft.json").write_bytes(b"draft")

    cleanup = templates._cleanup_auto_caption_workspace(job_id)

    assert cleanup["removed_bytes"] == len(b"sourceaudiodraft")
    assert cleanup["removed_files"] == 2
    assert cleanup["removed_dirs"] >= 1
    assert not job_dir.exists()
