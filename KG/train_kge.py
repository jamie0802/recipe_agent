"""
TransE / RotatE
安裝依賴：pip install pykeen torch

執行：
    python train_kge.py               # 預設跑 TransE + RotatE
    python train_kge.py --model TransE
    python train_kge.py --model RotatE
"""
#這個檔案我只有用來訓練，預測跟比較模型 我分另外兩個檔案
import argparse #用來解析命令列參數(--model TransE)
from pathlib import Path #用物件方式管理路徑
import torch #PyKEEN 底層用它
from pykeen.triples import TriplesFactory #讀取 knowledge graph triples（head, relation, tail）
from pykeen.pipeline import pipeline #PyKEEN 的一鍵訓練工具

DATA_DIR   = Path(".")          # train/valid/test.tsv 在當前資料夾
OUTPUT_DIR = Path("./results")  # 模型與結果輸出位置

#建立兩個模型的超參數設定
CONFIG = {
    "TransE": {
        "embedding_dim": 128, #embedding維度（向量大小）
        "scoring_fct_norm": 1,# L1 norm(對離群值（outlier）比較不敏感)
        "epochs": 300,
        "batch_size": 1024, #每次更新使用1024個triples去計算loss
        "learning_rate": 0.001,
        "num_negs_per_pos": 64, #每個正例要產生的負例數
        "optimizer": "Adam", #自動調整學習率
        "lr_scheduler": "ExponentialLR", #訓練過程中逐漸降低學習率（有助於收斂）
        "lr_scheduler_kwargs": {"gamma": 0.99}, #每次更新後，learning rate乘上0.99(讓 learning rate 每一步慢慢變小)
    },
    "RotatE": {
        "embedding_dim": 128,
        "epochs": 300,
        "batch_size": 1024,
        "learning_rate": 0.001,
        "num_negs_per_pos": 64,
        "optimizer": "Adam",
        "lr_scheduler": "ExponentialLR",
        "lr_scheduler_kwargs": {"gamma": 0.99},
    },
}

#載入資料
def load_data():
    print("=" * 50)
    print("  載入資料集")
    print("=" * 50)

    training = TriplesFactory.from_path(DATA_DIR / "train.tsv") #從train.tsv 讀取三元組資料 (triples)
    #會自動：1.建 entity dictionary 2.建 relation dictionary 3.將triples轉成ID序列(head_id, relation_id, tail_id) 4.建internal tensor結構
    '''PyKEEN 會在 memory 裡建：
    relation_to_id = {
        "HAS_INGREDIENT": 0,
        "BELONGS_TO": 1,
        "COOKED_WITH": 2
    }
    所以才可以直接relation_to_id
    '''
    validation = TriplesFactory.from_path(
        DATA_DIR / "valid.tsv",
        entity_to_id=training.entity_to_id,
        relation_to_id=training.relation_to_id, #保證 valid/test 使用同一套 id mapping（非常重要）
    )
    testing = TriplesFactory.from_path(
        DATA_DIR / "test.tsv",
        entity_to_id=training.entity_to_id,
        relation_to_id=training.relation_to_id,
    )

    print(f"  entities  : {training.num_entities:,}")#顯示 entity 數量，並加上千分位逗號
    print(f"  relations : {training.num_relations}")
    for rel, rid in training.relation_to_id.items():
        print(f"    [{rid}] {rel}")
    print(f"  train     : {training.num_triples:,} triples")
    print(f"  valid     : {validation.num_triples:,} triples")
    print(f"  test      : {testing.num_triples:,} triples")
    print(f"  device    : {'cuda' if torch.cuda.is_available() else 'cpu'}")
    print()

    return training, validation, testing

#訓練
def train_model(model_name: str, training, validation, testing):
    print("=" * 50)
    print(f"  訓練 {model_name}")
    print("=" * 50)

    cfg = CONFIG[model_name] #取該模型設定
    out_dir = OUTPUT_DIR / model_name
    out_dir.mkdir(parents=True, exist_ok=True)#建立資料夾，如果上層資料夾不存在，也一起建立

    # 根據模型名稱設定 model_kwargs、negative_sampler、loss 等參數
    if model_name == "RotatE":
        model_kwargs = {
            "embedding_dim": cfg["embedding_dim"],
            "random_seed": 42,
        }

        negative_sampler = "basic" #隨機抽樣(用過Bernoulli但她結果沒有比basic好(比較適合用大型的資料))
        negative_sampler_kwargs=dict(
            num_negs_per_pos=cfg["num_negs_per_pos"],
        )#每1正例，產生64負例

        loss = "nssa" #我們看的論文用的(Negative Sampling Softmax Approximation)
        loss_kwargs = dict(
            adversarial_temperature=1.0,
            reduction="mean",
        )
    elif model_name== "TransE":
        model_kwargs = {
            "embedding_dim": cfg["embedding_dim"],
            "scoring_fct_norm": cfg["scoring_fct_norm"],
        }

        negative_sampler="basic"
        negative_sampler_kwargs=dict(
            num_negs_per_pos=cfg["num_negs_per_pos"],
        )
        
        loss="marginranking"#正樣本至少要比負樣本高1(margin)分，否則就會被懲罰
        loss_kwargs=dict(
            margin=1.0, 
        )
    
    result = pipeline(
        # 資料
        training=training,
        validation=validation,
        testing=testing,
        # 模型
        model=model_name,
        model_kwargs=model_kwargs,
        # 訓練
        training_kwargs=dict(
            num_epochs=cfg["epochs"],
            batch_size=cfg["batch_size"],
        ),
       
        negative_sampler=negative_sampler,
        negative_sampler_kwargs=negative_sampler_kwargs,

        loss=loss,
        loss_kwargs=loss_kwargs,
        
        # 優化器
        optimizer=cfg["optimizer"],
        optimizer_kwargs=dict(lr=cfg["learning_rate"]),
        lr_scheduler=cfg["lr_scheduler"],
        lr_scheduler_kwargs=cfg["lr_scheduler_kwargs"],
        # 評估（以 SUBSTITUTABLE_WITH 為主）
        evaluator_kwargs=dict(filtered=True), #在評估時，排除所有其他已知正確答案，避免把它們當成錯誤

        device="cuda" if torch.cuda.is_available() else "cpu",
        # 輸出
        result_tracker="csv", #訓練過程寫入 CSV 檔案
        result_tracker_kwargs=dict(name=str(out_dir / "training_log")),
        random_seed=42,
    )

    # 儲存模型
    result.save_to_directory(str(out_dir))#PyKEEN 會存權重 + config + embedding
    print(f"\n  模型已儲存至：{out_dir}/")

    # 印出評估指標 metric_results:評估結果物件（包含 MRR / Hits@K）  .to_dict():轉成字典格式
    metrics = result.metric_results.to_dict()
    print(f"\n  ── 測試集評估結果 ({model_name}) ──")
    key_metrics = ["hits_at_1", "hits_at_3", "hits_at_10", "mean_reciprocal_rank"]
    for k in key_metrics: #處理每個指標
        # PyKEEN 的 key 格式可能有 both.realistic. 前綴
        for mk, mv in metrics.items(): #mk 是完整的指標名稱（可能有前綴），mv 是指標值
            if k in mk and "realistic" in mk: #找到包含 k 且 realistic 的指標（這是 PyKEEN 的評估方式）
                print(f"    {k:30s}: {mv:.4f}")
                break

    return result

def main():
    parser = argparse.ArgumentParser() #建立CLI parser
    parser.add_argument(
        "--model",
        choices=["TransE", "RotatE", "both"],#限制輸入只能是這三個選項
        default="both", #如果你沒輸入就預設跑兩個模型
    )
    args = parser.parse_args() #解析參數

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    training, validation, testing = load_data()# 載入資料

    # 決定要跑哪些模型
    models=["TransE", "RotatE"] if args.model == "both" else [args.model]

    for model_name in models:
        train_model(model_name, training, validation, testing)

    print("訓練完成")

if __name__ == "__main__":
    main()
