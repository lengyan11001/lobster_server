from backend.app.models import ShanjianDigitalHumanVideoTask, UserContentRecord


def test_failed_digital_human_writes_content_record(db_session, test_user):
    from backend.app.api import digital_human_records

    row = ShanjianDigitalHumanVideoTask(
        user_id=test_user.id,
        title='测试数字人口播',
        status='failed',
        task_id='task-failed-001',
        error_message='上游任务超时',
    )
    db_session.add(row)
    db_session.commit()

    digital_human_records.upsert_failed_record(db_session, user_id=test_user.id, row=row)

    rec = (
        db_session.query(UserContentRecord)
        .filter_by(user_id=test_user.id, source='shanjian_digital_human', source_id='task-failed-001')
        .first()
    )
    assert rec is not None
    assert rec.status == 'failed'
    assert '生成失败' in (rec.summary or '')
    assert '上游任务超时' in (rec.summary or '')

    digital_human_records.upsert_failed_record(db_session, user_id=test_user.id, row=row)
    count = (
        db_session.query(UserContentRecord)
        .filter_by(user_id=test_user.id, source='shanjian_digital_human', source_id='task-failed-001')
        .count()
    )
    assert count == 1
