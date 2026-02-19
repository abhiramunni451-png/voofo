import os
import bcrypt
import uvicorn
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from ytmusicapi import YTMusic
from pathlib import Path

# --- DATABASE SETUP ---
# Render provides DATABASE_URL. If not found, it uses your provided internal URL.
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://vofodb_user:Y7MQfAWwEtsiHQLiGHFV7ikOI2ruTv3u@dpg-d5lm4ongi27c7390kq40-a/vofodb")

# Fix: SQLAlchemy 2.0 requires 'postgresql://' but some platforms provide 'postgres://'
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

try:
    engine = create_engine(DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base = declarative_base()
except Exception as e:
    print(f"Database Connection Error: {e}")

# --- MODELS ---
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    password = Column(String)

class LikedSong(Base):
    __tablename__ = "liked_songs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    song_id = Column(String)
    title = Column(String)
    artist = Column(String)
    thumbnail = Column(String)

# Create tables if they don't exist
Base.metadata.create_all(bind=engine)

app = FastAPI()
yt = YTMusic()

# Enable CORS for Mobile WebViews and external pings
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).resolve().parent

# --- AUTH HELPERS ---
def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- AUTH ROUTES ---
@app.post("/api/register")
async def register(data: dict, db: Session = Depends(get_db)):
    if not data.get('username') or not data.get('password'):
        raise HTTPException(400, "Username and password required")
    if db.query(User).filter(User.username == data['username']).first():
        raise HTTPException(400, "Username already exists")
    user = User(username=data['username'], password=hash_password(data['password']))
    db.add(user)
    db.commit()
    return {"success": True}

@app.post("/api/login")
async def login(data: dict, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == data['username']).first()
    if not user or not verify_password(data['password'], user.password):
        raise HTTPException(401, "Invalid credentials")
    return {"success": True, "user_id": user.id, "username": user.username}

# --- LIKES ROUTES ---
@app.post("/api/like")
async def toggle_like(data: dict, db: Session = Depends(get_db)):
    existing = db.query(LikedSong).filter(
        LikedSong.user_id == data['user_id'], 
        LikedSong.song_id == data['song_id']
    ).first()
    if existing:
        db.delete(existing)
        db.commit()
        return {"status": "unliked"}
    new_like = LikedSong(
        user_id=data['user_id'], 
        song_id=data['song_id'], 
        title=data['title'], 
        artist=data['artist'], 
        thumbnail=data['thumbnail']
    )
    db.add(new_like)
    db.commit()
    return {"status": "liked"}

@app.get("/api/liked/{user_id}")
async def get_liked(user_id: int, db: Session = Depends(get_db)):
    likes = db.query(LikedSong).filter(LikedSong.user_id == user_id).all()
    return [{"id": l.song_id, "title": l.title, "artist": l.artist, "thumbnail": l.thumbnail} for l in likes]

# --- MUSIC ROUTES ---
@app.get("/api/trending")
async def trending():
    try:
        songs = yt.get_charts(country="IN")['songs']['items']
        return [{"id": s['videoId'], "title": s['title'], "artist": s['artists'][0]['name'], "thumbnail": s['thumbnails'][-1]['url']} for s in songs[:15]]
    except Exception:
        return []

@app.get("/api/search")
async def search(q: str):
    try:
        results = yt.search(q, filter="songs")
        return [{"id": r['videoId'], "title": r['title'], "artist": r['artists'][0]['name'], "thumbnail": r['thumbnails'][-1]['url']} for r in results]
    except Exception:
        return []

# --- SERVING THE FRONTEND & PING SUPPORT ---
# Added "HEAD" method to allow UptimeRobot to check status without 405 error
@app.api_route("/", methods=["GET", "HEAD"])
async def serve_home():
    html_file = BASE_DIR / "index.html"
    if not html_file.exists():
        return HTMLResponse(content="<h1>index.html not found</h1>", status_code=404)
    return FileResponse(html_file)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
