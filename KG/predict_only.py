"""
載入已訓練好的模型，直接做預測
不需要重新訓練

執行：
    python predict_only.py                    # 預測所有 SUBSTITUTABLE 食材，用RotatE，很多，如果你要看的話要把我下面註解的儲存結果取消
    python predict_only.py --ingredient 砂糖  # 查詢特定食材
    python predict_only.py --model RotatE     # 指定模型(我預設是用這個，所以如果要用transe才要特別用)
    python predict_only.py --model RotatE --ingredient 花椰菜  # 指定模型 + 查詢特定食材(我預設是顯示前10個預測結果，可以用--topk 5 調整 )
"""
import argparse
import json
from pathlib import Path
from pykeen.triples import TriplesFactory
from pykeen.predict import predict_target #用模型做 link prediction（預測 tail）
#from pykeen.models.predict import get_model_prediction_df#舊版的api 需要改成新版的
import torch

DATA_DIR   = Path(".")
OUTPUT_DIR = Path("./results")
TOP_K = 10

def load_model(model_name: str):
    model_dir = OUTPUT_DIR / model_name

    if not model_dir.exists():
        raise FileNotFoundError(model_dir)
    
    model = torch.load(
        model_dir / "trained_model.pkl",
        map_location="cpu",
        weights_only=False #讀完整模型（不是只有權重）
    )
    model.eval() #推論模式

    print(f"模型{model_dir}載入成功")
    return model

#預測替代食材
def predict(result, training, ingredient: str = None, top_k: int = TOP_K):
    model = result
    model.eval()

    rel_id = training.relation_to_id.get("SUBSTITUTABLE_WITH")#找relation ID
    if rel_id is None:
        raise ValueError("training 裡找不到 SUBSTITUTABLE_WITH 關係")

    id_to_entity = {v: k for k, v in training.entity_to_id.items()}#id → entity

    # 決定預測哪些食材
    if ingredient:
        if ingredient not in training.entity_to_id:
            similar = [e for e in training.entity_to_id if ingredient in e]
            print(f"找不到「{ingredient}」")
            if similar:
                print(f"你是否要查詢：{similar[:5]}")
            return {}
        targets = [ingredient]
    else:#如果沒有指定食材
        sub_mask = training.mapped_triples[:, 1] == rel_id #取train裡有這個關係的triple
        #sub_mask：只保留符合條件的列  0：取第0欄（head_id） .unique():去除重複值
        sub_head_ids = training.mapped_triples[sub_mask, 0].unique().tolist()#
        targets = [id_to_entity[i] for i in sub_head_ids] #id轉食材
        print(f"共 {len(targets)} 個食材，開始預測...\n")

    #預測
    all_predictions = {}
    for idx, head_name in enumerate(targets, 1):
        if len(targets) > 1:
            print(f"[{idx}/{len(targets)}] {head_name}", end="\r")

        df = predict_target(
            model=model,
            head=head_name,
            relation="SUBSTITUTABLE_WITH",
            triples_factory=training,
        ).df
        df = df[df["tail_label"] != head_name].head(top_k)

        all_predictions[head_name] = [
            {"entity": row["tail_label"], "score": round(float(row["score"]), 4)}
            for _, row in df.iterrows()
        ]

    print()
    print("=" * 50)
    for head_name, candidates in all_predictions.items():
        print(f"\n【{head_name}】替代食材 Top-{min(top_k, 10)}：")
        for rank, c in enumerate(candidates[:10], 1):
            print(f"  {rank:2d}. {c['entity']:<15}  score = {c['score']:.4f}")

    return all_predictions

def main():
    parser = argparse.ArgumentParser(description="食材替代預測")
    parser.add_argument("--model",      default="RotatE", choices=["TransE", "RotatE"])
    parser.add_argument("--ingredient", default=None)
    parser.add_argument("--topk",       default=TOP_K, type=int)
    args = parser.parse_args()

    result   = load_model(args.model)
    training = TriplesFactory.from_path(DATA_DIR / "train.tsv")

    predictions = predict(result, training, args.ingredient, args.topk)
    '''#如果要儲存你在取消註解
    if predictions:
        out_path = OUTPUT_DIR / args.model / "predictions.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(predictions, f, ensure_ascii=False, indent=2)
        print(f"\n完整結果已儲存至：{out_path}")'''

if __name__ == "__main__":
    main()
