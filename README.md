## 王佳佳是GAY

## 目前進度：
- 新增agent上下文(後端除了faster_whisper跟practice都有串上你的外其他檔案都有改過)
- 然後有小改一下你的shutdown路由，因為FastAPI好像把on_event功能要撤掉了改成lifespan(但邏輯等價於你的程式)
- neo4j資料採取部分我印象我把config整個變多了 阿crawl_parser.py的search_icook(有加例外處理的情況，page找不到導致整個抓取任務中止)和state.py的兩個函式(其實就只是多加了如果沒有crawl_state爬蟲進度檔的話就開一個新的)

## 待新增功能：
- 太多了我懶得講了，反正我要優化推薦食譜的路還遠得很


