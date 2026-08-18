"""
文件：scripts/runtime/install_nltk_data.py
作用：在镜像构建阶段安装兽医 Agent 运行所需的 NLTK 数据包。
范围：下载固定版本的 NLTK tokenizer 数据、校验 SHA256、解压到镜像只读资源目录并执行加载校验。
说明：本脚本用于 Dockerfile 构建阶段，不应在生产容器启动阶段执行。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class NltkPackageSpec:
    """表示一个可安装的 NLTK 数据包。

    :param name: 数据包名称。
    :param url: 固定下载地址。
    :param sha256: 期望的 SHA256 摘要。
    :param resource_path: NLTK data.find 使用的资源路径。
    :return: 无返回值。
    """

    name: str
    url: str
    sha256: str
    resource_path: str


PACKAGE_SPECS: Final[dict[str, NltkPackageSpec]] = {
    "punkt": NltkPackageSpec(
        name="punkt",
        url="https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/tokenizers/punkt.zip",
        sha256="51c3078994aeaf650bfc8e028be4fb42b4a0d177d41c012b6a983979653660ec",
        resource_path="tokenizers/punkt",
    ),
    "punkt_tab": NltkPackageSpec(
        name="punkt_tab",
        url="https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/tokenizers/punkt_tab.zip",
        sha256="e57f64187974277726a3417ca6f181ec5403676c717672eef6a748a7b20e0106",
        resource_path="tokenizers/punkt_tab",
    ),
}


def install_nltk_data(target_dir: Path, package_names: tuple[str, ...]) -> None:
    """安装指定 NLTK 数据包并执行加载校验。

    :param target_dir: NLTK 数据安装根目录。
    :param package_names: 需要安装的数据包名称。
    :return: 无返回值。
    :raises ValueError: 数据包名称不受支持时抛出。
    """
    specs = _resolve_specs(package_names)
    target_dir.mkdir(parents=True, exist_ok=True)
    tokenizer_dir = target_dir / "tokenizers"
    tokenizer_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="vet-agent-nltk-") as work_dir:
        work_path = Path(work_dir)
        for spec in specs:
            archive_path = work_path / f"{spec.name}.zip"
            _download(spec, archive_path)
            _verify_sha256(archive_path, spec.sha256)
            _extract_zip_safely(archive_path, tokenizer_dir)
    _validate_resources(target_dir, tuple(spec.resource_path for spec in specs))


def _resolve_specs(package_names: tuple[str, ...]) -> tuple[NltkPackageSpec, ...]:
    """解析待安装的 NLTK 数据包配置。

    :param package_names: 数据包名称集合。
    :return: 返回数据包配置元组。
    :raises ValueError: 数据包名称为空或存在未知名称时抛出。
    """
    names = tuple(dict.fromkeys(name.strip() for name in package_names if name.strip()))
    if not names:
        raise ValueError("至少需要指定一个 NLTK 数据包。")
    unknown = [name for name in names if name not in PACKAGE_SPECS]
    if unknown:
        raise ValueError(f"不支持的 NLTK 数据包: {', '.join(unknown)}")
    return tuple(PACKAGE_SPECS[name] for name in names)


def _download(spec: NltkPackageSpec, archive_path: Path) -> None:
    """下载指定 NLTK 数据包压缩文件。

    :param spec: NLTK 数据包配置。
    :param archive_path: 本地临时压缩文件路径。
    :return: 无返回值。
    """
    request = Request(spec.url, headers={"User-Agent": "veterinary-agent-build"})
    with urlopen(request, timeout=120) as response:
        archive_path.write_bytes(response.read())


def _verify_sha256(path: Path, expected_sha256: str) -> None:
    """校验文件 SHA256 摘要。

    :param path: 待校验文件路径。
    :param expected_sha256: 期望的 SHA256 摘要。
    :return: 无返回值。
    :raises ValueError: 实际摘要与期望摘要不一致时抛出。
    """
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_sha256:
        raise ValueError(f"NLTK 数据包摘要不匹配: {path.name}")


def _extract_zip_safely(archive_path: Path, target_dir: Path) -> None:
    """安全解压 NLTK 数据包。

    :param archive_path: NLTK 数据包压缩文件路径。
    :param target_dir: 解压目标目录。
    :return: 无返回值。
    :raises ValueError: 压缩包条目尝试写出目标目录时抛出。
    """
    target_root = target_dir.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        # 逐项校验解压目标，避免异常压缩包通过 ../ 跳出受控目录。
        for member in archive.infolist():
            destination = (target_root / member.filename).resolve()
            if not _is_relative_to(destination, target_root):
                raise ValueError(f"NLTK 数据包包含非法路径: {member.filename}")
        archive.extractall(target_root)


def _validate_resources(target_dir: Path, resource_paths: tuple[str, ...]) -> None:
    """校验安装后的 NLTK 资源能够被运行时代码加载。

    :param target_dir: NLTK 数据安装根目录。
    :param resource_paths: 需要定位的 NLTK 资源路径。
    :return: 无返回值。
    """
    os.environ["NLTK_DATA"] = str(target_dir)
    from nltk import data as nltk_data
    from nltk.tokenize import sent_tokenize

    target_value = str(target_dir)
    if target_value not in nltk_data.path:
        nltk_data.path.insert(0, target_value)
    for resource_path in resource_paths:
        nltk_data.find(resource_path)
    sent_tokenize("The cat vomited twice. The owner called the veterinarian.")


def _is_relative_to(path: Path, root: Path) -> bool:
    """判断路径是否位于指定根目录内。

    :param path: 待判断路径。
    :param root: 根目录路径。
    :return: 位于根目录内时返回 True。
    """
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """解析命令行参数。

    :param argv: 命令行参数序列；为空时由 argparse 读取 sys.argv。
    :return: 返回解析后的命令行参数对象。
    """
    parser = argparse.ArgumentParser(description="安装兽医 Agent 镜像运行所需的 NLTK 数据包。")
    parser.add_argument("--target-dir", required=True, type=Path, help="NLTK 数据安装根目录。")
    parser.add_argument("--packages", nargs="+", default=("punkt", "punkt_tab"), help="需要安装的数据包名称。")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """执行 NLTK 数据安装命令行入口。

    :param argv: 命令行参数序列；为空时由 argparse 读取 sys.argv。
    :return: 安装成功时返回 0，否则返回 1。
    """
    args = _parse_args(argv)
    try:
        install_nltk_data(args.target_dir, tuple(args.packages))
    except Exception as exc:
        print(f"NLTK 数据安装失败: {exc}", file=sys.stderr)
        return 1
    print(f"NLTK 数据安装完成: {args.target_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
