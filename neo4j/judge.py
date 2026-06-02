#用來做判斷的function檔案
import re

#檢查標題是否像食譜名稱
def title_is_valid(title: str) -> bool:#回傳：布林值
    title = title.strip()#移除前後空白字元

    if contains_emoji(title):
        return False
    # 常見心得或文章詞
    bad_words = ["心得", "分享", "紀錄", "日記", "開箱", "實測"]
    for w in bad_words:
        if w in title:
            return False

    return True

#檢查步驟數量
def steps_count_valid(steps: list) -> bool:
    if not steps:
        return False
    #步驟太少就不要了
    if len(steps) < 4:
        return False

    return True

#檢查步驟是否有奇怪符號
def step_symbol_valid(step: str) -> bool:
    if not step or step.strip() == "": #我發現有些步驟是空字串或只有空白
        return False
    
    if contains_emoji(step):
        return False
    return True

#先把規則「做成一個工具」這樣在其他地方要用的時候就直接呼叫，不需要重複寫規則了
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001F5FF" #🌈 🌙 ☀️ 🌀(天氣、星座、自然)
    "\U0001F600-\U0001F64F" #😀 😃 😄 😁 😂 😊 😢 😡(最常見的表情符號)
    "\U0001F680-\U0001F6FF" #🚀 🚗 ✈️ 🚢 🚉(車輛、飛機、方向、地圖)
    "\U0001F780-\U0001F7FF" #🟠 🟡 🟢 🟣
    "\U0001F800-\U0001F8FF" #🡠 🡢 🡣 🡤 (各種方向箭頭)
    "\U0001F900-\U0001F9FF" #🤣 🤯 🤩 🤔 🤗
    "\U0001FA00-\U0001FAFF" #🪑 🪄 🪆 🪥 🫠(新工具、物品、動作類 emoji)
    "\U00002700-\U000027BF" #✂ ✈ ✉ ✔ ✖ ★(星星、勾勾、剪刀等)
    "\U00002600-\U000026FF" #☀ ☁ ☂ ☎ ⚡(雜項符號)
    "\U00002460-\U000024FF"  # 圈圈數字 ①②③
    "]+", #]+：表示「以上任一區間出現一次以上
    flags=re.UNICODE #讓 regex 支援 Unicode 字元（emoji / 中文等）
)
#檢查有沒有emoji
def contains_emoji(text: str) -> bool:
    return bool(EMOJI_PATTERN.search(text))#.search()：只要找到一個 emoji 就成立

#綜合判斷
def recipe_is_valid(title: str, steps: list) -> bool:
    if not title_is_valid(title):
        return False
    
    if not steps_count_valid(steps):
        return False
    # 每個步驟檢查
    for s in steps:
        if not step_symbol_valid(s):
            return False
    return True