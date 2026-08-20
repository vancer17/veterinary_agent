# 临床安全离线数据治理脚本

本目录只放置临床安全参考数据的离线转换过程，不参与在线 Agent 安全裁决。

- `assets/vet_safety_reference.json`：原始长文本安全参考资产。
- `converter.py`：将原始资产转换为标准临床安全资产和向量检索片段。
- `convert_safety_reference.py`：命令行入口，默认输出到 `assets/clinical_safety/`。

原始资产必须显式声明稳定 `code`，转换器不会根据条目序号、资产类型或医学名称生成默认编码；缺失 `code` 时直接失败，避免旧版兜底编码进入发布链路。

`emergencyRedFlags` 条目当前使用 `EMERGENCY_MODE_[A-Z0-9]{10}` 的 opaque 资产级编码，并在同一发布批次内保持唯一；`canonical_name` 负责人可读审计，`required_context.symptoms` 继续保持自然语言准入描述。

急诊条目还必须显式声明 `code_governance.strategy` 与 `code_governance.legacy_code`；转换器会原样写入资产 metadata。该信息不参与召回或裁决，只用于阶段 4 历史编码审计，缺失或不完整时转换失败。

转换结果中的 `recognition_phrases` 同时保存主标题整体、标题拆分项、用户表达、原子症状和同句组合症状；`recognition` chunk 只从这些稳定字段构造候选召回文本，避免标题组合在转换时丢失。

运行时安全层位于 `src/vet_agent/clinical_safety/`，只保留模型、仓库和评估器等在线链路需要的对象。
