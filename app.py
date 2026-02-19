import os
import bcrypt
import uvicorn
import sys
from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, text
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from ytmusicapi import YTMusic
from pathlib import Path

# --- DATABASE SETUP WITH DEBUGGING ---
# Render provides DATABASE_URL. If not found, it uses your provided internal URL.
DATABASE_URL = os.environ.get("DATABASE_URL")

# Debug: Print what we're using (these will appear in Render logs)
print("=" * 50, file=sys.stderr)
print("🔍 DATABASE CONFIGURATION", file=sys.stderr)
print(f"📌 DATABASE_URL from env: {DATABASE_URL}", file=sys.stderr)

if not DATABASE_URL:
    print("⚠️  No DATABASE_URL in environment, using fallback for local development", file=sys.stderr)
    DATABASE_URL = "postgresql://vofo_db_user:8fKZRuFjMfI4T1rg282zHkb5tUKDfPT7@dpg-d6bjni3h46gs739ao3k0-a/vofo_db"
    print(f"📌 Using fallback URL: {DATABASE_URL}", file=sys.stderr)

# Fix: SQLAlchemy 2.0 requires 'postgresql://' but some platforms provide 'postgres://'
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    print(f"🔄 Fixed URL format: {DATABASE_URL}", file=sys.stderr)

# Initialize these as None in case of connection failure
engine = None
SessionLocal = None
Base = None

try:
    # Add SSL requirements for Render
    connect_args = {}
    if 'render.com' in DATABASE_URL or '.onrender.com' in DATABASE_URL:
        connect_args = {'sslmode': 'require'}
        print("🔒 SSL mode enabled for Render", file=sys.stderr)
    
    # Create engine with connection pooling for Render
    engine = create_engine(
        DATABASE_URL,
        connect_args=connect_args,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,  # Verify connections before using
        pool_recycle=300,     # Recycle connections after 5 minutes
        echo=False            # Set to True for SQL debugging
    )
    
    # Test the connection
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        print("✅ Database connection test successful!", file=sys.stderr)
    
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base = declarative_base()
    
    # Create tables if they don't exist
    Base.metadata.create_all(bind=engine)
    print("✅ Database tables created/verified!", file=sys.stderr)
    
except Exception as e:
    print(f"❌ Database Connection Error: {e}", file=sys.stderr)
    print("⚠️  App will continue but database features won't work!", file=sys.stderr)
    # Create Base even without connection for app to load
    Base = declarative_base()

print("=" * 50, file=sys.stderr)

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

app = FastAPI()

# Initialize YTMusic with error handling
try:
    yt = YTMusic()
    print("✅ YTMusic initialized successfully", file=sys.stderr)
except Exception as e:
    print(f"❌ YTMusic initialization error: {e}", file=sys.stderr)
    yt = None

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
    """Dependency to get database session"""
    if SessionLocal is None:
        raise HTTPException(status_code=503, detail="Database service unavailable")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- HEALTH CHECK ENDPOINT ---
@app.get("/health")
async def health_check():
    """Health check endpoint for monitoring"""
    health_status = {
        "status": "healthy",
        "database": "connected" if SessionLocal else "disconnected",
        "ytmusic": "initialized" if yt else "unavailable"
    }
    
    # Test database if available
    if SessionLocal:
        try:
            db = SessionLocal()
            db.execute(text("SELECT 1")).scalar()
            db.close()
        except Exception as e:
            health_status["database"] = f"error: {str(e)}"
            health_status["status"] = "degraded"
    
    # Test YTMusic if available
    if yt:
        try:
            yt.get_charts(country="IN")
        except Exception as e:
            health_status["ytmusic"] = f"error: {str(e)}"
            health_status["status"] = "degraded"
    
    return health_status

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
    if not yt:
        return {"error": "YouTube Music service unavailable"}, 503
    try:
        songs = yt.get_charts(country="IN")['songs']['items']
        return [{"id": s['videoId'], "title": s['title'], "artist": s['artists'][0]['name'], "thumbnail": s['thumbnails'][-1]['url']} for s in songs[:15]]
    except Exception as e:
        print(f"Trending error: {e}", file=sys.stderr)
        return []

@app.get("/api/search")
async def search(q: str):
    if not yt:
        return {"error": "YouTube Music service unavailable"}, 503
    try:
        results = yt.search(q, filter="songs")
        return [{"id": r['videoId'], "title": r['title'], "artist": r['artists'][0]['name'], "thumbnail": r['thumbnails'][-1]['url']} for r in results]
    except Exception as e:
        print(f"Search error: {e}", file=sys.stderr)
        return []

# --- SERVING THE FRONTEND & PING SUPPORT ---
@app.api_route("/", methods=["GET", "HEAD"])
async def serve_home():
    html_file = BASE_DIR / "index.html"
    if not html_file.exists():
        return HTMLResponse(content="<h1>index.html not found</h1>", status_code=404)
    return FileResponse(html_file)

# --- ADD A SIMPLE ROOT ENDPOINT FOR TESTING ---
@app.get("/ping")
async def ping():
    return {"status": "alive", "message": "VoFo Music API is running"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"🚀 Starting server on port {port}", file=sys.stderr)
    uvicorn.run(app, host="0.0.0.0", port=port)
