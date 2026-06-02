'''
直接執行比較transe跟rotate
python eval_models.py
'''
import torch
from pathlib import Path
from pykeen.triples import TriplesFactory
from pykeen.evaluation import RankBasedEvaluator #PyKEEN評估工具

BASE = Path(r"C:\Users\user\Desktop\vs\Recipe_recommend\KG")
DATA_DIR = BASE
RESULTS_DIR = BASE / "results"


def load_model(name):
    path = RESULTS_DIR / name / "trained_model.pkl"
    model = torch.load(path, map_location="cpu", weights_only=False)
    model.eval()
    return model

def check_oov(train, test):#檢查測試集中是否存在訓練集中沒見過的詞
    train_entities = set(train.entity_to_id.keys())#train.entity_to_id 轉換成集合（set）
    train_relations = set(train.relation_to_id.keys())#只取出KEY讓查詢速度變快

    bad_triples = 0

    for h, r, t in test.triples:
        if h not in train_entities or r not in train_relations or t not in train_entities:
            bad_triples += 1

    print(f"[DEBUG] OOV triples in test: {bad_triples}/{len(test.triples)}")

def extract_metrics(result_dict):
    metrics = result_dict["both"]["realistic"]

    mrr = metrics.get("inverse_harmonic_mean_rank", None)
    if mrr is None:
        mrr = metrics.get("mean_reciprocal_rank", 0.0)

    hits10 = metrics.get("hits_at_10", 0.0)
    return mrr, hits10

def main():
    train = TriplesFactory.from_path(DATA_DIR / "train.tsv")

    test = TriplesFactory.from_path(
        DATA_DIR / "test.tsv",
        entity_to_id=train.entity_to_id,
        relation_to_id=train.relation_to_id,
    )

    print("[INFO] train entities:", len(train.entity_to_id))
    print("[INFO] test entities:", len(test.entity_to_id))
    print("[INFO] train relations:", len(train.relation_to_id))
    print("[INFO] test relations:", len(test.relation_to_id))

    check_oov(train, test)

    evaluator = RankBasedEvaluator()

    print("\nModel\tMRR\tHits@10")

    for name in ["TransE", "RotatE"]:
        print(f"\n[INFO] Evaluating {name}")

        model = load_model(name)

        result = evaluator.evaluate(
            model=model,
            mapped_triples=test.mapped_triples,
            additional_filter_triples=[train.mapped_triples],#測試時，如果候選答案在 training 出現過，就不要算它是錯誤排名
        )

        d = result.to_dict()
        mrr, hits10 = extract_metrics(d)

        print(f"{name}\t{mrr:.3f}\t{hits10:.3f}")

if __name__ == "__main__":
    main()