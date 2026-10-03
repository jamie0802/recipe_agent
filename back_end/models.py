from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime
from enum import Enum

# =======================
# 任務一：給主Agent的上下文
# =======================
#這個是當回合使用者明確說的需求(子Agent抓)
class ExplicitConstraints(BaseModel):
    required: list[str] = Field(description="使用者明確說「要」「想要」的食材",default_factory=list)        
    excluded: list[str] = Field(description="使用者明確說「不要」「不能有」的食材",default_factory=list)     
    cuisine_type: list[str] = Field(description="只要是料理類型或標籤都放這個欄位",default_factory=list)
    servings: Optional[int] = None          # 明確說的人數
    max_cook_time: Optional[int] = None     # 明確說的最長製作時長（分鐘）

#衝突訊號(使用者當下要求與使用者畫像紀錄的內容有矛盾)
class ConflictItem(BaseModel):
    type: Literal["cuisine", "ingredient", "flavor"]
    item: str
    reason: str
    resolution: Literal["ignored_preference", "override_by_user"]

class Context(BaseModel):
    user_id: str
    intent: str = Field(description="使用者本輪最核心的意圖，一句話，動詞開頭",default=None)  # 使用者此回合意圖
    intent_confidence: float = Field(
        description="對 intent 判斷的信心值，範圍 0.0～1.0",
        default=0.0,
        ge=0.0,
        le=1.0
    )
    user_profile: Optional[str] = Field(description="使用者基本資料，僅列出有值的欄位",default=None)  # 使用者基本資料
    explicit_constraints: ExplicitConstraints = Field(
        description="使用者本輪明確說出的需求，未提及的欄位保持預設值",
        default_factory=ExplicitConstraints
    )  # 使用者明確表達的需求細節
    preference_summary: Optional[str] = Field(
        description="使用者近期偏好的自然語言描述，例如「偏好清淡口味，喜歡義式料理，避免海鮮」",
        default=None
    )
    conflicts: list[ConflictItem] = Field(default_factory=list)

# =================
# 任務二：偏好訊號，子Agent跑完由系統把訊號寫入DB給後面Preference Analyzer使用
# =================
class PreferenceSignal(BaseModel):
    certainty: Literal["explicit", "implicit"]
    raw_text: str
    turn_index: int

# 子Agent最終輸出
class ContextAgentOutput(BaseModel):
    context: Context # 任務一
    preference_signals: list[PreferenceSignal] = Field(default_factory=list)  # 任務二

# ===================
# Preference Analyzer
# ===================

class PreferenceUpdate(BaseModel):
    entity: str
    value: bool | str
    confidence: float = Field(ge=0.0, le=1.0)
    tier: str  # stable / flexible
    decay_rate: str  # low / medium / high

class PreferenceAnalyzerOutput(BaseModel):
    updates: list[PreferenceUpdate]
    reason: str = Field(description="為什麼決定更新或不更新，一句話說明")
    summary: str = Field(description="這個 session 的偏好相關摘要")


