"""
文件：scripts/clinical_safety/convert_safety_reference.py
作用：将原始临床安全参考 JSON 转换为标准临床安全资产与向量检索片段。
说明：该脚本用于离线数据治理与版本生成，不参与在线安全决策链路。
"""


from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from scripts.clinical_safety import build_standard_safety_documents, load_safety_reference


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    :return: 返回命令行参数对象。
    """
    parser = argparse.ArgumentParser(description="Convert veterinary safety reference into standard assets.")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("scripts/clinical_safety/assets/vet_safety_reference.json"),
        help="原始安全参考 JSON 文件路径。",
    )
    parser.add_argument(
        "--asset-output",
        type=Path,
        default=Path("assets/clinical_safety/vet_safety_assets.v1.json"),
        help="标准临床安全资产输出路径。",
    )
    parser.add_argument(
        "--chunk-output",
        type=Path,
        default=Path("assets/clinical_safety/vet_safety_chunks.v1.json"),
        help="标准临床安全向量片段输出路径。",
    )
    parser.add_argument("--version", default="v1", help="生成资产版本。")
    parser.add_argument(
        "--review-status",
        default="approved",
        help="生成资产的默认审核状态。",
    )
    return parser.parse_args()


def main() -> None:
    """执行命令行转换入口。

    :return: 无返回值。
    """
    args = parse_args()
    payload = load_safety_reference(args.source)
    asset_document, chunk_document = build_standard_safety_documents(
        payload,
        source_file=str(args.source),
        version=args.version,
        review_status=args.review_status,
    )
    args.asset_output.parent.mkdir(parents=True, exist_ok=True)
    args.chunk_output.parent.mkdir(parents=True, exist_ok=True)
    args.asset_output.write_text(json.dumps(asset_document, ensure_ascii=False, indent=2), encoding="utf-8")
    args.chunk_output.write_text(json.dumps(chunk_document, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "asset_output": str(args.asset_output),
                "chunk_output": str(args.chunk_output),
                "asset_count": asset_document["_meta"]["asset_count"],
                "chunk_count": chunk_document["_meta"]["chunk_count"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
