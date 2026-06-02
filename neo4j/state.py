import json
import os

STATE_FILE = "crawl_state.json"#爬蟲進度檔案的檔名

def load_crawl_state():#讀取 JSON 檔中的爬蟲進度
    if not os.path.exists(STATE_FILE):#若不存在 → 建立新檔案
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f, ensure_ascii=False, indent=2)
        return {}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_crawl_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f: #ensure_ascii=False→保留中文，而不是轉成\uXXXX
        json.dump(state, f, ensure_ascii=False, indent=2) #indent=2→格式化輸出(縮排)，方便閱讀
        #json.dump():把 Python 物件寫入 JSON 檔案(dict → JSON)