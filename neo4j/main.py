import asyncio
from fastapi import FastAPI
from apscheduler.schedulers.background import BackgroundScheduler
from crawl_parser import scheduled_crawl
from contextlib import asynccontextmanager #非同步生命週期管理工具
'''
#建立一個背景排程器實例
scheduler = BackgroundScheduler()'''

#讓這個函式可以在「啟動」與「關閉」時分別執行不同邏輯
@asynccontextmanager
async def lifespan(app: FastAPI): #FastAPI 啟動應用時會呼叫這個函式
    # 啟動事件：啟動背景爬蟲循環
    task = asyncio.create_task(scheduled_crawl())
    print("🔥 Scheduler started with lifespan 🔥")
    try:
        yield #代表應用運行期間 #在 yield 前是「啟動時」，之後是「關閉時」
    finally:
        task.cancel() #停止背景任務
        try:
            await task
        except asyncio.CancelledError:
            print("🔥 Scheduler shutdown 🔥")

# -----------------------------
# 建立 FastAPI App
# -----------------------------
app = FastAPI(lifespan=lifespan) #使用定義的lifespan管理啟動與關閉流程

# -----------------------------
# Route API 定義
# -----------------------------
@app.get("/")
def root():
    return {"status": "FastAPI is running"}

@app.get("/debug/run-crawl")
def run_crawl():
    """
    手動觸發爬蟲 API
    用途：排程器間隔太長時，可手動呼叫爬蟲抓資料
    注意：
        - 適合測試或立即更新
        - 不建議頻繁呼叫以免重複爬蟲
    回傳：
        {"status": "Crawl triggered"}
    """
    scheduled_crawl()
    return {"status": "Crawl triggered"}