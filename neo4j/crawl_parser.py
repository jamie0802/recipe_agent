#cd crawl然後執行uvicorn main:app --reload就可以爬蟲了
#用程式過濾的邏輯在judg.py檔案，用llm判斷的部分我有註解在scheduled_crawl()

import requests #發送HTTP請求->模擬瀏覽器「點擊」網頁，以獲取網頁內容
import asyncio
from bs4 import BeautifulSoup#BeautifulSoup是python的網頁解析工具:將html原始碼轉成「可以搜尋的樹狀結構」
from neo4j import GraphDatabase
from config import CRAWL_KEYWORDS #用來決定要搜尋哪些詞
from state import load_crawl_state, save_crawl_state #用來讀取上次爬到第幾頁跟儲存這次爬到第幾頁，防止每次都從第一頁重爬
from urllib.parse import quote #quote→將中文或特殊字元轉成URL可以使用的格式
#例如:"紅燒牛肉" -> "%E7%B4%85%E7%87%92%E7%89%9B%E8%82%89"
from sentence_transformers import SentenceTransformer
from openai import OpenAI
import os
from dotenv import load_dotenv #讀取環境變數(之後把neo4j拿進去改)
from judge import recipe_is_valid

load_dotenv()#讀取 .env 檔案中的環境變數
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

driver = GraphDatabase.driver( #driver是之後所有資料寫入的入口
    "bolt://localhost:7687",
    auth=("neo4j", "Jamie0802_")
)
'''
def get_neo4j():
    uri = os.getenv("NEO4J_URI","bolt://localhost:7687")
    user = os.getenv("NEO4J_USER")
    password = os.getenv("NEO4J_PASSWORD")
    
    driver = GraphDatabase.driver(
        uri,
        auth=(user, password)
    )
    return driver'''

#page: int = 1 頁數，型別是整數，預設從第 1 頁開始，max_results:最多回傳10筆結果
def search_icook(keyword: str, page: int = 1, max_results: int = 10):
    keyword = quote(keyword)
    url = f"https://icook.tw/search/{keyword}/?page={page}"

    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)#偉裝成瀏覽器，避免被網站封鎖，且設定10秒超時
        resp.raise_for_status()#檢查 HTTP 狀態碼
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            # 沒有這頁，直接回空 list
            print(f"[Info] {keyword} 第 {page} 頁不存在 (404)")
            return []
        else:
            raise e

    soup = BeautifulSoup(resp.text, "html.parser") #解析 HTML(把 HTML 變成「可用 CSS查詢的結構」)
    links = [ "https://icook.tw" + a.get("href") for a in soup.select("a.browse-recipe-link") ][:max_results]
    #找出所有 <a class="browse-recipe-link"> 的元素，然後取出 href 屬性，組成完整 URL，最後只取前 max_results 筆
    return links

def fetch_html(url: str) -> str: #根據URL獲取網頁HTML內容  #參數: url:str->傳入網址(字串)， ->str ->回傳HTML內容(字串)
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    #User-Agent:模擬瀏覽器行為，避免被網站封鎖 (告訴伺服器你是誰，使用的是什麼裝置/系統/瀏覽器)
    #Moxilla/5.0:通用的User-Agent字串，表示你使用的是一個現代的瀏覽器
    resp.raise_for_status() #檢查請求是否成功(狀態碼200)，若不成功則拋出異常
    return resp.text #回傳網頁HTML內容(字串)

# 解析食譜頁面，提取標題、食材資訊
def parse_recipe(html: str, url: str, source: str = "icook") -> dict:
    soup = BeautifulSoup(html, "lxml") #把抓回來的 HTML 字串轉成 BeautifulSoup 可操作的物件

    # 標題
    title_tag = soup.select_one("h1") #選擇第一個<h1>標籤(如果是select就會抓到裡面所有符合條件的標籤)
    title = title_tag.get_text(strip=True) if title_tag else "無標題"#strip=True：去掉前後空白
    #get_text(strip=True)把標籤內的文字抓出來，如果沒有<h1>標籤就回傳"無標題"
    #get("屬性名稱") 抓標籤的屬性值

    # --- 新增：解析份量與時間 ---
    # 份量 # 使用 .get_text() 之前先確保標籤存在，並給予預設值 "未知" 或 None
    servings_num = soup.select_one(".servings-info .num")
    servings_unit = soup.select_one(".servings-info .unit")#抓人數單位（人、份）
    if servings_num and servings_unit: #先確認在不在，再抓文字
        servings = f"{servings_num.get_text(strip=True)}{servings_unit.get_text(strip=True)}"
    else:
        servings = "1人份" # 或者放None(我預設1 沒有特別寫應該就是1人份吧)

    # 時間
    time_num = soup.select_one(".time-info .num")
    time_unit = soup.select_one(".time-info .unit")#抓單位（分鐘、時）
    if time_num and time_unit:
        cooking_time = f"{time_num.get_text(strip=True)}{time_unit.get_text(strip=True)}"
    else:
        cooking_time = "None"
    # -----------------------

    # 食材（icook 結構）
    ingredients = [
        item.get_text() #item.get_text(strip=True)是去掉空白
        for item in soup.select(".ingredient")
    ]

    # 步驟
    steps = [
        clean_text(step.get_text(strip=True))
        for step in soup.select("p.recipe-step-description-content")
    ]

    return {
        "title": title,
        "ingredients": ingredients,
        "steps": steps,
        "url": url,
        "source": source,
        "servings": servings,
        "cooking_time": cooking_time
    }

import re
#解析食材字串，提取名稱、數量和單位
def parse_ingredient(text: str):
    """
    輸入: '砂糖   50g'
    輸出: {'name': '砂糖','metadata':'50g'}
    """
    text = text.strip()
    metadata = None
    name = text

    # 找第一個空白，把前面當 name，後面當 metadata
    match = re.match(r"([^\s]+)\s+(.*)", text)
    if match:
        name = match.group(1)
        metadata = match.group(2).strip()

    return {"name": name, "metadata": metadata}

#去掉食譜標點符號用的
def clean_text(text: str) -> str:
    #只保留中文字符（漢字），移除其他文字、數字、標點符號
    text = re.sub(r"[!~★|♡]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

# 初始化模型 (只要初始化一次)
embedding_model = SentenceTransformer("BAAI/bge-large-zh-v1.5")
#embedding_model = SentenceTransformer("shibing624/text2vec-base-chinese")  # 免費中文模型

def get_embedding(text: str):
    vec = embedding_model.encode([text])[0]  # 回傳向量
    return vec.tolist()  # Neo4j 只接受 Python list(numpy array→ Python list)

def save_recipe_to_neo4j(recipe: dict):
    parsed_ingredients = [
        {
            "name": parse_ingredient(i)["name"],
            "metadata": parse_ingredient(i)["metadata"],
            "embedding": get_embedding(parse_ingredient(i)["name"])
        }
        for i in recipe["ingredients"]
    ]

    # 清理食譜名稱，去掉標點符號
    recipe_name_clean = clean_text(recipe["title"])

    # 生成 embedding，存入節點
    embedding = get_embedding(recipe_name_clean)  # 也可以用完整食譜文字

    with driver.session() as session:
        session.run(
            """
            MERGE (r:Recipe {name: $title})
            SET r.embedding = $embedding,
                r.servings = $servings,      
                r.cooking_time = $cooking_time

            MERGE (u:Url {url: $url})
            MERGE (r)-[:HAS_URL]->(u)

            WITH r
            UNWIND $ingredients AS ing
                MERGE (i:Ingredient {name: ing.name})
                SET i.metadata = ing.metadata,
                    i.embedding = ing.embedding
                MERGE (r)-[:HAS_INGREDIENT]->(i)

            WITH r
            UNWIND RANGE(0, SIZE($steps)-1) AS idx  
                MERGE (s:Step {description: $steps[idx], step_number: idx + 1})
                MERGE (r)-[:USES_METHOD {order: idx + 1}]->(s)
            """,
            title=recipe_name_clean,
            url=recipe["url"],
            ingredients=parsed_ingredients,
            steps=recipe["steps"],
            embedding=embedding,
            servings=recipe["servings"],      # 增加了人數
            cooking_time=recipe["cooking_time"] # 增加了時間
        )

# 定時爬蟲任務
async def scheduled_crawl():
    while True:
        print("排程器啟動，開始爬取資料...")

        crawl_state = load_crawl_state()#用來記錄每個關鍵字已經爬到第幾頁，避免重複抓取
        MAX_PAGES_PER_RUN = 2  # 每一輪最多只爬 2 頁，

        for category, keywords in CRAWL_KEYWORDS.items():
            for kw in keywords:
                last_page = crawl_state.get(kw, 0) #紀錄每個關鍵字上次抓到第幾頁，預設是0
                start_page = last_page + 1
                end_page = start_page + MAX_PAGES_PER_RUN - 1

                print(f"➡️ [{kw}] 爬取 page {start_page} ~ {end_page}")

                for page in range(start_page, end_page + 1):
                    urls = search_icook(kw, page=page) #去 iCook 搜尋 kw，取得第 page 頁的食譜網址列表
                    if not urls: #如果這頁沒有資料，直接跳出頁數迴圈
                        break

                    for url in urls:
                        try:
                            html = fetch_html(url)
                            recipe = parse_recipe(html, url, source="icook")
                            
                            #過濾不良好得食譜(judge.py)
                            if not recipe_is_valid(recipe["title"], recipe["steps"]):
                                print(f"[Skipped] {recipe['title']} ({category}) - 無效食譜")
                                continue
                            '''ai判斷
                            # ===== 使用 LLM 篩選 =====
                            steps_text = "\n".join(recipe.get('steps', []))

                            clean_title = clean_text(recipe["title"])

                            if not is_recipe_valid(clean_title, steps_text):
                                print(f"[Skipped] {clean_title} ({category}) - 無效食譜")
                                continue
                            #=========================='''

                            save_recipe_to_neo4j(recipe) #存到neo4j

                            print(f"Saved recipe:[Indexed]{recipe['title']} ({category})")
                        except Exception as e:
                            print(f"[Error] {url} 失敗: {e}")

                    crawl_state[kw] = page

        save_crawl_state(crawl_state) #儲存爬取進度

        print("休息 60 秒後開始下一輪爬取...")
        await asyncio.sleep(60)  # 休息 60 秒再下一輪  這些時間可以做其他事 因為是非同步的
        #await 只能用在 async function 裡