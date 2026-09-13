"""manage（项目管理与 AI 赋能）独立站的表结构。

与主站共用 users / 同一套 JWT；只新增 m_ 前缀表，不改动既有表。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)

from .db import Base


class MCompany(Base):
    __tablename__ = "m_company"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(160), nullable=False)
    short_name = Column(String(64), default="", nullable=False)
    owner_user_id = Column(Integer, nullable=False, index=True)
    industry = Column(String(64), default="", nullable=False)
    status = Column(String(24), default="active", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class MMembership(Base):
    __tablename__ = "m_membership"
    __table_args__ = (UniqueConstraint("company_id", "user_id", name="uq_m_membership_user"),)

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    display_name = Column(String(80), nullable=False)
    dept = Column(String(48), default="", nullable=False)
    email = Column(String(255), default="", nullable=False)
    load_pct = Column(Integer, default=0, nullable=False)
    remark = Column(Text, default="", nullable=False)
    status = Column(String(24), default="active", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class MMembershipRole(Base):
    __tablename__ = "m_membership_role"
    __table_args__ = (UniqueConstraint("membership_id", "role_code", name="uq_m_member_role"),)

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, index=True)
    membership_id = Column(Integer, nullable=False, index=True)
    role_code = Column(String(32), nullable=False)
    level = Column(String(16), default="p1", nullable=False)
    is_primary = Column(Boolean, default=True, nullable=False)


class MProduct(Base):
    __tablename__ = "m_product"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, index=True)
    name = Column(String(160), nullable=False)
    price = Column(Numeric(14, 2), default=0, nullable=False)
    unit = Column(String(32), default="", nullable=False)
    cycle_days = Column(Integer, default=0, nullable=False)
    deliverable = Column(Text, default="", nullable=False)
    description = Column(Text, default="", nullable=False)
    status = Column(String(24), default="active", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class MProject(Base):
    __tablename__ = "m_project"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, index=True)
    name = Column(String(160), nullable=False)
    goal = Column(Text, default="", nullable=False)
    success_criteria = Column(Text, default="", nullable=False)
    start_at = Column(String(16), default="", nullable=False)
    end_at = Column(String(16), default="", nullable=False)
    status = Column(String(24), default="planning", nullable=False)
    arrangement_status = Column(String(16), default="draft", nullable=False)   # draft|confirmed|changed
    progress = Column(Integer, default=0, nullable=False)
    owner_membership_id = Column(Integer, nullable=True)
    products = Column(JSON, default=list, nullable=True)
    members = Column(JSON, default=list, nullable=True)
    plan_source = Column(String(16), default="", nullable=False)               # ai|template
    arrangement_draft = Column(JSON, default=dict, nullable=True)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class MPlanNode(Base):
    __tablename__ = "m_plan_node"
    __table_args__ = (Index("ix_m_plan_node_project_order", "project_id", "order_index"),)

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, index=True)
    project_id = Column(Integer, nullable=False, index=True)
    parent_id = Column(Integer, nullable=True)
    node_type = Column(String(16), default="task", nullable=False)   # phase|task|milestone|risk
    title = Column(String(200), nullable=False)
    detail = Column(Text, default="", nullable=False)
    owner_kind = Column(String(16), default="unassigned", nullable=False)   # human|virtual_employee|unassigned
    owner_membership_id = Column(Integer, nullable=True)
    owner_ai_employee_id = Column(Integer, nullable=True)
    owner_name = Column(String(80), default="", nullable=False)
    requirement = Column(Text, default="", nullable=False)
    start_at = Column(String(16), default="", nullable=False)
    end_at = Column(String(16), default="", nullable=False)
    kpi = Column(Text, default="", nullable=False)
    deliverable = Column(Text, default="", nullable=False)
    status = Column(String(24), default="not_started", nullable=False)
    progress = Column(Integer, default=0, nullable=False)
    weight = Column(Integer, default=1, nullable=False)
    order_index = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class MCheckup(Base):
    __tablename__ = "m_checkup"
    __table_args__ = (UniqueConstraint("project_id", "slot", "checked_on", name="uq_m_checkup_slot"),)

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, index=True)
    project_id = Column(Integer, nullable=False, index=True)
    slot = Column(String(8), nullable=False)           # 0900 | 1700
    checked_on = Column(String(10), nullable=False)    # YYYY-MM-DD
    health = Column(String(16), default="good", nullable=False)
    headline = Column(Text, default="", nullable=False)
    evidence = Column(JSON, default=list, nullable=True)
    suggestions = Column(JSON, default=list, nullable=True)
    decisions = Column(JSON, default=list, nullable=True)
    changes = Column(JSON, default=dict, nullable=True)
    source = Column(String(16), default="rules", nullable=False)
    confidence = Column(String(16), default="high", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class MCondition(Base):
    __tablename__ = "m_condition"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, index=True)
    project_id = Column(Integer, nullable=True, index=True)
    category = Column(String(24), default="people", nullable=False)
    title = Column(String(120), nullable=False)
    need = Column(Text, default="", nullable=False)
    now_state = Column(Text, default="", nullable=False)
    gap = Column(Text, default="", nullable=False)
    impact = Column(Text, default="", nullable=False)
    severity = Column(String(16), default="medium", nullable=False)
    actions = Column(JSON, default=list, nullable=True)
    decided = Column(Text, default="", nullable=False)
    source = Column(String(24), default="ai", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class MFinanceEntry(Base):
    __tablename__ = "m_finance_entry"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, index=True)
    project_id = Column(Integer, nullable=True)
    entry_type = Column(String(16), default="income", nullable=False)   # income|expense
    category = Column(String(48), default="", nullable=False)
    amount = Column(Numeric(14, 2), default=0, nullable=False)
    happened_on = Column(String(10), default="", nullable=False)
    summary = Column(String(200), default="", nullable=False)
    status = Column(String(24), default="settled", nullable=False)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class MAuditLog(Base):
    __tablename__ = "m_audit_log"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=True, index=True)
    actor_user_id = Column(Integer, nullable=True)
    action = Column(String(64), nullable=False)
    target_type = Column(String(48), default="", nullable=False)
    target_id = Column(String(48), default="", nullable=False)
    detail = Column(JSON, default=dict, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class MCustomer(Base):
    """客户（业务岗）：商机阶段 / 跟进 / 成交。"""

    __tablename__ = "m_customer"
    __table_args__ = (Index("ix_m_customer_company_stage", "company_id", "stage"),)

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, index=True)
    name = Column(String(120), nullable=False)
    company_name = Column(String(160), default="", nullable=False)
    phone = Column(String(64), default="", nullable=False)
    wechat = Column(String(80), default="", nullable=False)
    source = Column(String(48), default="", nullable=False)
    stage = Column(String(24), default="lead", nullable=False)   # lead|contacted|proposal|negotiating|won|lost
    amount = Column(Numeric(14, 2), default=0, nullable=False)
    owner_membership_id = Column(Integer, nullable=True, index=True)
    next_action = Column(String(200), default="", nullable=False)
    next_follow_at = Column(String(16), default="", nullable=False)
    last_follow_at = Column(String(16), default="", nullable=False)
    notes = Column(Text, default="", nullable=False)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class MCustomerLog(Base):
    """客户跟进记录（时间线）。"""

    __tablename__ = "m_customer_log"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, index=True)
    customer_id = Column(Integer, nullable=False, index=True)
    actor_user_id = Column(Integer, nullable=True)
    kind = Column(String(24), default="note", nullable=False)   # call|wechat|visit|note|stage
    content = Column(Text, default="", nullable=False)
    from_stage = Column(String(24), default="", nullable=False)
    to_stage = Column(String(24), default="", nullable=False)
    happened_at = Column(String(16), default="", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class MDelivery(Base):
    """交付单（交付岗）：已成交客户 -> 交付 -> 验收。"""

    __tablename__ = "m_delivery"
    __table_args__ = (Index("ix_m_delivery_company_status", "company_id", "status"),)

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, index=True)
    customer_id = Column(Integer, nullable=True, index=True)
    project_id = Column(Integer, nullable=True)
    name = Column(String(160), nullable=False)
    status = Column(String(24), default="pending", nullable=False)  # pending|doing|review|accepted
    owner_membership_id = Column(Integer, nullable=True)
    promised_at = Column(String(16), default="", nullable=False)
    delivered_at = Column(String(16), default="", nullable=False)
    accepted_at = Column(String(16), default="", nullable=False)
    note = Column(Text, default="", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class MWorkLog(Base):
    """工作记录（所有岗位提交）。"""

    __tablename__ = "m_work_log"
    __table_args__ = (Index("ix_m_work_log_company_created", "company_id", "created_at"),)

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, index=True)
    membership_id = Column(Integer, nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    author_name = Column(String(80), default="", nullable=False)
    kind = Column(String(24), default="daily", nullable=False)   # daily|task|issue
    content = Column(Text, default="", nullable=False)
    minutes = Column(Integer, default=0, nullable=False)
    project_id = Column(Integer, nullable=True)
    node_id = Column(Integer, nullable=True)
    worked_on = Column(String(10), default="", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class MAiEmployee(Base):
    """虚拟员工（原系统设备槽位）。"""

    __tablename__ = "m_ai_employee"
    __table_args__ = (UniqueConstraint("company_id", "installation_id", name="uq_m_ai_employee_slot"),)

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, index=True)
    name = Column(String(120), nullable=False)
    installation_id = Column(String(128), nullable=False, index=True)
    owner_membership_id = Column(Integer, nullable=True)
    capabilities = Column(JSON, default=list, nullable=True)
    status = Column(String(24), default="enabled", nullable=False)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class MPlanVersion(Base):
    """规划版本快照（支持对比与回滚）。"""

    __tablename__ = "m_plan_version"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_m_plan_version"),)

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, index=True)
    project_id = Column(Integer, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    source = Column(String(16), default="ai", nullable=False)
    summary = Column(Text, default="", nullable=False)
    requirement = Column(Text, default="", nullable=False)
    snapshot = Column(JSON, default=dict, nullable=True)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class MDispatch(Base):
    """节点 -> 虚拟员工 的派活记录（关联主站定时任务）。"""

    __tablename__ = "m_dispatch"

    id = Column(Integer, primary_key=True, index=True)
    company_id = Column(Integer, nullable=False, index=True)
    project_id = Column(Integer, nullable=False, index=True)
    node_id = Column(Integer, nullable=False, index=True)
    ai_employee_id = Column(Integer, nullable=True)
    installation_id = Column(String(128), default="", nullable=False)
    task_id = Column(String(64), default="", nullable=False)
    run_id = Column(String(64), default="", nullable=False)
    status = Column(String(24), default="requested", nullable=False)
    error = Column(Text, default="", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
