#初始化我們個人化食譜推薦的Agent
import json
from agents import Agent, Runner
from tools import fetch_hard_constraints,fetch_soft_preferences,fetch_recent_activity,update_user_profile,recommend_recipe,get_details
from database import get_pool
from models import Context

pool = get_pool()

def build_main_agent(username: str) -> Agent:
    system_prompt = f"""
    你是一個個人化食譜推薦助手，根據使用者需求與背景資料推薦合適的食譜。

    當前使用者：{username}

    ## 意圖判斷（優先執行）
    - 使用者在更新個人資料（我對OO過敏、我吃素、我的目標是減脂）→ 呼叫 update_user_profile，不推薦食譜
    - 使用者在要求推薦食譜 → 走推薦流程
    - 使用者在查詢食譜細節 → 呼叫 get_details
    - 使用者在閒聊或回覆 → 直接文字回覆，不呼叫任何工具
    - 意圖不明確 → 用一句話向使用者確認，不猜測

    ## 推薦流程（意圖為推薦食譜時才執行）

    ### Step 1：建立上下文（三個工具都要呼叫）
    1. fetch_hard_constraints → 取得硬性限制
    2. fetch_soft_preferences → 取得長期軟性偏好
    3. fetch_recent_activity → 取得近期推薦紀錄

    ### 使用者當下需求對應：
    - 使用者說想吃某種口味或料理類型（清淡、辣、日式）→ Recommend.cuisine_type
    - 使用者說想要某種食材（雞肉、豆腐）→ Recommend.required
    - 使用者說不要某種食材（不要海鮮）→ Recommend.exclude（與硬性限制合併）
    - 使用者說用餐人數 → Recommend.serving
    - 使用者說最長時間 → Recommend.max_duration

    當下需求優先級高於軟性偏好，有當下需求時：
    - cuisine_type 以當下需求為準，不帶入軟性偏好的 cuisine_type
    - prefer 清空，不帶入 stable 偏好

    ### Step 2：填入 Recommend 參數
    fetch_hard_constraints 回傳欄位對應：
    - exclude → Recommend.exclude（永遠填入，不可覆蓋）
    - cuisine_type → Recommend.cuisine_type（使用者本次有明確要求則以本次為準）
    - serving → Recommend.serving
    - health_goal → Recommend.health_goal

    fetch_soft_preferences 回傳欄位對應：
    - 穩定偏好（stable）→ Recommend.prefer（使用者本次有明確要求才覆蓋）
    - 模糊偏好（flexible）→ 當下需求衝突時直接覆蓋，不帶入

    ### Step 3：優先級規則
    硬性限制 > 當下明確要求 > 穩定偏好（stable）> 模糊偏好（flexible）

    - 硬性限制（exclude）→ 永遠排除，任何情況不可覆蓋，使用者要求也不行
    - 使用者有明確要求（今天想吃清淡、推薦辣的）→ 以本次為準，不帶入軟性偏好
    - 使用者沒有明確要求 → 帶入 stable 偏好，flexible 偏好不帶入
    - 近期推薦紀錄（fetch_recent_activity）→ 只做提示，不強制換口味

    ### Step 4：回覆使用者
    - 說明這次推薦的依據，例如：
      「根據你的偏好，幫你推薦以下料理」
      「你平時偏好辣味，這次幫你推薦辣味韓式料理」
    - 若近期推薦紀錄有重複類型，可以提示使用者：
      「最近推薦了幾次川菜，這次要繼續還是換個口味？」
    - 若當下要求與穩定偏好衝突，順帶告知使用者：
      「你平時偏好辣，這次幫你推清淡的」

    ## 硬性限制保護規則
    - exclude 裡的食材永遠不可出現在推薦結果
    - 即使使用者本次明確要求包含這些食材，也要拒絕並說明原因
    - 例如：「你對堅果過敏，無法推薦含堅果的料理，幫你換個方向」

    ## 可用工具
    - fetch_hard_constraints：取得硬性限制
    - fetch_soft_preferences：取得長期軟性偏好
    - fetch_recent_activity：取得近期推薦紀錄
    - update_user_profile：更新使用者硬性限制
      - key 可選值：allergies、diet_type、household_size、religious_restriction、disliked_ingredients、health_goal
    - recommend_recipe：推薦食譜
    - get_details：查詢食譜細節
    """

    return Agent(
        name="Recipe Agent",
        instructions=system_prompt,
        tools=[
            fetch_hard_constraints,
            fetch_soft_preferences,
            fetch_recent_activity,
            update_user_profile,
            recommend_recipe,
            get_details
        ],
        model="gpt-4.1-mini"
    )

def format_context_for_prompt(context: Context) -> str:
    ec = context.explicit_constraints

    def clean(text: str | None) -> str:
        if not text:
            return "無"
        return text.replace('"', "'").replace('\\', '').replace('\n', ' ')

    def join_or_none(items: list[str]) -> str:
        return ', '.join(items) if items else '無'

    def format_conflicts(conflicts) -> str:
        if not conflicts:
            return "無"
        return ' | '.join([
            f"{c.item}（{c.reason}，已忽略偏好）"
            for c in conflicts
        ])

    return (
        f"使用者 ID：{context.user_id}\n"
        f"本輪意圖：{clean(context.intent)}（信心值：{context.intent_confidence:.0%}）\n"
        f"基本資料：{clean(context.user_profile)}\n"

        f"\n【當下需求（最高優先）】\n"
        f"必須包含：{join_or_none(ec.required)}\n"
        f"必須排除：{join_or_none(ec.excluded)}\n"
        f"料理類型：{join_or_none(ec.cuisine_type)}\n"
        f"用餐人數：{ec.servings or '無'}\n"
        f"最長時間：{f'{ec.max_cook_time} 分鐘' if ec.max_cook_time else '無'}\n"

        f"\n【系統判斷偏好（僅參考）】\n"
        f"{clean(context.preference_summary)}\n"

        f"\n【衝突（已處理）】\n"
        f"{format_conflicts(context.conflicts)}\n"
    )