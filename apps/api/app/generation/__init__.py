"""生成链路冻结快照与 renderer 合同（T08）。

- :mod:`snapshot`：canonical JSON / SHA-256 / PromptTemplate / ModelConfig 快照构建与秘密扫描。
- :mod:`renderer`：稳定 renderer version、消息渲染与 rendered prompt hash。

创建 GenerationBatch 时在同一事务冻结模板/模型快照与 hash；worker 只读这些快照，
不得重新读取 PromptTemplate/ModelConfig 的可变参数。凭证仅通过 model config 的
服务端引用在调用前即时解析，不进入快照。
"""
