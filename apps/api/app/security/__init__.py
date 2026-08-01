"""出站网络与解析器凭证安全核心。

本包实现 T03 的统一安全传输层：
- EgressPolicy：URL 规范化、hostname/DNS/IP 分类、重定向与敏感头转发策略
- EndpointRegistry：服务端 ParserEndpointRegistry（endpoint_ref 的唯一可信解析源）
- snapshot：ParseJob 冻结快照的规范化 JSON / SHA-256 工具
- redaction：Token / 预签名 query / 秘密字段的递归脱敏
"""
