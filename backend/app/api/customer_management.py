"""Customer records and communication history."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Customer, CustomerCommunication, RecorderAudioRecord, User
from .auth import get_current_user

router = APIRouter()


class CustomerBody(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    company: str = Field(default="", max_length=255)
    position: str = Field(default="", max_length=160)
    phone: str = Field(default="", max_length=64)
    email: str = Field(default="", max_length=255)
    source: str = Field(default="", max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=30)
    status: str = Field(default="active", max_length=32)
    notes: str = Field(default="", max_length=10000)


class CommunicationBody(BaseModel):
    communication_type: str = Field(default="note", max_length=32)
    occurred_at: datetime | None = None
    content: str = Field(default="", max_length=50000)
    summary: str = Field(default="", max_length=10000)
    recording_id: int | None = None


def _clean_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))[:30]


def _customer_payload(row: Customer, *, owner: User | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "owner_user_id": row.owner_user_id,
        "owner_label": (owner.email if owner else ""),
        "name": row.name,
        "company": row.company or "",
        "position": row.position or "",
        "phone": row.phone or "",
        "email": row.email or "",
        "source": row.source or "",
        "tags": _clean_tags(row.tags),
        "status": row.status or "active",
        "notes": row.notes or "",
        "last_contact_at": row.last_contact_at.isoformat() if row.last_contact_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _communication_payload(row: CustomerCommunication, recording: RecorderAudioRecord | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "customer_id": row.customer_id,
        "owner_user_id": row.owner_user_id,
        "communication_type": row.communication_type,
        "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
        "content": row.content or "",
        "summary": row.summary or "",
        "recording_id": row.recording_id,
        "recording": ({
            "id": recording.id,
            "name": recording.display_name or recording.file_name,
            "summary": recording.summary_text or "",
            "status": recording.status,
            "recorded_at": recording.recorded_at.isoformat() if recording.recorded_at else None,
        } if recording else None),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _owned_customer(db: Session, user_id: int, customer_id: int) -> Customer:
    row = db.query(Customer).filter(Customer.id == customer_id, Customer.owner_user_id == user_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="客户不存在")
    return row


def _owned_communication(db: Session, user_id: int, communication_id: int) -> CustomerCommunication:
    row = db.query(CustomerCommunication).filter(
        CustomerCommunication.id == communication_id,
        CustomerCommunication.owner_user_id == user_id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="沟通记录不存在")
    return row


def _refresh_last_contact(db: Session, customer: Customer) -> None:
    latest = (
        db.query(CustomerCommunication.occurred_at)
        .filter(CustomerCommunication.customer_id == customer.id)
        .order_by(CustomerCommunication.occurred_at.desc(), CustomerCommunication.id.desc())
        .first()
    )
    customer.last_contact_at = latest[0] if latest else None


def _recording_for_user(db: Session, user_id: int, recording_id: int | None) -> RecorderAudioRecord | None:
    if recording_id is None:
        return None
    recording = db.query(RecorderAudioRecord).filter(
        RecorderAudioRecord.id == recording_id,
        RecorderAudioRecord.user_id == user_id,
    ).first()
    if not recording:
        raise HTTPException(status_code=404, detail="录音不存在或不属于当前用户")
    return recording


def _save_communication(db: Session, customer: Customer, body: CommunicationBody) -> CustomerCommunication:
    occurred_at = body.occurred_at or datetime.utcnow()
    recording = _recording_for_user(db, customer.owner_user_id, body.recording_id)
    row = CustomerCommunication(
        customer_id=customer.id,
        owner_user_id=customer.owner_user_id,
        communication_type=(body.communication_type or "note").strip()[:32] or "note",
        occurred_at=occurred_at,
        content=body.content.strip(),
        summary=body.summary.strip() or (recording.summary_text.strip() if recording else ""),
        recording_id=recording.id if recording else None,
    )
    customer.last_contact_at = occurred_at
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/api/customers")
def list_customers(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: str = Query("", max_length=160),
    status: str = Query("", max_length=32),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(Customer).filter(Customer.owner_user_id == current_user.id)
    text = q.strip()
    if text:
        like = f"%{text}%"
        query = query.filter(or_(Customer.name.ilike(like), Customer.company.ilike(like), Customer.phone.ilike(like), Customer.email.ilike(like)))
    if status.strip():
        query = query.filter(Customer.status == status.strip())
    total = query.count()
    rows = query.order_by(Customer.updated_at.desc(), Customer.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": [_customer_payload(row) for row in rows], "page": page, "page_size": page_size, "total": total, "has_next": page * page_size < total}


@router.post("/api/customers")
def create_customer(body: CustomerBody, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="客户姓名不能为空")
    row = Customer(owner_user_id=current_user.id, **body.model_dump(exclude={"tags", "name"}), name=name, tags=_clean_tags(body.tags))
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"ok": True, "customer": _customer_payload(row)}


@router.patch("/api/customers/{customer_id}")
def update_customer(customer_id: int, body: CustomerBody, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _owned_customer(db, current_user.id, customer_id)
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="客户姓名不能为空")
    for key, value in body.model_dump(exclude={"tags", "name"}).items():
        setattr(row, key, value)
    row.name = name
    row.tags = _clean_tags(body.tags)
    db.commit()
    db.refresh(row)
    return {"ok": True, "customer": _customer_payload(row)}


@router.delete("/api/customers/{customer_id}")
def delete_customer(customer_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _owned_customer(db, current_user.id, customer_id)
    db.query(CustomerCommunication).filter(CustomerCommunication.customer_id == row.id).delete(synchronize_session=False)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/api/customers/{customer_id}/communications")
def create_communication(customer_id: int, body: CommunicationBody, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    customer = _owned_customer(db, current_user.id, customer_id)
    row = _save_communication(db, customer, body)
    recording = db.query(RecorderAudioRecord).filter(RecorderAudioRecord.id == row.recording_id).first() if row.recording_id else None
    return {"ok": True, "communication": _communication_payload(row, recording)}


@router.patch("/api/customer-communications/{communication_id}")
def update_communication(communication_id: int, body: CommunicationBody, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _owned_communication(db, current_user.id, communication_id)
    recording = _recording_for_user(db, current_user.id, body.recording_id)
    row.communication_type = (body.communication_type or "note").strip()[:32] or "note"
    row.occurred_at = body.occurred_at or row.occurred_at or datetime.utcnow()
    row.content = body.content.strip()
    row.recording_id = recording.id if recording else None
    row.summary = body.summary.strip() or (recording.summary_text.strip() if recording else "")
    customer = _owned_customer(db, current_user.id, row.customer_id)
    customer.last_contact_at = row.occurred_at
    db.commit()
    db.refresh(row)
    return {"ok": True, "communication": _communication_payload(row, recording)}


@router.delete("/api/customer-communications/{communication_id}")
def delete_communication(communication_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = _owned_communication(db, current_user.id, communication_id)
    customer = _owned_customer(db, current_user.id, row.customer_id)
    db.delete(row)
    db.flush()
    _refresh_last_contact(db, customer)
    db.commit()
    return {"ok": True}


@router.get("/api/customers/recordings")
def list_customer_recordings(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(RecorderAudioRecord).filter(RecorderAudioRecord.user_id == current_user.id, RecorderAudioRecord.status == "completed")
    total = query.count()
    rows = query.order_by(RecorderAudioRecord.recorded_at.desc().nullslast(), RecorderAudioRecord.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": [{"id": row.id, "name": row.display_name or row.file_name, "summary": row.summary_text or "", "recorded_at": row.recorded_at.isoformat() if row.recorded_at else row.created_at.isoformat(), "status": row.status} for row in rows], "page": page, "page_size": page_size, "total": total, "has_next": page * page_size < total}


@router.get("/api/customers/{customer_id}/recordings")
def list_customer_recordings_for_customer(
    customer_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _owned_customer(db, current_user.id, customer_id)
    query = db.query(RecorderAudioRecord).filter(RecorderAudioRecord.user_id == current_user.id, RecorderAudioRecord.status == "completed")
    total = query.count()
    rows = query.order_by(RecorderAudioRecord.recorded_at.desc().nullslast(), RecorderAudioRecord.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"items": [{"id": row.id, "name": row.display_name or row.file_name, "summary": row.summary_text or "", "recorded_at": row.recorded_at.isoformat() if row.recorded_at else row.created_at.isoformat(), "status": row.status} for row in rows], "page": page, "page_size": page_size, "total": total, "has_next": page * page_size < total}


@router.get("/api/customers/{customer_id}")
def customer_detail(customer_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    customer = _owned_customer(db, current_user.id, customer_id)
    communications = db.query(CustomerCommunication).filter(CustomerCommunication.customer_id == customer.id).order_by(CustomerCommunication.occurred_at.desc(), CustomerCommunication.id.desc()).all()
    recording_ids = [c.recording_id for c in communications if c.recording_id]
    recordings = {r.id: r for r in db.query(RecorderAudioRecord).filter(RecorderAudioRecord.id.in_(recording_ids)).all()} if recording_ids else {}
    return {"customer": _customer_payload(customer), "communications": [_communication_payload(row, recordings.get(row.recording_id)) for row in communications]}
