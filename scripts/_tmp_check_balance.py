import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.app.db import SessionLocal
from backend.app.models import User
db = SessionLocal()
u = db.query(User).filter(User.id == 4).first()
if u:
    print(f"user_id=4 username={u.username} credits={u.credits}")
else:
    print("user_id=4 not found")
db.close()
