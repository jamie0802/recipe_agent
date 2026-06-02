import asyncpg
import json
import os
from psycopg2.extras import RealDictCursor
from collections import Counter
from dotenv import load_dotenv

load_dotenv()

_pool = None

async def get_pool():
    global _pool

    if _pool is None:
        _pool = await asyncpg.create_pool(
            host=os.getenv("POSTGRES_DB_HOST"),
            port=int(os.getenv("POSTGRES_DB_PORT", 5432)),
            database=os.getenv("POSTGRES_DB_NAME"),
            user=os.getenv("POSTGRES_DB_USER"),
            password=os.getenv("POSTGRES_DB_PASSWORD"),
        )

    return _pool

async def write_memories(username: str, profile: dict) -> bool:
    SINGLE_FIELDS = {"diet_type", "household_size", "cooking_skill_level"}

    try:
        has_allergies = "allergies" in profile
        single_fields = [f for f in SINGLE_FIELDS if f in profile]

        if not has_allergies and not single_fields:
            return True

        insert_cols = ["user_id"]
        insert_vals = [username]
        update_sets = []

        if has_allergies:
            insert_cols.append("allergies")
            insert_vals.append(profile["allergies"])
            update_sets.append("""
                allergies = (
                    SELECT ARRAY(
                        SELECT DISTINCT unnest(user_explicit_profile.allergies || EXCLUDED.allergies)
                    )
                )
            """)

        for col in single_fields:
            insert_cols.append(col)
            insert_vals.append(profile[col])
            update_sets.append(f"{col} = EXCLUDED.{col}")

        placeholders = ", ".join(f"${i+1}" for i in range(len(insert_vals)))
        cols = ", ".join(insert_cols)

        sql_str = f"""
            INSERT INTO user_explicit_profile ({cols}, last_updated)
            VALUES ({placeholders}, NOW())
            ON CONFLICT (user_id) DO UPDATE SET
                {", ".join(update_sets)},
                last_updated = NOW()
        """

        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(sql_str, *insert_vals)

        return True

    except Exception as e:
        print(f"[DB] 寫入錯誤：{e}")
        return False

async def get_user_explicit_profile(user_id: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT allergies, diet_type, household_size
            FROM user_explicit_profile
            WHERE user_id = $1
        """, user_id)
        return dict(row) if row else {}