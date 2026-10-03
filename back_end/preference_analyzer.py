import asyncpg
import json

from agents import Agent, Runner
from pydantic import BaseModel, Field
from typing import Optional
from models import PreferenceAnalyzerOutput
from database import get_pool
from datetime import datetime, timedelta, timezone

ANALYZER_PROMPT = """
你是個人化食譜系統的「偏好分析師」，負責從使用者的對話紀錄中判斷是否需要更新長期偏好。

---

【輸入資料】
1. current_session：這次完整的對話紀錄
2. recent_summaries：過去幾週的 session 摘要
3. current_preferences：目前已整理好的使用者偏好

---

【判斷門檻（很重要）】
像一個了解這個人的朋友，不會因為對方說一句話就改變對他的印象。

應該更新：
- 使用者明確說長期傾向改變：「我不愛吃辣了」、「我最近不太想吃重口味」
- 此次session內多次同方向訊號「2次以上」也要更新（如同session內多次提問某類型食譜推薦）
- 跨session摘要也有同方向訊號必須要更新

不應該更新：
- 當下情境需求：「今天想吃清淡」、「這次試試看清淡的」
- 單次提到，沒有其他佐證
- 硬性限制（過敏、宗教）→ 不歸這裡管
- 對推薦食譜的當下反應：「這個不錯」、「換一個」
- 使用者替其他人詢問、規劃、挑選、搜尋，該內容不得視為使用者本人的長期偏好

沒有足夠證據時，寧可不更新。

---

【更新規則】
confidence 計算：
- explicit 訊號：+0.3
- implicit 訊號：+0.1
- 多筆同方向：每多一筆 +0.05
- 與現有偏好矛盾：現有 confidence × 0.6，新方向以 0.2 加入
- 上限 1.0

tier：
- confidence >= 0.6 → stable
- confidence < 0.6 → flexible

decay_rate：
- 飲食習慣、口味偏好 → low
- 料理類型興趣 → medium
- 季節性、短期興趣 → high

---

【摘要任務】
分析完後，用一到兩句話摘要這個 session 裡有無偏好相關訊號。
只保留偏好相關的內容，忽略閒聊和當下需求。

格式：
「使用者這個 session [提到/要求/表達] OO，[頻率]次，[明確/隱約]表達長期傾向/未表達長期改變意圖」，因此Confidence +[按照更新規則填入數值/未改變]，tier為[stable/flexible]，decay_rate為[low/medium/high]。

沒有任何偏好訊號時，summary 填「無明顯偏好訊號」。
"""
def clean_for_big5(text: str) -> str:
    return text.encode('big5', errors='ignore').decode('big5')

def build_preference_analyzer() -> Agent:
    return Agent(
        name="PreferenceAnalyzer",
        instructions=ANALYZER_PROMPT,
        output_type=PreferenceAnalyzerOutput,
    )

def apply_decay(confidence: float, decay_rate: str, last_confirmed) -> float:
    if last_confirmed is None:
        return confidence

    if last_confirmed.tzinfo is None:
        last_confirmed = last_confirmed.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    days_elapsed = (now - last_confirmed).days

    rate_map = {
        "low": 0.995,
        "medium": 0.985,
        "high": 0.97,
    }

    rate = rate_map.get(decay_rate, 0.985)
    decayed = confidence * (rate ** days_elapsed)
    return round(max(decayed, 0.0), 4)

def build_preference_analyzer() -> Agent:
    return Agent(
        name="PreferenceAnalyzer",
        instructions=ANALYZER_PROMPT,
        output_type=PreferenceAnalyzerOutput,
        model="gpt-4.1-mini"
    )

async def run_preference_analyzer(user_id: str, session_id: str, session_cache: dict):
    pool = await get_pool()

    # 1. 從 SQLiteSession 取得這個 session 的完整對話
    session = session_cache.get(user_id)
    if not session:
        print(f"[Analyzer] {user_id} 找不到 session，略過")
        return
    #print(f"[Analyzer] session_id：{session.session_id}")
    
    items = await session.get_items()
    #print(f"[Analyzer] items 總數：{len(items)}")
    for i, item in enumerate(items):
        print(f"[Analyzer] item[{i}] role={item.get('role')}, content type={type(item.get('content'))}")
    
    user_items = [item for item in items if item.get("role") == "user"]
    assistant_items = [item for item in items if item.get("role") == "assistant"]

    current_session_turns = []
    for user_item, assistant_item in zip(user_items, assistant_items):
        user_text = user_item.get("content", "")
        
        assistant_content = assistant_item.get("content", "")
        if isinstance(assistant_content, list):
            assistant_text = " ".join(
                block.get("text", "")
                for block in assistant_content
                if block.get("type") == "output_text"
            )
        else:
            assistant_text = assistant_content

        current_session_turns.append(
            f"使用者：{user_text}\n助手：{assistant_text}"
        )

        if not current_session_turns:
            print(f"[Analyzer] {user_id} 無有效對話，略過")
            return

    # 2. 從 PostgreSQL 取得過去幾週的 session 摘要
    recent_summaries = await pool.fetch(
        """
        SELECT summary, created_at
        FROM session_summaries
        WHERE user_id = $1
        AND created_at > now() - interval '3 weeks'
        ORDER BY created_at DESC
        """,
        user_id,
    )

    summaries_text = "\n".join(
        [f"- {row['summary']}" for row in recent_summaries]
    ) or "無歷史摘要"

    # 3. 取得現有偏好（套用衰減）
    existing_preferences = await pool.fetch(
        """
        SELECT entity, value, confidence, tier, decay_rate, last_confirmed
        FROM user_preferences
        WHERE user_id = $1
        """,
        user_id,
    )

    existing_text = "\n".join(
        f"- entity: {row['entity']}, value: {row['value']}, "
        f"confidence: {apply_decay(row['confidence'], row['decay_rate'], row['last_confirmed'])}, "
        f"tier: {row['tier']}"
        for row in existing_preferences
    ) or "尚無現有偏好"

    # 4. 組裝輸入給 Analyzer
    input_text = f"""
    【current_session 完整對話】
    {chr(10).join(current_session_turns)}

    【recent_summaries 過去幾週摘要】
    {summaries_text}

    【current_preferences 現有偏好】
    {existing_text}
    """

    # 5. 跑 Analyzer
    analyzer = build_preference_analyzer()
    result = await Runner.run(analyzer, input=input_text)
    output: PreferenceAnalyzerOutput = result.final_output

    print(f"[Analyzer] {user_id} 判斷原因：{output.reason}")

    # 6. 寫入 session_summaries
    await pool.execute(
        """
        INSERT INTO session_summaries (user_id, session_id, summary)
        VALUES ($1, $2, $3)
        """,
        user_id,
        session_id,
        clean_for_big5(output.summary),
    )

    # 7. 更新 user_preferences
    if not output.updates:
        print(f"[Analyzer] {user_id} 無偏好更新")
        return

    async with pool.acquire() as conn:
        async with conn.transaction():
            for update in output.updates:
                await conn.execute(
                    """
                    INSERT INTO user_preferences
                        (user_id, entity, value, confidence, tier, decay_rate, source_ids, last_confirmed)
                    VALUES ($1, $2, $3, $4, $5, $6, ARRAY[$7], now())
                    ON CONFLICT (user_id, entity) DO UPDATE SET
                        value          = EXCLUDED.value,
                        confidence     = EXCLUDED.confidence,
                        tier           = EXCLUDED.tier,
                        decay_rate     = EXCLUDED.decay_rate,
                        source_ids     = array_append(user_preferences.source_ids, $7),
                        last_confirmed = now()
                    """,
                    user_id,
                    clean_for_big5(update.entity),
                    json.dumps(update.value) if isinstance(update.value, bool) else clean_for_big5(str(update.value)),
                    update.confidence,
                    update.tier,
                    update.decay_rate,
                    session_id,
                )

    print(f"[Analyzer] {user_id} 更新了 {len(output.updates)} 條偏好")