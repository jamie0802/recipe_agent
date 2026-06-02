#Agent能用的工具，你後面要調neo4j的功能可以在這邊做擴充
#大概就是你寫一個函式，然後函式頭上加一個@function_tool就可以了

from agents import function_tool
from pydantic import BaseModel, Field
from typing import Literal
from sentence_transformers import SentenceTransformer
from enum import Enum
from PPR import recommend_tool
from database import get_user_explicit_profile,write_memories, get_pool
from pykeen.predict import predict_target
from pathlib import Path
#from pykeen.models import model_from_checkpoint #我的pykeen是1.11.1這個是舊版才能用
import torch
from pykeen.triples import TriplesFactory#用來讀三元組跟entity ↔ id mapping


import json

model = SentenceTransformer("BAAI/bge-large-zh-v1.5")

class Type(str,Enum):
    LIKE = "prefer"
    DISLIKE = "dislike"

class Item(str,Enum):
    INGREDIENTS = "ingre"
    STEPS = "step"
    TIME = "time"
    SERVINGS = "servings"

class Recommend(BaseModel):
    user_id: str
    serving: int | None = Field(default=None, description="用餐人數")
    max_duration: int | None = Field(default=None, description="最大可接受製作時長（分鐘）")
    cuisine_type: str | None = Field(default=None, description="只要是料理類型或標籤都放這個欄位")
    required: list[str] | None = Field(default=None, description="必須包含的食材列表，必須是真正的食材名稱")
    exclude: list[str] | None = Field(default=None, description="必須排除的食材列表")
    prefer: list[str] | None = Field(default=None, description="偏好食材列表，只能是食材 其餘食譜類型、標籤類禁止寫入此欄位")

tool = recommend_tool()

@function_tool
async def fetch_user_profile(user_id: str) -> str:
    print("查詢使用者資訊...")
    
    persona = await get_user_explicit_profile(user_id)
    
    if persona is None:
        return f"User {user_id} not found"

    return (
        f"User: {user_id}\n"
        f"Allergies: {persona.get('allergies', [])}\n"
        f"Diet type: {persona.get('diet_type')}\n"
        f"Cooking level: {persona.get('cooking_skill_level')}\n"
        f"Household_size: {persona.get('household_size')}\n"
        f"last_updated: {persona.get('last_updated')}\n"
    )

'''
@function_tool
async def fetch_hard_constraints(user_id: str) -> str:
    """取得使用者硬性限制"""
    print("查詢硬性限制...")
    persona = await get_user_explicit_profile(user_id)
    
    if persona is None:
        return "無硬性限制資料"


    return (
        f"exclude（必須排除）：{persona.get('allergies', []) + persona.get('disliked_ingredients', [])}\n"
        f"cuisine_type（飲食類型標籤）：{persona.get('diet_type') or '無'}\n"
        f"serving（用餐人數）：{persona.get('household_size') or '無'}\n"
        f"health_goal（健康目標）：{persona.get('health_goal') or '無'}\n"
    )
'''

@function_tool
async def fetch_hard_constraints(user_id: str) -> str:
    """取得使用者硬性限制"""
    print("查詢硬性限制...")
    persona = await get_user_explicit_profile(user_id)

    if persona is None:
        return "無硬性限制資料"

    # 排除 user_id，檢查其他欄位是否全為空
    constraint_fields = {
        k: v
        for k, v in persona.items()
        if k != "user_id"
    }

    if not any(constraint_fields.values()):
        return "目前用戶還無任何硬性限制"

    return (
        f"exclude（必須排除）：{persona.get('allergies', []) + persona.get('disliked_ingredients', [])}\n"
        f"cuisine_type（飲食類型標籤）：{persona.get('diet_type') or '無'}\n"
        f"serving（用餐人數）：{persona.get('household_size') or '無'}\n"
        f"health_goal（健康目標）：{persona.get('health_goal') or '無'}\n"
    )

@function_tool
async def fetch_soft_preferences(user_id: str) -> str:
    """取得使用者長期軟性偏好"""
    print("查詢長期軟性偏好...")
    pool = await get_pool()
    
    rows = await pool.fetch(
        """
        SELECT entity, value, confidence, tier
        FROM user_preferences
        WHERE user_id = $1
        AND confidence >= 0.3
        ORDER BY confidence DESC
        """,
        user_id,
    )

    if not rows:
        return "尚無長期偏好資料"

    stable, flexible = [], []

    for row in rows:
        entity = row["entity"]
        value = row["value"]
        tier = row["tier"]

        if isinstance(value, str):
            value = json.loads(value)

        label = f"{entity}（{'喜歡' if value else '不喜歡'}）"

        if tier == "stable":
            stable.append(label)
        else:
            flexible.append(label)

    parts = []
    if stable:
        parts.append(f"【穩定偏好 stable，不輕易覆蓋】{', '.join(stable)}")
    if flexible:
        parts.append(f"【模糊偏好 flexible，當下需求衝突時直接覆蓋】{', '.join(flexible)}")

    return "\n".join(parts)

ALLOWED_KEYS = {
    "allergies",
    "diet_type", 
    "household_size",
    "religious_restriction",
    "disliked_ingredients",
    "health_goal"
}

ARRAY_KEYS = {"allergies", "disliked_ingredients"}

#拿來新增使用者偏好食材
#先暫時更新上下文，後面再寫入DB(還沒做完)
@function_tool
async def update_user_profile(
    user_id: str,
    key: Literal[
        "allergies",
        "diet_type",
        "household_size",
        "religious_restriction",
        "disliked_ingredients",
    ],
    value: str,
) -> str:
    print("UPDATE_PROFILE TOOL CALLED")

    try:
        if key not in ALLOWED_KEYS:
            return "禁止其他 key 寫入"

        persona = await get_user_explicit_profile(user_id)

        if key in ARRAY_KEYS:
            items = [v.strip() for v in value.split(",") if v.strip()]
            existing = persona.get(key, []) or []
            duplicates = [i for i in items if i in existing]
            if duplicates:
                return f"以下內容已存在，略過寫入：{', '.join(duplicates)}"
            profile = {key: existing + items}
        else:
            existing = persona.get(key)
            if existing == value:
                return f"{key} 內容與現有資料相同，略過寫入"
            profile = {key: value}

        success = await write_memories(user_id, profile)
        print("write result:", success)

        return (
            f"已更新 {user_id} 的 {key}：{value}"
            if success else "更新失敗，請稍後再試"
        )

    except Exception as e:
        print("TOOL ERROR:", repr(e))
        raise

@function_tool
async def fetch_recent_activity(user_id: str) -> str:
    """取得使用者近期推薦紀錄"""
    print("查詢近期推薦紀錄...")
    pool = await get_pool()
    
    rows = await pool.fetch(
        """
        SELECT cuisine_type, count(*) as cnt
        FROM recommendation_history
        WHERE user_id = $1
        AND recommended_at > now() - interval '3 days'
        GROUP BY cuisine_type
        ORDER BY cnt DESC
        """,
        user_id,
    )

    if not rows:
        return "無近期推薦紀錄"

    repeat = [r["cuisine_type"] for r in rows if r["cnt"] >= 2]
    all_types = "、".join([f"{r['cuisine_type']}（{r['cnt']}次）" for r in rows])

    result = f"近期推薦：{all_types}"
    if repeat:
        result += f"\n推薦 2 次以上（可提示使用者是否換口味）：{'、'.join(repeat)}"

    return result
    
#拿來推薦食譜
@function_tool
async def recommend_recipe(param: Recommend) -> str:
    """
    推薦食譜。

    Args:
        user_id: 使用者 ID，從上下文填入
        exclude: 必須排除的食材，包含過敏食材和當下不想要的食材，從上下文「過敏食物」欄位填入
        prefer: 偏好食材列表，從上下文「近期偏好」填入
        cuisine_type: 料理類型，使用者未指定傳 null
        serving: 用餐人數，使用者未指定傳 null
        max_duration: 最長製作時長（分鐘），使用者未指定傳 null
    """
    print("RECOMMEND_TOOL CALLED")
    print("主Agent此次推薦參數")
    print(f"""
    使用者ID: {param.user_id}
    用餐人數: {param.serving}
    最大可接受製作時長: {param.max_duration}
    食譜類型: {param.cuisine_type}
    必須包含: {param.required}
    必須排除: {param.exclude}
    偏好食材列表: {param.prefer}
    """)
    try:
        #轉成dict給tool用
        param_dict = param.model_dump()
        print("param_dict的型態:", type(param_dict))

        if not param_dict.get("cuisine_type"):
            print("沒有特定的食譜類型，直接隨機推薦")
            results = await tool.random_get_recipe(param_dict,20)
        else:
            print("有特定的食譜類型，使用PPR推薦")
            results = await tool.PPR(param_dict)
        print("推薦結果:", results)

        if results and param.cuisine_type:
            pool = await get_pool()
            await pool.executemany(
                """
                INSERT INTO recommendation_history (user_id, recipe_name, cuisine_type)
                VALUES ($1, $2, $3)
                """,
                [(param.user_id, recipe_name, param.cuisine_type) for recipe_name in results]
            )
        return results

    except Exception as e:
        import traceback
        print(f"RECOMMEND_TOOL ERROR: {traceback.format_exc()}")
        return f"推薦失敗：{str(e)}"


@function_tool
async def get_details(recipe: str,item: Item):
    print(f"獲取食譜{recipe}的{item}...")

    try:
        if item == Item.INGREDIENTS:
            return await tool.get_ingredients(recipe)
        elif item == Item.STEPS:
            return await tool.get_steps(recipe)
        elif item == Item.TIME:
            return await tool.get_time(recipe)
        elif item == Item.SERVINGS:
            return await tool.get_servings(recipe)
        else:
            result = []

        if not result:
            print(f"查詢結果為空: {recipe}, item={item}")

        return f"{item} of {recipe}: {result}"
    
    except Exception as e:
        print(f"[ERROR] {e}")
        raise e

BASE_DIR = Path(__file__).resolve().parents[1]  # 到Recipe_recommend這一層
MODEL_DIR = BASE_DIR / "KG" / "results" / "RotatE"
#載入訓練好的 KG embedding model
model_substitute = torch.load( 
    MODEL_DIR / "trained_model.pkl",
    map_location="cpu",  #強制載入到 CPU
    weights_only=False  #不是只載入weights
)
model_substitute.eval() #推論模式
tf = TriplesFactory.from_path_binary(MODEL_DIR / "training_triples") #知識圖譜的三元組資料 + ID 對應表

#找替代的食材
@function_tool
def get_substitute(ingredient: str):
    from PPR import model #避免循環引用(embedding model (NOT PyKEEN))

    print(f"尋找替代食材: {ingredient}")#debug用
    try:
        #檢查 ingredient 在我的id mapping表裡(不是neo4j的)
        if ingredient in tf.entity_to_id:
            #print("[INFO] exact match in KG")

            df = predict_target(
                model=model_substitute,
                head=ingredient,
                relation="SUBSTITUTABLE_WITH",
                triples_factory=tf,
            ).df

            df = df[df["tail_label"] != ingredient].head(10)# 過濾自己+取 top-k

            substitutes = [
                {
                    "name": row["tail_label"],
                    "score": float(row["score"]),
                }
                for _, row in df.iterrows()
            ]

            #print(f"結果: {substitutes}")
            return substitutes
        
        else:
            #print("[INFO] not in KG → search to embedding similarity")
            query_embedding = model.encode(ingredient).tolist()

            substitutes = tool.find_substitute(query_embedding,ingredient,5)
            print(f"結果: {substitutes}")
            return substitutes

    except Exception as e:
        print(f"[ERROR] {e}")
        return None


