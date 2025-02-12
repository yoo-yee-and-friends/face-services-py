from sqlalchemy.orm import Session
from app.db.models.User import User

def get_user(db: Session, username: str) -> User:
    return db.query(User).filter(User.username == username).first()

def create_user(db: Session, username: str, password_hash: str, role_id: int):
    new_user = User(
        username=username,
        password_hash=password_hash,
        role_id=role_id
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user