# How to start

## 1. 建立 `.env` 檔

將專案中的 `.env_example` 重新命名為 `.env`。

## 2. 填寫環境變數

打開 `.env`，把所有需要填寫的內容都填好，包含：

- `OPENAI_API_KEY`
- `PICOVOICE_API_KEY`
- Neo4j 帳號與密碼
- 密鑰（Secret Key）

## 3. 啟動後端（終端機 1）

```bash
cd back_end
```

## 4. 執行 FastAPI

```bash
uvicorn main:app --reload
```

## 5. 啟動前端（終端機 2）

另開一個終端機：

```bash
cd front_end
```

## 6. 進入 src 資料夾

```bash
cd src
```

## 7. 啟動前端服務

```bash
npm start
```

## 8. 啟動 Neo4j（終端機 3）

再開第三個終端機，進入 Neo4j 的 `bin` 資料夾：

```bash
cd ...\your_path\neo4j-community-2025.11.2\bin
```

（請將 `your_path` 換成你實際安裝 Neo4j 的路徑。）

## 9. 執行 Neo4j

```bash
neo4j console
```

---
