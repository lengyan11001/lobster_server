from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_personal_survey_renders_all_questions_as_one_vertical_list() -> None:
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "h5_static" / "h5-app.js").read_text(encoding="utf-8")
    styles = (ROOT / "h5_static" / "h5-designer-v2.css").read_text(encoding="utf-8")

    question_block = script.split("function personalSurveyQuestions()", 1)[1].split(
        "function syncPersonalSurveyAnswerToField()", 1
    )[0]

    assert question_block.count("field:") == 14
    assert question_block.count("hint:") == 14
    assert 'id="personalSurveyQuestionList"' in html
    assert 'id="personalSurveyCompletionText"' in html
    assert "personalSurveyPrevBtn" not in html
    assert "personalSurveyNextBtn" not in html
    assert 'data-personal-survey-answer="${escapeHtml(question.field)}"' in script
    assert re.search(r"\.personal-survey-list\s*\{[^}]*display:\s*block", styles, re.S)
    assert ".personal-survey-item:last-child" in styles


def test_personal_survey_keeps_existing_persisted_fields() -> None:
    html = (ROOT / "h5_static" / "index.html").read_text(encoding="utf-8")
    expected_fields = [
        "personalProfileName",
        "personalGender",
        "personalProfilePhoto",
        "personalBirthEra",
        "personalCurrentProvince",
        "personalCurrentCity",
        "personalHometown",
        "personalRole",
        "personalShareTopic",
        "personalVideoStyle",
        "personalAfterViewAction",
        "personalBusinessProduct",
        "personalTargetCustomer",
        "personalAdvantages",
    ]

    for field in expected_fields:
        assert f'id="{field}"' in html

