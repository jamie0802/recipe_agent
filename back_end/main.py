from fastapi import FastAPI, APIRouter, Depends, HTTPException ,status, Header, UploadFile, File, BackgroundTasks, WebSocket
from fastapi.middleware.cors import CORSMiddleware #CORSMiddleware：用來處理跨來源資源共享（CORS）問題，允許前端應用程式從不同的域名訪問後端 API
from fastapi.responses import StreamingResponse, JSONResponse #JSONResponse：用來返回 JSON 格式的響應，適合用於 API 的回應
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from contextlib import asynccontextmanager
#from gtts import gTTS
from jose import JWTError, jwt
from jwt.exceptions import InvalidTokenError
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel
from typing import Annotated, List, Optional
from agents import Runner, SQLiteSession
from agent import build_main_agent, format_context_for_prompt
from context_agent import run_context_agent
from preference_analyzer import run_preference_analyzer
from dotenv import load_dotenv
from openai import OpenAI
from faster_whisper_record import transcribe_audio, VoiceListener
from models import ContextAgentOutput
from database import get_pool
from PPR import recommend_tool
import mysql.connector
import bcrypt
import openai
import os
import asyncio
import io
import json
import edge_tts

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("語音監聽任務已在背景啟動...")
    asyncio.create_task(listener.start_loop()) #start_loop()返回一個coroutine物件
    #如果只用await listener.start_loop()，會等待coroutine完成才往下執行(但整個FastAPI會卡在這裡，等start_loop()完全結束後才到yeild)
    #但監聽就是無限循環的，會在背景持續監聽，如果還這樣卡就下不去了
    asyncio.create_task(idle_checker())

    yield #FastAPI正常啟動並開始運行路由(監聽task可以持續運行)

    print("Shutting down... stopping listener")
    listener.close()

async def idle_checker():
    while True:
        await asyncio.sleep(60)  # 每分鐘檢查一次
        now = datetime.now()
        for username, last in list(last_activity.items()):
            if now - last > IDLE_TIMEOUT:
                print(f"⏰ {username} idle timeout，觸發 Summarizer")
                asyncio.create_task(run_preference_analyzer(username))
                del last_activity[username]

app = FastAPI(lifespan=lifespan) #FastAPI()：建立一個 FastAPI 應用程式實例
router = APIRouter()
pt = recommend_tool()

session_cache: dict[str, SQLiteSession] = {} #把session當全域變數
user_contexts = {} #上下文當全域變數，因為上下文物件在@/login宣告的(區域變數)
#因為server開著可能同時有兩個人在使用此系統，要區隔他們上下文就利用username來區分
IDLE_TIMEOUT = timedelta(seconds=30) #minutes=30
last_activity: dict[str, datetime] = {}


load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
#PICOVOICE_API_KEY = os.getenv("PICOVOICE_API_KEY")
#listener = VoiceListener(PICOVOICE_API_KEY)
listener = VoiceListener()  # 不再需要 API key，直接初始化
client = openai.OpenAI(api_key=OPENAI_API_KEY)

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

oauth2_shceme = OAuth2PasswordBearer(tokenUrl="login")

prompt_template = """
    username: {username}
    question: {question}
"""

app.add_middleware(
    CORSMiddleware, 
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    return mysql.connector.connect(
        host = "localhost",
        user = "root",
        password = "Pi1234567890_",
        database = "auth"
    )

#生成JWT token
def create_access_token(data:dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

#解析JWT token確認內容
async def get_current_user(token: Annotated[str, Depends(oauth2_shceme)]):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="無法驗證憑證",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except (JWTError, InvalidTokenError): #如果 jwt.decode 失敗（token 無效、過期、被篡改)
        raise credentials_exception
    
    #去資料庫確認使用者存在
    db = get_db()
    cursor = db.cursor(dictionary=True)
    cursor.execute("SELECT * FROM users WHERE username=%s", (username,))
    user = cursor.fetchone()
    cursor.close()
    db.close()

    if not user:
        raise credentials_exception
    
    return user

def get_user_session(username: str):
    key = username

    if key not in session_cache:
        '''
        session_cache[key] = UserSession(
            username,
            keep_last_n_turns=3,
            context_limit=6,
            summarizer=LLMSummarizer(memory_manager)
        )
        '''
        session_cache[key] = SQLiteSession(username)

    return session_cache[key]

async def append_session_log(user_id: str, question: str, reply: str, pool):
    async with pool.acquire() as conn:
        # 抓現有這個 session 的最新一筆
        row = await conn.fetchrow("""
            SELECT id, messages FROM session_logs
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT 1
        """, user_id)

        new_turn = [
            {"role": "user",      "content": question},
            {"role": "assistant", "content": reply}
        ]

        if row:
            existing = json.loads(row["messages"])
            updated = existing + new_turn
            await conn.execute("""
                UPDATE session_logs SET messages = $1
                WHERE id = $2
            """, json.dumps(updated), row["id"])
        else:
            # 第一輪，建新的一筆
            await conn.execute("""
                INSERT INTO session_logs (user_id, messages)
                VALUES ($1, $2)
            """, user_id, json.dumps(new_turn))

class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    password: str

class QuestionRequest(BaseModel):
    question: str
    #conversation_id: str

class Token(BaseModel):
    access_token: str #存放JWT token
    token_type: str #存放token類型，通常是"bearer"，表示這是一個Bearer token，可以在HTTP請求的Authorization標頭中使用

class TokenData(BaseModel):
    username: str | None = None

#負責做登入驗證的路由，邏輯是接收前端傳來的使用者名稱和密碼，然後在資料庫中查詢對應的使用者資料，使用 bcrypt 進行密碼驗證，最後返回登入結果給前端
@app.post("/login")
def login(data: LoginRequest):
    username = data.username
    password = data.password

    db = get_db()
    cursor = db.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = cursor.fetchone()

        if not user or not bcrypt.checkpw(password.encode(), user["password"].encode()):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="無法驗證憑證",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(data={"sub":user["username"]}, expires_delta=access_token_expires)

        return Token(access_token=access_token, token_type="bearer")
    finally:
        cursor.close()
        db.close()

#負責做註冊的路由，邏輯是接收前端傳來的使用者名稱和密碼，然後在資料庫中檢查是否已經存在相同的使用者名稱，如果不存在則使用 bcrypt 將密碼進行哈希處理後存入資料庫，最後返回註冊結果給前端    
@app.post("/register")
async def register(data: RegisterRequest):
    username = data.username
    password = data.password

    db = get_db()
    cursor = db.cursor(dictionary=True)

    try:
        cursor.execute("SELECT * FROM users WHERE username=%s", (username,))
        existing_user = cursor.fetchone()
        if existing_user:
            return JSONResponse(
                {"status": "error", "message": "使用者已存在"},
                status_code=400
            )

        hashed_password = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        cursor.execute("INSERT INTO users (username, password) VALUES (%s, %s)", (username, hashed_password))
        db.commit()

        # 同步在 PostgreSQL 建立使用者資料
        pool = await get_pool()
        await pool.execute(
            """
            INSERT INTO user_explicit_profile (user_id)
            VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
            """,
            username,
        )

        return JSONResponse({"status": "success", "message": "註冊成功"})

    finally:
        cursor.close()
        db.close()

#負責處理前端提問的路由，邏輯是接收前端傳來的使用者名稱和問題，然後使用 Runner.run_sync 方法來執行 agent，將問題和使用者名稱作為上下文傳入，最後將 agent 的回覆返回給前端
@app.post("/ask")
async def ask_agent(data: QuestionRequest, user: dict = Depends(get_current_user)):
    try:
        username = user["username"]
        now = datetime.now()
        last_activity[username] = now

        session = get_user_session(username)
        
        agent_output: ContextAgentOutput = await run_context_agent(
            user_id=username,
            session_id=username,
            query=data.question,
        )
        context_str = format_context_for_prompt(agent_output.context)
        print(f"子Agent整理的上下文:\n{context_str}")

        main_agent = build_main_agent(username, context_str)
        result = await Runner.run(main_agent, data.question, session=session)
        reply = result.final_output

        return {"reply": reply}

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"reply": f"發生錯誤: {str(e)}"}

#語音用faster-whisper轉文字檔
@app.post("/fridge/voice")
async def voice_to_text(file: UploadFile = File(description="please enter a voice file")):
    text = await transcribe_audio(file)

    return {"text": text}
"""
#文字轉語音
@app.get("/fridge/speak")
async def speak(text: str):
    tts = gTTS(text=text, lang="zh-tw")#建立語音

    mp3 = io.BytesIO()#建立記憶體檔案
    
    tts.write_to_fp(mp3) #把語音寫入記憶體
    
    mp3.seek(0) #指標移到檔案開頭(為了播放用)

    return StreamingResponse(mp3, media_type="audio/mpeg")#回傳音訊串流->瀏覽器會撥放    
"""
@app.get("/fridge/speak")
async def speak(text: str):
    mp3 = io.BytesIO() #不寫入硬碟，直接存在 RAM
    communicate = edge_tts.Communicate(
        text=text,
        voice="zh-TW-HsiaoChenNeural"#zh-TW-YunJheNeural(男生版)
    )

    async for chunk in communicate.stream(): #非同步逐段讀取 TTS 產生的音訊 stream
        if chunk["type"] == "audio":
            mp3.write(chunk["data"])

    mp3.seek(0)
    return StreamingResponse(mp3, media_type="audio/mpeg")

@app.post("/recipe")
async def get_recipe_data(data: dict):
    recipe_name = data.get("name")

    try:
        recipe = await pt.get_full_recipe(recipe_name)
        if not recipe:
            return JSONResponse(
                {"status": "error", "message": "recipe not found"},
                status_code=404
            )

        return {
            "status": "success",
            "name": recipe["name"],
            "ingredients": [ing["name"] for ing in recipe["ingredients"]],
            "steps": [s["description"] for s in recipe["steps"]],
            "cooking_time": recipe["cooking_time"],
            "servings": recipe["servings"]
        }
    
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
        
# 假設 listener 內有兩個列表(用的是同一個麥克風)
listener.websockets = {
    "ai": [],
    "practice": []
}#在 main.py 中，把listener實例裡面的字典「手動填入」了資料

#將訊息推送給特定分組的所有前端 WebSocket
async def broadcast_message(text, tab="ai"):#預設ai分組
    targets = listener.websockets.get(tab, [])#如果 tab 不存在，就回傳空列表 []，避免錯誤

    for ws in targets[:]:  # 複製 list
        try:
            await ws.send_json({"text": text})
        except Exception:
            targets.remove(ws) #連線斷掉就把這個websocket從列表移除

@app.websocket("/ws/voice")
async def websocket_voice(websocket: WebSocket): #建立一個websocket API
    tab = websocket.query_params.get("tab", "ai")# 從 URL 參數取得 tab 名稱（例如：/ws/voice?tab=practice），預設為 "ai"
    await websocket.accept() #後端接受這個websocket連線

    listener.websockets.setdefault(tab, []).append(websocket) #將此連線加入到對應的分組
    try:
        while True: #保持連線開啟，直到前端主動中斷或發生錯誤
            await asyncio.sleep(1)
    except Exception: #只要斷開連線(如使用者關閉網頁)
        if websocket in listener.websockets.get(tab, []):
            listener.websockets[tab].remove(websocket) #把這個連線從列表移除，避免未來broadcast再推送訊息給不存在的client
        await websocket.close() #關閉websocket連線




