from backend.app.api import cutcli_templates as templates


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


def _captions_for_template(template_id, stt_data):
    style = templates._caption_style_for_template(templates._TEMPLATES[template_id])
    return templates._captions_from_stt(
        stt_data,
        video_duration_sec=3.0,
        caption_style=style,
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


def test_caption_templates_have_distinct_design_layouts():
    styles = {
        template_id: templates._caption_style_for_template(template)
        for template_id, template in templates._TEMPLATES.items()
    }

    assert styles[templates._AUTO_CAPTION_TEMPLATE_ID]["ass_layout"] == "center_burst"
    assert styles[templates._AUTO_CAPTION_CLEAN_TEMPLATE_ID]["ass_layout"] == "lower_clean"
    assert styles[templates._AUTO_CAPTION_NEON_TEMPLATE_ID]["ass_layout"] == "side_neon"
    assert styles[templates._AUTO_CAPTION_NEON_TEMPLATE_ID]["caption_motion"] == "typewriter"
    assert styles[templates._AUTO_CAPTION_NEON_TEMPLATE_ID]["in_animation"] == "故障打字"
    assert styles[templates._AUTO_CAPTION_PUNCH_TEMPLATE_ID]["ass_layout"] == "dramatic_hook"
    assert len({style["ass_layout"] for style in styles.values()}) == 4
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

    side_caps = _captions_for_template(templates._AUTO_CAPTION_NEON_TEMPLATE_ID, stt_data)
    punch_caps = _captions_for_template(templates._AUTO_CAPTION_PUNCH_TEMPLATE_ID, stt_data)
    clean_caps = _captions_for_template(templates._AUTO_CAPTION_CLEAN_TEMPLATE_ID, stt_data)

    assert len(side_caps) == 2
    assert all(item["transformX"] <= -0.45 for item in side_caps)
    assert len({item["transformY"] for item in side_caps}) == 2
    assert all(item["inAnimation"] == "故障打字" for item in side_caps)
    assert max(item["fontSize"] for item in punch_caps) > max(item["fontSize"] for item in clean_caps)
