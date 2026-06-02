from neo4j import AsyncGraphDatabase #Neo4j 的非同步資料庫驅動
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
import os
import numpy as np
import re

load_dotenv()

model = SentenceTransformer("BAAI/bge-large-zh-v1.5")

def get_neo4j():
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER")
    password = os.getenv("NEO4J_PASSWORD")
    return AsyncGraphDatabase.driver(uri, auth=(user, password))
    #因為我整個agent pipeline是用非同步，所以這裡也用非同步的neo4j driver

class recommend_tool:
    def __init__(self):
        try:
            self.driver = get_neo4j()
        except Exception as e:
            print(f"無法連接到 Neo4j 資料庫: {e}")
            raise

    async def close(self):
        await self.driver.close()
    
    #檢查投影圖是否有被建立
    async def check_subgraph(self):
        async with self.driver.session() as session:
            result = await session.run("""
                CALL gds.graph.exists('recipeGraph')
                YIELD exists
            """)

            record = await result.single()
            exists = record["exists"] if record else False
            #Result.single() → 取 查詢結果中的第一筆紀錄，["exists"] → 取得回傳的 exists 欄位值
            if not exists:
                await session.run("""
                    CALL gds.graph.project(/*Neo4j Graph Data Science (GDS) 建圖 API*/
                        'recipeGraph',/*圖名稱*/
                        'Recipe',/*節點標籤*/
                        { SIMILAR: {} }/*關係類型*/
                    )
                """)
    
    #給食譜跟練頁面用的全部都給他
    async def get_full_recipe(self, recipe_name: str) -> dict | None:
        recipe_id, _ = await self.get_best_recipe(recipe_name)

        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (r:Recipe)
                WHERE elementId(r) = $recipe_id

                OPTIONAL MATCH (r)-[:HAS_INGREDIENT]->(i:Ingredient)
                OPTIONAL MATCH (r)-[:USES_METHOD]->(s:Step)

                WITH r,
                    collect(DISTINCT {
                        name: i.name,
                        metadata: i.metadata
                    }) AS ingredients,
                    collect(DISTINCT {
                        order: s.step_number,
                        description: s.description
                    }) AS steps

                RETURN elementId(r) AS id,
                    r.name AS name,
                    r.cooking_time AS cooking_time,
                    r.servings AS servings,
                    ingredients,
                    steps
            """, recipe_id=recipe_id)

            record = await result.single()
            if not record:
                return None

            ingredients = []
            for i in record["ingredients"]:
                if i["name"] is None:
                    continue
                combined = f"{i['name']} {i['metadata']}" if i["metadata"] else i["name"]
                ingredients.append({"name": combined})

            steps = [s for s in record["steps"] if s["order"] is not None]
            steps.sort(key=lambda x: x["order"])

            return {
                "id": record["id"],
                "name": record["name"],
                "cooking_time": record["cooking_time"],
                "servings": record["servings"],
                "ingredients": ingredients,
                "steps": steps
            }

         
        '''給我之後設定回傳參考
        candidate_ids: list[str], #一開始是所有的食譜名節點(可以去看tools.py的recommmend_tool邏輯)
        must_exclude: list[str], #當回合不要的食材+過敏食材
        serving: int | None, #用餐人數
        max_duration: int | None, #最大可解接受的製作時長
        similarity_threshold: float = 0.70, #相似度大於這個數值就認為這兩個食材相同->過濾'''
    
        '''
        return await self.filter_by_allergen_embedding(
            candidate_ids=candidate_ids,
            exclude_embeddings=exclude_embeddings,
            serving=serving,
            max_duration=max_duration,
            similarity_threshold=similarity_threshold,
        )'''


    #計算「食譜有多符合使用者喜好」 (每個「食材」去看它像不像使用者喜好)
    async def compute_preference_score_vec(
        self,
        recipe_name: str,
        liked_embeddings: list[list[float]],
        threshold: float = 0.80,
    ) -> float:
        if not liked_embeddings:
            return 0.0
        
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (r:Recipe {name: $name})-[:HAS_INGREDIENT]->(i:Ingredient)
                WHERE i.embedding IS NOT NULL
                WITH r, i,
                    reduce(mx = 0.0, emb IN $liked_embeddings |
                        CASE WHEN gds.similarity.cosine(i.embedding, emb) > mx
                            THEN gds.similarity.cosine(i.embedding, emb)
                            ELSE mx END
                    ) AS max_score
                WITH
                    count(i) AS total,
                    sum(CASE WHEN max_score >= $threshold THEN 1 ELSE 0 END) AS hit
                RETURN
                    CASE WHEN total = 0 THEN 0.0
                        ELSE toFloat(hit) / total END AS pref_score
            """, name=recipe_name,
                liked_embeddings=liked_embeddings,
                threshold=threshold)

            record = await result.single()
        return record["pref_score"] if record else 0.0#這道食譜有多少比例是使用者喜歡的


    #使用者要求的食材，有幾個被滿足(每個「需求」去看食譜有沒有對應食材) 
    async def compute_required_score_vec(
        self,
        recipe_name: str,
        required_embeddings: list[list[float]],
        threshold: float = 0.80,
    ) -> float:
        if not required_embeddings:
            return 0.0

        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (r:Recipe {name: $name})-[:HAS_INGREDIENT]->(i:Ingredient)
                WHERE i.embedding IS NOT NULL
                WITH collect(i.embedding) AS recipe_embeddings

                UNWIND $required_embeddings AS req_emb

                WITH req_emb, recipe_embeddings,
                    reduce(mx = 0.0, r_emb IN recipe_embeddings |
                        CASE WHEN gds.similarity.cosine(r_emb, req_emb) > mx
                            THEN gds.similarity.cosine(r_emb, req_emb)
                            ELSE mx END
                    ) AS best_match

                WITH
                    count(req_emb) AS total,
                    sum(CASE WHEN best_match >= $threshold THEN 1 ELSE 0 END) AS hit

                RETURN
                    CASE WHEN total = 0 THEN 0.0
                        ELSE toFloat(hit) / total END AS required_score
            """, name=recipe_name,
                required_embeddings=required_embeddings,
                threshold=threshold)

            record = await result.single()
        return record["required_score"] if record else 0.0


    def parse_servings(self, val):
        if val is None:
            return None
        try:
            return int(re.search(r"\d+", str(val)).group())#用正規表達式找第一段數字
        except:
            return None
        
    def parse_time(self, val): #5分鐘→5 1小時30分→90 None→None
        if val is None:
            return None

        val = str(val)
        hours = re.search(r"(\d+)\s*小時", val)
        mins = re.search(r"(\d+)\s*分", val)

        total = 0
        if hours:
            total += int(hours.group(1)) * 60
        if mins:
            total += int(mins.group(1))

        return total if total > 0 else None

    #PPR 內部用，直接用名字查，不做向量搜尋
    async def get_ingredients_by_name(self, recipe_name: str):
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (r:Recipe {name: $name})-[:HAS_INGREDIENT]->(i:Ingredient)
                RETURN i.name AS ingredient
            """, name=recipe_name)

            records = await result.data()
            return [{"name": r["ingredient"]} for r in records]
        
    async def check_ingredients_vec(
        self,
        recipe_name: str,
        required_embeddings: list[list[float]],
        disliked_embeddings: list[list[float]],
        threshold: float = 0.80,
    ) -> bool:
        # 兩個都是空的就直接放行，不用查 DB
        if not required_embeddings and not disliked_embeddings:
            return True

        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (r:Recipe {name: $name})-[:HAS_INGREDIENT]->(i:Ingredient)
                WHERE i.embedding IS NOT NULL
                WITH collect(i.embedding) AS recipe_embs

                WITH recipe_embs,
                    CASE WHEN $has_disliked
                        THEN any(r_emb IN recipe_embs WHERE
                            any(d_emb IN $disliked_embeddings WHERE
                                gds.similarity.cosine(r_emb, d_emb) >= $threshold))
                        ELSE false
                    END AS has_disliked_hit,

                    CASE WHEN $has_required
                        THEN any(r_emb IN recipe_embs WHERE
                            any(req_emb IN $required_embeddings WHERE
                                gds.similarity.cosine(r_emb, req_emb) >= $threshold))
                        ELSE true
                    END AS has_required_hit

                RETURN
                    has_required_hit AND NOT has_disliked_hit AS keep
            """, name=recipe_name,
                required_embeddings=required_embeddings or [],
                disliked_embeddings=disliked_embeddings or [],
                threshold=threshold,
                has_required=len(required_embeddings) > 0,
                has_disliked=len(disliked_embeddings) > 0)#只有在符合使用者喜好且沒有觸發討厭條件的情況下，才保留這筆資料(如果沒有喜好的話預設true)

            row = await result.single()
            return bool(row and row["keep"])
        
    #PPR
    async def PPR(self,filters: dict = None):
        try:    
            await self.check_subgraph()
            '''print(f"PPR的user_query:{user_query}")
            print(f"PPR的filters:{filters}")'''#我debug用的你可以刪掉

            if filters is None:#防止filters為None時crash
                filters = {}
            
            disliked = filters.get("exclude", [])
            liked_ingredients = filters.get("prefer", [])
            required_ingredients = filters.get("required", [])
            max_time = filters.get("max_duration",None)
            servings = filters.get("serving",1)
            cusine_type = filters.get("cuisine_type", None)

            print("使用者的要求")
            print(f"喜歡的食材:{liked_ingredients}")
            print(f"不要的食材:{disliked}")
            print(f"必須有的食材:{required_ingredients}")
            print(f"最大烹飪時間:{max_time}")
            print(f"用餐人數:{servings}")
            print(f"料理類型:{cusine_type}")

            ingredient_cache = {}# cache ingredients(因為很常需要用到食材 所以加一個function放)

            async def get_ing(name):
                if name not in ingredient_cache:
                    ingredient_cache[name] = await self.get_ingredients_by_name(name)
                return ingredient_cache[name]

            query_embedding = model.encode([cusine_type])[0].tolist()
            query_vec = np.array(query_embedding)#query_vec：將 Python list 轉成 numpy array，以便計算精確cosine 相似度
            
            liked_embeddings = (
                [model.encode([ing])[0].tolist() for ing in liked_ingredients]
                if liked_ingredients else []
            )

            required_embeddings = (
                [model.encode([ing])[0].tolist() for ing in required_ingredients]
                if required_ingredients else []
            )

            disliked_embeddings = (
                [model.encode([ing])[0].tolist() for ing in disliked]
                if disliked else []
            )
            SIM_THRESHOLD = 0.85 # 超過這個就視為「一樣的食材」

            #Step 1:近似最相似(用向量比對Recipe節點的embedding屬性，找出前200個最相似的食譜節點)
            async with self.driver.session() as session:
                result = await session.run("""
                    CALL db.index.vector.queryNodes(
                        'recipe_embedding_index',
                        200,
                        $query_embedding
                    )
                    YIELD node, score
                    RETURN node, id(node) AS nid, score
                """, query_embedding=query_embedding)
                #neo4j向量索引是用HNSW算法實現的近似最近鄰搜索，這裡的score是ANN算法計算的相似度分數，不是精確的cosine相似度，所以後面還要再算一次真正的cosine相似度來排序選出seed nodes
                candidates = await result.data()
                print("ANN搜尋到的候選食譜數量:", len(candidates))

            #Step 2: 根據條件做過濾
            filtered_candidates = []

            for r in candidates:
                node = r["node"]#一個 Neo4j Recipe 節點
                recipe_name = node["name"]

                ingredients = await get_ing(recipe_name)

                #hard filter: disliked、servings
                if not await self.check_ingredients_vec(
                    recipe_name, required_embeddings, disliked_embeddings, SIM_THRESHOLD
                ):
                    continue


                if servings is not None:
                    node_servings = self.parse_servings(node.get("servings"))
                    if node_servings != servings:
                        continue
                
                # soft prune: time
                if max_time is not None:
                    node_time = self.parse_time(node.get("cooking_time")) or 0
                    if node_time > max_time * 3:# 太離譜的可以先砍掉
                        continue

                filtered_candidates.append(r)
            print(f"條件過濾後的候選食譜數量: {len(filtered_candidates)}")

        
            #step 3:算cosine similarity
            scored = []

            for r in filtered_candidates:
                node = r["node"]
                nid = r["nid"]

                embedding = np.array(node["embedding"])#node["embedding"]->從 Neo4j 節點的屬性取得該節點的向量（embedding）並用np.array(...)轉成array
                #np.dot(query_vec, embedding)：內積，np.linalg.norm(query_vec)：計算向量長度 
                sim = np.dot(query_vec, embedding) / (
                    np.linalg.norm(query_vec) * np.linalg.norm(embedding)
                )

                scored.append((node["name"], nid, sim))

            if not scored:
                return []

            '''#debug用
            for name, nid, sim, in scored[:10]:
                print(f"{name} | sim={sim:.4f}")'''
            
            top_k = sorted(scored, key=lambda x: x[2], reverse=True)[:20] #依相似度排序，從大到小。[:20]：取前20個最相似的節點
            #lambda x: x[2] 是匿名函數，x → list 裡的每一個元素，x[2] → 取 tuple 的第三個元素(相似度 sim)，reverse=True → 從大到小排列
            
            sims = np.array([x[2] for x in scored])
            sim_min, sim_max = sims.min(), sims.max()
            denom = (sim_max - sim_min) + 1e-6#加這個是避免max == min → 分母變 0 → 爆掉

            #給PPR用的seed nodes id列表，和一個字典對應每個seed node id到它的相似度分數(sim)，後面計算final score會用到
            seed_ids = [nid for _, nid,_ in top_k]#_ → 忽略不需要的欄位（name, sim）

            similarity_map = {
                nid: (sim - sim_min) / denom
                for _, nid, sim in scored
            }

            # Step 4: PPR
            async with self.driver.session() as session:
                result = await session.run("""
                    CALL gds.pageRank.stream(
                        'recipeGraph',
                        {
                            dampingFactor: 0.85,
                            sourceNodes: $seed_ids
                        }
                    )
                    YIELD nodeId, score
                    WITH gds.util.asNode(nodeId) AS n, score
                    RETURN
                        n.name AS name,
                        id(n) AS nid,
                        score,
                        n.cooking_time AS cook_time,
                        n.servings AS servings
                    ORDER BY score DESC
                    LIMIT 80
                """, seed_ids=seed_ids)

                ppr_results = await result.data()

            #我確認他有沒有篩選用的
            print(f"PPR的seed_ids數量: {len(seed_ids)}")

            if not ppr_results:
                return []
            
            # normalize PPR(先準備好 下面的程式碼會用)
            ppr_scores = np.array([r["score"] for r in ppr_results])
            ppr_min, ppr_max = ppr_scores.min(), ppr_scores.max()
            ppr_denom = (ppr_max - ppr_min) + 1e-6

            #step 5再過濾
            final_candidates = []

            for r in ppr_results:
                recipe_name = r["name"]
                ingredients = await get_ing(recipe_name)

                #hard filter: disliked、servings
                if not await self.check_ingredients_vec(
                    recipe_name, required_embeddings, disliked_embeddings, SIM_THRESHOLD
                ):
                    continue

                if servings is not None:
                    r_servings = self.parse_servings(r["servings"])
                    if r_servings != servings:
                        continue
                
                #soft constraint：時間
                time_score = 1.0
                if max_time is not None:
                    cook_time = self.parse_time(r["cook_time"]) or 0
                    if cook_time <= max_time:
                        time_score = 1.0
                    else:#超時 → 指數懲罰
                        time_score = np.exp(-(cook_time - max_time) / max_time)

                final_candidates.append((r, time_score))
            print(f"過濾後的候選食譜數量: {len(final_candidates)}")    

            '''
            for r, time_score in final_candidates[:10]:
                print(f"{r['name']} | ppr={r['score']:.4f} | time_score={time_score:.4f}")'''
            #step 6 final
            alpha, beta, gamma, delta, epsilon = 0.3, 0.25, 0.15, 0.1, 0.2

            final_results = []
            
            '''# debug
            print(f"Step2 filtered_candidates: {len(filtered_candidates)}")
            print(f"Step3 scored: {len(scored)}")
            print(f"Step4 ppr_results: {len(ppr_results)}")
            print(f"Step5 final_candidates: {len(final_candidates)}")'''
            
            for r, time_score in final_candidates:
                nid = r["nid"]
                recipe_name = r["name"]

                #避免分數尺度不一致 所以都有做正規化(壓縮到0~1)
                sim = similarity_map.get(nid, 0.0)
                ppr_score = (r["score"] - ppr_min) / ppr_denom

                ingredients = await get_ing(recipe_name)

                required_score = await self.compute_required_score_vec(recipe_name, required_embeddings, threshold=SIM_THRESHOLD)
                pref_score = await self.compute_preference_score_vec(recipe_name, liked_embeddings, threshold=SIM_THRESHOLD)

                final_score = (
                    alpha * sim +
                    beta * ppr_score +
                    gamma * time_score +
                    delta * pref_score +
                    epsilon * required_score
                )
                
                #如果這道食譜 完全符合使用者要求食材→ 額外加分
                if required_embeddings and required_score == 1.0:
                    final_score += 0.1

                final_results.append({
                    "name": recipe_name,
                    "id": nid,
                    "final_score": final_score
                })

            #print(f"Step6 final_results: {len(final_results)}")

            final_results.sort(key=lambda x: x["final_score"], reverse=True)
            '''#回傳分數高的前10名
            for i in final_results[:10]:
                print(f"PPR的filters:{i}")'''
            print("PPR推薦的前10名食譜:", [r["name"] for r in final_results[:10]])

            return [r["name"] for r in final_results[:10]]
        except Exception as e:
            print(f"PPR過程中發生錯誤: {e}")
            return []


    #隨機抓20個食譜(配合使用者喜好)
    async def random_get_recipe(self, filters: dict = None, limit=20):
        filters = filters or {}

        disliked = filters.get("exclude", [])
        liked_ingredients = filters.get("prefer", [])
        required_ingredients = filters.get("required", [])
        max_time = filters.get("max_duration",None)
        servings = filters.get("serving",1)

        SIM_THRESHOLD = 0.80
        liked_embeddings = model.encode(liked_ingredients).tolist() if liked_ingredients else []
        required_embeddings = model.encode(required_ingredients).tolist() if required_ingredients else []
        disliked_embeddings = model.encode(disliked).tolist() if disliked else []
                
        print(f"隨機抓食譜 喜歡的:{liked_ingredients} 不要的:{disliked} max_time:{max_time} servings:{servings},使用者要求的{required_ingredients}")
        cypher = """
        MATCH (r:Recipe)-[:HAS_INGREDIENT]->(i:Ingredient)
        WITH r, collect(i) AS ingredients

        //HARD FILTER: disliked
        WHERE NOT any(i IN ingredients WHERE
            i.embedding IS NOT NULL AND
            any(d IN $disliked_embeddings WHERE
                gds.similarity.cosine(i.embedding, d) >= $threshold
            )
        )

        WITH r, ingredients,

        //required hit
        size([
            req IN $required_embeddings
            WHERE any(i IN ingredients WHERE
                i.embedding IS NOT NULL AND
                gds.similarity.cosine(i.embedding, req) >= $threshold
            )
        ]) AS required_hit,

        //liked score
        size([
            lk IN $liked_embeddings
            WHERE any(i IN ingredients WHERE
                i.embedding IS NOT NULL AND
                gds.similarity.cosine(i.embedding, lk) >= $threshold
            )
        ]) AS hit_count,

        CASE
            WHEN $max_time IS NULL THEN 0
            WHEN toInteger(r.cooking_time) <= toInteger($max_time) THEN 10
            ELSE 0
        END AS time_score,

        CASE
            WHEN $servings IS NULL THEN 0
            WHEN toInteger(r.servings) = toInteger($servings) THEN 10
            ELSE 0
        END AS serving_score

        WITH r, ingredients,
            required_hit,
            hit_count,
            time_score,
            serving_score,
            (required_hit * 5.0) + (hit_count * 2.0) + time_score + serving_score AS final_score

        ORDER BY final_score DESC, rand()

        RETURN r.name AS name
        LIMIT $limit
        """

        async with self.driver.session() as session:
            result = await session.run(
                cypher,
                liked_embeddings=liked_embeddings,
                required_embeddings=required_embeddings,
                disliked_embeddings=disliked_embeddings,
                max_time=max_time,
                servings=servings,
                threshold=SIM_THRESHOLD,
                limit=limit
            )

            return await result.data()
   
    #用向量相似度尋找最相似的食譜名(以防使用者打錯名字用)
    async def get_best_recipe(self, query_name: str):
        query_embedding = model.encode([query_name])[0].tolist()
        async with self.driver.session() as session:
            result = await session.run("""
                CALL db.index.vector.queryNodes('recipe_embedding_index', 1, $embedding)
                YIELD node, score
                RETURN elementId(node) AS rid, node.name AS name
            """, embedding=query_embedding)
            record = await result.single()
            if record:
                return record["rid"], record["name"]
        return None

    async def get_ingredients(self, recipe_name: str):
        recipe_id, best_name = await self.get_best_recipe(recipe_name)

        async with self.driver.session() as session:

            result = await session.run("""
                MATCH (r:Recipe)-[:HAS_INGREDIENT]->(i:Ingredient)
                WHERE elementId(r) = $recipe_id
                RETURN i.name AS ingredient, i.metadata AS metadata
            """, recipe_id=recipe_id)

            ingredients = []

            async for record in result:
                name = record["ingredient"]
                metadata = record["metadata"]
                combined = f"{name} {metadata}" if metadata is not None else name
                ingredients.append({"name": combined})

            if not ingredients:
                print(f"食譜 '{recipe_name}' 沒有食材資料")
            return ingredients

    async def get_steps(self, recipe_name: str):
        recipe_id, best_name = await self.get_best_recipe(recipe_name)
        async with self.driver.session() as session:

            result = await session.run("""
                MATCH (r:Recipe)-[:USES_METHOD]->(s:Step)
                WHERE elementId(r) = $recipe_id
                RETURN s.step_number AS order, s.description AS description
                ORDER BY s.step_number ASC
            """, recipe_id=recipe_id)

            steps = []

            async for record in result:
                steps.append((record["order"], record["description"]))

            steps.sort(key=lambda x: x[0])#按照步驟順序去排序

            if not steps:
                print(f"食譜 '{recipe_name}' 沒有步驟資料")
            return steps

    async def get_time(self, recipe_name: str):
        recipe_id, best_name = await self.get_best_recipe(recipe_name)
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (r:Recipe) WHERE elementId(r) = $recipe_id
                RETURN r.cooking_time AS cooking_time
            """, recipe_id=recipe_id)
            record = await result.single()
            return record["cooking_time"] if record else None

    async def get_servings(self, recipe_name: str):
        recipe_id, best_name = await self.get_best_recipe(recipe_name)
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (r:Recipe) WHERE elementId(r) = $recipe_id
                RETURN r.servings AS servings
            """, recipe_id=recipe_id)
            record = await result.single()
            return record["servings"] if record else None
        
    
    async def find_substitute(self, query_embedding: list[float],ingredient_name:str,limit=5):
            async with self.driver.session() as session:
                result = await session.run("""
                    CALL db.index.vector.queryNodes('ingredient_embedding_index', $top_k, $query_embedding)
                    YIELD node, score
                    WITH node, score
                    WHERE node.name <> $name
                    RETURN node.name AS name, score
                    LIMIT $limit
                """, {
                    "query_embedding": query_embedding,
                    "name": ingredient_name,
                    "limit": limit,
                    "top_k": limit + 1
                })#本身就會從高到低排序
                
                records = []
                async for r in result:
                    records.append(r)
                    
                #print(f"Substitute candidates for '{ingredient_name}': {[r['name'] for r in records]}")#debug用，印出找到的替代食材候選列表
                return [
                    {"name": r["name"]}
                    for r in records
                ]